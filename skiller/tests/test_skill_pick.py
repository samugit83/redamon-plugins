"""Which built-in skill a session used, and whether it can be analysed at all (F-11, R-20, R-49)."""

import pytest
from redamon_plugin_sdk import PluginError

from engine import skill_pick
from engine.transcript_view import TranscriptView, ensure_finished
from support import session, view


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


def test_a_user_skill_only_is_refused_and_says_so():
    msg = _refused("user_skill_only", "no_builtin_skill")
    assert "a user skill" in msg and "nothing for Skiller to improve" in msg


def test_an_unclassified_request_is_refused_and_says_so():
    assert "an unclassified request" in _refused("unclassified", "no_builtin_skill")


def test_a_label_whose_playbook_was_never_rendered_counts_for_nothing():
    msg = _refused("skill_not_rendered", "no_builtin_skill")
    assert "never in the agent's instructions" in msg


def test_requested_skill_not_used_is_refused():
    msg = _refused("sqli_workflow", "skill_not_used", requested="xss")
    assert "xss" in msg


def test_requested_skill_that_was_used_wins_over_the_latest():
    assert skill_pick.choose(view("tie"), requested="xss")["skillId"] == "xss"


def test_requested_skill_is_still_held_to_the_minimum():
    _refused("switch_midsession", "session_too_short", requested="xss")


def test_requested_skill_must_be_a_builtin_id():
    _refused("sqli_workflow", "skill_not_used", requested="user_skill:x")


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
