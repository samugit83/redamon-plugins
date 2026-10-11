#!/usr/bin/env python3
"""Writes the plugin catalog RedAmon installs from (README.PLUGINS.md §6.2).

    python tools/catalog.py add-release <plugin> --archive <tar.gz> (--url URL | --repo OWNER/NAME)
    python tools/catalog.py refresh
    python tools/catalog.py local <plugin> --archive <tar.gz> (--url URL | --repo OWNER/NAME) --out dist/catalog.json

`add-release` reads `catalog.json` (or starts an empty one), adds or replaces the plugin's
release with fields taken from its manifest and the archive, raises `serial` by one and sets
`expires`. `refresh` changes only `serial` and `expires`, for the monthly re-signing. `local`
writes a one-release catalog for an offline install; its `serial` is the clock, because
RedAmon keeps the highest serial it accepted per signing key and refuses a lower one as a
replay. Every result is checked against the catalog schema of each synced platform before it
is written. Signing is tools/sign.py's job.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import sys

import jsonschema

import semver

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "catalog.json"
DEFAULT_DAYS = 90


class CatalogError(Exception):
    pass


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now(now) -> dt.datetime:
    return now if now is not None else dt.datetime.now(dt.timezone.utc)


def _schemas() -> list:
    out = []
    for version in semver.synced_platforms(ROOT):
        path = ROOT / ".platform-sdk" / version / "schemas" / "catalog.schema.json"
        out.append(json.loads(path.read_text(encoding="utf-8")))
    if not out:
        raise CatalogError("no platform SDK synced: run tools/sync_sdk.sh <path-to-redamon>")
    return out


def _check(doc: dict) -> None:
    for schema in _schemas():
        errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(doc), key=lambda e: list(e.absolute_path))
        if errors:
            e = errors[0]
            where = "/".join(str(p) for p in e.absolute_path) or "(root)"
            raise CatalogError(f"the catalog would not be valid: {where}: {e.message}")


def _write(path: pathlib.Path, doc: dict) -> None:
    _check(doc)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read(path: pathlib.Path) -> dict:
    if not path.is_file():
        raise CatalogError(f"no catalog at {path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("catalogVersion") != 1 or not isinstance(doc.get("serial"), int):
        raise CatalogError(f"{path} is not a version 1 catalog")
    return doc


def _manifest(plugin: pathlib.Path) -> dict:
    return json.loads((pathlib.Path(plugin) / "plugin.json").read_text(encoding="utf-8"))


def release_url(repo: str, plugin_id: str, version: str) -> str:
    return f"https://github.com/{repo}/releases/download/{plugin_id}-v{version}/{plugin_id}-{version}.tar.gz"


def _release(m: dict, archive: pathlib.Path, url: str) -> dict:
    if not isinstance(url, str) or not url.startswith("https://"):
        raise CatalogError(f"url must be an https URL (RedAmon downloads archives over https only): {url!r}")
    data = pathlib.Path(archive).read_bytes()
    return {
        "version": m["version"],
        "platform": m["platform"],
        "dataVersion": m["dataVersion"],
        "url": url,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "permissions": list(m["permissions"]),
    }


def _with_release(doc: dict, m: dict, release: dict) -> dict:
    plugins = [p for p in doc.get("plugins", []) if p.get("id") != m["id"]]
    previous = next((p for p in doc.get("plugins", []) if p.get("id") == m["id"]), None)
    releases = [r for r in (previous or {}).get("releases", []) if r.get("version") != release["version"]]
    releases.append(release)
    releases.sort(key=lambda r: semver.parse_version(r["version"]))
    plugins.append({"id": m["id"], "displayName": m["displayName"], "description": m["description"],
                    "releases": releases})
    plugins.sort(key=lambda p: p["id"])
    return {**doc, "plugins": plugins}


def _empty() -> dict:
    return {"catalogVersion": 1, "serial": 0, "expires": "", "plugins": [], "revoked": []}


def _ordered(doc: dict, serial: int, expires: str) -> dict:
    return {"catalogVersion": 1, "serial": serial, "expires": expires,
            "plugins": doc.get("plugins", []), "revoked": doc.get("revoked", [])}


def add_release(catalog_path, plugin, *, archive, url, now=None, days: int = DEFAULT_DAYS) -> dict:
    catalog_path = pathlib.Path(catalog_path)
    doc = _read(catalog_path) if catalog_path.exists() else _empty()
    m = _manifest(plugin)
    doc = _with_release(doc, m, _release(m, archive, url))
    out = _ordered(doc, doc["serial"] + 1, _iso(_now(now) + dt.timedelta(days=days)))
    _write(catalog_path, out)
    return out


def refresh(catalog_path, *, now=None, days: int = DEFAULT_DAYS) -> dict:
    catalog_path = pathlib.Path(catalog_path)
    doc = _read(catalog_path)
    out = _ordered(doc, doc["serial"] + 1, _iso(_now(now) + dt.timedelta(days=days)))
    _write(catalog_path, out)
    return out


def local(out_path, plugin, *, archive, url, now=None, days: int = DEFAULT_DAYS, serial=None) -> dict:
    now = _now(now)
    m = _manifest(plugin)
    doc = _with_release(_empty(), m, _release(m, archive, url))
    out = _ordered(doc, int(now.timestamp()) if serial is None else int(serial),
                   _iso(now + dt.timedelta(days=days)))
    _write(pathlib.Path(out_path), out)
    return out


def _parse_now(text):
    if text is None:
        return None
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Write the plugin catalog.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--days", type=int, default=DEFAULT_DAYS, help="expiry, in days from now (default 90)")
        p.add_argument("--now", default=None, help=argparse.SUPPRESS)

    for name in ("add-release", "local"):
        p = sub.add_parser(name)
        p.add_argument("plugin", type=pathlib.Path)
        p.add_argument("--archive", type=pathlib.Path, required=True)
        where = p.add_mutually_exclusive_group(required=True)
        where.add_argument("--url")
        where.add_argument("--repo", help="OWNER/NAME: the GitHub release URL is derived from it")
        common(p)
        if name == "add-release":
            p.add_argument("--catalog", type=pathlib.Path, default=DEFAULT_CATALOG)
        else:
            p.add_argument("--out", type=pathlib.Path, default=ROOT / "dist" / "catalog.json")
            p.add_argument("--serial", type=int, default=None)
    p = sub.add_parser("refresh")
    p.add_argument("--catalog", type=pathlib.Path, default=DEFAULT_CATALOG)
    common(p)

    args = ap.parse_args(argv)
    now = _parse_now(args.now)
    try:
        if args.cmd == "refresh":
            path, doc = args.catalog, refresh(args.catalog, now=now, days=args.days)
            what = "refreshed"
        else:
            m = _manifest(args.plugin)
            url = args.url or release_url(args.repo, m["id"], m["version"])
            if args.cmd == "add-release":
                path = args.catalog
                doc = add_release(path, args.plugin, archive=args.archive, url=url, now=now, days=args.days)
            else:
                path = args.out
                doc = local(path, args.plugin, archive=args.archive, url=url, now=now, days=args.days, serial=args.serial)
            what = f"{m['id']} {m['version']}"
    except (CatalogError, OSError, ValueError, KeyError) as e:
        print(f"catalog: {e}", file=sys.stderr)
        return 1
    print(f"{path}: serial {doc['serial']}, expires {doc['expires']}, {what}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
