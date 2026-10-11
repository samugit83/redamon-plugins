"""Paths shared by the tools' tests. conftest.py puts this folder and tools/ on sys.path."""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
PLATFORM_SDK = ROOT / ".platform-sdk"
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def synced_versions() -> list:
    if not PLATFORM_SDK.is_dir():
        return []
    return sorted(p.name for p in PLATFORM_SDK.iterdir() if p.is_dir() and not p.name.startswith("."))
