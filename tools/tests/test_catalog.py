"""tools/catalog.py: the catalog CI writes and RedAmon verifies (D-02, D-25, TD-11)."""

import datetime as dt
import json

import jsonschema
import pytest

import catalog

NOW = dt.datetime(2026, 10, 11, 12, 0, 0, tzinfo=dt.timezone.utc)
URL = "https://github.com/samugit83/redamon-plugins/releases/download/good-v1.2.0/good-1.2.0.tar.gz"


def _archive(tmp_path, content=b"archive bytes"):
    p = tmp_path / "good-1.2.0.tar.gz"
    p.write_bytes(content)
    return p


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_ok(sdk_dir, doc):
    schema = json.loads((sdk_dir / "schemas" / "catalog.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(doc)


def test_add_release_to_a_new_catalog(good_plugin, tmp_path, sdk_dir):
    out = tmp_path / "catalog.json"
    archive = _archive(tmp_path)
    catalog.add_release(out, good_plugin, archive=archive, url=URL, now=NOW)
    doc = _read(out)
    _schema_ok(sdk_dir, doc)
    assert doc["catalogVersion"] == 1
    assert doc["serial"] == 1
    assert doc["expires"] == "2027-01-09T12:00:00Z"
    assert doc["revoked"] == []
    [plugin] = doc["plugins"]
    assert plugin["id"] == "good" and plugin["displayName"] == "Good"
    assert plugin["description"].startswith("A plugin that passes")
    [release] = plugin["releases"]
    import hashlib
    assert release == {
        "version": "1.2.0",
        "platform": ">=1.0.0 <2.0.0",
        "dataVersion": 2,
        "url": URL,
        "sha256": hashlib.sha256(b"archive bytes").hexdigest(),
        "size": len(b"archive bytes"),
        "permissions": ["session:read", "llm:invoke", "override:skill.text"],
    }


def test_serial_only_grows_and_a_release_is_replaced_not_duplicated(good_plugin, tmp_path):
    out = tmp_path / "catalog.json"
    catalog.add_release(out, good_plugin, archive=_archive(tmp_path, b"one"), url=URL, now=NOW)
    catalog.add_release(out, good_plugin, archive=_archive(tmp_path, b"two"), url=URL, now=NOW)
    doc = _read(out)
    assert doc["serial"] == 2
    [release] = doc["plugins"][0]["releases"]
    import hashlib
    assert release["sha256"] == hashlib.sha256(b"two").hexdigest()


def test_releases_of_one_plugin_are_kept_in_version_order(good_plugin, tmp_path):
    out = tmp_path / "catalog.json"
    m = json.loads((good_plugin / "plugin.json").read_text())
    for version in ("1.10.0", "1.2.0", "1.9.0"):
        m["version"] = version
        (good_plugin / "plugin.json").write_text(json.dumps(m))
        catalog.add_release(out, good_plugin, archive=_archive(tmp_path), url=URL.replace("1.2.0", version), now=NOW)
    versions = [r["version"] for r in _read(out)["plugins"][0]["releases"]]
    assert versions == ["1.2.0", "1.9.0", "1.10.0"]


def test_other_plugins_and_revocations_are_kept(good_plugin, tmp_path):
    out = tmp_path / "catalog.json"
    out.write_text(json.dumps({
        "catalogVersion": 1, "serial": 41, "expires": "2026-11-01T00:00:00Z",
        "plugins": [{"id": "echo", "displayName": "Echo", "description": "d", "releases": [{
            "version": "1.0.0", "platform": ">=1.0.0 <2.0.0", "dataVersion": 1,
            "url": "https://github.com/x/y/releases/download/echo-v1.0.0/echo-1.0.0.tar.gz",
            "sha256": "0" * 64, "permissions": []}]}],
        "revoked": [{"id": "echo", "version": "0.9.0", "reason": "broken"}]}))
    catalog.add_release(out, good_plugin, archive=_archive(tmp_path), url=URL, now=NOW)
    doc = _read(out)
    assert doc["serial"] == 42
    assert [p["id"] for p in doc["plugins"]] == ["echo", "good"]
    assert doc["revoked"] == [{"id": "echo", "version": "0.9.0", "reason": "broken"}]


def test_an_invalid_result_is_refused_and_nothing_is_written(good_plugin, tmp_path):
    out = tmp_path / "catalog.json"
    with pytest.raises(catalog.CatalogError, match="url"):
        catalog.add_release(out, good_plugin, archive=_archive(tmp_path), url="ftp://example.com/x.tar.gz", now=NOW)
    assert not out.exists()


def test_refresh_changes_only_serial_and_expiry(good_plugin, tmp_path):
    out = tmp_path / "catalog.json"
    catalog.add_release(out, good_plugin, archive=_archive(tmp_path), url=URL, now=NOW)
    before = _read(out)
    catalog.refresh(out, now=NOW + dt.timedelta(days=30), days=90)
    after = _read(out)
    assert after["serial"] == before["serial"] + 1
    assert after["expires"] == "2027-02-08T12:00:00Z"
    assert after["plugins"] == before["plugins"] and after["revoked"] == before["revoked"]


def test_refresh_needs_a_catalog(tmp_path):
    with pytest.raises(catalog.CatalogError, match="no catalog"):
        catalog.refresh(tmp_path / "catalog.json", now=NOW)


def test_local_catalog_for_an_offline_install(good_plugin, tmp_path, sdk_dir):
    out = tmp_path / "dist" / "catalog.json"
    catalog.local(out, good_plugin, archive=_archive(tmp_path), url=URL, now=NOW)
    doc = _read(out)
    _schema_ok(sdk_dir, doc)
    # The platform keeps a serial mark per signing key and refuses a lower serial as a
    # replay, so a local catalog takes the clock: every new one is higher than the last.
    assert doc["serial"] == int(NOW.timestamp())
    assert [p["id"] for p in doc["plugins"]] == ["good"]
    assert len(doc["plugins"][0]["releases"]) == 1
    catalog.local(out, good_plugin, archive=_archive(tmp_path), url=URL, now=NOW, serial=7)
    assert _read(out)["serial"] == 7


def test_bytes_are_stable(good_plugin, tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    for out in (a, b):
        catalog.local(out, good_plugin, archive=_archive(tmp_path), url=URL, now=NOW)
    assert a.read_bytes() == b.read_bytes()
    assert a.read_bytes().endswith(b"}\n")


def test_cli_add_release_by_repository(good_plugin, tmp_path, capsys):
    out = tmp_path / "catalog.json"
    archive = _archive(tmp_path)
    code = catalog.main(["add-release", str(good_plugin), "--archive", str(archive), "--repo", "samugit83/redamon-plugins",
                         "--catalog", str(out), "--now", "2026-10-11T12:00:00Z"])
    assert code == 0
    assert _read(out)["plugins"][0]["releases"][0]["url"] == URL
    assert "serial 1" in capsys.readouterr().out
