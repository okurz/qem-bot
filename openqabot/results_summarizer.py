# Copyright SUSE LLC
# SPDX-License-Identifier: MIT
"""Common commenting logic for openQA result summarization."""

from __future__ import annotations

from logging import getLogger
from typing import TYPE_CHECKING, Any

import osc.core

if TYPE_CHECKING:
    from .openqa import OpenQAInterface

log = getLogger("bot.results_summarizer")


def summarize_message(client: OpenQAInterface, jobs: list[dict[str, Any]]) -> str:
    """Summarize multiple openQA jobs into a single message."""
    groups: dict[str, dict[str, Any]] = {}
    for job in jobs:
        _process_job(client, groups, job)

    msg = ""
    for group in sorted(groups.keys()):
        msg += _format_group_message(groups[group])
    return msg.rstrip("\n")


def _process_job(client: OpenQAInterface, groups: dict[str, dict[str, Any]], job: dict[str, Any]) -> None:
    """Process a single openQA job and update its group summary."""
    if "job_group" not in job:
        log.warning("Job %s skipped: Missing 'job_group'", job["job_id"])
        return

    gl = f"{_escape_for_markdown(job['job_group'])}@{_escape_for_markdown(job['flavor'])}"
    _create_group_if_missing(client, groups, job, gl)

    job_summary = _summarize_one_openqa_job(client, job)
    if job_summary is None:
        groups[gl]["unfinished"] += 1
        return

    if not job_summary:
        groups[gl]["passed"] += 1
        return

    groups[gl]["failed"].append(job_summary)


def _create_group_if_missing(
    client: OpenQAInterface, groups: dict[str, dict[str, Any]], job: dict[str, Any], gl: str
) -> None:
    """Create a new group summary entry if it doesn't exist."""
    if gl not in groups:
        groupurl = osc.core.makeurl(
            client.openqa.baseurl,
            ["tests", "overview"],
            {
                "version": job["version"],
                "groupid": job["group_id"],
                "flavor": job["flavor"],
                "distri": job["distri"],
                "build": job["build"],
            },
        )
        groups[gl] = {
            "title": f"__Group [{gl}]({groupurl})__\n",
            "passed": 0,
            "unfinished": 0,
            "failed": [],
        }


def _format_group_message(group_data: dict[str, Any]) -> str:
    """Format a single group summary into a markdown string."""
    msg = "\n\n" + group_data["title"]
    infos = []
    if group_data["passed"]:
        infos.append(f"{group_data['passed']:d} tests passed")
    if group_data["failed"]:
        infos.append(f"{len(group_data['failed']):d} tests failed")
    if group_data["unfinished"]:
        infos.append(f"{group_data['unfinished']:d} unfinished tests")
    msg += "(" + ", ".join(infos) + ")\n"
    for fail in group_data["failed"]:
        msg += fail
    return msg


def _escape_for_markdown(string: str) -> str:
    """Escape underscores for markdown."""
    return string.replace("_", r"\_")


def _summarize_one_openqa_job(client: OpenQAInterface, job: dict[str, Any]) -> str | None:
    """Summarize a single openQA job."""
    testurl = osc.core.makeurl(client.openqa.baseurl, ["tests", str(job["job_id"])])
    name = job["name"]
    if job["status"] not in {"passed", "failed", "softfailed"}:
        rstring = job["status"]
        if rstring == "none":
            return None
        return f"\n- [{name}]({testurl}) is {rstring}"

    if job["status"] == "failed":
        return f"\n- [{name}]({testurl}) failed"
    return ""
