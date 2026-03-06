# Copyright SUSE LLC
# SPDX-License-Identifier: MIT
"""Tests for Gitea comments in Approver."""

import logging
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest
import requests

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


def test_update_gitea_comment_invalid_url(approver: Approver, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    approver._update_gitea_comment("invalid_url", "msg")  # noqa: SLF001
    assert "Could not parse Gitea PR URL: invalid_url" in caplog.text


def test_update_gitea_comment_api_error(approver: Approver, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    with patch("openqabot.loader.gitea.get_json", side_effect=requests.RequestException("API Error")):
        approver._update_gitea_comment("https://src.suse.de/api/v1/repos/products/SLFO/pulls/123", "msg")  # noqa: SLF001
        assert "Could not fetch comments for products/SLFO PR 123" in caplog.text


def test_update_gitea_comment_create(approver: Approver, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    with (
        patch("openqabot.loader.gitea.get_json", return_value=[]),
        patch("openqabot.loader.gitea.post_json") as mock_post_json,
    ):
        approver._update_gitea_comment("https://src.suse.de/api/v1/repos/products/SLFO/pulls/123", "msg")  # noqa: SLF001
        assert "Creating new comment for products/SLFO PR 123" in caplog.text
        mock_post_json.assert_called_once()
        assert "<!-- openqabot-report -->\nmsg" in mock_post_json.call_args[0][2]["body"]


def test_update_gitea_comment_create_dry(approver: Approver, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    approver.dry = True
    with patch("openqabot.loader.gitea.get_json", return_value=[]):
        approver._update_gitea_comment("https://src.suse.de/api/v1/repos/products/SLFO/pulls/123", "msg")  # noqa: SLF001
        assert "Dry run: Would create new comment for products/SLFO PR 123" in caplog.text


def test_update_gitea_comment_update_same(approver: Approver, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    existing_comments = [{"id": 1, "body": "<!-- openqabot-report -->\nmsg"}]
    with patch("openqabot.loader.gitea.get_json", return_value=existing_comments):
        approver._update_gitea_comment("https://src.suse.de/api/v1/repos/products/SLFO/pulls/123", "msg")  # noqa: SLF001
        assert "Comment for products/SLFO PR 123 is up to date" in caplog.text


def test_update_gitea_comment_update_diff(approver: Approver, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    existing_comments = [{"id": 1, "body": "<!-- openqabot-report -->\nold msg"}]
    with (
        patch("openqabot.loader.gitea.get_json", return_value=existing_comments),
        patch("openqabot.loader.gitea.patch_json") as mock_patch_json,
    ):
        approver._update_gitea_comment("https://src.suse.de/api/v1/repos/products/SLFO/pulls/123", "new msg")  # noqa: SLF001
        assert "Updating comment for products/SLFO PR 123" in caplog.text
        mock_patch_json.assert_called_once()
        assert "<!-- openqabot-report -->\nnew msg" in mock_patch_json.call_args[0][2]["body"]


def test_update_gitea_comment_update_diff_dry(approver: Approver, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    approver.dry = True
    existing_comments = [{"id": 1, "body": "<!-- openqabot-report -->\nold msg"}]
    with patch("openqabot.loader.gitea.get_json", return_value=existing_comments):
        approver._update_gitea_comment("https://src.suse.de/api/v1/repos/products/SLFO/pulls/123", "new msg")  # noqa: SLF001
        assert "Dry run: Would update comment for products/SLFO PR 123" in caplog.text


def test_update_gitea_comment_invalid_url_no_pulls(approver: Approver, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    approver._update_gitea_comment("https://src.suse.de/api/v1/repos/products/SLFO/123", "msg")  # noqa: SLF001
    assert "Could not parse Gitea PR URL: https://src.suse.de/api/v1/repos/products/SLFO/123" in caplog.text


def test_update_gitea_comment_invalid_url_too_short(approver: Approver, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    approver._update_gitea_comment("https://src.suse.de/pulls/123", "msg")  # noqa: SLF001
    assert "Could not parse Gitea PR URL: https://src.suse.de/pulls/123" in caplog.text


def test_update_gitea_comment_invalid_url_no_number(approver: Approver, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    approver._update_gitea_comment("https://src.suse.de/repos/products/SLFO/pulls/", "msg")  # noqa: SLF001
    assert "Could not parse Gitea PR URL: https://src.suse.de/repos/products/SLFO/pulls/" in caplog.text


def test_update_gitea_comment_invalid_url_nan(approver: Approver, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    approver._update_gitea_comment("https://src.suse.de/repos/products/SLFO/pulls/abc", "msg")  # noqa: SLF001
    assert "Could not parse Gitea PR URL: https://src.suse.de/repos/products/SLFO/pulls/abc" in caplog.text


def test_git_approve_invalid_url_no_pulls(approver: Approver, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    sub = MagicMock()
    sub.url = "https://src.suse.de/api/v1/repos/products/SLFO/123"
    assert approver.git_approve(sub, "msg") is False
    assert "Could not parse Gitea PR URL: https://src.suse.de/api/v1/repos/products/SLFO/123" in caplog.text


def test_git_approve_invalid_url_too_short(approver: Approver, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    sub = MagicMock()
    sub.url = "https://src.suse.de/pulls/123"
    assert approver.git_approve(sub, "msg") is False
    assert "Could not parse Gitea PR URL: https://src.suse.de/pulls/123" in caplog.text


def test_post_gitea_comment_full(approver: Approver) -> None:
    sub = MagicMock()
    sub.url = "https://src.suse.de/api/v1/repos/products/SLFO/pulls/123"
    sub.sub = 123
    sub.type = "git"

    with (
        patch("openqabot.approver.get_submission_results", return_value=[{"status": "failed"}]),
        patch("openqabot.approver.get_aggregate_results", return_value=[]),
        patch("openqabot.approver.summarize_message", return_value="fail info"),
        patch.object(approver, "_update_gitea_comment") as mock_update,
    ):
        approver.post_gitea_comment(sub)
        mock_update.assert_called_once_with(sub.url, "fail info")
