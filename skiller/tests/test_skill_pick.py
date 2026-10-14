"""Which skill a session used, built-in or the person's own, and whether it can be analysed at all
(F-11, R-20, R-49; platform 1.1.0)."""

import pytest
from redamon_plugin_sdk import PluginError

from engine import skill_pick
from engine.transcript_view import TranscriptView, ensure_finished
from fixtures.sessions_build import USER_SKILL_ID, USER_SKILL_TEXT
from support import session, view

USER_REF = f"user_skill:{USER_SKILL_ID}"


def _refused(name, code, **kw):
    with pytest.raises(PluginError) as e:
        skill_pick.choose(view(name), **kw)
    assert e.value.code == code, e.value.message
    return e.value.message


def test_the_fourteen_builtin_skills():
    assert len(skill_pick.BUILTIN_SKILLS) == 14
    assert {"sql_injection", "xss", "path_traversal", "cve_exploit", "denial_of_service"} <= set(skill_pick.BUILTIN_SKILLS)


def test_one_skill_counted_from_the_playbook_record():
    pick = skill_pick.choose(view("sqli_workflow"))
    assert pick["skillId"] == "sql_injection"
    assert pick["source"] == "workflow"
    assert pick["iterations"] == 7
    assert pick["toolCalls"] == 7
    assert pick["revision"] == 1
    assert pick["others"] == []
    assert pick["warnings"] == []
    assert pick["startedAt"] < pick["endedAt"]


def test_a_switch_mid_session_picks_the_skill_with_more_turns():
    pick = skill_pick.choose(view("switch_midsession"))
    assert pick["skillId"] == "sql_injection"
    assert pick["iterations"] == 4
    assert pick["others"] == [{"skillId": "xss", "iterations": 2}]


def test_ties_go_to_the_latest_skill():
    pick = skill_pick.choose(view("tie"))
    assert pick["skillId"] == "ssrf"
    assert pick["others"] == [{"skillId": "xss", "iterations": 3}]


def test_a_session_on_a_user_skill_before_the_agent_recorded_it_counts_by_label_with_a_warning():
    pick = skill_pick.choose(view("user_skill_only"))
    assert (pick["skillId"], pick["kind"], pick["point"], pick["target"]) == (
        "user_skill:cm1abc", "user", "user_skill.text", "cm1abc")
    assert pick["source"] == "label" and pick["iterations"] == 4
    assert pick["revision"] is None and pick["contentSha256"] is None
    assert pick["warnings"] == [skill_pick.USER_LABEL_WARNING]


def test_a_recorded_user_skill_is_counted_from_its_record():
    pick = skill_pick.choose(view("user_skill_workflow"))
    assert (pick["skillId"], pick["kind"], pick["source"], pick["iterations"]) == (USER_REF, "user", "workflow", 5)
    assert pick["warnings"] == []
    assert pick["contentSha256"] == skill_pick.content_sha16(USER_SKILL_TEXT)


def test_a_user_label_counts_in_every_phase_when_the_session_has_no_user_record():
    pick = skill_pick.choose(view("user_skill_label_only"))
    assert pick["iterations"] == 3  # informational, exploitation and post-exploitation


def test_a_user_skill_turned_off_midway_counts_only_the_turns_it_was_rendered_in():
    # The session holds a user record, so a later label without one means nothing was rendered.
    pick = skill_pick.choose(view("user_skill_disabled_midway"))
    assert (pick["skillId"], pick["iterations"], pick["source"]) == (USER_REF, 3, "workflow")


def test_built_in_and_user_skills_compete_by_turns():
    pick = skill_pick.choose(view("mixed_builtin_user"))
    assert pick["skillId"] == USER_REF and pick["iterations"] == 4
    assert pick["others"] == [{"skillId": "sql_injection", "iterations": 3}]
    built_in = skill_pick.choose(view("mixed_builtin_user"), requested="sql_injection")
    assert (built_in["kind"], built_in["point"], built_in["iterations"]) == ("builtin", "skill.text", 3)


def test_a_requested_user_skill_wins_and_one_not_used_is_refused():
    assert skill_pick.choose(view("mixed_builtin_user"), requested=USER_REF)["skillId"] == USER_REF
    assert _refused("mixed_builtin_user", "skill_not_used", requested="user_skill:cother") == (
        "This session did not use that skill of yours, so Skiller cannot improve it from this session.")


def test_a_user_record_beats_the_label():
    # skill_workflow names what the prompt held; the label can still say something else.
    t = session("user_skill_workflow")
    for e in t["events"]:
        if e["type"] == "phase_update":
            e["data"]["attack_path_type"] = "sql_injection"
    pick = skill_pick.choose(TranscriptView.from_transcript(t))
    assert pick["skillId"] == USER_REF and pick["source"] == "workflow"


def test_predates_text_compares_the_last_recorded_text():
    pick = skill_pick.choose(view("user_skill_workflow"))
    assert skill_pick.predates_text(pick, USER_SKILL_TEXT) is False
    assert skill_pick.predates_text(pick, USER_SKILL_TEXT + "One more step.\n") is True
    assert skill_pick.predates_text({"contentSha256": None}, "anything") is False


def test_an_unclassified_request_is_refused_and_says_so():
    assert "an unclassified request" in _refused("unclassified", "no_skill")


def test_a_label_whose_playbook_was_never_rendered_counts_for_nothing():
    msg = _refused("skill_not_rendered", "no_skill")
    assert "never in the agent's instructions" in msg


def test_requested_skill_not_used_is_refused():
    msg = _refused("sqli_workflow", "skill_not_used", requested="xss")
    assert "xss" in msg


def test_requested_skill_that_was_used_wins_over_the_latest():
    assert skill_pick.choose(view("tie"), requested="xss")["skillId"] == "xss"


def test_requested_skill_is_still_held_to_the_minimum():
    _refused("switch_midsession", "session_too_short", requested="xss")


def test_requested_skill_must_be_a_skill_the_session_used():
    _refused("sqli_workflow", "skill_not_used", requested="user_skill:x")
    _refused("sqli_workflow", "skill_not_used", requested="not_a_skill")


def test_legacy_session_refused():
    # No phase_update carries attack_path_type: never the misleading "no built-in skill".
    msg = _refused("legacy_no_attack_path", "session_format_unsupported")
    assert msg == "This session was recorded in a format Skiller cannot read."


def test_older_sessions_fall_back_to_the_label_with_a_warning():
    pick = skill_pick.choose(view("shape_2026_02_21"))
    assert pick["skillId"] == "path_traversal"
    assert pick["source"] == "label"
    assert pick["revision"] is None
    assert any("may not have been in the prompt for every labelled turn" in w for w in pick["warnings"])


def test_label_turns_in_post_exploitation_do_not_count():
    # The builder never injects a playbook in post-exploitation (01_current_state.md §7.2).
    pick = skill_pick.choose(view("shape_2026_08_05"))
    assert pick["skillId"] == "xss"
    assert pick["iterations"] == 3


def test_the_record_without_change_set_id_is_read():
    pick = skill_pick.choose(view("shape_2026_10_09"))
    assert (pick["skillId"], pick["revision"], pick["source"]) == ("sql_injection", 2, "workflow")


def test_fireteam_member_iterations_do_not_count():
    # Members record no skill_workflow, so they never prove the playbook was in their prompt.
    assert skill_pick.choose(view("fireteam"))["iterations"] == 4


@pytest.mark.parametrize("name", ["too_short", "no_tool_calls"])
def test_too_short(name):
    msg = _refused(name, "session_too_short")
    assert "SQL" in msg or "sql_injection" in msg


def test_too_short_on_your_own_skill_names_it_as_yours_not_by_its_id(monkeypatch):
    monkeypatch.setattr(skill_pick, "MIN_ITERATIONS", 99)
    msg = _refused("mixed_builtin_user", "session_too_short", requested=USER_REF)
    assert msg.startswith("Only 4 turn(s) with ")
    assert "used that skill of yours in this session; Skiller needs at least 99 turns" in msg
    assert USER_SKILL_ID not in msg


def test_running_and_unverified_sessions_are_refused_before_anything():
    t = session("sqli_workflow")
    t["meta"]["running"] = True
    with pytest.raises(PluginError) as e:
        ensure_finished(t["meta"])
    assert e.value.code == "session_running"
    assert e.value.message == "The agent is still working in this session. Try again when it stops."
    t["meta"]["running"] = "unverified"
    with pytest.raises(PluginError) as e:
        ensure_finished(t["meta"])
    assert e.value.code == "session_state_unverified"
    t["meta"]["running"] = False
    ensure_finished(t["meta"])


def test_an_empty_session_is_too_short_not_unsupported():
    t = {"meta": {"id": "s", "running": False}, "events": [], "truncated": {}, "untrusted": True}
    with pytest.raises(PluginError) as e:
        skill_pick.choose(TranscriptView.from_transcript(t))
    assert e.value.code == "session_too_short"
