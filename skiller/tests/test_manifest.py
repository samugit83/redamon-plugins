"""Skiller's manifest is the contract the platform installs it by (F-02, D-22)."""

import importlib
import inspect
import json
import pathlib
import sys

import jsonschema

PLUGIN = pathlib.Path(__file__).resolve().parents[1]
REPO = PLUGIN.parent
sys.path.insert(0, str(REPO / "tools"))

import semver  # noqa: E402
import validate  # noqa: E402


def _manifest():
    return json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))


def test_the_validator_accepts_skiller():
    assert validate.validate(PLUGIN) == []


def test_manifest_matches_the_platform_schema_of_every_synced_version_it_accepts():
    m = _manifest()
    versions = [v for v in semver.synced_platforms(REPO) if semver.satisfies(v, m["platform"])]
    assert versions
    for v in versions:
        schema = json.loads((REPO / ".platform-sdk" / v / "schemas" / "manifest.schema.json").read_text())
        jsonschema.Draft202012Validator(schema).validate(m)


def test_the_contract_as_designed():
    m = _manifest()
    assert (m["id"], m["version"], m["displayName"], m["dataVersion"]) == ("skiller", "1.0.0", "Skiller", 1)
    assert m["platform"] == ">=1.0.0 <2.0.0"
    assert m["permissions"] == ["session:read", "llm:invoke", "override:skill.text"]
    assert m["actions"] == [{"id": "analyze_session", "entry": "engine/main.py:analyze_session",
                             "timeoutSeconds": 600, "async": True, "llm": True}]
    assert m["contributes"] == {"chat.session.actions": [
        {"label": "Improve skill from this session", "icon": "sparkles", "action": "analyze_session"},
        {"label": "Skill versions", "icon": "history", "coreScreen": "skill.text.versions"}]}
    assert "Nothing changes until a person approves it" in m["description"]


def test_the_entry_is_an_async_function_of_ctx_and_params():
    fn = importlib.import_module("engine.main").analyze_session
    assert inspect.iscoroutinefunction(fn)
    assert list(inspect.signature(fn).parameters) == ["ctx", "params"]


def test_the_readme_says_what_is_read_and_what_is_stored():
    text = (PLUGIN / "README.md").read_text(encoding="utf-8")
    for needed in ("## What it reads", "## What it stores", "## How approval works", "## Limits"):
        assert needed in text, needed
