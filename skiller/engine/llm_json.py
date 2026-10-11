"""Reading the model's JSON answers, with one retry that names the problem (TD-27)."""

from __future__ import annotations

import json
import re

from redamon_plugin_sdk import PluginError

from .transcript_view import as_text, storable

_FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*\s*\n?(.*?)```", re.S)
SKILL_FITS = ("good", "partial", "poor")
CATEGORIES = ("tool_usage", "ordering", "missed_signal", "verification", "wasted_steps", "missing_guidance")
IMPACTS = ("high", "medium", "low")
OPS = ("insert_after", "insert_before", "replace", "append")
MAX_LESSONS = 8
# What the retry quotes back of a failed answer.
ECHO_MAX_CHARS = 20_000


class LlmJsonError(ValueError):
    pass


def _first_object(text: str):
    """The first JSON object in `text` that parses, scanning from each `{`."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text, i)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def extract(content: str) -> dict:
    """The JSON object in a model's answer: alone, in a code fence, or among prose."""
    if not isinstance(content, str) or not content.strip():
        raise LlmJsonError("the answer was empty")
    candidates = [m.group(1) for m in _FENCE_RE.finditer(content)] + [content]
    for text in candidates:
        value = _first_object(text)
        if value is not None:
            return value
    raise LlmJsonError("the answer holds no JSON object")


def _str_list(value) -> list:
    return [as_text(v) for v in value if isinstance(v, (str, int, float))] if isinstance(value, list) else []


def check_diagnosis(obj: dict) -> tuple:
    errors = []
    fit = obj.get("skill_fit")
    if fit not in SKILL_FITS:
        errors.append(f"skill_fit must be one of {', '.join(SKILL_FITS)}, not {json.dumps(fit)}")
    lessons = obj.get("lessons", [])
    if not isinstance(lessons, list):
        errors.append("lessons must be a list")
        lessons = []
    kept = []
    for i, lesson in enumerate(lessons[:MAX_LESSONS], 1):
        if not isinstance(lesson, dict) or not isinstance(lesson.get("lesson"), str) or not lesson["lesson"].strip():
            errors.append(f"lessons[{i}] needs a non-empty lesson text")
            continue
        category = lesson.get("category")
        impact = lesson.get("impact")
        kept.append({
            "id": as_text(lesson.get("id")) or f"L{i}",
            "category": category if category in CATEGORIES else "missing_guidance",
            "evidence": as_text(lesson.get("evidence")),
            "lesson": lesson["lesson"].strip(),
            "target_parts": _str_list(lesson.get("target_parts")),
            "impact": impact if impact in IMPACTS else "medium",
        })
    value = {"skill_fit": fit, "outcome": as_text(obj.get("outcome")), "lessons": kept,
             "no_change_reason": as_text(obj.get("no_change_reason"))}
    return value, errors


def check_edits(obj: dict) -> tuple:
    errors = []
    edits = obj.get("edits", [])
    if not isinstance(edits, list):
        errors.append("edits must be a list")
        edits = []
    kept = []
    for i, e in enumerate(edits, 1):
        if not isinstance(e, dict):
            errors.append(f"edits[{i}] must be an object")
            continue
        op, part, text, anchor = e.get("op"), e.get("part"), e.get("text"), e.get("anchor")
        if op not in OPS:
            errors.append(f"edits[{i}].op must be one of {', '.join(OPS)}")
        elif not isinstance(part, str) or not part:
            errors.append(f"edits[{i}] needs the part id")
        elif not isinstance(text, str) or not text.strip():
            errors.append(f"edits[{i}] needs a non-empty text")
        elif op != "append" and (not isinstance(anchor, str) or not anchor):
            errors.append(f"edits[{i}] ({op}) needs an anchor copied from the part")
        else:
            kept.append({"part": part, "op": op, "anchor": anchor if op != "append" else None, "text": text,
                         "lessons": _str_list(e.get("lessons"))})
    summary = obj.get("summary", "")
    if not isinstance(summary, str):
        errors.append("summary must be text")
        summary = ""
    return {"edits": kept, "summary": summary.strip(), "changelog": _str_list(obj.get("changelog"))}, errors


async def ask(sdk, messages: list, check, *, max_tokens: int, temperature: float = 0.2) -> dict:
    """One model call; on an unreadable answer, one more call that names the problem.

    Errors from the SDK (no model chosen, the daily cap, the job ended) are not retried.
    """
    attempts, convo = 0, list(messages)
    problem = ""
    while attempts < 2:
        attempts += 1
        out = await sdk.llm.invoke(storable(convo), max_tokens=max_tokens, temperature=temperature)
        content = out.get("content") if isinstance(out, dict) else ""
        try:
            value, errors = check(extract(content))
            if errors:
                raise LlmJsonError("; ".join(errors[:5]))
            return {"value": value, "raw": content, "model": as_text(out.get("model_used")), "attempts": attempts}
        except LlmJsonError as e:
            problem = str(e)
            convo = list(messages) + [
                {"role": "assistant", "content": as_text(content)[:ECHO_MAX_CHARS] or "(empty)"},
                {"role": "user", "content": f"Your answer could not be used: {problem}. Answer again with one JSON "
                                            "object only, in exactly the shape asked, and nothing else."}]
    raise PluginError("llm_bad_output", "The model did not answer with the JSON Skiller asked for, twice in a row "
                                        f"({problem}). Try again, or choose another model for Skiller in Models by feature.")
