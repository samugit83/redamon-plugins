"""analyze_session on one of the person's own skills (platform 1.1.0, `user_skill.text`): the pick,
the user-skill prompt, the one plain part, the proposal RedAmon saves as a NEW skill, and the
refusals that come before any model call."""

import asyncio
import json

import pytest
from redamon_plugin_sdk import PluginError
from redamon_plugin_sdk.testing import ScriptedLlm, make_context

from engine import prompts, skill_pick
from engine.main import analyze_session
from fixtures.sessions_build import HOST, USER_SKILL_ID, USER_SKILL_NAME, USER_SKILL_TEXT
from support import session

USER_REF = f"user_skill:{USER_SKILL_ID}"
PICK = {"kind": "session.skill", "point": "user_skill.text", "target": USER_SKILL_ID, "ref": USER_REF}
REGISTRY = {"sql_injection": {"displayName": "SQL Injection", "skillMaxChars": 20_000, "parts": {
    "tools": {"text": "## SQL injection\nRun sqlmap.\n", "kind": "plain", "maxChars": 8_000}}}}

LESSON = {"id": "L1", "category": "tool_usage", "impact": "high", "evidence": "EVIDENCE-ONLY #3 #4",
          "lesson": "When a scanner crawl stalls, test one parameter with one thread, because crawls hang.",
          "target_parts": ["body"]}
A_GOOD = json.dumps({"skill_fit": "good", "outcome": "Injection confirmed after a stall.", "lessons": [LESSON]})
ANCHOR = "3. Run sqlmap on the parameter that broke"
EDIT = {"part": "body", "op": "insert_before", "anchor": ANCHOR,
        "text": "Never crawl first: --crawl can stall for minutes; test one parameter with --threads 1.", "lessons": ["L1"]}
B_GOOD = json.dumps({"edits": [EDIT], "summary": "Steer sqlmap away from long crawls.",
                     "changelog": ["Before step 3: test one parameter at a time."]})


def _run(answers, *, name="user_skill_workflow", params=None, skill=True, content=USER_SKILL_TEXT, setup=None):
    llm = ScriptedLlm(answers)
    t = session(name)
    ctx = make_context(plugin_id="skiller", version="1.1.0", user_id="user-1", project_id="project-1",
                       session_id=t["meta"]["id"], job_id="job-1", transcript=t, llm=llm, registry=REGISTRY)
    if skill:
        ctx.sdk.add_user_skill(USER_SKILL_ID, USER_SKILL_NAME, content, description="My own SQL injection flow.")
    if setup:
        setup(ctx.sdk)
    result = asyncio.run(analyze_session(ctx, params if params is not None else {"pick": PICK}))
    return result, ctx.sdk, llm


def _refused(code, **kw):
    with pytest.raises(PluginError) as e:
        _run([A_GOOD, B_GOOD], **kw)
    assert e.value.code == code, e.value.message
    return e.value


def test_a_picked_user_skill_is_proposed_at_its_own_point():
    result, sdk, llm = _run([A_GOOD, B_GOOD])
    [cs] = sdk.proposals
    assert result == {"status": "proposed", "skillId": USER_REF,
                      "reviewProposal": {"extensionPoint": "user_skill.text", "changeSetId": cs["changeSetId"]},
                      "message": f"Skiller proposes a new version of {USER_SKILL_NAME}.",
                      "stats": {"modelCalls": {"diagnose": 1, "edit": 1, "repair": 0}, "lessons": 1,
                                "droppedLessons": 0, "edits": 1, "droppedEdits": 0, "repaired": 0}}
    assert (cs["point"], cs["targetGroup"], cs["scope"], list(cs["parts"])) == (
        "user_skill.text", USER_SKILL_ID, "user", ["body"])
    assert "Never crawl first" in cs["parts"]["body"] and cs["parts"]["body"].endswith("outside the scope.\n")
    assert cs["note"].startswith(f"Skiller's proposal for {USER_SKILL_NAME}, built on its current text.")
    meta = cs["meta"]
    assert (meta["skillKind"], meta["sourceSkillName"], meta["sessionRevision"]) == ("user", USER_SKILL_NAME, None)
    assert meta["sessionPredatesActive"] is False and meta["skillerVersion"] == "1.1.0"


def test_the_user_skill_prompt_and_the_one_plain_part_reach_the_model():
    _, _, llm = _run([A_GOOD, B_GOOD])
    stage_a, stage_b = llm.prompts
    assert stage_a[0]["content"] == prompts.SYSTEM_A_USER
    assert stage_a[1]["content"].startswith(f"Skill: {USER_SKILL_NAME} (your skill), its current text.")
    assert "### Part body (plain)" in stage_a[1]["content"] and HOST in stage_a[1]["content"]
    assert stage_b[0]["content"].startswith(prompts.STAGE_B_MARKER)
    assert "EVIDENCE-ONLY" not in json.dumps(stage_b) and HOST not in json.dumps(stage_b)


def test_progress_names_the_skill_as_the_persons_own():
    _, sdk, _ = _run([A_GOOD, B_GOOD])
    assert sdk.progress[0]["fields"] == {"Skill": f"{USER_SKILL_NAME} (your skill)", "Current version": USER_SKILL_NAME,
                                         "Stage": "1 of 3"}


def test_without_a_pick_the_skill_with_the_most_turns_is_chosen():
    result, sdk, _ = _run([A_GOOD, B_GOOD], name="mixed_builtin_user", params={})
    assert result["skillId"] == USER_REF and sdk.proposals[0]["point"] == "user_skill.text"


def test_a_pick_of_the_built_in_skill_of_a_mixed_session_proposes_that_one():
    built_in = {"kind": "session.skill", "point": "skill.text", "target": "sql_injection", "ref": "sql_injection"}
    edit = {"part": "tools", "op": "append", "text": "Test one parameter at a time.", "lessons": ["L1"]}
    b = json.dumps({"edits": [edit], "summary": "s", "changelog": ["c"]})
    result, sdk, _ = _run([A_GOOD, b], name="mixed_builtin_user", params={"pick": built_in})
    assert result["skillId"] == "sql_injection" and result["reviewProposal"]["extensionPoint"] == "skill.text"
    assert sdk.proposals[0]["point"] == "skill.text"


def test_a_session_that_ran_another_text_says_so():
    edited = USER_SKILL_TEXT + "4. Report the parameter.\n"
    _, sdk, _ = _run([A_GOOD, B_GOOD], content=edited)
    assert sdk.proposals[0]["meta"]["sessionPredatesActive"] is True


def test_the_text_edited_while_skiller_works_is_redone_on_the_new_text_once():
    def edit_after_first_read(sdk):
        original = sdk.gateway._override_get
        calls = {"n": 0}

        def wrapped(body):
            out = original(body)
            calls["n"] += 1
            if calls["n"] == 1:
                sdk.gateway.user_skills[USER_SKILL_ID]["content"] = USER_SKILL_TEXT + "4. Report.\n"
            return out
        sdk.gateway._override_get = wrapped
    result, sdk, llm = _run([A_GOOD, B_GOOD], setup=edit_after_first_read)
    assert result["status"] == "proposed" and len(llm.prompts) == 2  # no new model call
    assert sdk.proposals[0]["parts"]["body"].endswith("4. Report.\n")


def test_edits_that_no_longer_fit_an_edited_text_are_refused():
    def drop_the_anchor(sdk):
        original = sdk.gateway._override_get
        calls = {"n": 0}

        def wrapped(body):
            out = original(body)
            calls["n"] += 1
            if calls["n"] == 1:
                sdk.gateway.user_skills[USER_SKILL_ID]["content"] = "# My SQLi flow\nOnly one step now.\n"
            return out
        sdk.gateway._override_get = wrapped
    err = _refused("factory_changed", setup=drop_the_anchor)
    assert err.message == f"{USER_SKILL_NAME} was edited while Skiller worked and its edits no longer fit it. Run Skiller again."


@pytest.mark.parametrize("code,kw", [
    ("skill_not_found", {"skill": False}),
    ("skill_contains_credentials", {"content": USER_SKILL_TEXT + "Sign requests with AKIAIOSFODNN7EXAMPLE first.\n"}),
    ("skill_not_used", {"params": {"pick": dict(PICK, ref="user_skill:cothersk1")}}),
])
def test_refused_before_any_model_call(code, kw):
    llm_seen = {}

    def keep(sdk):
        llm_seen["llm"] = sdk.llm_script
    with pytest.raises(PluginError) as e:
        _run([A_GOOD, B_GOOD], setup=keep, **kw)
    assert e.value.code == code, e.value.message
    assert llm_seen["llm"].prompts == []


def test_another_users_skill_is_not_found():
    def foreign(sdk):
        sdk.gateway.user_skills[USER_SKILL_ID]["owner"] = "user-2"
    _refused("skill_not_found", setup=foreign)


def test_five_waiting_proposals_for_the_skill_stop_it_before_any_model_call():
    def five(sdk):
        for i in range(5):
            sdk.gateway.proposals.append({"changeSetId": f"p{i}", "status": "proposed", "scope": "user",
                                          "targetGroup": USER_SKILL_ID, "point": "user_skill.text"})
    err = _refused("too_many_pending", setup=five)
    assert USER_SKILL_NAME in err.message


def test_the_limit_is_counted_as_redamon_counts_it():
    # An astral character is one Python character but two UTF-16 code units: RedAmon's limit.
    near = USER_SKILL_TEXT + ("\U0001F600" * ((51_200 - len(USER_SKILL_TEXT)) // 2 - 10))
    assert len(near) < 51_200 and skill_pick  # within the limit in Python characters
    edit = dict(EDIT, op="append", anchor=None, text="x" * 30)
    b = json.dumps({"edits": [edit], "summary": "s", "changelog": ["c"]})
    result, sdk, _ = _run([A_GOOD, b, b], content=near)
    assert result["status"] == "no_improvement" and sdk.proposals == []
