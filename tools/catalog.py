#!/usr/bin/env python3
"""Writes the plugin catalog RedAmon installs from (README.PLUGINS.md §6.2).

    python tools/catalog.py add-release <plugin> --archive <tar.gz> (--url URL | --repo OWNER/NAME)
    python tools/catalog.py revoke <plugin-id> <version> --reason TEXT
    python tools/catalog.py unrevoke <plugin-id> <version>
    python tools/catalog.py resign
    python tools/catalog.py local <plugin> --archive <tar.gz> (--url URL | --repo OWNER/NAME) --out dist/catalog.json

`add-release` reads `catalog.json` (or starts an empty one) and adds or replaces the plugin's
release with fields taken from its manifest and the archive. `revoke` lists a released version
in `revoked` with the reason RedAmon shows, which stops that version on every installation;
`unrevoke` lifts it. `resign` changes nothing but the serial, so the unchanged catalog can be
signed again (with a new key, after a rotation). Each raises `serial` by one. `local` writes a
one-release catalog for an offline install; its `serial` is the clock, because RedAmon keeps the
highest serial it accepted per signing key and refuses a lower one as a replay.

A catalog has no expiry (the owner's decision of 2026-10-11): nothing writes `expires`, and the
first write after it drops the field from a catalog signed before. Every result is checked
against the catalog schema of each synced platform before it is written. Signing is
tools/sign.py's job.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys

import jsonschema

import semver

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "catalog.json"
# The same id and version shapes as the platform's catalog schema (webapp ids.ts, semver.ts).
PLUGIN_ID_RE = re.compile(r"[a-z][a-z0-9-]{1,39}")
REASON_MAX = 500


class CatalogError(Exception):
    pass


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
    return {"catalogVersion": 1, "serial": 0, "plugins": [], "revoked": []}


def _ordered(doc: dict, serial: int) -> dict:
    # Rebuilt key by key, so a field this tool no longer writes (`expires`) is dropped.
    return {"catalogVersion": 1, "serial": serial,
            "plugins": doc.get("plugins", []), "revoked": doc.get("revoked", [])}


def _sorted_revoked(entries: list) -> list:
    return sorted(entries, key=lambda r: (r["id"], semver.parse_version(r["version"])))


def _target(doc: dict, plugin_id: str, version: str) -> None:
    if not isinstance(plugin_id, str) or not PLUGIN_ID_RE.fullmatch(plugin_id):
        raise CatalogError(f"{plugin_id!r} is not a plugin id")
    if not semver.is_valid_version(version):
        raise CatalogError(f"{version!r} is not a version x.y.z")
    entry = next((p for p in doc.get("plugins", []) if p.get("id") == plugin_id), None)
    if entry is None or not any(r.get("version") == version for r in entry.get("releases", [])):
        raise CatalogError(f"{plugin_id} {version} is not in the catalog: only a released version can be revoked")


def add_release(catalog_path, plugin, *, archive, url) -> dict:
    catalog_path = pathlib.Path(catalog_path)
    doc = _read(catalog_path) if catalog_path.exists() else _empty()
    m = _manifest(plugin)
    doc = _with_release(doc, m, _release(m, archive, url))
    out = _ordered(doc, doc["serial"] + 1)
    _write(catalog_path, out)
    return out


def revoke(catalog_path, plugin_id: str, version: str, *, reason: str) -> dict:
    """Lists a released version in `revoked`; RedAmon stops it at its next reconcile."""
    doc = _read(pathlib.Path(catalog_path))
    _target(doc, plugin_id, version)
    reason = (reason or "").strip()
    if not reason:
        raise CatalogError("a revocation needs a reason: RedAmon shows it on the plugin's card")
    if len(reason) > REASON_MAX:
        raise CatalogError(f"the reason is longer than {REASON_MAX} characters")
    revoked = [r for r in doc.get("revoked", []) if (r.get("id"), r.get("version")) != (plugin_id, version)]
    if len(revoked) < len(doc.get("revoked", [])):
        old = next(r for r in doc["revoked"] if (r.get("id"), r.get("version")) == (plugin_id, version))
        if old.get("reason") == reason:
            raise CatalogError(f"{plugin_id} {version} is already revoked with this reason: nothing to change")
    revoked.append({"id": plugin_id, "version": version, "reason": reason})
    out = _ordered({**doc, "revoked": _sorted_revoked(revoked)}, doc["serial"] + 1)
    _write(pathlib.Path(catalog_path), out)
    return out


def unrevoke(catalog_path, plugin_id: str, version: str) -> dict:
    """Lifts a revocation; RedAmon serves the version again at its next reconcile."""
    doc = _read(pathlib.Path(catalog_path))
    revoked = [r for r in doc.get("revoked", []) if (r.get("id"), r.get("version")) != (plugin_id, version)]
    if len(revoked) == len(doc.get("revoked", [])):
        raise CatalogError(f"{plugin_id} {version} is not revoked")
    out = _ordered({**doc, "revoked": revoked}, doc["serial"] + 1)
    _write(pathlib.Path(catalog_path), out)
    return out


def resign(catalog_path) -> dict:
    """The unchanged catalog with `serial` + 1, to be signed again (after a key rotation)."""
    catalog_path = pathlib.Path(catalog_path)
    doc = _read(catalog_path)
    out = _ordered(doc, doc["serial"] + 1)
    _write(catalog_path, out)
    return out


def local(out_path, plugin, *, archive, url, now=None, serial=None) -> dict:
    now = _now(now)
    m = _manifest(plugin)
    doc = _with_release(_empty(), m, _release(m, archive, url))
    out = _ordered(doc, int(now.timestamp()) if serial is None else int(serial))
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
    p = sub.add_parser("revoke", help="list a released version in revoked; RedAmon stops it")
    p.add_argument("plugin_id")
    p.add_argument("version")
    p.add_argument("--reason", required=True, help="shown on the plugin's card in RedAmon (500 characters at most)")
    p.add_argument("--catalog", type=pathlib.Path, default=DEFAULT_CATALOG)
    p = sub.add_parser("unrevoke", help="lift a revocation")
    p.add_argument("plugin_id")
    p.add_argument("version")
    p.add_argument("--catalog", type=pathlib.Path, default=DEFAULT_CATALOG)
    p = sub.add_parser("resign", help="serial + 1 only, to sign the unchanged catalog again")
    p.add_argument("--catalog", type=pathlib.Path, default=DEFAULT_CATALOG)

    args = ap.parse_args(argv)
    try:
        if args.cmd == "resign":
            path, doc, what = args.catalog, resign(args.catalog), "re-signed"
        elif args.cmd == "revoke":
            path = args.catalog
            doc = revoke(path, args.plugin_id, args.version, reason=args.reason)
            what = f"revoked {args.plugin_id} {args.version}"
        elif args.cmd == "unrevoke":
            path = args.catalog
            doc = unrevoke(path, args.plugin_id, args.version)
            what = f"lifted the revocation of {args.plugin_id} {args.version}"
        else:
            now = _parse_now(args.now)
            m = _manifest(args.plugin)
            url = args.url or release_url(args.repo, m["id"], m["version"])
            if args.cmd == "add-release":
                path = args.catalog
                doc = add_release(path, args.plugin, archive=args.archive, url=url)
            else:
                path = args.out
                doc = local(path, args.plugin, archive=args.archive, url=url, now=now, serial=args.serial)
            what = f"{m['id']} {m['version']}"
    except (CatalogError, OSError, ValueError, KeyError) as e:
        print(f"catalog: {e}", file=sys.stderr)
        return 1
    print(f"{path}: serial {doc['serial']}, {what}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
