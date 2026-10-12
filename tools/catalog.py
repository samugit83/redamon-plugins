#!/usr/bin/env python3
"""Writes the plugin catalog RedAmon installs from (README.PLUGINS.md §6.2).

    python tools/catalog.py add-release <plugin> --archive <tar.gz> (--url URL [--listing-repo OWNER/NAME] | --repo OWNER/NAME) [--ref master]
    python tools/catalog.py revoke <plugin-id> <version> --reason TEXT
    python tools/catalog.py unrevoke <plugin-id> <version>
    python tools/catalog.py resign
    python tools/catalog.py listings --repo OWNER/NAME [--ref master]
    python tools/catalog.py local <plugin> --archive <tar.gz> (--url URL [--listing-repo OWNER/NAME] | --repo OWNER/NAME) [--ref master] --out dist/catalog.json

`add-release` reads `catalog.json` (or starts an empty one) and adds or replaces the plugin's
release with fields taken from the archive and the manifest inside it (not the folder, which may
already be a later version when a release is re-run). `revoke` lists a released version
in `revoked` with the reason RedAmon shows, which stops that version on every installation;
`unrevoke` lifts it. `resign` changes nothing but the serial, so the unchanged catalog can be
signed again (with a new key, after a rotation). `listings` refreshes the listing of every
plugin in the catalog whose folder is here. Each raises `serial` by one. `local` writes a
one-release catalog for an offline install; its `serial` is the clock, because RedAmon keeps the
highest serial it accepted per signing key and refuses a lower one as a replay.

A plugin's listing (`listing.json` and its icon, tools/listing.py) is the entry's `icon` and
`docsUrl`: presentation for RedAmon's Plugins page, never in the manifest or the archive. It
comes from the folder, not the archive, because it is how the plugin is shown now, whatever
version is released; a folder without one drops the entry's listing. The icon is served from the
raw file on `--ref` of the repository (`--repo`, or `--listing-repo` with `--url`), which can
change after signing, so the entry pins its sha256: RedAmon shows the icon only while the bytes
match, and a changed icon needs `listings`. Without a repository the entry has no icon.

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
import tarfile

import jsonschema

import listing
import semver

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "catalog.json"
# The branch the catalog workflows commit to: an icon URL names it.
DEFAULT_REF = "master"
# The same id and version shapes as the platform's catalog schema (webapp ids.ts, semver.ts).
PLUGIN_ID_RE = re.compile(r"[a-z][a-z0-9-]{1,39}")
# Counted in UTF-16 units, as RedAmon's schema (JavaScript) counts a string: an emoji is two.
REASON_MAX_UTF16 = 500
REPO_RE = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
# Path segments that cannot start with a dot, so a ref never walks out of the repository's URL.
REF_RE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*(?:/[A-Za-z0-9_-][A-Za-z0-9._-]*)*")


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


def _archive_manifest(archive, plugin_id: str) -> dict:
    """The plugin.json packed in the archive (tools/pack.py puts it at `<id>/plugin.json`)."""
    try:
        with tarfile.open(archive, "r:gz") as tar:
            member = tar.extractfile(f"{plugin_id}/plugin.json")
            return json.loads(member.read().decode("utf-8"))
    except KeyError:
        raise CatalogError(f"{archive} has no {plugin_id}/plugin.json") from None
    except (tarfile.TarError, EOFError, OSError) as e:
        if isinstance(e, FileNotFoundError):
            raise
        raise CatalogError(f"{archive} is not a .tar.gz archive made by tools/pack.py") from None


def _released_manifest(plugin, archive, expect_version=None) -> dict:
    plugin_id = _manifest(plugin)["id"]
    m = _archive_manifest(archive, plugin_id)
    if m.get("id") != plugin_id:
        raise CatalogError(f"the archive is {m.get('id')}, not {plugin_id}")
    if expect_version is not None and m.get("version") != expect_version:
        raise CatalogError(f"the archive is {m.get('id')} {m.get('version')}, not {expect_version}")
    return m


def release_url(repo: str, plugin_id: str, version: str) -> str:
    return f"https://github.com/{repo}/releases/download/{plugin_id}-v{version}/{plugin_id}-{version}.tar.gz"


def icon_url(repo: str, ref: str, plugin_id: str, name: str) -> str:
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
        raise CatalogError(f"{repo!r} is not a GitHub repository OWNER/NAME")
    if not isinstance(ref, str) or not REF_RE.fullmatch(ref):
        raise CatalogError(f"{ref!r} is not a branch or tag name")
    return f"https://raw.githubusercontent.com/{repo}/{ref}/{plugin_id}/{name}"


def _listing(plugin, plugin_id: str, repo, ref: str) -> dict:
    """The entry's `icon` and `docsUrl` from the plugin folder's listing.json; {} without one."""
    plugin = pathlib.Path(plugin)
    try:
        given = listing.read(plugin)
    except listing.ListingError as e:
        raise CatalogError(f"{plugin_id}: {e}") from None
    fields = {}
    name = given.get("icon")
    if name is not None and repo is None:
        print(f"catalog: warning: {plugin_id} is listed without its icon: the icon URL needs --repo or --listing-repo",
              file=sys.stderr)
    elif name is not None:
        data = (plugin / name).read_bytes()
        fields["icon"] = {"url": icon_url(repo, ref, plugin_id, name), "sha256": hashlib.sha256(data).hexdigest(),
                          "size": len(data), "type": listing.ICON_TYPES[name]}
    if "docsUrl" in given:
        fields["docsUrl"] = given["docsUrl"]
    return fields


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


def _entry(about: dict, fields: dict, releases: list) -> dict:
    """A plugin's entry, key by key in the order RedAmon's catalog lists them."""
    return {"id": about["id"], "displayName": about["displayName"], "description": about["description"],
            **{k: fields[k] for k in listing.KEYS if k in fields}, "releases": releases}


def _with_release(doc: dict, m: dict, release: dict, fields: dict) -> dict:
    plugins = [p for p in doc.get("plugins", []) if p.get("id") != m["id"]]
    previous = next((p for p in doc.get("plugins", []) if p.get("id") == m["id"]), None)
    releases = [r for r in (previous or {}).get("releases", []) if r.get("version") != release["version"]]
    releases.append(release)
    releases.sort(key=lambda r: semver.parse_version(r["version"]))
    plugins.append(_entry(m, fields, releases))
    plugins.sort(key=lambda p: p["id"])
    return {**doc, "plugins": plugins}


def _empty() -> dict:
    return {"catalogVersion": 1, "serial": 0, "plugins": [], "revoked": []}


def _ordered(doc: dict, serial: int) -> dict:
    # Rebuilt key by key, so a field this tool does not write (an older catalog's `expires`)
    # is dropped.
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


def add_release(catalog_path, plugin, *, archive, url, expect_version=None, repo=None, ref=DEFAULT_REF) -> dict:
    """`repo` (OWNER/NAME) serves the icon; without it the entry has no icon."""
    catalog_path = pathlib.Path(catalog_path)
    doc = _read(catalog_path) if catalog_path.exists() else _empty()
    m = _released_manifest(plugin, archive, expect_version)
    doc = _with_release(doc, m, _release(m, archive, url), _listing(plugin, m["id"], repo, ref))
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
    if len(reason.encode("utf-16-le")) // 2 > REASON_MAX_UTF16:
        raise CatalogError(f"the reason is longer than {REASON_MAX_UTF16} characters (an emoji counts as two)")
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


def listings(catalog_path, *, repo: str, ref: str = DEFAULT_REF, root=ROOT) -> dict:
    """Every entry's `icon` and `docsUrl` again from its plugin folder under `root` (this
    repository), without a release. An entry whose folder is not there is left as it is."""
    catalog_path = pathlib.Path(catalog_path)
    root = pathlib.Path(root)
    doc = _read(catalog_path)
    before = doc.get("plugins", [])
    plugins = []
    for p in before:
        folder = root / p["id"]
        plugins.append(_entry(p, _listing(folder, p["id"], repo, ref), p["releases"]) if folder.is_dir() else p)
    if plugins == before:
        raise CatalogError("every listing already matches its plugin folder: nothing to change")
    out = _ordered({**doc, "plugins": plugins}, doc["serial"] + 1)
    _write(catalog_path, out)
    return out


def local(out_path, plugin, *, archive, url, now=None, serial=None, repo=None, ref=DEFAULT_REF) -> dict:
    now = _now(now)
    m = _released_manifest(plugin, archive)
    doc = _with_release(_empty(), m, _release(m, archive, url), _listing(plugin, m["id"], repo, ref))
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

    for name in ("add-release", "local"):
        p = sub.add_parser(name)
        p.add_argument("plugin", type=pathlib.Path)
        p.add_argument("--archive", type=pathlib.Path, required=True)
        where = p.add_mutually_exclusive_group(required=True)
        where.add_argument("--url")
        where.add_argument("--repo", help="OWNER/NAME: the GitHub release URL and the icon URL are derived from it")
        p.add_argument("--listing-repo", default=None, help="with --url: OWNER/NAME that serves the icon")
        p.add_argument("--ref", default=DEFAULT_REF, help="the branch that serves the icon")
        if name == "add-release":
            p.add_argument("--catalog", type=pathlib.Path, default=DEFAULT_CATALOG)
            p.add_argument("--expect-version", default=None, help="refuse an archive of another version")
        else:
            p.add_argument("--out", type=pathlib.Path, default=ROOT / "dist" / "catalog.json")
            p.add_argument("--serial", type=int, default=None)
            p.add_argument("--now", default=None, help=argparse.SUPPRESS)
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
    p = sub.add_parser("listings", help="refresh every entry's icon and docsUrl from its plugin folder")
    p.add_argument("--repo", required=True, help="OWNER/NAME that serves the icons")
    p.add_argument("--ref", default=DEFAULT_REF, help="the branch that serves the icons")
    p.add_argument("--catalog", type=pathlib.Path, default=DEFAULT_CATALOG)
    p.add_argument("--root", type=pathlib.Path, default=ROOT, help=argparse.SUPPRESS)

    args = ap.parse_args(argv)
    if getattr(args, "listing_repo", None) and args.repo:
        ap.error("--listing-repo goes with --url: --repo already names the repository")
    try:
        if args.cmd == "resign":
            path, doc, what = args.catalog, resign(args.catalog), "re-signed"
        elif args.cmd == "listings":
            path = args.catalog
            doc = listings(path, repo=args.repo, ref=args.ref, root=args.root)
            what = "refreshed the listings"
        elif args.cmd == "revoke":
            path = args.catalog
            doc = revoke(path, args.plugin_id, args.version, reason=args.reason)
            what = f"revoked {args.plugin_id} {args.version}"
        elif args.cmd == "unrevoke":
            path = args.catalog
            doc = unrevoke(path, args.plugin_id, args.version)
            what = f"lifted the revocation of {args.plugin_id} {args.version}"
        else:
            m = _released_manifest(args.plugin, args.archive, getattr(args, "expect_version", None))
            url = args.url or release_url(args.repo, m["id"], m["version"])
            repo = args.listing_repo or args.repo
            if args.cmd == "add-release":
                path = args.catalog
                doc = add_release(path, args.plugin, archive=args.archive, url=url, expect_version=args.expect_version,
                                  repo=repo, ref=args.ref)
            else:
                path = args.out
                doc = local(path, args.plugin, archive=args.archive, url=url, now=_parse_now(args.now), serial=args.serial,
                            repo=repo, ref=args.ref)
            what = f"{m['id']} {m['version']}"
    except (CatalogError, OSError, ValueError, KeyError) as e:
        print(f"catalog: {e}", file=sys.stderr)
        return 1
    print(f"{path}: serial {doc['serial']}, {what}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
