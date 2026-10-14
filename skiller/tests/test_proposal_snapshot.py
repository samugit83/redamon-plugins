"""A proposal is a complete version: the current version's changes carried forward (R-44, TD-19)."""

import json

from engine import proposal

FACTORY = {"tools": "factory tools", "oob": "factory oob", "payloads": "factory payloads"}


def test_carries_the_current_versions_other_parts_forward():
    current = {"tools": "v3 tools", "oob": "factory oob", "payloads": "factory payloads"}
    edited = {"oob": "new oob"}
    assert proposal.snapshot(FACTORY, current, edited) == {"tools": "v3 tools", "oob": "new oob"}


def test_omits_parts_equal_to_the_factory_text():
    current = {"tools": "v3 tools", "oob": "factory oob", "payloads": "factory payloads"}
    edited = {"tools": "factory tools", "payloads": "new payloads"}
    assert proposal.snapshot(FACTORY, current, edited) == {"payloads": "new payloads"}


def test_unchanged_compares_with_the_current_version_not_the_factory():
    current = {"tools": "v3 tools", "oob": "factory oob", "payloads": "factory payloads"}
    assert proposal.unchanged(current, {"tools": "v3 tools"})
    assert not proposal.unchanged(current, {"tools": "v4 tools"})


def test_note_holds_summary_and_changes_within_the_platform_limit():
    note = proposal.note(summary="Two small changes.", changelog=["Add a thread limit.", "Confirm with a pair."],
                         name="SQL Injection", from_label="v1", lessons=3, dropped=1)
    # No version number of its own: the platform numbers versions with a counter that is never
    # reused, so "v1 + 1" would be wrong after any deletion; and plain text, because the
    # versions list shows notes unrendered.
    assert note.startswith("Skiller's proposal for SQL Injection, built on v1.")
    assert "*" not in note and "v2" not in note
    assert "Two small changes." in note and "- Add a thread limit." in note
    long_note = proposal.note(summary="s" * 5000, changelog=["c" * 500] * 30, name="X", from_label="v1",
                              lessons=1, dropped=0)
    assert len(long_note) <= proposal.NOTE_MAX_CHARS


def test_meta_stays_small():
    meta = proposal.meta(summary="s" * 5000, changelog=["c" * 1000] * 50,
                         lessons=[{"id": f"L{i}", "category": "tool_usage", "impact": "high",
                                   "lesson": "l" * 2000, "target_parts": ["tools"], "evidence": "never kept"}
                                  for i in range(8)],
                         guard={"droppedLessons": 1, "droppedEdits": 2, "repaired": 1, "riskRefusals": 0},
                         source_session_id="session_x", high_water_seq=41, session_revision=2,
                         predates_active=True, model_used="scripted", skiller_version="1.0.0")
    assert len(json.dumps(meta).encode()) <= proposal.META_MAX_BYTES
    assert all("evidence" not in lesson for lesson in meta["lessons"])
    assert meta["sessionPredatesActive"] is True and meta["sourceHighWaterSeq"] == 41


def test_a_user_skill_note_and_meta():
    note = proposal.note(summary="One change.", changelog=["Step 3: one parameter at a time."], name="My SQLi flow v2",
                         from_label="its current text", lessons=1, dropped=0)
    assert note.startswith("Skiller's proposal for My SQLi flow v2, built on its current text.")
    meta = proposal.meta(summary="s", changelog=[], lessons=[], guard={}, source_session_id="s", high_water_seq=1,
                         session_revision=None, predates_active=False, model_used="scripted", skiller_version="1.1.0",
                         skill_kind="user", source_skill_name="My SQLi flow v2")
    assert (meta["skillKind"], meta["sourceSkillName"], meta["sessionRevision"]) == ("user", "My SQLi flow v2", None)
