"""tools/validate.py: what a plugin must satisfy before it is packed (D-22, D-23, D-76).

The schema and cross-field cases mirror RedAmon's manifestSchema.test.ts and
screenSchema.test.ts, so a plugin this validator accepts is one the platform installs.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

import validate
from support import DOCS_URL, ICONS, ROOT


def _manifest(plugin):
    return json.loads((plugin / "plugin.json").read_text(encoding="utf-8"))


def _write_manifest(plugin, m):
    (plugin / "plugin.json").write_text(json.dumps(m, indent=2), encoding="utf-8")


def _errors_after(plugin, mutate):
    m = _manifest(plugin)
    mutate(m)
    _write_manifest(plugin, m)
    errors = validate.validate(plugin)
    assert errors, "expected the plugin to be refused"
    return "\n".join(errors)


def test_the_fixture_passes(good_plugin):
    assert validate.validate(good_plugin) == []


def test_cli_ok_and_refusal(good_plugin):
    script = str(ROOT / "tools" / "validate.py")
    ok = subprocess.run([sys.executable, script, str(good_plugin)], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "ok: good 1.2.0" in ok.stdout
    (good_plugin / "README.md").unlink()
    bad = subprocess.run([sys.executable, script, str(good_plugin)], capture_output=True, text=True)
    assert bad.returncode == 1
    assert "README.md" in bad.stdout


# -- the manifest: schema --------------------------------------------------------------

def test_unknown_top_level_key(good_plugin):
    assert "hooks" in _errors_after(good_plugin, lambda m: m.update(hooks={}))


def test_unknown_key_inside_an_action(good_plugin):
    assert "shell" in _errors_after(good_plugin, lambda m: m["actions"][0].update(shell=True))


def test_unknown_permission(good_plugin):
    assert "network:any" in _errors_after(good_plugin, lambda m: m["permissions"].append("network:any"))


def test_unknown_extension_point(good_plugin):
    assert "graph.toolbar" in _errors_after(good_plugin, lambda m: m["contributes"].update({"graph.toolbar": []}))


@pytest.mark.parametrize("entry", ["main.py:run", "engine/main.py", "engine/../x.py:run", "engine/Main.py:run"])
def test_entry_outside_engine_or_malformed(good_plugin, entry):
    assert "actions/0/entry" in _errors_after(good_plugin, lambda m: m["actions"][0].update(entry=entry))


@pytest.mark.parametrize("value", [0, 3601, 1.5])
def test_timeout_out_of_range(good_plugin, value):
    assert "timeoutSeconds" in _errors_after(good_plugin, lambda m: m["actions"][0].update(timeoutSeconds=value))


@pytest.mark.parametrize("plugin_id", ["Good", "g", "staging", "good\n", "a" * 41])
def test_bad_or_reserved_id(good_plugin, plugin_id):
    assert "id" in _errors_after(good_plugin, lambda m: m.update(id=plugin_id))


@pytest.mark.parametrize("version", ["1.0", "v1.2.0", "1.2.0-rc.1", "1.2.0\n"])
def test_bad_version(good_plugin, version):
    assert "version" in _errors_after(good_plugin, lambda m: m.update(version=version))


@pytest.mark.parametrize("rng", ["^1.0.0", ">= 1.0.0", ">=1.0.0 || <0.5.0", ">=1.0.0\n"])
def test_bad_platform_range(good_plugin, rng):
    assert "platform" in _errors_after(good_plugin, lambda m: m.update(platform=rng))


def test_icon_outside_the_allowlist(good_plugin):
    msg = _errors_after(good_plugin, lambda m: m["contributes"]["chat.session.actions"][0].update(icon="skull"))
    assert "icon" in msg


def test_more_contributions_than_a_point_allows(good_plugin):
    many = [{"label": f"x{i}", "action": "run"} for i in range(6)]
    assert "chat.session.actions" in _errors_after(good_plugin, lambda m: m["contributes"].update({"chat.session.actions": many}))


# -- the manifest: rules a JSON Schema cannot hold --------------------------------------

def test_llm_action_without_llm_permission(good_plugin):
    def mutate(m):
        m["permissions"].remove("llm:invoke")
        del m["llmFeature"]
    assert "an action that calls a model needs the llm:invoke permission" in _errors_after(good_plugin, mutate)


def test_llm_permission_without_llm_feature(good_plugin):
    assert "llmFeature: is required with the llm:invoke permission" in _errors_after(good_plugin, lambda m: m.pop("llmFeature"))


def test_llm_feature_without_llm_permission(good_plugin):
    def mutate(m):
        m["permissions"].remove("llm:invoke")
        for a in m["actions"]:
            a.pop("llm", None)
    assert "llmFeature: needs the llm:invoke permission" in _errors_after(good_plugin, mutate)


def test_core_screen_without_its_permission(good_plugin):
    msg = _errors_after(good_plugin, lambda m: m.update(permissions=["session:read", "llm:invoke"]))
    assert "skill.text.versions needs the override:skill.text permission" in msg


def test_contribution_with_both_an_action_and_a_core_screen(good_plugin):
    msg = _errors_after(good_plugin, lambda m: m["contributes"]["chat.session.actions"][1].update(action="run"))
    assert "give either action or coreScreen" in msg


def test_contribution_with_neither(good_plugin):
    msg = _errors_after(good_plugin, lambda m: m["contributes"]["chat.session.actions"][0].pop("action"))
    assert "give either action or coreScreen" in msg


def test_contribution_naming_an_undeclared_action(good_plugin):
    msg = _errors_after(good_plugin, lambda m: m["contributes"]["chat.session.actions"][0].update(action="analyse"))
    assert 'no action "analyse" is declared' in msg
    msg = _errors_after(good_plugin, lambda m: m["contributes"].update({"project.skills.row": [{"label": "x", "action": "nope"}]}))
    assert 'no action "nope" is declared' in msg


def test_two_actions_with_one_id(good_plugin):
    msg = _errors_after(good_plugin, lambda m: m["actions"].append(dict(m["actions"][0])))
    assert "two actions share an id" in msg


def test_permission_listed_twice(good_plugin):
    assert "lists a permission twice" in _errors_after(good_plugin, lambda m: m["permissions"].append("session:read"))


def test_screen_listed_twice(good_plugin):
    assert "lists a screen twice" in _errors_after(good_plugin, lambda m: m["screens"].append("ui/home.json"))


def test_id_must_be_the_folder_name(good_plugin):
    assert "folder" in _errors_after(good_plugin, lambda m: m.update(id="other"))


def test_no_synced_platform_accepts_the_range(good_plugin):
    assert "no synced platform" in _errors_after(good_plugin, lambda m: m.update(platform=">=9.0.0"))


# -- screens ---------------------------------------------------------------------------

def _screen(plugin, name, node):
    (plugin / "ui" / name).write_text(json.dumps(node), encoding="utf-8")
    errors = validate.validate(plugin)
    assert errors, "expected the screen to be refused"
    return "\n".join(errors)


def test_unknown_primitive(good_plugin):
    assert "ui/detail.json" in _screen(good_plugin, "detail.json", {"type": "iframe", "src": "x"})


def test_button_both_action_and_screen(good_plugin):
    node = {"type": "page", "children": [{"type": "button", "label": "B", "action": "run", "screen": "ui/home.json"}]}
    assert "a button calls an action or opens a screen, not both" in _screen(good_plugin, "detail.json", node)


def test_row_action_with_neither(good_plugin):
    node = {"type": "list", "source": {"action": "list_items"}, "itemTitle": "n", "rowActions": [{"label": "x"}]}
    assert "a row action opens a screen or calls an action, not both" in _screen(good_plugin, "detail.json", node)


def test_binding_with_both_text_and_field(good_plugin):
    node = {"type": "markdown", "content": {"text": "a", "field": "b"}}
    assert "give either text or field" in _screen(good_plugin, "detail.json", node)


def test_select_field_without_options(good_plugin):
    node = {"type": "form", "fields": [{"name": "k", "label": "K", "kind": "select"}], "submit": {"action": "run"}}
    assert "options are required for a select field" in _screen(good_plugin, "detail.json", node)


def _nested(depth):
    node = {"type": "markdown", "content": {"text": "leaf"}}
    for _ in range(depth - 1):
        node = {"type": "page", "children": [node]}
    return node


def test_nesting_six_deep_is_fine_seven_is_not(good_plugin):
    (good_plugin / "ui" / "detail.json").write_text(json.dumps(_nested(6)))
    assert validate.validate(good_plugin) == []
    assert "nested 7 deep; at most 6" in _screen(good_plugin, "detail.json", _nested(7))


def test_hostile_json_depth_is_refused_not_a_crash(good_plugin):
    deep = "[" * 5000 + "]" * 5000
    (good_plugin / "ui" / "detail.json").write_text(deep)
    assert "nested too deep" in "\n".join(validate.validate(good_plugin))


def test_screen_calling_an_undeclared_action(good_plugin):
    node = {"type": "page", "children": [{"type": "button", "label": "B", "action": "missing"}]}
    assert 'ui/detail.json: no action "missing" is declared' in _screen(good_plugin, "detail.json", node)


def test_screen_opening_an_unlisted_screen(good_plugin):
    node = {"type": "page", "children": [{"type": "button", "label": "B", "screen": "ui/other.json"}]}
    assert "opens ui/other.json, which is not listed in screens" in _screen(good_plugin, "detail.json", node)


def test_nav_screen_must_be_listed(good_plugin):
    assert "ui/home.json is used but not listed in screens" in _errors_after(
        good_plugin, lambda m: m.update(screens=["ui/detail.json"]))


def test_listed_screen_missing(good_plugin):
    (good_plugin / "ui" / "detail.json").unlink()
    assert "ui/detail.json is listed but missing" in "\n".join(validate.validate(good_plugin))


def test_screen_not_json(good_plugin):
    (good_plugin / "ui" / "detail.json").write_text("{not json")
    assert "ui/detail.json: not valid JSON" in "\n".join(validate.validate(good_plugin))


# -- layout, entries, imports -------------------------------------------------------------

@pytest.mark.parametrize("name", ["README.md", "CHANGELOG.md", "engine/__init__.py"])
def test_required_files(good_plugin, name):
    (good_plugin / name).unlink()
    assert name in "\n".join(validate.validate(good_plugin))


def test_unexpected_top_level_entry(good_plugin):
    (good_plugin / "scripts").mkdir()
    assert "unexpected top-level entry: scripts" in "\n".join(validate.validate(good_plugin))


def test_caches_are_ignored(good_plugin):
    (good_plugin / "__pycache__").mkdir()
    (good_plugin / "engine" / "__pycache__").mkdir()
    (good_plugin / "engine" / "__pycache__" / "main.cpython-311.pyc").write_bytes(b"\0")
    assert validate.validate(good_plugin) == []


def test_symlink_refused(good_plugin):
    os.symlink("/etc/passwd", good_plugin / "engine" / "link.py")
    assert "engine/link.py is a symbolic link" in "\n".join(validate.validate(good_plugin))


def test_changelog_needs_the_manifest_version(good_plugin):
    assert "CHANGELOG.md has no section for 1.3.0" in _errors_after(good_plugin, lambda m: m.update(version="1.3.0"))


def test_entry_file_missing(good_plugin):
    msg = _errors_after(good_plugin, lambda m: m["actions"][1].update(entry="engine/items.py:list_items"))
    assert "engine/items.py does not exist" in msg


def test_entry_function_missing(good_plugin):
    msg = _errors_after(good_plugin, lambda m: m["actions"][1].update(entry="engine/main.py:nothing"))
    assert "engine/main.py defines no function nothing" in msg


def test_entry_must_be_async(good_plugin):
    main = good_plugin / "engine" / "main.py"
    main.write_text(main.read_text().replace("async def list_items", "def list_items"))
    assert "list_items must be an async def" in "\n".join(validate.validate(good_plugin))


def test_entry_file_with_a_syntax_error(good_plugin):
    (good_plugin / "engine" / "helper.py").write_text("def broken(:\n")
    assert "engine/helper.py: syntax error" in "\n".join(validate.validate(good_plugin))


def _add_import(plugin, line):
    main = plugin / "engine" / "main.py"
    main.write_text(line + "\n" + main.read_text())
    return "\n".join(validate.validate(plugin))


@pytest.mark.parametrize("line", ["import requests", "from yaml import safe_load", "import numpy.linalg"])
def test_third_party_import_refused(good_plugin, line):
    top = line.split()[1].split(".")[0]
    assert f"imports {top}, which is not the standard library, the SDK or its vendor/ folder" in _add_import(good_plugin, line)


def test_cross_plugin_import_refused(good_plugin):
    other = good_plugin.parent / "other"
    shutil.copytree(good_plugin, other)
    m = _manifest(other)
    m["id"] = "other"
    _write_manifest(other, m)
    msg = _add_import(good_plugin, "from other.engine import helper as stolen")
    assert "imports from another plugin (other)" in msg
    msg = _add_import(good_plugin, "import other")
    assert "imports from another plugin (other)" in msg


def test_vendor_and_relative_imports_are_fine(good_plugin):
    assert _add_import(good_plugin, "import tinylib\nfrom .helper import shape") == ""


def test_tests_folder_may_import_anything(good_plugin):
    (good_plugin / "tests" / "check_more.py").write_text("import requests\n")
    assert validate.validate(good_plugin) == []


def test_migration_names_and_shape(good_plugin):
    (good_plugin / "migrations" / "2_up.py").write_text("def migrate_row(row):\n    return row\n")
    assert "migrations/2_up.py is not named NNNN_up.py or NNNN_down.py" in "\n".join(validate.validate(good_plugin))
    (good_plugin / "migrations" / "2_up.py").unlink()
    (good_plugin / "migrations" / "0002_up.py").write_text("async def migrate_row(row):\n    return row\n")
    assert "migrations/0002_up.py must define a plain def migrate_row(row)" in "\n".join(validate.validate(good_plugin))


def test_data_version_needs_its_up_migrations(good_plugin):
    msg = _errors_after(good_plugin, lambda m: m.update(dataVersion=3))
    assert "dataVersion 3 needs migrations/0003_up.py" in msg


def test_migrations_follow_the_import_rule(good_plugin):
    (good_plugin / "migrations" / "0002_up.py").write_text("import requests\n\ndef migrate_row(row):\n    return row\n")
    assert "migrations/0002_up.py:1: imports requests" in "\n".join(validate.validate(good_plugin))


# -- the listing: listing.json and its icon ---------------------------------------------------

def _listing(plugin, doc, files=None):
    """Writes listing.json (none when `doc` is None) and the given files; the errors, one per line."""
    if doc is not None:
        (plugin / "listing.json").write_text(json.dumps(doc), encoding="utf-8")
    for name, data in (files or {}).items():
        (plugin / name).write_bytes(data)
    return "\n".join(validate.validate(plugin))


@pytest.mark.parametrize("name", sorted(ICONS))
def test_a_listing_with_its_icon_passes(good_plugin, name):
    assert _listing(good_plugin, {"icon": name, "docsUrl": DOCS_URL}, {name: ICONS[name]}) == ""


@pytest.mark.parametrize("doc", [{}, {"docsUrl": DOCS_URL}])
def test_a_listing_without_an_icon_passes(good_plugin, doc):
    assert _listing(good_plugin, doc) == ""


def test_an_icon_of_exactly_256_kib_passes(good_plugin):
    assert _listing(good_plugin, {"icon": "icon.png"}, {"icon.png": ICONS["icon.png"].ljust(262_144, b"\0")}) == ""


@pytest.mark.parametrize("doc, files, message", [
    ({"icon": "icon.png"}, {}, "listing.json icon: icon.png is missing"),
    ({"icon": "icon.png"}, {"icon.png": ICONS["icon.jpg"]}, "icon.png: the bytes are not image/png (they must match the extension)"),
    ({"icon": "icon.jpg"}, {"icon.jpg": ICONS["icon.png"]}, "icon.jpg: the bytes are not image/jpeg"),
    ({"icon": "icon.webp"}, {"icon.webp": ICONS["icon.webp"].replace(b"WEBP", b"AVI ")}, "icon.webp: the bytes are not image/webp"),
    ({"icon": "icon.png"}, {"icon.png": ICONS["icon.png"].ljust(262_145, b"\0")}, "icon.png: 262145 bytes; at most 262144 (256 KiB)"),
    ({"icon": "icon.png"}, {"icon.png": b""}, "icon.png is empty"),
    ({"icon": "logo.png"}, {}, "listing.json icon: must be one of icon.jpg, icon.png, icon.webp"),
    ({"icon": "ui/icon.png"}, {}, "listing.json icon: must be one of"),
    ({"icon": None}, {}, "listing.json icon: must be one of"),
    ({"icon": ["icon.png"]}, {"icon.png": ICONS["icon.png"]}, "listing.json icon: must be one of"),
    ({"color": "red"}, {}, 'listing.json: unknown key "color" (only icon and docsUrl)'),
    (None, {"icon.png": ICONS["icon.png"]}, "icon.png is not named by listing.json"),
    ({"docsUrl": DOCS_URL}, {"icon.png": ICONS["icon.png"]}, "icon.png is not named by listing.json"),
    ({"icon": "icon.jpg"}, {"icon.jpg": ICONS["icon.jpg"], "icon.webp": ICONS["icon.webp"]},
     "icon.webp is not named by listing.json"),
    (["icon.png"], {}, "listing.json is not a JSON object"),
])
def test_a_listing_that_breaks_a_rule(good_plugin, doc, files, message):
    assert message in _listing(good_plugin, doc, files)


# RedAmon parses docsUrl with `new URL` and counts it in UTF-16 units, as JavaScript does.
@pytest.mark.parametrize("url, message", [
    ("http://example.com/docs", "must be an https:// URL"),
    ("HTTPS://example.com/docs", "must be an https:// URL"),
    (42, "must be an https:// URL"),
    ("https://example.com/a docs", "must not contain whitespace"),
    ("https://example.com/docs\n", "must not contain whitespace"),
    pytest.param("https://example.com/" + "a" * 481, "is longer than 500 characters", id="501-characters"),
    pytest.param("https://example.com/" + "\U0001F600" * 241, "is longer than 500 characters", id="502-utf16-units"),
    ("https://", "needs a host name"),
    ("https://[::1]/docs", "needs a host name"),
    ("https://192.0.2.1/docs", "needs a host name"),
    ("https://ex%61mple.com/docs", "needs a host name"),
    ("https://[::1/docs", "is not a valid URL"),
    ("https://example.com:99999/docs", "is not a valid URL"),
])
def test_docs_url_must_be_an_https_url_redamon_accepts(good_plugin, url, message):
    assert f"listing.json docsUrl: {message}" in _listing(good_plugin, {"docsUrl": url})


def test_a_docs_url_of_exactly_500_characters_passes(good_plugin):
    url = "https://example.com/" + "a" * 480
    assert len(url) == 500
    assert _listing(good_plugin, {"docsUrl": url}) == ""


def test_listing_not_json(good_plugin):
    (good_plugin / "listing.json").write_text("{not json")
    assert "listing.json: not valid JSON" in "\n".join(validate.validate(good_plugin))


def test_a_symlinked_icon_is_refused(good_plugin, tmp_path):
    (tmp_path / "elsewhere.png").write_bytes(ICONS["icon.png"])
    os.symlink(tmp_path / "elsewhere.png", good_plugin / "icon.png")
    assert "icon.png is a symbolic link" in _listing(good_plugin, {"icon": "icon.png"})


def test_an_image_under_another_name_is_unexpected(good_plugin):
    assert "unexpected top-level entry: logo.png" in _listing(good_plugin, None, {"logo.png": ICONS["icon.png"]})


def test_cli_names_the_plugin_on_a_listing_error(good_plugin):
    (good_plugin / "listing.json").write_text(json.dumps({"icon": "icon.png"}))
    bad = subprocess.run([sys.executable, str(ROOT / "tools" / "validate.py"), str(good_plugin)], capture_output=True, text=True)
    assert bad.returncode == 1
    assert "good: listing.json icon: icon.png is missing" in bad.stdout
