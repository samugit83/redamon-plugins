"""The tools are scripts; their tests import them as modules from tools/."""

import json
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
for path in (HERE, HERE.parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from support import PLATFORM_SDK, synced_versions  # noqa: E402


@pytest.fixture
def sdk_dir() -> pathlib.Path:
    """The lowest synced platform copy (the tools validate against it, never the base repo)."""
    versions = synced_versions()
    assert versions, "no platform SDK synced: run tools/sync_sdk.sh <path-to-redamon>"
    return PLATFORM_SDK / versions[0]


@pytest.fixture
def semver_cases(sdk_dir) -> list:
    return json.loads((sdk_dir / "semver-cases.json").read_text(encoding="utf-8"))
