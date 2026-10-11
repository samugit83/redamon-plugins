"""Reading the model's JSON answers: fences, prose, the shape, one retry (TD-27)."""

import asyncio
import json

import pytest
from redamon_plugin_sdk import PluginError
from redamon_plugin_sdk.testing import FakeSdk, ScriptedLlm

from engine import llm_json

GOOD_A = {"skill_fit": "good", "outcome": "Injection confirmed.",
          "lessons": [{"id": "L1", "category": "tool_usage", "evidence": "#3", "impact": "high",
                       "lesson": "When a crawl stalls, test one parameter.", "target_parts": ["tools"]}]}
GOOD_B = {"edits": [{"part": "tools", "op": "append", "text": "New line.", "lessons": ["L1"]}],
          "summary": "One change.", "changelog": ["Added a line."]}


@pytest.mark.parametrize("content", [
    json.dumps(GOOD_A),
    "```json\n" + json.dumps(GOOD_A) + "\n```",
    "Here is my answer:\n" + json.dumps(GOOD_A, indent=2) + "\nThat is all.",
])
def test_extract_finds_the_object(content):
    assert llm_json.extract(content) == GOOD_A


def test_braces_inside_strings_do_not_confuse_extraction():
    obj = {"edits": [], "summary": "Use {{ and }} and a } alone", "changelog": []}
    assert llm_json.extract("x " + json.dumps(obj) + " y") == obj


@pytest.mark.parametrize("content", ["", "no json here", "[1, 2]", '{"a": ', "```\n```"])
def test_extract_refuses(content):
    with pytest.raises(llm_json.LlmJsonError):
        llm_json.extract(content)


def test_diagnosis_shape():
    value, errors = llm_json.check_diagnosis(GOOD_A)
    assert errors == [] and value["lessons"][0]["id"] == "L1"
    _, errors = llm_json.check_diagnosis(dict(GOOD_A, skill_fit="great"))
    assert errors and "skill_fit" in errors[0]
    _, errors = llm_json.check_diagnosis(dict(GOOD_A, lessons="none"))
    assert errors
    _, errors = llm_json.check_diagnosis(dict(GOOD_A, lessons=[{"id": "L1"}]))
    assert errors and "lesson" in errors[0]


def test_diagnosis_keeps_at_most_eight_lessons():
    many = dict(GOOD_A, lessons=[dict(GOOD_A["lessons"][0], id=f"L{i}") for i in range(12)])
    value, errors = llm_json.check_diagnosis(many)
    assert errors == [] and len(value["lessons"]) == 8


def test_no_lessons_is_a_valid_answer():
    value, errors = llm_json.check_diagnosis({"skill_fit": "good", "lessons": [], "no_change_reason": "Fine as is."})
    assert errors == [] and value["no_change_reason"] == "Fine as is."


def test_edits_shape():
    value, errors = llm_json.check_edits(GOOD_B)
    assert errors == [] and value["edits"][0]["op"] == "append"
    for bad in ({"part": "tools", "op": "rewrite", "text": "x"},
                {"part": "tools", "op": "replace", "text": "x"},
                {"part": "tools", "op": "append", "text": ""},
                {"op": "append", "text": "x"}):
        _, errors = llm_json.check_edits(dict(GOOD_B, edits=[bad]))
        assert errors, bad
    _, errors = llm_json.check_edits({"edits": [], "summary": 3})
    assert errors


def _run(llm, validator=llm_json.check_diagnosis):
    sdk = FakeSdk(llm=llm)
    return asyncio.run(llm_json.ask(sdk, [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
                                    validator, max_tokens=1000))


def test_one_retry_with_the_problem_named():
    llm = ScriptedLlm(["not json at all", json.dumps(GOOD_A)])
    out = _run(llm)
    assert out["value"]["skill_fit"] == "good" and out["attempts"] == 2 and out["model"] == "scripted"
    retry = llm.prompts[1]
    assert [m["role"] for m in retry] == ["system", "user", "assistant", "user"]
    assert "could not be used" in retry[-1]["content"]


def test_a_shape_error_also_gets_the_retry():
    llm = ScriptedLlm([json.dumps(dict(GOOD_A, skill_fit="great")), json.dumps(GOOD_A)])
    assert _run(llm)["attempts"] == 2
    assert "skill_fit" in llm.prompts[1][-1]["content"]


def test_invalid_twice_is_llm_bad_output():
    with pytest.raises(PluginError) as e:
        _run(ScriptedLlm(["nope", "still nope"]))
    assert e.value.code == "llm_bad_output"


def test_an_sdk_error_is_not_retried():
    llm = ScriptedLlm([PluginError("model_required", "Choose a model for this plugin in Models by feature.")])
    with pytest.raises(PluginError) as e:
        _run(llm)
    assert e.value.code == "model_required" and len(llm.prompts) == 1
