"""Paths shared by the tools' tests. conftest.py puts this folder and tools/ on sys.path."""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
PLATFORM_SDK = ROOT / ".platform-sdk"
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"

# The smallest icons tools/listing.py accepts: the format's magic bytes, then padding. Nothing
# decodes the image, so no real picture is needed.
ICONS = {
    "icon.jpg": b"\xff\xd8\xff\xe0" + b"\0" * 60,
    "icon.png": b"\x89PNG\r\n\x1a\n" + b"\0" * 56,
    "icon.webp": b"RIFF\x38\0\0\0WEBPVP8 " + b"\0" * 48,
}
DOCS_URL = "https://example.com/docs/good"


def synced_versions() -> list:
    if not PLATFORM_SDK.is_dir():
        return []
    return sorted(p.name for p in PLATFORM_SDK.iterdir() if p.is_dir() and not p.name.startswith("."))
