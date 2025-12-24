# Copyright SUSE LLC
# SPDX-License-Identifier: MIT
"""Utility functions."""

from __future__ import annotations

import logging
import os
import re
from copy import deepcopy
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests
import urllib3
import urllib3.exceptions
import yaml
from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

if TYPE_CHECKING:
    from .types.types import Data

ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

log = getLogger("bot.utils")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def create_logger(name: str) -> logging.Logger:
    """Create and configure a logger with a stream handler."""
    log = logging.getLogger(name)
    log.setLevel(logging.INFO)
    if log.handlers:
        return log
    handler = logging.StreamHandler()
    formatter = logging.Formatter(fmt="%(asctime)s %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    handler.setFormatter(formatter)
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    return log


def strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences from text for resilient matching."""
    return ANSI_ESCAPE_RE.sub("", text)


def normalize_whitespace(text: str) -> str:
    """Collapse multiple spaces and normalize line endings for resilient comparison."""
    # Collapse multiple spaces into one
    text = re.sub(r" +", " ", text)
    # Strip leading/trailing whitespace from each line and the whole block
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def get_yml_list(path: Path) -> list[Path]:
    """Get a list of YAML files from a path."""
    if path.is_file():
        if path.suffix == ".yml":
            return [path]
        return []
    if path.is_dir():
        return sorted(p for p in path.iterdir() if p.suffix == ".yml")
    return []


def walk(data: Any) -> Any:  # noqa: ANN401
    """Recursively walk through a data structure and convert it to a dictionary."""
    if isinstance(data, dict):
        return {k: walk(v) for k, v in data.items()}
    if isinstance(data, list):
        return [walk(i) for i in data]
    if hasattr(data, "__dict__"):
        return walk(data.__dict__)
    return data


def normalize_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort and deduplicate a list of dictionaries."""
    unique = []
    for r in results:
        if r not in unique:
            unique.append(r)
    return sorted(unique, key=lambda x: str(x))


def compare_submission_data(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> bool:
    """Compare two lists of submission data for equality."""
    if len(old) != len(new):
        return False
    # Sort by number to compare
    old_sorted = sorted(old, key=lambda x: x["number"])
    new_sorted = sorted(new, key=lambda x: x["number"])
    for o, n in zip(old_sorted, new_sorted):
        # only compare relevant keys
        keys = ["number", "status", "successful_packages", "failed_or_unpublished_packages"]
        for k in keys:
            if o.get(k) != n.get(k):
                return False
    return True


def merge_dicts(dict1: dict[Any, Any], dict2: dict[Any, Any]) -> dict[Any, Any]:
    """Merge two dictionaries recursively."""
    res = dict1.copy()
    for k, v in dict2.items():
        if k in res and isinstance(res[k], dict) and isinstance(v, dict):
            res[k] = merge_dicts(res[k], v)
        else:
            res[k] = v
    return res


def number_of_retries(fallback: int = 3) -> int:
    """Determine the number of retries from environment or fallback."""
    return int(os.environ.get("QEM_BOT_RETRIES", fallback))


def make_retry_session(retries: int, backoff_factor: float) -> Session:
    """Create a requests session with retry logic."""
    session = requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=(500, 502, 503, 504),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.verify = False
    return session


retry3 = make_retry_session(3, 2)
retry5 = make_retry_session(5, 1)
retry10 = make_retry_session(10, 0.1)


def load_yaml(path: Path) -> Any:  # noqa: ANN401
    """Load a YAML file."""
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)
