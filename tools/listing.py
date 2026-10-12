"""A plugin's listing: the icon and documentation link RedAmon's Plugins page shows on its card.

    <plugin>/listing.json   {"icon": "icon.jpg", "docsUrl": "https://…"}, both keys optional
    <plugin>/icon.jpg       or icon.png, icon.webp: the file `icon` names

Presentation only, so it stays out of the manifest (plugin.json is strict on platform 1.0.0: a
new field there would make older RedAmons refuse the plugin) and out of the archive (tools/pack.py
leaves these files out; the Runner never reads them). tools/catalog.py copies it into the plugin's
catalog entry, whose schema is not strict, and tools/validate.py checks it. The synced 1.0.0
catalog schema does not describe these fields, so the rules of RedAmon's
`webapp/src/lib/plugins/schemas/catalog.ts` are applied here: a RedAmon that knows them refuses
the whole catalog when one entry breaks them.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import stat
import urllib.parse

LISTING = "listing.json"
KEYS = ("icon", "docsUrl")
# RedAmon serves the icon with this type, so the bytes must be what the extension says.
ICON_TYPES = {"icon.jpg": "image/jpeg", "icon.png": "image/png", "icon.webp": "image/webp"}
_MAGIC = {
    "icon.jpg": ((0, b"\xff\xd8\xff"),),
    "icon.png": ((0, b"\x89PNG\r\n\x1a\n"),),
    "icon.webp": ((0, b"RIFF"), (8, b"WEBP")),
}
FILES = frozenset({LISTING, *ICON_TYPES})
# catalog.ts ICON_MAX_BYTES.
ICON_MAX_BYTES = 262_144
# Counted in UTF-16 units, as RedAmon's schema (JavaScript) counts a string.
DOCS_URL_MAX_UTF16 = 500
# Stricter than the WHATWG parser RedAmon uses (`new URL`), so nothing passing here fails there:
# no IP literal, no percent-encoded or non-ASCII host.
_HOST_RE = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*")


class ListingError(Exception):
    pass


def _docs_url_problem(url) -> str | None:
    if not isinstance(url, str) or not url.startswith("https://"):
        return "must be an https:// URL"
    if any(ch.isspace() for ch in url):
        return "must not contain whitespace"
    if len(url.encode("utf-16-le", "surrogatepass")) // 2 > DOCS_URL_MAX_UTF16:
        return f"is longer than {DOCS_URL_MAX_UTF16} characters"
    try:
        parts = urllib.parse.urlsplit(url)
        parts.port  # raises for a port that is not a number up to 65535, which `new URL` refuses
    except ValueError:
        return "is not a valid URL"
    host = parts.hostname or ""
    if not _HOST_RE.fullmatch(host) or host.rsplit(".", 1)[-1].isdigit():
        return "needs a host name of letters, digits, dots and hyphens"
    return None


def _icon_problems(plugin: pathlib.Path, name: str) -> list:
    path = plugin / name
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return [f"{LISTING} icon: {name} is missing"]
    if stat.S_ISLNK(mode):
        return [f"{name} is a symbolic link (the catalog pins the bytes of the file itself)"]
    if not stat.S_ISREG(mode):
        return [f"{name} is not a file"]
    size = path.stat().st_size
    if size == 0:
        return [f"{name} is empty"]
    if size > ICON_MAX_BYTES:
        return [f"{name}: {size} bytes; at most {ICON_MAX_BYTES} (256 KiB)"]
    with path.open("rb") as f:
        head = f.read(12)
    if not all(head[at:at + len(magic)] == magic for at, magic in _MAGIC[name]):
        return [f"{name}: the bytes are not {ICON_TYPES[name]} (they must match the extension)"]
    return []


def _unnamed(present: list, named) -> list:
    return [f'{name} is not named by {LISTING} (give it as "icon" there, or remove the file)'
            for name in present if name != named]


def check(plugin) -> list:
    """Every problem with the plugin's listing, one line each; [] for a good one or none."""
    plugin = pathlib.Path(plugin)
    present = [name for name in ICON_TYPES if os.path.lexists(plugin / name)]
    path = plugin / LISTING
    if not os.path.lexists(path):
        return _unnamed(present, None)
    if path.is_symlink():
        return [f"{LISTING} is a symbolic link"]
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        return [f"{LISTING}: cannot be read ({e.__class__.__name__})"]
    except (ValueError, RecursionError):
        return [f"{LISTING}: not valid JSON"]
    if not isinstance(doc, dict):
        return [f"{LISTING} is not a JSON object"]

    errors = [f'{LISTING}: unknown key "{key}" (only {" and ".join(KEYS)})' for key in sorted(set(doc) - set(KEYS))]
    named = doc.get("icon")
    if "icon" in doc:
        if not isinstance(named, str) or named not in ICON_TYPES:
            errors.append(f"{LISTING} icon: must be one of {', '.join(ICON_TYPES)}")
        else:
            errors.extend(_icon_problems(plugin, named))
    errors.extend(_unnamed(present, named))
    if "docsUrl" in doc:
        problem = _docs_url_problem(doc["docsUrl"])
        if problem:
            errors.append(f"{LISTING} docsUrl: {problem}")
    return errors


def read(plugin) -> dict:
    """The checked listing.json of a plugin folder; {} without one."""
    errors = check(plugin)
    if errors:
        raise ListingError("; ".join(errors))
    path = pathlib.Path(plugin) / LISTING
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
