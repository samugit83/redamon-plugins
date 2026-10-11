"""The proposal Skiller hands to the platform (TD-19): a complete version, a note, a small meta.

A version is a snapshot: every part whose new text differs from the factory text, so the
current version's own changes are carried forward and the version never depends on another
(R-44). Parts equal to the factory text are left out: the platform treats a part not sent as
factory text. The note (at most 4,000 characters on the platform) and the meta (at most 64 KB)
hold only text already cleared by the guard.
"""

from __future__ import annotations

import json

NOTE_MAX_CHARS = 4_000
META_MAX_BYTES = 60_000
LESSON_MAX_CHARS = 600
SUMMARY_MAX_CHARS = 1_200
CHANGE_MAX_CHARS = 300
MAX_CHANGES = 20


def snapshot(factory: dict, current: dict, edited: dict) -> dict:
    out = {}
    for pid, factory_text in factory.items():
        text = edited.get(pid, current.get(pid, factory_text))
        if text != factory_text:
            out[pid] = text
    return out


def unchanged(current: dict, edited: dict) -> bool:
    return all(current.get(pid) == text for pid, text in edited.items())


def _cut(text, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def note(*, summary: str, changelog, name: str, from_label: str, lessons: int, dropped: int) -> str:
    """Plain text: the versions list shows notes unrendered. No version number of its own: the
    platform numbers a version with a counter that is never reused (TD-19), unknown here."""
    lines = [f"Skiller's proposal for {name}, built on {from_label}.", "", _cut(summary, SUMMARY_MAX_CHARS), ""]
    if changelog:
        lines.append("Changes:")
        lines += [f"- {_cut(c, CHANGE_MAX_CHARS)}" for c in list(changelog)[:MAX_CHANGES]]
        lines.append("")
    tail = f"From {lessons} general lesson(s) of one session"
    tail += f"; {dropped} lesson(s) or edit(s) were dropped by Skiller's checks." if dropped else "."
    lines.append(tail)
    text = "\n".join(lines)
    if len(text) > NOTE_MAX_CHARS:
        text = text[: NOTE_MAX_CHARS - 2] + "\n…"
    return text


def meta(*, summary: str, changelog, lessons, guard: dict, source_session_id, high_water_seq, session_revision,
         predates_active: bool, model_used: str, skiller_version: str) -> dict:
    out = {
        "summary": _cut(summary, SUMMARY_MAX_CHARS),
        "changelog": [_cut(c, CHANGE_MAX_CHARS) for c in list(changelog)[:MAX_CHANGES]],
        # Generalized lessons only: their evidence cites the session and never leaves Skiller.
        "lessons": [{"id": str(lesson.get("id")), "category": lesson.get("category"), "impact": lesson.get("impact"),
                     "lesson": _cut(lesson.get("lesson"), LESSON_MAX_CHARS),
                     "targetParts": list(lesson.get("target_parts") or [])}
                    for lesson in lessons],
        "guard": dict(guard),
        "sourceSessionId": source_session_id,
        "sourceHighWaterSeq": high_water_seq,
        "sessionRevision": session_revision,
        "sessionPredatesActive": bool(predates_active),
        "modelUsed": model_used,
        "skillerVersion": skiller_version,
    }
    while len(json.dumps(out).encode("utf-8")) > META_MAX_BYTES and (out["lessons"] or out["changelog"]):
        (out["lessons"] or out["changelog"]).pop()
    return out
