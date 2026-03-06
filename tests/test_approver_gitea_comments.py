# Copyright SUSE LLC
# SPDX-License-Identifier: MIT
"""Tests for Gitea comments in Approver."""

import logging
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from openqabot.approver import Approver
from openqabot.errors import NoResultsError


@pytest.fixture
def args() -> Namespace:
    return Namespace(
        dry=False,
        token="dashboard_token",
        gitea_token="gitea_token",
        openqa_instance="https://openqa.suse.de",
        all_submissions=False,
        submission=None,
    )


@pytest.fixture
def approver(args: Namespace) -> Approver:
    with patch("openqabot.approver.OpenQAInterface"):
        return Approver(args)


def test_post_gitea_comment_no_url(approver: Approver, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    sub = MagicMock()
    sub.url = None
    sub.sub = 123
    approver.post_gitea_comment(sub)
    assert "Submission 123 has no URL" in caplog.text


def test_post_gitea_comment_no_results(approver: Approver, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    sub = MagicMock()
    sub.url = "https://src.suse.de/api/v1/repos/products/SLFO/pulls/123"
    sub.sub = 123
    sub.type = "git"

    with patch("openqabot.approver.get_submission_results", side_effect=NoResultsError):
        approver.post_gitea_comment(sub)
        assert "No results for 123" in caplog.text


def test_post_gitea_comment_no_jobs(approver: Approver, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    sub = MagicMock()
    sub.url = "https://src.suse.de/api/v1/repos/products/SLFO/pulls/123"
    sub.sub = 123
    sub.type = "git"

    with (
        patch("openqabot.approver.get_submission_results", return_value=[]),
        patch("openqabot.approver.get_aggregate_results", return_value=[]),
    ):
        approver.post_gitea_comment(sub)
        assert "Submission 123: No jobs found" in caplog.text


def test_post_gitea_comment_running_jobs(approver: Approver, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    sub = MagicMock()
    sub.url = "https://src.suse.de/api/v1/repos/products/SLFO/pulls/123"
    sub.sub = 123
    sub.type = "git"

    with (
        patch("openqabot.approver.get_submission_results", return_value=[{"status": "running"}]),
        patch("openqabot.approver.get_aggregate_results", return_value=[]),
    ):
        approver.post_gitea_comment(sub)
        assert "Postponing comment for 123: Some tests are still running" in caplog.text


def test_post_gitea_comment_empty_msg(approver: Approver, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    sub = MagicMock()
    sub.url = "https://src.suse.de/api/v1/repos/products/SLFO/pulls/123"
    sub.sub = 123
    sub.type = "git"

    with (
        patch("openqabot.approver.get_submission_results", return_value=[{"status": "passed"}]),
        patch("openqabot.approver.get_aggregate_results", return_value=[]),
        patch("openqabot.approver.summarize_message", return_value=""),
    ):
        approver.post_gitea_comment(sub)
        assert "Skipping empty comment for 123" in caplog.text


def test_post_gitea_comment_full(approver: Approver) -> None:
    sub = MagicMock()
    sub.url = "https://src.suse.de/api/v1/repos/products/SLFO/pulls/123"
    sub.sub = 123
    sub.type = "git"

    with (
        patch("openqabot.approver.get_submission_results", return_value=[{"status": "failed"}]),
        patch("openqabot.approver.get_aggregate_results", return_value=[]),
        patch("openqabot.approver.summarize_message", return_value="fail info"),
        patch("openqabot.loader.gitea.update_pr_comment") as mock_update,
    ):
        approver.post_gitea_comment(sub)
        mock_update.assert_called_once_with(sub.url, "fail info", approver.gitea_token, dry=False)
