# Copyright SUSE LLC
# SPDX-License-Identifier: MIT
"""Commenter class for commenting on submissions."""

from __future__ import annotations

from logging import getLogger
from pprint import pformat
from typing import TYPE_CHECKING, Any

import osc.conf

from openqabot import config
from openqabot.errors import NoResultsError

from .loader.qem import get_aggregate_results, get_submission_results, get_submissions
from .openqa import OpenQAInterface
from .osclib.comments import CommentAPI
from .results_summarizer import summarize_message

if TYPE_CHECKING:
    from argparse import Namespace

    from .types.submission import Submission

log = getLogger("bot.commenter")


class Commenter:
    """Logic for commenting on submissions in OBS."""

    def __init__(self, args: Namespace) -> None:
        """Initialize the Commenter class."""
        self.dry = args.dry
        self.token = {"Authorization": f"Token {args.token}"}
        self.client = OpenQAInterface(args)
        self.submissions = get_submissions(self.token)
        osc.conf.get_config(override_apiurl=config.settings.obs_url)
        self.commentapi = CommentAPI(config.settings.obs_url)

    def __call__(self) -> int:
        """Run the commenting process."""
        log.info("Starting to comment SMELT incidents in OBS")

        for sub in self.submissions:
            if sub.type != config.settings.default_submission_type:
                log.debug("Submission %s skipped: Not a SMELT incident (type: %s)", sub, sub.type)
                continue
            try:
                s_jobs = get_submission_results(sub.id, self.token, submission_type=sub.type)
                a_jobs = get_aggregate_results(sub.id, self.token, submission_type=sub.type)
            except ValueError as e:
                log.debug(e)
                continue
            except NoResultsError as e:
                log.debug(e)
                continue

            state = "none"
            all_jobs = s_jobs + a_jobs
            if any(j["status"] == "running" for j in all_jobs):
                log.info("Postponing comment for %s: Some tests are still running", sub)
            elif any(j["status"] not in {"passed", "softfailed"} for j in all_jobs):
                log.info("Creating 'failed' comment for %s: At least one job failed", sub)
                state = "failed"
            else:
                state = "passed"

            msg = summarize_message(self.client, all_jobs)
            self.osc_comment(sub, msg, state)

        return 0

    def osc_comment(self, sub: Submission, msg: str, state: str) -> None:
        """Comment a submission in OBS."""
        if sub.rr is None:
            log.debug("Comment skipped for submission %s: No release request defined", sub)
            return

        if not msg:
            log.debug("Skipping empty comment")
            return

        bot_name = "openqa"
        info: dict[str, Any] = {"state": state}
        if sub.revisions:
            for key in sub.revisions:
                info[f"revision_{key.version}_{key.arch}"] = sub.revisions[key]

        msg = self.commentapi.add_marker(msg, bot_name, info)
        msg = self.commentapi.truncate(msg.strip())

        kw = {"request_id": str(sub.rr)}
        comments = self.commentapi.get_comments(**kw)
        comment, _ = self.commentapi.comment_find(comments, bot_name, info)

        # To prevent spam, assume same state/result
        # and number of lines in message is a duplicate message
        if comment is not None and comment["comment"].count("\n") == msg.count("\n"):
            log.debug("Comment skipped: Previous comment is too similar")
            return

        if comment is None:
            log.debug("No comment with this state, looking without the state filter")
            comment, _ = self.commentapi.comment_find(comments, bot_name)

        if comment is None:
            log.debug("No previous comment found to replace")
        elif not self.dry:
            self.commentapi.delete(comment["id"])
        else:
            log.info("Dry run: Would delete comment %s", comment["id"])

        if not self.dry:
            self.commentapi.add_comment(comment=msg, **kw)
        else:
            log.info("Dry run: Would write comment to request %s", sub)
            log.debug(pformat(msg))
