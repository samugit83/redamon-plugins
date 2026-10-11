"""tools/catalog.py: the catalog CI writes and RedAmon verifies (D-02, D-25, D-30, TD-11).

A catalog has no expiry (the owner's decision of 2026-10-11): nothing writes `expires`, and the
first write after it drops the field from a catalog signed before.
"""

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
    catalog.add_release(out, good_plugin, archive=archive, url=URL)
    doc = _read(out)
    _schema_ok(sdk_dir, doc)
    assert list(doc) == ["catalogVersion", "serial", "plugins", "revoked"]
    assert doc["catalogVersion"] == 1
    assert doc["serial"] == 1
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
    catalog.add_release(out, good_plugin, archive=_archive(tmp_path, b"one"), url=URL)
    catalog.add_release(out, good_plugin, archive=_archive(tmp_path, b"two"), url=URL)
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
        catalog.add_release(out, good_plugin, archive=_archive(tmp_path), url=URL.replace("1.2.0", version))
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
    catalog.add_release(out, good_plugin, archive=_archive(tmp_path), url=URL)
    doc = _read(out)
    assert doc["serial"] == 42
    assert [p["id"] for p in doc["plugins"]] == ["echo", "good"]
    assert doc["revoked"] == [{"id": "echo", "version": "0.9.0", "reason": "broken"}]
    assert "expires" not in doc, "the expiry of a catalog signed before 2026-10-11 is dropped"


def test_an_invalid_result_is_refused_and_nothing_is_written(good_plugin, tmp_path):
    out = tmp_path / "catalog.json"
    with pytest.raises(catalog.CatalogError, match="url"):
        catalog.add_release(out, good_plugin, archive=_archive(tmp_path), url="ftp://example.com/x.tar.gz")
    assert not out.exists()


def test_resign_changes_only_the_serial(good_plugin, tmp_path):
    out = tmp_path / "catalog.json"
    catalog.add_release(out, good_plugin, archive=_archive(tmp_path), url=URL)
    before = _read(out)
    catalog.resign(out)
    after = _read(out)
    assert after == {**before, "serial": before["serial"] + 1}


def test_resign_drops_an_old_expiry(tmp_path):
    out = tmp_path / "catalog.json"
    out.write_text(json.dumps({"catalogVersion": 1, "serial": 1, "expires": "2027-01-09T14:05:28Z",
                               "plugins": [], "revoked": []}))
    catalog.resign(out)
    assert _read(out) == {"catalogVersion": 1, "serial": 2, "plugins": [], "revoked": []}


def test_resign_needs_a_catalog(tmp_path):
    with pytest.raises(catalog.CatalogError, match="no catalog"):
        catalog.resign(tmp_path / "catalog.json")


def test_local_catalog_for_an_offline_install(good_plugin, tmp_path, sdk_dir):
    out = tmp_path / "dist" / "catalog.json"
    catalog.local(out, good_plugin, archive=_archive(tmp_path), url=URL, now=NOW)
    doc = _read(out)
    _schema_ok(sdk_dir, doc)
    assert "expires" not in doc
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
    printed = capsys.readouterr().out
    assert "serial 1" in printed and "expires" not in printed


# --- revocation (D-30, OQ-24): RedAmon stops a listed version at its next reconcile ---------

def _released(good_plugin, tmp_path, *versions):
    out = tmp_path / "catalog.json"
    m = json.loads((good_plugin / "plugin.json").read_text())
    for version in versions:
        m["version"] = version
        (good_plugin / "plugin.json").write_text(json.dumps(m))
        catalog.add_release(out, good_plugin, archive=_archive(tmp_path), url=URL.replace("1.2.0", version))
    return out


def test_revoke_lists_the_version_with_its_reason(good_plugin, tmp_path, sdk_dir):
    out = _released(good_plugin, tmp_path, "1.2.0", "1.3.0")
    before = _read(out)
    catalog.revoke(out, "good", "1.2.0", reason="  Leaks session hostnames into proposals.  ")
    doc = _read(out)
    _schema_ok(sdk_dir, doc)
    assert doc["revoked"] == [{"id": "good", "version": "1.2.0", "reason": "Leaks session hostnames into proposals."}]
    assert doc["serial"] == before["serial"] + 1
    assert doc["plugins"] == before["plugins"], "a revoked release stays listed: RedAmon needs it to name what it stops"


def test_revoked_entries_are_kept_in_plugin_then_version_order(good_plugin, tmp_path):
    out = _released(good_plugin, tmp_path, "1.2.0", "1.10.0", "1.9.0")
    for version in ("1.10.0", "1.2.0", "1.9.0"):
        catalog.revoke(out, "good", version, reason="r")
    assert [r["version"] for r in _read(out)["revoked"]] == ["1.2.0", "1.9.0", "1.10.0"]


@pytest.mark.parametrize("plugin_id, version, match", [
    ("good", "9.9.9", "good 9.9.9 is not in the catalog"),
    ("other", "1.2.0", "other 1.2.0 is not in the catalog"),
    ("Good", "1.2.0", "not a plugin id"),
    ("good", "1.2", "not a version"),
    ("good", "v1.2.0", "not a version"),
])
def test_revoke_refuses_what_the_catalog_does_not_list(good_plugin, tmp_path, plugin_id, version, match):
    out = _released(good_plugin, tmp_path, "1.2.0")
    before = out.read_bytes()
    with pytest.raises(catalog.CatalogError, match=match):
        catalog.revoke(out, plugin_id, version, reason="r")
    assert out.read_bytes() == before


@pytest.mark.parametrize("reason, match", [("", "a reason"), ("   ", "a reason"), ("x" * 501, "500 characters")])
def test_revoke_needs_a_short_reason(good_plugin, tmp_path, reason, match):
    out = _released(good_plugin, tmp_path, "1.2.0")
    before = out.read_bytes()
    with pytest.raises(catalog.CatalogError, match=match):
        catalog.revoke(out, "good", "1.2.0", reason=reason)
    assert out.read_bytes() == before


def test_revoking_again_changes_the_reason_or_refuses_a_no_op(good_plugin, tmp_path):
    out = _released(good_plugin, tmp_path, "1.2.0")
    catalog.revoke(out, "good", "1.2.0", reason="first")
    serial = _read(out)["serial"]
    with pytest.raises(catalog.CatalogError, match="already revoked with this reason"):
        catalog.revoke(out, "good", "1.2.0", reason="first")
    assert _read(out)["serial"] == serial
    catalog.revoke(out, "good", "1.2.0", reason="second")
    doc = _read(out)
    assert doc["revoked"] == [{"id": "good", "version": "1.2.0", "reason": "second"}]
    assert doc["serial"] == serial + 1


def test_unrevoke_lifts_a_revocation(good_plugin, tmp_path):
    out = _released(good_plugin, tmp_path, "1.2.0", "1.3.0")
    catalog.revoke(out, "good", "1.2.0", reason="a")
    catalog.revoke(out, "good", "1.3.0", reason="b")
    serial = _read(out)["serial"]
    catalog.unrevoke(out, "good", "1.2.0")
    doc = _read(out)
    assert doc["revoked"] == [{"id": "good", "version": "1.3.0", "reason": "b"}]
    assert doc["serial"] == serial + 1


def test_unrevoke_refuses_a_version_that_is_not_revoked(good_plugin, tmp_path):
    out = _released(good_plugin, tmp_path, "1.2.0")
    before = out.read_bytes()
    with pytest.raises(catalog.CatalogError, match="good 1.2.0 is not revoked"):
        catalog.unrevoke(out, "good", "1.2.0")
    assert out.read_bytes() == before


def test_revoke_and_unrevoke_need_a_catalog(tmp_path):
    with pytest.raises(catalog.CatalogError, match="no catalog"):
        catalog.revoke(tmp_path / "catalog.json", "good", "1.2.0", reason="r")
    with pytest.raises(catalog.CatalogError, match="no catalog"):
        catalog.unrevoke(tmp_path / "catalog.json", "good", "1.2.0")


def test_a_later_release_keeps_the_revocations(good_plugin, tmp_path):
    out = _released(good_plugin, tmp_path, "1.2.0")
    catalog.revoke(out, "good", "1.2.0", reason="broken")
    _released(good_plugin, tmp_path, "1.2.1")
    assert _read(out)["revoked"] == [{"id": "good", "version": "1.2.0", "reason": "broken"}]


def test_cli_revoke_unrevoke_and_resign(good_plugin, tmp_path, capsys):
    out = _released(good_plugin, tmp_path, "1.2.0")
    capsys.readouterr()
    assert catalog.main(["revoke", "good", "1.2.0", "--reason", "Leaks data.", "--catalog", str(out)]) == 0
    assert "revoked good 1.2.0" in capsys.readouterr().out
    assert catalog.main(["revoke", "good", "1.2.0", "--reason", "Leaks data.", "--catalog", str(out)]) == 1
    assert "already revoked" in capsys.readouterr().err
    assert catalog.main(["unrevoke", "good", "1.2.0", "--catalog", str(out)]) == 0
    assert "lifted the revocation of good 1.2.0" in capsys.readouterr().out
    assert catalog.main(["resign", "--catalog", str(out)]) == 0
    assert "re-signed" in capsys.readouterr().out
    with pytest.raises(SystemExit) as no_reason:
        catalog.main(["revoke", "good", "1.2.0", "--catalog", str(out)])
    assert no_reason.value.code == 2, "--reason is required"
