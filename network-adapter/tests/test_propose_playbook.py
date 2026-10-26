"""propose_playbook through the real FakeSdk: append-only, refuses missing parts
and over-long texts, and lands as a proposal a person must approve."""
import asyncio

from redamon_plugin_sdk import PluginError
from redamon_plugin_sdk.testing import FakeSdk, make_context

import engine.main as plugin

SKILL = "brute_force_credential_guess"
PART = "wordlist_guidance"

REGISTRY = {
    SKILL: {
        "skillMaxChars": 20000,
        "parts": {
            PART: {"text": "factory guidance", "maxChars": 4000},
            "tools": {"text": "tools text", "maxChars": 4000},
        },
    }
}


def invoke(registry=None, params=None):
    sdk = FakeSdk(plugin_id="network-adapter", registry=registry or REGISTRY,
                  permissions=["override:skill.text"])
    context = make_context(plugin_id="network-adapter", sdk=sdk)
    return sdk, asyncio.run(plugin.propose_playbook(context, params or {}))


def test_appends_playbook_after_factory_text():
    sdk, out = invoke()
    (cs,) = sdk.proposals
    assert cs["point"] == "skill.text" and cs["targetGroup"] == SKILL
    assert set(cs["parts"]) == {PART}
    text = cs["parts"][PART]
    assert text.startswith("factory guidance"), "the factory text must be kept, not replaced"
    for needle in ("egress_status", "enforcement_confirmed", "idempotent", "ambiguous_block",
                   "Retry-After"):
        assert needle in text
    assert out["reviewProposal"]["changeSetId"] == cs["changeSetId"]
    assert out["addedChars"] > 500


def test_missing_part_refused_without_proposing():
    sdk = FakeSdk(plugin_id="network-adapter", registry=REGISTRY,
                  permissions=["override:skill.text"])
    context = make_context(plugin_id="network-adapter", sdk=sdk)
    try:
        asyncio.run(plugin.propose_playbook(context, {"skill": SKILL, "part": "does_not_exist"}))
    except PluginError as caught:
        assert caught.code == "part_not_found"
    else:
        raise AssertionError("part_not_found expected")
    assert sdk.proposals == []


def test_size_limit_refused_before_proposing():
    registry = {SKILL: {"parts": {PART: {"text": "x" * 3900, "maxChars": 4000}}}}
    sdk = FakeSdk(plugin_id="network-adapter", registry=registry,
                  permissions=["override:skill.text"])
    context = make_context(plugin_id="network-adapter", sdk=sdk)
    try:
        asyncio.run(plugin.propose_playbook(context, {}))
    except PluginError as caught:
        assert caught.code == "playbook_too_large"
    else:
        raise AssertionError("playbook_too_large expected")
    assert sdk.proposals == []
