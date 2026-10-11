#!/usr/bin/env python3
"""The platform's version-range grammar (RedAmon webapp/src/lib/semver.ts, TD-13).

A range is one to four space-separated comparators, each one of `>= > <= < =` glued to a full
`x.y.z` version (no pre-release, no build, no leading zeros, at most nine digits per part); a
version satisfies a range when it meets every comparator. The shared cases in
`.platform-sdk/<version>/semver-cases.json` hold this module equal to the platform's.

    python tools/semver.py lowest-sdk <plugin>    the lowest synced platform folder the range accepts
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

_PART = r"(0|[1-9][0-9]{0,8})"
_VERSION_RE = re.compile(rf"{_PART}\.{_PART}\.{_PART}")
_COMPARATOR_RE = re.compile(rf"(>=|<=|>|<|=)({_PART}\.{_PART}\.{_PART})")
MAX_COMPARATORS = 4
ROOT = pathlib.Path(__file__).resolve().parents[1]


class RangeSyntaxError(ValueError):
    pass


def parse_version(v) -> tuple:
    # fullmatch, never `$`: Python's `$` also matches before a final newline.
    m = _VERSION_RE.fullmatch(v) if isinstance(v, str) else None
    if not m:
        raise RangeSyntaxError(f'"{v}" is not a version: expected x.y.z, digits only')
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def parse_range(r) -> list:
    if not isinstance(r, str):
        raise RangeSyntaxError("a version range must be a string")
    tokens = [t for t in r.split(" ") if t != ""]
    if not tokens:
        raise RangeSyntaxError("a version range needs at least one comparator")
    if len(tokens) > MAX_COMPARATORS:
        raise RangeSyntaxError(f"a version range has at most {MAX_COMPARATORS} comparators")
    out = []
    for t in tokens:
        m = _COMPARATOR_RE.fullmatch(t)
        if not m:
            raise RangeSyntaxError(f'"{t}" is not a comparator: expected one of >= > <= < = glued to a version x.y.z')
        out.append((m.group(1), parse_version(m.group(2))))
    return out


def _holds(op: str, have: tuple, want: tuple) -> bool:
    return {">=": have >= want, ">": have > want, "<=": have <= want, "<": have < want, "=": have == want}[op]


def satisfies(version: str, rng: str) -> bool:
    v = parse_version(version)
    return all(_holds(op, v, want) for op, want in parse_range(rng))


def is_valid_version(v) -> bool:
    try:
        parse_version(v)
        return True
    except RangeSyntaxError:
        return False


def is_valid_range(r) -> bool:
    try:
        parse_range(r)
        return True
    except RangeSyntaxError:
        return False


def synced_platforms(root: pathlib.Path = ROOT) -> list:
    """The platform versions with a folder in .platform-sdk/, lowest first."""
    base = root / ".platform-sdk"
    found = []
    if base.is_dir():
        for d in base.iterdir():
            if d.is_dir() and is_valid_version(d.name) and (d / "VERSION").is_file():
                found.append(d.name)
    return sorted(found, key=parse_version)


def lowest_sdk(plugin: pathlib.Path, root: pathlib.Path = ROOT) -> str:
    """`.platform-sdk/<v>` for the lowest synced platform the plugin's range accepts."""
    manifest = json.loads((plugin / "plugin.json").read_text(encoding="utf-8"))
    rng = manifest.get("platform")
    for v in synced_platforms(root):
        if satisfies(v, rng):
            return f".platform-sdk/{v}"
    raise RangeSyntaxError(f"no synced platform version satisfies {rng!r}: run tools/sync_sdk.sh")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    low = sub.add_parser("lowest-sdk", help="print the lowest synced platform folder the plugin accepts")
    low.add_argument("plugin", type=pathlib.Path)
    low.add_argument("--root", type=pathlib.Path, default=ROOT)
    args = ap.parse_args(argv)
    try:
        print(lowest_sdk(args.plugin, args.root))
    except (RangeSyntaxError, OSError, ValueError) as e:
        print(f"semver: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
