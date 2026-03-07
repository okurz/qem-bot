# Copyright SUSE LLC
# SPDX-License-Identifier: MIT
"""Tests for Gitea comments in Approver."""

import logging
from argparse import Namespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from openqabot.approver import Approver
from openqabot.errors import NoResultsError

if TYPE_CHECKING:
    from pytest_mock import MockerFixture
else:
    MockerFixture = Any


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
def approver(args: Namespace, mocker: MockerFixture) -> Approver:
    mocker.patch("openqabot.approver.OpenQAInterface")
    return Approver(args)


def test_post_gitea_comment_no_url(approver: Approver, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    sub = MagicMock()
    sub.url = None
    sub.sub = 123
    approver.post_gitea_comment(sub)
    assert "Submission 123 has no URL" in caplog.text


def test_post_gitea_comment_no_results(
    approver: Approver, caplog: pytest.LogCaptureFixture, mocker: MockerFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    sub = MagicMock()
    sub.url = "https://src.suse.de/api/v1/repos/products/SLFO/pulls/123"
    sub.sub = 123
    sub.type = "git"

    mocker.patch("openqabot.approver.get_submission_results", side_effect=NoResultsError)
    mocker.patch("openqabot.approver.get_aggregate_results", side_effect=NoResultsError)
    mock_update = mocker.patch("openqabot.loader.gitea.update_pr_comment")
    approver.post_gitea_comment(sub)
    assert "No submission results for 123" in caplog.text
    assert "No aggregate results for 123" in caplog.text
    assert "Submission 123: No jobs found" in caplog.text
    mock_update.assert_not_called()


def test_post_gitea_comment_no_jobs(
    approver: Approver, caplog: pytest.LogCaptureFixture, mocker: MockerFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    sub = MagicMock()
    sub.url = "https://src.suse.de/api/v1/repos/products/SLFO/pulls/123"
    sub.sub = 123
    sub.type = "git"

    mocker.patch("openqabot.approver.get_submission_results", return_value=[])
    mocker.patch("openqabot.approver.get_aggregate_results", return_value=[])
    mock_update = mocker.patch("openqabot.loader.gitea.update_pr_comment")
    approver.post_gitea_comment(sub)
    assert "Submission 123: No jobs found" in caplog.text
    mock_update.assert_not_called()


def test_post_gitea_comment_running_jobs(
    approver: Approver, caplog: pytest.LogCaptureFixture, mocker: MockerFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    sub = MagicMock()
    sub.url = "https://src.suse.de/api/v1/repos/products/SLFO/pulls/123"
    sub.sub = 123
    sub.type = "git"

    mocker.patch("openqabot.approver.get_submission_results", return_value=[{"status": "running"}])
    mocker.patch("openqabot.approver.get_aggregate_results", return_value=[])
    approver.post_gitea_comment(sub)
    assert "Postponing comment for 123: Some tests are still running" in caplog.text


def test_post_gitea_comment_empty_msg(
    approver: Approver, caplog: pytest.LogCaptureFixture, mocker: MockerFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="bot.approver")
    sub = MagicMock()
    sub.url = "https://src.suse.de/api/v1/repos/products/SLFO/pulls/123"
    sub.sub = 123
    sub.type = "git"

    mocker.patch("openqabot.approver.get_submission_results", return_value=[{"status": "passed"}])
    mocker.patch("openqabot.approver.get_aggregate_results", return_value=[])
    mocker.patch("openqabot.approver.summarize_message", return_value="")
    approver.post_gitea_comment(sub)
    assert "Skipping empty comment for 123" in caplog.text


def test_post_gitea_comment_full(approver: Approver, mocker: MockerFixture) -> None:
    sub = MagicMock()
    sub.url = "https://src.suse.de/api/v1/repos/products/SLFO/pulls/123"
    sub.sub = 123
    sub.type = "git"

    mocker.patch("openqabot.approver.get_submission_results", return_value=[{"status": "failed"}])
    mocker.patch("openqabot.approver.get_aggregate_results", return_value=[])
    mocker.patch("openqabot.approver.summarize_message", return_value="fail info")
    mock_update = mocker.patch("openqabot.loader.gitea.update_pr_comment")
    approver.post_gitea_comment(sub)
    mock_update.assert_called_once_with(sub.url, "fail info", approver.gitea_token, dry=False)
