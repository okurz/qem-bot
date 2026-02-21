# Copyright SUSE LLC
# SPDX-License-Identifier: MIT
"""Live API integration tests."""

import pytest
import requests


@pytest.mark.integration
@pytest.mark.usefixtures("wait_for_dashboard")
def test_dashboard_reachable(dashboard_url: str) -> None:
    """Basic test to verify qem-bot can talk to the dashboard."""
    resp = requests.get(f"{dashboard_url}/api/v1/incidents", timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
