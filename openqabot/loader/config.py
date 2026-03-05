# Copyright SUSE LLC
# SPDX-License-Identifier: MIT
"""Configuration loader."""

from __future__ import annotations

from logging import getLogger
from typing import TYPE_CHECKING, Any

from ruamel.yaml import YAML, YAMLError
from ruamel.yaml.constructor import ConstructorError, SafeConstructor
from ruamel.yaml.nodes import SequenceNode

from openqabot.errors import NoTestIssuesError
from openqabot.types.aggregate import Aggregate
from openqabot.types.baseconf import JobConfig
from openqabot.types.submissions import Submissions
from openqabot.types.types import Data
from openqabot.utils import get_yml_list

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


log = getLogger("bot.loader.config")


def _try_load(loader: YAML, path: Path) -> dict | None:
    """Try to load a YAML file and return its content as a dictionary."""
    try:
        data = loader.load(path)
    except YAMLError:
        log.exception("YAML load failed: File %s", path)
        return None

    if data is None:
        log.info("Configuration skipped: File %s is empty", path)
        return None

    if not isinstance(data, dict):
        log.info("Configuration skipped: File %s has invalid format", path)
        return None

    return data


def _load_one_metadata(
    path: Path,
    data: dict,
    *,
    disable_aggregate: bool,
    disable_submissions: bool,
    extrasettings: set[str],
) -> Iterator[Aggregate | Submissions]:
    """Parse a single metadata configuration dictionary.

    Yields:
        Found job configurations.

    """
    settings = data.get("settings")
    if not settings:
        log.info("Configuration skipped: Missing settings in '%s'", path)
        return

    product = data.get("product")
    if not product:
        log.debug("Configuration skipped: Missing 'product' in '%s'", path)
        return

    product_repo = data.get("product_repo")
    product_version = data.get("product_version")

    for key in data:
        if key == "incidents" and not disable_submissions:
            yield Submissions(
                JobConfig(product, product_repo, product_version, settings, data["incidents"]), extrasettings
            )
        elif key == "aggregate" and not disable_aggregate:
            try:
                yield Aggregate(JobConfig(product, product_repo, product_version, settings, data["aggregate"]))
            except NoTestIssuesError:
                log.info("Aggregate configuration skipped: Missing 'test_issues' for product %s", product)


class ConcatSafeConstructor(SafeConstructor):
    """Custom YAML constructor with !concat support.

    Subclasses ``SafeConstructor`` to isolate the ``!concat`` tag from
    the global namespace. In ``ruamel.yaml``, tag constructors registered via
    ``add_constructor`` on a class are shared across all loader instances using
    that class. By design qem-bot only support !concat on the metadata yaml.
    Subclassing ensure that ``!concat`` is only available for parser used for metadata.
    """


def concat_constructor(constructor: SafeConstructor, node: SequenceNode) -> Iterator[list[Any]]:
    """Concatenate multiple lists for the YAML !concat tag.

    This constructor uses a two-step process to support recursive references
    (anchors/aliases). It first yields an empty placeholder list so the
    library can register its reference before it is fully populated. This
    allows aliases pointing to this node to be resolved correctly.

    Yields:
        The concatenated list (initially empty to support recursive references).

    """
    # ensures that the !concat tag is only used on a YAML sequence
    if not isinstance(node, SequenceNode):
        raise ConstructorError(
            None,
            None,
            f"expected a sequence node for !concat, but found {node.id!r}",
            node.start_mark,
        )
    res: list[Any] = []
    yield res
    # Unwrapping: takes every item in the !concat and merges them into the res list.
    for child in node.value:
        obj = constructor.construct_object(child, deep=True)
        # ruamel.yaml SafeConstructor returns generators for sequence and
        # mapping nodes to support recursive references. Resolve them by
        # calling next(), using __next__ to target these generators while
        # avoiding accidental consumption of other iterables (like tuples).
        if not isinstance(obj, (list, dict, str, bytes)) and hasattr(obj, "__next__"):
            obj = next(iter(obj))
        if isinstance(obj, list):
            res.extend(obj)
        else:
            res.append(obj)


ConcatSafeConstructor.add_constructor("!concat", concat_constructor)


def load_metadata(
    path: Path,
    *,
    aggregate: bool,
    submissions: bool,
    extrasettings: set[str],
) -> list[Aggregate | Submissions]:
    """Load metadata configurations from a directory of YAML files."""
    loader = YAML(typ="safe")
    loader.Constructor = ConcatSafeConstructor
    log.debug("Loading metadata from %s: Submissions=%s, Aggregates=%s", path, not submissions, not aggregate)

    return [
        item
        for p in get_yml_list(path)
        if (data := _try_load(loader, p))
        for item in _load_one_metadata(
            p, data, disable_aggregate=aggregate, disable_submissions=submissions, extrasettings=extrasettings
        )
    ]


def _parse_product(path: Path, data: dict) -> Iterator[Data]:
    """Parse product information from a configuration dictionary.

    Yields:
        Parsed product data.

    """
    try:
        aggregate = data["aggregate"]
        flavor = aggregate["FLAVOR"]
        archs = aggregate["archs"]
        settings = data["settings"]
        distri = settings["DISTRI"]
        version = settings["VERSION"]
        product = data["product"]
    except KeyError as e:
        log.debug("Configuration skipped: File %s missing required setting %s", path, e)
        return

    yield from (Data(0, "aggregate", 0, flavor, arch, distri, version, "", product) for arch in archs)


def read_products(path: Path) -> list[Data]:
    """Read product definitions from a directory of YAML files."""
    # Intentional: !concat tag is only supported in load_metadata.
    loader = YAML(typ="safe")
    log.debug("Loading product definitions from %s", path)

    return [item for p in get_yml_list(path) if (data := _try_load(loader, p)) for item in _parse_product(p, data)]


def get_onearch(path: Path) -> set[str]:
    """Read single-architecture package names from a YAML file."""
    # Intentional: !concat tag is only supported in load_metadata.
    loader = YAML(typ="safe")

    try:
        data = loader.load(path)
    except (YAMLError, FileNotFoundError):
        return set()

    return set(data)
