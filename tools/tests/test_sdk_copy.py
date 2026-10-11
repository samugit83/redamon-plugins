"""The synced platform copy is complete, unedited and stdlib-only (TD-24, D-49)."""

import hashlib
import re
import subprocess
import sys

import pytest

from support import PLATFORM_SDK, synced_versions

REQUIRED = (
    "VERSION",
    "SOURCE",
    "SHA256SUMS",
    "contract_cases.json",
    "semver-cases.json",
    "schemas/manifest.schema.json",
    "schemas/catalog.schema.json",
    "schemas/screen.schema.json",
    "redamon_plugin_sdk/__init__.py",
    "redamon_plugin_sdk/client.py",
    "redamon_plugin_sdk/testing.py",
)


def test_at_least_one_platform_is_synced():
    assert synced_versions(), "no platform SDK synced: run tools/sync_sdk.sh <path-to-redamon>"


@pytest.mark.parametrize("version", synced_versions())
def test_folder_holds_the_whole_contract(version):
    folder = PLATFORM_SDK / version
    missing = [f for f in REQUIRED if not (folder / f).is_file()]
    assert not missing, f"{version} lacks {missing}"


@pytest.mark.parametrize("version", synced_versions())
def test_version_file_and_sdk_constant_match_the_folder(version):
    folder = PLATFORM_SDK / version
    assert (folder / "VERSION").read_text(encoding="utf-8").strip() == version
    init = (folder / "redamon_plugin_sdk" / "__init__.py").read_text(encoding="utf-8")
    assert re.search(rf'^PLATFORM_VERSION = "{re.escape(version)}"$', init, re.M)
    assert re.fullmatch(r"[0-9a-f]{40}", (folder / "SOURCE").read_text(encoding="utf-8").strip())


@pytest.mark.parametrize("version", synced_versions())
def test_sums_cover_exactly_the_files_and_match(version):
    folder = PLATFORM_SDK / version
    listed = {}
    for line in (folder / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        listed[name] = digest
    present = {p.relative_to(folder).as_posix() for p in folder.rglob("*")
               if p.is_file() and p.name != "SHA256SUMS" and "__pycache__" not in p.parts}
    assert set(listed) == present, "SHA256SUMS does not list exactly the files present"
    edited = [n for n, d in listed.items() if hashlib.sha256((folder / n).read_bytes()).hexdigest() != d]
    assert not edited, f"edited by hand (re-run tools/sync_sdk.sh instead): {edited}"


@pytest.mark.parametrize("version", synced_versions())
def test_sdk_imports_with_only_the_standard_library(version):
    # Workers start with `python -I -S` and the SDK folder first on sys.path.
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import redamon_plugin_sdk as s; "
            "from redamon_plugin_sdk.testing import FakeSdk; print(s.PLATFORM_VERSION)")
    out = subprocess.run([sys.executable, "-I", "-S", "-c", code, str(PLATFORM_SDK / version)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == version
