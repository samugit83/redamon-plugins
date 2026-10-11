"""Applying the model's anchored edits in code (TD-27 step 5).

Operations: `insert_after` / `insert_before` an anchor, `replace` an anchor, `append` to the
part. An anchor must occur exactly once, in the text as already edited by the edits before it.
Limits, each refusal collected with its reason for the repair round:
- one edit adds at most 4,000 characters;
- a part gains at most max(1,500, 25 % of the part) characters over all its edits;
- a replacement removes at most 600 characters;
- a part stays within its platform limit, and the whole skill within `skillMaxChars`, so
  successive versions can never grow past the platform's bound (RV-05).
"""

from __future__ import annotations

from dataclasses import dataclass, field

EDIT_MAX_CHARS = 4_000
GROWTH_FLOOR = 1_500
GROWTH_SHARE = 0.25
REPLACE_REMOVE_MAX = 600


def growth_cap(text: str) -> int:
    return max(GROWTH_FLOOR, int(len(text) * GROWTH_SHARE))


@dataclass
class Applied:
    index: int
    edit: dict
    added: int
    removed: int
    inserted: str
    removed_text: str = ""


@dataclass
class Refusal:
    index: int
    edit: dict
    reason: str

    def problem(self) -> str:
        return f"{self.edit.get('part')} edit {self.index}: {self.reason}"


@dataclass
class Result:
    parts: dict
    applied: list = field(default_factory=list)
    refused: list = field(default_factory=list)


def _insert_after(text: str, at: int, anchor: str, new: str) -> tuple:
    end = at + len(anchor)
    if end == len(text) or text[end] == "\n":
        block = "\n" + new.strip("\n")
    else:
        block = " " + new.strip()
    return text[:end] + block + text[end:], block


def _insert_before(text: str, at: int, new: str) -> tuple:
    if at == 0 or text[at - 1] == "\n":
        block = new.strip("\n") + "\n"
    else:
        block = new.strip() + " "
    return text[:at] + block + text[at:], block


def _append(text: str, new: str) -> tuple:
    body = text.rstrip("\n")
    block = new.strip("\n")
    trailing = "\n" if text.endswith("\n") else ""
    return body + "\n" + block + trailing, "\n" + block


def apply(current: dict, edits: list, *, limits: dict) -> Result:
    parts = dict(current)
    part_limits = (limits or {}).get("parts") or {}
    skill_max = (limits or {}).get("skillMaxChars")
    grown = {pid: 0 for pid in parts}
    result = Result(parts=parts)
    for index, edit in enumerate(edits, 1):
        def refuse(reason: str) -> None:
            result.refused.append(Refusal(index, edit, reason))

        pid, op, anchor, new = edit.get("part"), edit.get("op"), edit.get("anchor"), edit.get("text") or ""
        if pid not in parts:
            refuse(f"unknown part {pid}; the parts are {', '.join(sorted(parts))}")
            continue
        if not new.strip():
            refuse("the text is empty")
            continue
        if len(new) > EDIT_MAX_CHARS:
            refuse(f"the text is {len(new)} characters; one edit adds at most {EDIT_MAX_CHARS}")
            continue
        text = parts[pid]
        at = -1
        if op != "append":
            count = text.count(anchor) if anchor else 0
            if count == 0:
                refuse(f"the anchor is not in part {pid}; copy it exactly from the part")
                continue
            if count > 1:
                refuse(f"the anchor appears {count} times in part {pid}; choose a unique one")
                continue
            at = text.index(anchor)
        removed_text = ""
        if op == "insert_after":
            updated, block = _insert_after(text, at, anchor, new)
        elif op == "insert_before":
            updated, block = _insert_before(text, at, new)
        elif op == "replace":
            if len(anchor) > REPLACE_REMOVE_MAX:
                refuse(f"the replacement removes {len(anchor)} characters; at most {REPLACE_REMOVE_MAX}")
                continue
            updated, block, removed_text = text[:at] + new + text[at + len(anchor):], new, anchor
        else:
            updated, block = _append(text, new)
        added = len(block.strip())
        cap = growth_cap(current[pid])
        if grown[pid] + added > cap:
            refuse(f"the edits add more than {cap} characters to part {pid}")
            continue
        limit = part_limits.get(pid)
        if isinstance(limit, int) and len(updated) > limit:
            refuse(f"part {pid} would be {len(updated)} characters; the limit for part {pid} is {limit}")
            continue
        total = sum(len(t) for p, t in parts.items() if p != pid) + len(updated)
        if isinstance(skill_max, int) and total > skill_max:
            refuse(f"the whole skill would be {total} characters; its limit is {skill_max}")
            continue
        parts[pid] = updated
        grown[pid] += added
        result.applied.append(Applied(index, edit, added, len(removed_text), block.strip(), removed_text))
    return result
