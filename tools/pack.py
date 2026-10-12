#!/usr/bin/env python3
"""Packs one plugin folder into the archive a release publishes.

    python tools/pack.py <plugin> [--out-dir dist]

Writes `<out-dir>/<id>-<version>.tar.gz` and prints `{"archive", "sha256", "size"}` as JSON.
The archive is what RedAmon's strict reader accepts (webapp/src/lib/plugins/archive.ts,
TD-12): POSIX ustar, regular files and folders only, everything under one top folder named
after the plugin id. It is deterministic, so two packs of one folder are byte-identical and a
release's sha256 can be reproduced from its tag: entries sorted, times and owners zero, modes
0644 for files and 0755 for folders, gzip header without a name or time. Left out: `tests/`,
`eval/` and the listing (`listing.json` and its icon, which only the catalog uses) at the top of
the plugin, and caches anywhere. A symbolic link is refused.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import pathlib
import stat
import sys
import tarfile

import listing

EXCLUDED_TOP = {"tests", "eval"} | listing.FILES
CACHE_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
# archive.ts refuses more entries than this.
MAX_ENTRIES = 2000


class PackError(Exception):
    pass


def _skipped(rel: pathlib.PurePosixPath) -> bool:
    return (rel.parts[0] in EXCLUDED_TOP or any(p in CACHE_NAMES for p in rel.parts)
            or rel.suffix in (".pyc", ".pyo"))


def _walk(plugin: pathlib.Path, folder: pathlib.Path, out: list) -> None:
    """Pre-order, children sorted by name: the order of the archive."""
    for path in sorted(folder.iterdir(), key=lambda p: p.name):
        rel = pathlib.PurePosixPath(path.relative_to(plugin).as_posix())
        if _skipped(rel):
            continue
        mode = os.lstat(path).st_mode
        if stat.S_ISLNK(mode):
            raise PackError(f"{rel} is a symbolic link; an archive may hold only files and folders")
        if stat.S_ISDIR(mode):
            out.append((rel, None))
            _walk(plugin, path, out)
        elif stat.S_ISREG(mode):
            out.append((rel, path))
        else:
            raise PackError(f"{rel} is neither a file nor a folder")


def _info(name: str, size: int, is_dir: bool) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE if is_dir else tarfile.REGTYPE
    info.size = 0 if is_dir else size
    info.mode = 0o755 if is_dir else 0o644
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def pack(plugin, out_dir) -> dict:
    plugin = pathlib.Path(plugin)
    out_dir = pathlib.Path(out_dir)
    m = json.loads((plugin / "plugin.json").read_text(encoding="utf-8"))
    plugin_id, version = m.get("id"), m.get("version")
    if plugin.name != plugin_id:
        raise PackError(f"the folder {plugin.name} holds plugin {plugin_id}; the folder must be named after the id")

    entries: list = []
    _walk(plugin, plugin, entries)
    if len(entries) + 1 > MAX_ENTRIES:
        raise PackError(f"{len(entries) + 1} entries; RedAmon accepts at most {MAX_ENTRIES}")

    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        tar.addfile(_info(f"{plugin_id}/", 0, True))
        for rel, path in entries:
            name = f"{plugin_id}/{rel}" + ("/" if path is None else "")
            try:
                if path is None:
                    tar.addfile(_info(name, 0, True))
                else:
                    data = path.read_bytes()
                    tar.addfile(_info(name, len(data), False), io.BytesIO(data))
            except ValueError as e:
                raise PackError(f"{rel}: the name is too long for a ustar archive ({e})") from None

    gz = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=gz, compresslevel=9, mtime=0) as g:
        g.write(raw.getvalue())
    data = gz.getvalue()

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{plugin_id}-{version}.tar.gz"
    tmp = out.with_name(out.name + ".partial")
    tmp.write_bytes(data)
    tmp.replace(out)
    return {"archive": str(out), "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Pack one plugin folder into a deterministic release archive.")
    ap.add_argument("plugin", type=pathlib.Path)
    ap.add_argument("--out-dir", type=pathlib.Path, default=None,
                    help="where the archive goes (default: dist/ next to the plugin folder)")
    args = ap.parse_args(argv)
    out_dir = args.out_dir or args.plugin.resolve().parent / "dist"
    try:
        print(json.dumps(pack(args.plugin, out_dir)))
    except (PackError, OSError, ValueError) as e:
        print(f"pack: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
