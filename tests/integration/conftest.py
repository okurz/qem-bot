# Copyright SUSE LLC
# SPDX-License-Identifier: MIT
"""Integration tests configuration."""

import os
import time

import pytest
import requests


@pytest.fixture(scope="session")
def dashboard_url() -> str:
    """Get the dashboard URL from environment."""
    url = os.getenv("QEM_DASHBOARD")
    if not url:
        pytest.skip("QEM_DASHBOARD environment variable not set")
    assert url is not None
    return url.rstrip("/")


@pytest.fixture(scope="session")
def wait_for_dashboard(dashboard_url: str) -> None:
    """Wait for dashboard to be ready."""
    max_retries = 30
    for _ in range(max_retries):
        try:
            resp = requests.get(f"{dashboard_url}/api/v1/incidents", timeout=1)
            if resp.status_code == 200:
                return
        except requests.ConnectionError:
            pass
        time.sleep(1)
    pytest.fail(f"Dashboard at {dashboard_url} not ready after {max_retries}s")
