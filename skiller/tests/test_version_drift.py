"""Whether the session ran an older version than the one that applies now (R-19)."""

from engine.skill_pick import predates_active


def _pick(revision=None, ended="2026-10-10T10:00:00.000Z"):
    return {"skillId": "sql_injection", "revision": revision, "endedAt": ended}


def _active(revision, decided="2026-10-09T10:00:00.000Z"):
    return {"changeSetId": "cs", "scope": "user", "revision": revision, "decidedAt": decided, "parts": {}}


def test_same_version_is_not_drift():
    assert predates_active(_pick(1), None) is False
    assert predates_active(_pick(3), _active(3)) is False


def test_a_newer_version_was_approved_since():
    assert predates_active(_pick(2), _active(3)) is True
    assert predates_active(_pick(1), _active(2)) is True


def test_the_version_the_session_ran_was_reset_since():
    assert predates_active(_pick(2), None) is True


def test_older_sessions_compare_dates():
    assert predates_active(_pick(None), _active(2, decided="2026-10-11T08:00:00.000Z")) is True
    assert predates_active(_pick(None), _active(2, decided="2026-10-09T08:00:00.000Z")) is False
    assert predates_active(_pick(None), None) is False
    assert predates_active(_pick(None, ended=None), _active(2)) is False
