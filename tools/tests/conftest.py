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

# The fixture plugins carry their own tests/ folders; they are data here, not tests to run.
collect_ignore_glob = ["fixtures/*"]


@pytest.fixture
def good_plugin(tmp_path) -> pathlib.Path:
    """A fresh copy of the valid fixture plugin inside a throwaway repository root."""
    import shutil

    from support import FIXTURES

    dest = tmp_path / "repo" / "good"
    shutil.copytree(FIXTURES / "plugins" / "good", dest)
    return dest
