"""analyze_session end to end on the SDK's fake Gateway and a scripted model (F-10, F-11, R-13, R-71)."""

import asyncio
import json

import pytest
from redamon_plugin_sdk import PluginError
from redamon_plugin_sdk.testing import ScriptedLlm, make_context

from engine.main import analyze_session
from fixtures.sessions_build import HOST, IP4
from support import session

TOOLS = ("## ATTACK SKILL: SQL INJECTION\n"
         "### Step 1: Confirm the injection point (execute_curl)\n"
         "Send a single quote and compare the response with the baseline.\n"
         "### Step 2: Run sqlmap (kali_shell)\n"
         "Use --level {sqli_level} --risk {sqli_risk} and tamper scripts {sqli_tamper_scripts}.\n"
         "Example: kali_shell({{\"command\": \"sqlmap -u 'TARGET_URL' --batch\"}})\n"
         "Do NOT test hosts outside the scope.\n")
OOB = "## OOB workflow\nUse interactsh-client for blind cases.\n"
PAYLOADS = "## Payloads\n' OR 1=1 --\n"
REGISTRY = {"sql_injection": {"displayName": "SQL Injection", "parts": {
    "tools": {"text": TOOLS, "kind": "template", "placeholders": ["sqli_level", "sqli_risk", "sqli_tamper_scripts"],
              "maxChars": 30_000},
    "oob_workflow": {"text": OOB, "maxChars": 8_000},
    "payload_reference": {"text": PAYLOADS, "maxChars": 8_000}}}}

LESSON = {"id": "L1", "category": "tool_usage", "impact": "high", "evidence": "EVIDENCE-ONLY #3 #4 #5",
          "lesson": "When a scanner crawl stalls, test one parameter with one thread, because crawls hang.",
          "target_parts": ["tools"]}
LEAKY = dict(LESSON, id="L2", lesson=f"When {HOST} stalls, test product_id only.")
A_GOOD = json.dumps({"skill_fit": "good", "outcome": "Injection confirmed after a stall.", "lessons": [LESSON, LEAKY]})
GOOD_EDIT = {"part": "tools", "op": "insert_after", "anchor": "### Step 2: Run sqlmap (kali_shell)",
             "text": "Test one parameter at a time with -p <param> and --threads 1; a crawl (--crawl) can stall for minutes.",
             "lessons": ["L1"]}
B_GOOD = json.dumps({"edits": [GOOD_EDIT], "summary": "Steer sqlmap away from long crawls.",
                     "changelog": ["Step 2: test one parameter with one thread before crawling."]})


def _run(answers, *, name="sqli_workflow", transcript=None, params=None, setup=None):
    llm = ScriptedLlm(answers)
    t = transcript if transcript is not None else session(name)
    ctx = make_context(plugin_id="skiller", version="1.0.0", user_id="user-1", project_id="project-1",
                       session_id=t["meta"]["id"], job_id="job-1", transcript=t, llm=llm, registry=REGISTRY)
    if setup:
        setup(ctx.sdk)
    result = asyncio.run(analyze_session(ctx, params or {}))
    return result, ctx.sdk, llm


def _refused(code, **kw):
    llm_holder = {}

    def setup(sdk):
        llm_holder["llm"] = sdk.llm_script
        if kw.get("setup"):
            kw["setup"](sdk)
    with pytest.raises(PluginError) as e:
        _run([A_GOOD, B_GOOD], **{k: v for k, v in kw.items() if k != "setup"}, setup=setup)
    assert e.value.code == code, e.value.message
    return e.value, llm_holder["llm"]


def test_proposes_a_snapshot_and_points_the_review_at_it():
    result, sdk, llm = _run([A_GOOD, B_GOOD])
    [cs] = sdk.proposals
    assert result == {"status": "proposed", "skillId": "sql_injection",
                      "reviewProposal": {"extensionPoint": "skill.text", "changeSetId": cs["changeSetId"]},
                      "message": "Skiller proposes v2 of SQL Injection.",
                      "stats": {"modelCalls": {"diagnose": 1, "edit": 1, "repair": 0}, "lessons": 1,
                                "droppedLessons": 1, "edits": 1, "droppedEdits": 0, "repaired": 0}}
    assert cs["scope"] == "user" and cs["targetGroup"] == "sql_injection"
    assert set(cs["parts"]) == {"tools"}
    assert "### Step 2: Run sqlmap (kali_shell)\nTest one parameter at a time" in cs["parts"]["tools"]
    assert "**Skiller proposes v2 of SQL Injection**" in cs["note"]
    meta = cs["meta"]
    assert meta["sourceSessionId"] == "session_5e1f7c2a9b3d4e60" and meta["sessionRevision"] == 1
    assert meta["sessionPredatesActive"] is False and meta["modelUsed"] == "scripted"
    assert meta["skillerVersion"] == "1.0.0" and meta["sourceHighWaterSeq"] > 0
    assert meta["guard"] == {"droppedLessons": 1, "droppedEdits": 0, "repaired": 0, "riskRefusals": 0}
    assert [lesson["id"] for lesson in meta["lessons"]] == ["L1"]
    assert "evidence" not in meta["lessons"][0]
    assert "fromRevision" not in meta  # the platform records it (RV-25)
    assert len(llm.prompts) == 2


def test_progress_names_the_skill_first_then_the_stages():
    _, sdk, _ = _run([A_GOOD, B_GOOD])
    assert [p["message"] for p in sdk.progress] == ["Reading the session", "Writing the new version",
                                                    "Saving the proposal"]
    assert sdk.progress[0]["fields"] == {"Skill": "SQL Injection (sql_injection)", "Current version": "v1",
                                         "Stage": "1 of 3"}
    assert [p["fields"]["Stage"] for p in sdk.progress] == ["1 of 3", "2 of 3", "3 of 3"]


def test_the_digest_is_fenced_and_evidence_never_reaches_the_edit_stage():
    _, _, llm = _run([A_GOOD, B_GOOD])
    stage_a, stage_b = llm.prompts
    assert stage_a[0]["content"].startswith("You review the attack playbooks of RedAmon")
    assert "<<<UNTRUSTED_SESSION id=" in stage_a[1]["content"] and HOST in stage_a[1]["content"]
    assert stage_b[0]["content"].startswith("You turn lessons into small, precise edits")
    assert "EVIDENCE-ONLY" not in json.dumps(stage_b) and HOST not in json.dumps(stage_b)
    assert "### Part tools (template: placeholders {sqli_level}, {sqli_risk}, {sqli_tamper_scripts})" in stage_b[1]["content"]


def _running(value):
    t = session("sqli_workflow")
    t["meta"]["running"] = value
    return t


@pytest.mark.parametrize("code,kw", [
    ("session_running", {"transcript": _running(True)}),
    ("session_state_unverified", {"transcript": _running("unverified")}),
    ("session_too_short", {"name": "too_short"}),
    ("no_builtin_skill", {"name": "user_skill_only"}),
    ("session_format_unsupported", {"name": "legacy_no_attack_path"}),
    ("skill_not_used", {"params": {"skillId": "xss"}}),
])
def test_refused_before_any_model_call(code, kw):
    _, llm = _refused(code, **kw)
    assert llm.prompts == []


def test_too_many_pending_before_any_model_call():
    def five_pending(sdk):
        for i in range(5):
            sdk.gateway.proposals.append({"changeSetId": f"p{i}", "status": "proposed", "scope": "user",
                                          "targetGroup": "sql_injection"})
    err, llm = _refused("too_many_pending", setup=five_pending)
    assert err.message == ("There are already 5 proposals for SQL Injection waiting for a decision. "
                           "Approve, reject or delete one first.")
    assert llm.prompts == []


def test_skill_fit_poor_is_no_improvement():
    a = json.dumps({"skill_fit": "poor", "outcome": "Not SQL injection at all.", "lessons": [LESSON]})
    result, sdk, llm = _run([a])
    assert result["status"] == "no_improvement" and result["skillId"] == "sql_injection"
    assert result["message"] == "This session did not fit the SQL Injection skill, so it is not a good basis for improving it."
    assert result["stats"]["modelCalls"] == {"diagnose": 1, "edit": 0, "repair": 0}
    assert sdk.proposals == [] and len(llm.prompts) == 1


def test_only_leaking_lessons_is_no_improvement():
    a = json.dumps({"skill_fit": "good", "outcome": "x", "lessons": [LEAKY]})
    result, sdk, llm = _run([a])
    assert result["status"] == "no_improvement" and sdk.proposals == [] and len(llm.prompts) == 1
    assert HOST not in result["message"]


def test_no_lessons_reason_is_shown_without_identifiers():
    a = json.dumps({"skill_fit": "good", "lessons": [], "no_change_reason": f"The playbook worked well on {HOST}."})
    result, _, _ = _run([a])
    assert result["status"] == "no_improvement" and HOST not in result["message"] and "<redacted>" in result["message"]


def test_repair_round_fixes_a_leaking_edit():
    leaking = dict(GOOD_EDIT, text=f"On {HOST} test one parameter at a time.")
    b1 = json.dumps({"edits": [leaking], "summary": "s", "changelog": ["c"]})
    result, sdk, llm = _run([A_GOOD, b1, B_GOOD])
    assert result["status"] == "proposed"
    assert sdk.proposals[0]["meta"]["guard"]["repaired"] == 1
    repair = llm.prompts[2]
    assert [m["role"] for m in repair] == ["system", "user", "assistant", "user"]
    assert "session-specific value" in repair[-1]["content"]
    assert HOST not in sdk.proposals[0]["parts"]["tools"]


def test_repair_round_drops_what_still_fails_and_keeps_the_rest():
    bad = {"part": "tools", "op": "insert_after", "anchor": "an anchor that is nowhere", "text": "x y z", "lessons": ["L1"]}
    b1 = json.dumps({"edits": [GOOD_EDIT, bad], "summary": "s", "changelog": ["c"]})
    b2 = json.dumps({"edits": [bad], "summary": "s", "changelog": ["c"]})
    result, sdk, _ = _run([A_GOOD, b1, b2])
    assert result["status"] == "proposed"
    guard = sdk.proposals[0]["meta"]["guard"]
    assert guard["droppedEdits"] == 1 and guard["repaired"] == 0
    assert "Test one parameter at a time" in sdk.proposals[0]["parts"]["tools"]


def test_nothing_left_after_the_repair_is_no_improvement():
    bad = {"part": "tools", "op": "replace", "anchor": "Do NOT test hosts outside the scope.",
           "text": "You may test hosts outside the scope.", "lessons": ["L1"]}
    b = json.dumps({"edits": [bad], "summary": "s", "changelog": ["c"]})
    result, sdk, llm = _run([A_GOOD, b, b])
    assert result["status"] == "no_improvement" and sdk.proposals == []
    assert len(llm.prompts) == 3


def test_template_problems_go_to_the_repair_round():
    broken = {"part": "tools", "op": "replace", "anchor": "--level {sqli_level} --risk {sqli_risk}",
              "text": "--level 3 --risk {sqli_risk}", "lessons": ["L1"]}
    b1 = json.dumps({"edits": [broken], "summary": "s", "changelog": ["c"]})
    result, sdk, llm = _run([A_GOOD, b1, B_GOOD])
    assert result["status"] == "proposed"
    assert "placeholder {sqli_level} removed" in llm.prompts[2][-1]["content"]


def test_no_edits_is_no_improvement():
    b = json.dumps({"edits": [], "summary": "The playbook already covers this.", "changelog": []})
    result, sdk, _ = _run([A_GOOD, b])
    assert result["status"] == "no_improvement" and "already covers" in result["message"] and sdk.proposals == []


def test_invalid_json_twice_is_llm_bad_output():
    with pytest.raises(PluginError) as e:
        _run(["not json", "still not json"])
    assert e.value.code == "llm_bad_output"


@pytest.mark.parametrize("code", ["model_required", "plugin_llm_cap", "job_ended"])
def test_sdk_errors_propagate(code):
    with pytest.raises(PluginError) as e:
        _run([PluginError(code, "from the Gateway")])
    assert e.value.code == code


def test_no_transcript_persisted():
    _, sdk, _ = _run([A_GOOD, B_GOOD])
    assert sdk.rows == {}


def test_the_active_version_is_the_base_and_its_parts_carry_forward():
    def active(sdk):
        sdk.set_active("sql_injection", revision=3, parts={"payload_reference": "## Payloads (v3)\n' OR 1=1 --\n"})
    result, sdk, llm = _run([A_GOOD, B_GOOD], setup=active)
    assert sdk.progress[0]["fields"]["Current version"] == "v3"
    parts = sdk.proposals[0]["parts"]
    assert parts["payload_reference"] == "## Payloads (v3)\n' OR 1=1 --\n" and "tools" in parts
    assert sdk.proposals[0]["meta"]["sessionPredatesActive"] is True  # the session ran v1
    assert result["message"] == "Skiller proposes v4 of SQL Injection."
    assert "## Payloads (v3)" in llm.prompts[0][1]["content"]


def test_skill_chosen_from_the_playbook_record():
    result, sdk, _ = _run([A_GOOD, B_GOOD], name="switch_midsession")
    assert result["skillId"] == "sql_injection"


def test_a_factory_change_while_working_is_redone_on_the_new_text_once():
    def change_factory_after_first_read(sdk):
        original = sdk.gateway._override_get
        calls = {"n": 0}

        def wrapped(body):
            out = original(body)
            calls["n"] += 1
            if calls["n"] == 1:
                sdk.gateway.registry["sql_injection"]["parts"]["oob_workflow"]["text"] = OOB + "Updated by RedAmon.\n"
            return out
        sdk.gateway._override_get = wrapped
    result, sdk, llm = _run([A_GOOD, B_GOOD], setup=change_factory_after_first_read)
    assert result["status"] == "proposed" and len(llm.prompts) == 2  # redone without asking the model again
    assert sdk.proposals[0]["parts"].get("oob_workflow") is None  # the new factory text runs untouched


def test_text_the_gateway_cannot_store_is_cleaned_before_it_is_sent():
    t = session("sqli_workflow")
    for e in t["events"]:
        if e["type"] == "tool_complete":
            e["data"]["output_summary"] = "bad \x00 and \ud800 here"
    _, _, llm = _run([A_GOOD, B_GOOD], transcript=t)
    sent = json.dumps(llm.prompts, ensure_ascii=False)
    assert "\x00" not in sent and "\\u0000" not in sent and "\ud800" not in sent


def test_identifiers_never_reach_the_proposal_note_or_meta():
    a = json.dumps({"skill_fit": "good", "outcome": f"Done on {HOST}.", "lessons": [LESSON]})
    b = json.dumps({"edits": [GOOD_EDIT], "summary": f"Seen on {HOST} at {IP4}.", "changelog": [f"for {HOST}"]})
    _, sdk, _ = _run([a, b])
    stored = json.dumps({"note": sdk.proposals[0]["note"], "meta": sdk.proposals[0]["meta"]})
    assert HOST not in stored and IP4 not in stored


def test_stats_count_retries_and_the_repair_round_without_session_text():
    leaking = dict(GOOD_EDIT, text=f"On {HOST} test one parameter at a time.")
    b1 = json.dumps({"edits": [leaking], "summary": "s", "changelog": ["c"]})
    result, _, _ = _run(["not json", A_GOOD, b1, B_GOOD])
    assert result["stats"]["modelCalls"] == {"diagnose": 2, "edit": 1, "repair": 1}
    assert result["stats"]["repaired"] == 1
    assert HOST not in json.dumps(result["stats"])
