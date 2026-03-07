# Copyright SUSE LLC
# SPDX-License-Identifier: MIT
"""Gitea loader."""

from __future__ import annotations

import json
import re
from collections import Counter
from concurrent import futures
from dataclasses import dataclass, field
from functools import lru_cache
from io import BytesIO
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING, Any

import osc.conf
import osc.core
import osc.util.xml
import requests
from lxml import etree  # type: ignore[unresolved-import]
from osc.connection import http_GET
from osc.core import MultibuildFlavorResolver

from openqabot import config
from openqabot.utils import retry10 as retried_requests

if TYPE_CHECKING:
    from openqabot.types.pullrequest import PullRequest
    from openqabot.types.types import Repos

ARCHS = {"x86_64", "aarch64", "ppc64le", "s390x"}
log = getLogger("bot.loader.gitea")


@dataclass
class BuildResults:
    """Build results."""

    projects: set[str] = field(default_factory=set)
    successful: set[str] = field(default_factory=set)
    unpublished: set[str] = field(default_factory=set)
    failed: set[str] = field(default_factory=set)
    unavailable: set[str] = field(default_factory=set)


PROJECT_PRODUCT_REGEX = re.compile(r".*:PullRequest:\d+:(.*)")
SCMSYNC_REGEX = re.compile(r".*/products/(.*)#([\d\.]{2,6})$")
VERSION_EXTRACT_REGEX = re.compile(r"[.\d]+")
OBS_PROJECT_SHOW_REGEX = re.compile(r".*/project/show/([^/\s\?\#\)]+)")
URL_FINDALL_REGEX = re.compile(r"https?://[^\s\?\#\)]*[^\s\?\#\)\.]")
GITEA_PR_URL_REGEX = re.compile(r".*/([^/]+/[^/]+)/pulls/(\d+)(?:$|[/?#])")


def make_token_header(token: str) -> dict[str, str]:  # noqa: D103
    return {} if token is None else {"Authorization": "token " + token}


def parse_pr_url(url: str) -> tuple[str, int] | None:  # noqa: D103
    match = GITEA_PR_URL_REGEX.search(url)
    if not match:
        return None
    return match.group(1), int(match.group(2))


def update_pr_comment(url: str, msg: str, token: dict[str, str], *, dry: bool = False) -> None:  # noqa: D103
    if not (res := parse_pr_url(url)):
        log.error("Could not parse PR URL: %s", url)
        return
    repo, num = res
    full = f"<!-- openqabot-report -->\n{msg}"
    c_url = _comments_url(repo, num)

    try:
        old = next((c for c in _get_json(c_url, token) if "<!-- openqabot-report -->" in c.get("body", "")), None)
    except Exception:  # noqa: BLE001
        log.exception("Could not fetch comments for %s PR %s", repo, num)
        return

    if old:
        if old["body"].strip() == full.strip():
            log.debug("Comment for %s PR %s up to date", repo, num)
            return
        if not dry:
            log.info("Updating comment for %s PR %s", repo, num)
            _patch_json(f"repos/{repo}/issues/comments/{old['id']}", token, {"body": full})
        else:
            log.info("Dry: Would update comment for %s PR %s", repo, num)
    elif not dry:
        log.info("Creating comment for %s PR %s", repo, num)
        _post_json(c_url, token, {"body": full})
    else:
        log.info("Dry: Would create comment for %s PR %s", repo, num)


def _get_json(query: str, token: dict[str, str], host: str | None = None) -> Any:  # noqa: ANN401
    host = host or config.settings.gitea_url
    response = retried_requests.get(host + "/api/v1/" + query, verify=config.settings.gitea_verify, headers=token)
    response.raise_for_status()
    return response.json()


def _post_json(query: str, token: dict[str, str], post_data: Any, host: str | None = None) -> Any:  # noqa: ANN401
    host = host or config.settings.gitea_url
    url = host + "/api/v1/" + query
    res = retried_requests.post(url, verify=config.settings.gitea_verify, headers=token, json=post_data)
    if not res.ok:
        log.error("Gitea API error: POST to %s failed: %s", url, res.text)


def _patch_json(query: str, token: dict[str, str], patch_data: Any, host: str | None = None) -> Any:  # noqa: ANN401
    host = host or config.settings.gitea_url
    url = host + "/api/v1/" + query
    res = retried_requests.patch(url, verify=config.settings.gitea_verify, headers=token, json=patch_data)
    if not res.ok:
        log.error("Gitea API error: PATCH to %s failed: %s", url, res.text)


@lru_cache(maxsize=128)
def _read_utf8(name: str) -> str:
    return Path(f"responses/{name}").read_text(encoding="utf8")


@lru_cache(maxsize=128)
def _read_json(name: str) -> Any:  # noqa: ANN401
    return json.loads(_read_utf8(name + ".json"))


@lru_cache(maxsize=128)
def _read_xml(name: str) -> etree.ElementTree:
    return etree.parse(BytesIO(_read_utf8(name + ".xml").encode("utf-8")))


def _reviews_url(repo_name: str, number: int) -> str:
    return f"repos/{repo_name}/pulls/{number}/reviews"


def _changed_files_url(repo_name: str, number: int) -> str:
    return f"repos/{repo_name}/pulls/{number}/files"


def _comments_url(repo_name: str, number: int) -> str:
    return f"repos/{repo_name}/issues/{number}/comments"


def _staging_config_url(repo_name: str, branch: str) -> str:
    """Generate url pointing to staging.config file for certain repo."""
    return f"{config.settings.gitea_url}/products/{repo_name}/raw/branch/{branch}/staging.config"


def get_product_name(obs_project: str) -> str:  # noqa: D103
    product_match = PROJECT_PRODUCT_REGEX.search(obs_project)
    return product_match.group(1) if product_match else ""


def _get_product_name_and_version_from_scmsync(scmsync_url: str) -> tuple[str, str]:
    m = SCMSYNC_REGEX.search(scmsync_url)
    return (m.group(1), m.group(2)) if m else ("", "")


def compute_repo_url_for_job_setting(  # noqa: D103
    base: str,
    repo: Repos,
    product_repo: list[str] | str | None,
    product_version: str | None,
) -> str:
    product_names = get_product_name(repo.version) if product_repo is None else product_repo
    p_ver = product_version or repo.product_version
    product_list = product_names if isinstance(product_names, list) else [product_names]
    repo_with_opts = repo._replace(product_version=p_ver)
    return ",".join(repo_with_opts.compute_url(base, p, path="", project="SLFO") for p in product_list)


def _get_single_pr(token: dict[str, str], repo: str, number: int) -> list[Any]:
    try:
        return [_get_json(f"repos/{repo}/pulls/{number}", token)]
    except Exception:  # noqa: BLE001
        log.exception("PR git:%s ignored: Could not read PR metadata", number)
        return []


def _get_all_prs(token: dict[str, str], repo: str) -> list[Any]:
    def it() -> Any:  # noqa: ANN401
        p = 1
        while True:
            prs = _get_json(f"repos/{repo}/pulls?state=open&page={p}", token)
            if not isinstance(prs, list) or not prs:
                break
            yield from prs
            p += 1

    try:
        return list(it())
    except Exception:  # noqa: BLE001
        log.exception("Fetching open PRs failed")
        return []


def get_open_prs(token: dict[str, str], repo: str, *, dry: bool, number: int | None) -> list[Any]:  # noqa: D103
    if dry:
        return _read_json("pulls")
    return _get_single_pr(token, repo, number) if number else _get_all_prs(token, repo)


def review_pr(  # noqa: PLR0913, D103
    token: dict[str, str],
    repo_name: str,
    pr_number: int,
    msg: str,
    commit_id: str,
    *,
    approve: bool = True,
    dry: bool = False,
) -> None:
    bot = config.settings.git_review_bot_user
    review_url = _comments_url(repo_name, pr_number) if bot else _reviews_url(repo_name, pr_number)
    if bot:
        cmd = "approved" if approve else "decline"
        data = {"body": f"@{bot}: {cmd}\n{msg}\nTested commit: {commit_id}"}
    else:
        data = {
            "body": msg,
            "comments": [],
            "commit_id": commit_id,
            "event": "APPROVED" if approve else "REQUEST_CHANGES",
        }

    if not dry:
        log.info("%s PR %s in Gitea", "Approving" if approve else "Declining", pr_number)
        _post_json(review_url, token, data)
    else:
        log.info("Dry run: Would %s PR %s in Gitea", "approve" if approve else "decline", pr_number)


def _get_name(review: dict[str, Any], of: str, via: str) -> str:
    entity = review.get(of)
    return entity.get(via, "") if entity is not None else ""


def _is_review_requested_by(
    review: dict[str, Any],
    users: tuple[str | None, ...] | None = None,
) -> bool:
    if users is None:
        users = (config.settings.obs_group, config.settings.git_review_bot_user)
    user_specifications = (
        _get_name(review, "user", "login"),
        _get_name(review, "team", "name"),
    )
    return any(user in user_specifications for user in users)


def _add_reviews(sub: dict[str, Any], revs: list[Any]) -> int:
    qam = [r for r in revs if not r.get("dismissed", True) and _is_review_requested_by(r)]
    cnt = Counter(r.get("state", "") for r in qam)
    p = cnt["PENDING"] + cnt["REQUEST_REVIEW"]
    sub.update({
        "approved": cnt["APPROVED"] > 0 and not (cnt["REQUEST_CHANGES"] + cnt["REQUEST_REVIEW"]),
        "inReviewQAM": p > 0,
        "inReview": p > 0
        or any(
            r.get("state") in {"PENDING", "REQUEST_REVIEW"}
            for r in revs
            if not r.get("dismissed", True) and not _is_review_requested_by(r)
        ),
    })
    return len(qam)


def _extract_version(name: str, prefix: str) -> str:
    remainder = name.removeprefix(prefix)
    return next((part for part in remainder.split("-") if VERSION_EXTRACT_REGEX.search(part)), "")


@lru_cache(maxsize=512)
def _get_product_version_from_repo_listing(project: str, product_name: str, repository: str) -> str:
    url = f"{config.settings.obs_download_url}/{project.replace(':', ':/')}/{repository}/repo?jsontable"
    start = f"{product_name}-"
    try:
        r = retried_requests.get(url)
        r.raise_for_status()
        data = r.json()["data"]
        versions = (_extract_version(e["name"], start) for e in data if e["name"].startswith(start))
        return next((v for v in versions if v), "")
    except (requests.RequestException, KeyError, json.JSONDecodeError) as e:
        log.warning("Could not query %s: %s", url, e)
        return ""


def _add_channel_for_build_result(
    project: str,
    arch: str,
    product_name: str,
    res: etree._Element,
    projects: set[str],
) -> str:
    """Construct a channel string for a build result and add it to the project set."""
    channel = f"{project}:{arch}"
    if arch == "local":
        return channel

    v = next(
        (
            pv
            for _, pv in (_get_product_name_and_version_from_scmsync(e.text) for e in res.findall("scmsync") if e.text)
            if pv
        ),
        "",
    )
    obs = config.settings.obs_products_set
    if not v and product_name and ("all" in obs or product_name in obs):
        v = _get_product_version_from_repo_listing(project, product_name, res.get("repository"))

    if v:
        channel = f"{channel}#{v}"
    elif product_name:
        log.debug("Channel skipped: No version for %s:%s", project, arch)
        return channel

    projects.add(channel)
    return channel


def _add_build_result(sub: dict[str, Any], res: etree._Element, results: BuildResults) -> None:
    p, a = res.get("project"), res.get("arch")
    prod = get_product_name(p)
    k = f"scminfo_{prod}" if prod else "scminfo"
    for t in (e.text for e in res.findall("scminfo") if e.text):
        if sub.get(k, t) != t:
            log.warning("Inconsistent SCM info for %s", p)
        else:
            sub[k] = t

    chan = _add_channel_for_build_result(p, a, prod, res, results.projects)
    obs = config.settings.obs_products_set
    if "all" in obs or prod in obs:
        if res.get("state") != "published":
            results.unpublished.add(chan)
        else:
            st = res.findall("status")
            results.successful.update(s.get("package") for s in st if s.get("code") == "succeeded")
            results.failed.update(s.get("package") for s in st if s.get("code") not in {"excluded", "succeeded"})


def _get_multibuild_data(obs_project: str) -> str:
    r = MultibuildFlavorResolver(config.settings.obs_url, obs_project, "000productcompose")
    data = r.get_multibuild_data()
    return data.decode("utf-8") if isinstance(data, bytes) else str(data or "")


def _determine_relevant_archs_from_multibuild_info(obs_project: str, *, dry: bool) -> set[str] | None:
    if not (p_name := get_product_name(obs_project)):
        return None
    p_pre = p_name.replace("SL-", "sle_").replace(":", "_").lower() + "_"

    try:
        if dry:
            data = _read_utf8(f"_multibuild-124-{obs_project}.xml")
        else:
            data = _get_multibuild_data(obs_project)
    except Exception as e:  # noqa: BLE001
        log.warning("No archs for %s: %s", obs_project, e)
        return None

    flavs = MultibuildFlavorResolver.parse_multibuild_data(data)
    archs = {a for f in flavs if f.startswith(p_pre) and (a := f[len(p_pre) :]) in ARCHS}
    log.debug("Archs for %s: %s", obs_project, sorted(archs))
    return archs


def _is_build_result_relevant(res: etree._Element, relevant_archs: set[str] | None) -> bool:
    """Check if a build result is relevant for the current product and architecture."""
    if config.settings.obs_repo_type and res.get("repository") != config.settings.obs_repo_type:
        return False
    arch = res.get("arch")
    return arch == "local" or relevant_archs is None or arch in relevant_archs


def _process_obs_url(
    url: str,
    submission: dict[str, Any],
    *,
    dry: bool,
    results: BuildResults,
) -> None:
    if not (project_match := OBS_PROJECT_SHOW_REGEX.search(url)):
        return
    obs_project = project_match.group(1)
    log.debug("Checking OBS project %s", obs_project)
    relevant_archs = _determine_relevant_archs_from_multibuild_info(obs_project, dry=dry)

    build_info_url = osc.core.makeurl(config.settings.obs_url, ["build", obs_project, "_result"])
    try:
        root = (
            _read_xml("build-results-124-" + obs_project).getroot()
            if dry
            else osc.util.xml.xml_parse(http_GET(build_info_url)).getroot()
        )
        for res in root.findall("result"):
            if _is_build_result_relevant(res, relevant_archs):
                _add_build_result(submission, res, results)
    except Exception:  # noqa: BLE001
        results.unavailable.add(obs_project)
        log.info("Build results for project %s unreadable", obs_project)


def _add_build_results(submission: dict[str, Any], obs_urls: list[str], *, dry: bool) -> None:  # noqa: D103
    results = BuildResults()

    for url in obs_urls:
        _process_obs_url(url, submission, dry=dry, results=results)

    submission.update({
        "failed_or_unpublished_packages": sorted(results.failed | results.unpublished | results.unavailable),
        "successful_packages": sorted(results.successful),
    })

    ch = submission.setdefault("channels", [])
    ch.extend(p for p in sorted(results.projects) if p not in ch)

    obs_p = config.settings.obs_products_set
    if "scminfo" not in submission and len(obs_p) == 1 and "all" not in obs_p:
        submission["scminfo"] = submission.get(f"scminfo_{next(iter(obs_p))}", "")


def _add_comments_and_referenced_build_results(
    submission: dict[str, Any],
    comments: list[Any],
    *,
    dry: bool,
) -> None:
    bot_comments = [
        comment for comment in comments if comment["user"]["username"] == config.settings.git_obs_staging_bot_user
    ]
    if not bot_comments:
        return

    obs_urls = {url for comment in bot_comments for url in URL_FINDALL_REGEX.findall(comment["body"])}

    if obs_urls:
        _add_build_results(submission, sorted(obs_urls), dry=dry)
    else:
        log.warning(
            "PR git:%s: No OBS URLs found in comments from %s",
            submission["number"],
            config.settings.git_obs_staging_bot_user,
        )


def generate_repo_url(pullrequest: PullRequest, token: dict[str, str]) -> str:
    """Generate repository URL for certain pull request.

    Args:
        pullrequest (PullRequest): pull request for which URL needs to be generated
        token (dict[str, str]): security token for Gitea API

    Returns:
        str: URL pointing to a folder with iso images generated for certain pullrequest

    """
    response = retried_requests.get(
        _staging_config_url(pullrequest.repo_name, pullrequest.branch),
        verify=False,
        headers=token,
    )
    response.raise_for_status()
    project = response.json()["StagingProject"].replace(":", ":/")
    return f"{config.settings.obs_download_url}/{project}:/{pullrequest.number}:/{pullrequest.product}/product/iso"


def _add_packages_from_patchinfo(
    submission: dict[str, Any],
    token: dict[str, str],
    patch_info_url: str,
    *,
    dry: bool,
) -> None:
    try:
        if dry:
            patch_info = _read_xml("patch-info")
        else:
            response = retried_requests.get(patch_info_url, verify=config.settings.gitea_verify, headers=token)
            response.raise_for_status()
            patch_info = etree.fromstring(response.content)

        submission["packages"].extend(res.text for res in patch_info.findall("package"))
    except Exception as e:  # noqa: BLE001
        log.info("Failed to parse patchinfo from %s: %s", patch_info_url, e)


def _add_packages_from_files(submission: dict[str, Any], token: dict[str, str], files: list[Any], *, dry: bool) -> None:
    for file_info in files:
        file_name = file_info.get("filename", "").split("/")[-1]
        raw_url = file_info.get("raw_url")
        if file_name == "_patchinfo" and raw_url is not None:
            _add_packages_from_patchinfo(submission, token, raw_url, dry=dry)


def _is_build_acceptable_and_log_if_not(submission: dict[str, Any], number: int) -> bool:
    failed = len(submission["failed_or_unpublished_packages"])
    if failed > 0:
        log.info("PR git:%i skipped: %i failed/unpub", number, failed)
        return False
    if len(submission["successful_packages"]) < 1:
        log.info("PR git:%i skipped: No built packages", number)
        return False
    return True


def _fetch_pr_data(
    repo_name: str, number: int, token: dict[str, str], *, dry: bool
) -> tuple[list[Any], list[Any], list[Any]]:
    if dry:
        if number == 124:  # noqa: PLR2004
            return _read_json("reviews-124"), _read_json("comments-124"), _read_json("files-124")
        return [], [], []
    return (
        _get_json(_reviews_url(repo_name, number), token),
        _get_json(_comments_url(repo_name, number), token),
        _get_json(_changed_files_url(repo_name, number), token),
    )


def make_submission_from_gitea_pr(  # noqa: D103
    pr: dict[str, Any],
    token: dict[str, str],
    *,
    only_successful_builds: bool,
    only_requested_prs: bool,
    dry: bool,
) -> dict[str, Any] | None:
    try:
        number, repo = pr["number"], pr["base"]["repo"]
        sub = {
            "number": number,
            "project": repo["name"],
            "emu": False,
            "isActive": pr["state"] == "open",
            "inReviewQAM": False,
            "inReview": False,
            "approved": False,
            "embargoed": False,
            "priority": 0,
            "rr_number": None,
            "packages": [],
            "channels": [],
            "url": pr["url"],
            "type": "git",
        }
        revs, comms, files = _fetch_pr_data(repo["full_name"], number, token, dry=dry)
        if _add_reviews(sub, revs) < 1 and only_requested_prs:
            log.info("PR git:%s skipped: No reviews", number)
            return None
        _add_comments_and_referenced_build_results(sub, comms, dry=dry)
        if not sub["channels"]:
            return None
        if only_successful_builds and not _is_build_acceptable_and_log_if_not(sub, number):
            return None
        _add_packages_from_files(sub, token, files, dry=dry)
        return sub if sub["packages"] else None

    except Exception:  # noqa: BLE001
        log.exception("PR git:%s processing failed", pr.get("number", "?"))
        return None


def get_submissions_from_open_prs(  # noqa: D103
    open_prs: list[dict[str, Any]],
    token: dict[str, str],
    *,
    only_successful_builds: bool,
    only_requested_prs: bool,
    dry: bool,
) -> list[dict[str, Any]]:
    # configure osc to be able to request build info from OBS
    osc.conf.get_config(override_apiurl=config.settings.obs_url)

    with futures.ThreadPoolExecutor() as executor:
        future_sub = [
            executor.submit(
                make_submission_from_gitea_pr,
                pr,
                token,
                only_successful_builds=only_successful_builds,
                only_requested_prs=only_requested_prs,
                dry=dry,
            )
            for pr in open_prs
        ]
        submissions = (future.result() for future in futures.as_completed(future_sub))
        return [sub for sub in submissions if sub]
