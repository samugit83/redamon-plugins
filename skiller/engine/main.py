"""Skiller's action: from one finished chat session to a proposed, session-agnostic version of the
built-in skill the agent used (README.md, "How it works").

Every refusal that needs no model comes before the first model call (running or unverified
session, unreadable or too short, no built-in skill, five proposals already waiting), so a
proposal that could not be stored never costs a model call (RV-40). Nothing is written to
plugin data: the transcript, the digest and the model's evidence live only in this call (R-13).
"""

from __future__ import annotations

from redamon_plugin_sdk import PluginError

from . import digest, edits, guard, identifiers, llm_json, prompts, proposal, skill_pick, templates
from .transcript_view import TranscriptView, ensure_finished, storable

SKILLER_VERSION = "1.0.0"
POINT = "skill.text"
MAX_PENDING = 5
A_MAX_TOKENS = 4_000
B_MAX_TOKENS = 8_000
TEMPERATURE = 0.2
DIGEST_MIN_CHARS = 20_000
EDIT_PROMPT_ADDED_CHARS = edits.GROWTH_FLOOR
RISK_RULES = ("risk_send", "risk_location", "risk_scope", "heading", "safety_removed")


class _Skill:
    """The skill as `override.get` answered: factory parts, the version that applies, the limits."""

    def __init__(self, cur: dict, fallback_name: str):
        self.name = cur.get("displayName") or fallback_name
        self.order, self.factory, self.kinds, self.placeholders = [], {}, {}, {}
        for part in cur.get("parts") or []:
            pid, text = part.get("partId"), part.get("text")
            if not isinstance(pid, str) or not isinstance(text, str):
                continue
            self.order.append(pid)
            self.factory[pid] = text
            if part.get("kind") in ("template", "plain"):
                self.kinds[pid] = part["kind"]
                self.placeholders[pid] = list(part.get("placeholders") or [])
            else:
                # A platform older than e9829245 does not say; infer it from the text.
                self.kinds[pid], self.placeholders[pid] = templates.infer_kind(text)
        self.active = cur.get("active") if isinstance(cur.get("active"), dict) else None
        active_parts = (self.active or {}).get("parts") or {}
        self.current = {pid: active_parts.get(pid, self.factory[pid]) for pid in self.order}
        self.from_revision = (self.active or {}).get("revision") or 1
        self.limits = cur.get("limits") if isinstance(cur.get("limits"), dict) else {}
        self.pending = cur.get("pendingCount") or 0

    def views(self) -> list:
        return [{"partId": pid, "kind": self.kinds[pid], "placeholders": self.placeholders[pid],
                 "text": self.current[pid]} for pid in self.order]

    def known_text(self) -> str:
        return "\n".join(list(self.factory.values()) + list(self.current.values()))


def _session_tools(view) -> list:
    names = [c.tool for t in view.turns for c in t.calls]
    for team in view.fireteams:
        names += [c.tool for m in team.members.values() for c in m.calls]
    return names


async def _read_skill(sdk, skill_id: str) -> _Skill:
    try:
        cur = await sdk.override.get(POINT, skill_id)
    except PluginError as e:
        if e.code == "invalid_target":
            raise PluginError("no_builtin_skill", f"RedAmon does not offer {skill_pick.display_name(skill_id)} "
                                                  f"({skill_id}) as an editable built-in skill.") from None
        raise
    return _Skill(cur, skill_pick.display_name(skill_id))


def _evaluate(skill: _Skill, edit_list: list, *, ids, tool_set: set) -> tuple:
    """(apply result, {edit number: [problems]}, risk refusals) for one set of edits."""
    result = edits.apply(skill.current, edit_list, limits=skill.limits)
    problems: dict = {}
    for refusal in result.refused:
        problems.setdefault(refusal.index, []).append(refusal.problem())
    risky = 0
    known = skill.known_text()
    for applied in result.applied:
        pid = applied.edit["part"]
        found = guard.check_added(applied.inserted, ids=ids, known_text=known, tool_names=tool_set)
        if applied.removed_text:
            found += guard.check_removed(applied.removed_text, applied.inserted)
        risky += sum(1 for v in found if v.rule in RISK_RULES)
        # Template rules judged on this edit alone, so the problem is pinned on the edit that made it.
        alone = edits.apply(skill.current, [applied.edit], limits=None).parts[pid]
        template_problems = templates.check(skill.kinds[pid], skill.factory[pid], skill.placeholders[pid], alone)
        for message in [v.problem() for v in found] + template_problems:
            problems.setdefault(applied.index, []).append(f"{pid} edit {applied.index}: {message}")
    return result, problems, risky


def _settle(skill: _Skill, edit_list: list, *, ids, tool_set: set) -> tuple:
    """Drops the edits that still fail, until none does: (result, kept edits, dropped, risky)."""
    kept, dropped, risky = list(edit_list), 0, 0
    while True:
        result, problems, found = _evaluate(skill, kept, ids=ids, tool_set=tool_set)
        risky += found
        if not problems:
            return result, kept, dropped, risky
        kept = [e for i, e in enumerate(kept, 1) if i not in problems]
        dropped += len(problems)


def _no_improvement(skill_id: str, message: str) -> dict:
    return {"status": "no_improvement", "skillId": skill_id, "message": message}


async def analyze_session(ctx, params):
    sdk = ctx.sdk
    params = params if isinstance(params, dict) else {}
    requested = params.get("skillId") if isinstance(params.get("skillId"), str) else None

    # 1-2. The session, and the skill it used.
    transcript = await sdk.session.read()
    meta = transcript.get("meta") if isinstance(transcript, dict) and isinstance(transcript.get("meta"), dict) else {}
    ensure_finished(meta)
    view = TranscriptView.from_transcript(transcript)
    pick = skill_pick.choose(view, requested)
    skill_id = pick["skillId"]

    # 3. The version that applies for this project, and the pending cap before any model call.
    skill = await _read_skill(sdk, skill_id)
    name = skill.name
    if skill.pending >= MAX_PENDING:
        raise PluginError("too_many_pending", f"There are already {MAX_PENDING} proposals for {name} waiting for a "
                                              "decision. Approve, reject or delete one first.")
    pick = dict(pick, name=name)
    predates = skill_pick.predates_active(pick, skill.active)
    shown = {"Skill": f"{name} ({skill_id})", "Current version": f"v{skill.from_revision}"}

    # 4. Tell the person which skill was chosen (F-11).
    await sdk.jobs.progress("Reading the session", {**shown, "Stage": "1 of 3"})

    # 5-6. The digest and the session's identifiers.
    parts_chars = sum(len(t) for t in skill.current.values())
    budget = max(DIGEST_MIN_CHARS, min(digest.DIGEST_MAX_CHARS,
                                       prompts.MAX_PROMPT_CHARS - len(prompts.SYSTEM_A) - parts_chars - 10_000))
    d = digest.build(view, pick, budget=budget)
    ids = identifiers.extract(view, exclude_texts=list(skill.factory.values()) + list(skill.current.values()))

    # 7. Stage A: lessons.
    version_label = f"v{skill.from_revision}"
    a = await llm_json.ask(sdk, prompts.messages_a(skill_id=skill_id, name=name, version_label=version_label,
                                                   parts=skill.views(), summary=d.summary, digest_text=d.text),
                           llm_json.check_diagnosis, max_tokens=A_MAX_TOKENS, temperature=TEMPERATURE)
    diagnosis = a["value"]
    model_used = a["model"]
    if diagnosis["skill_fit"] == "poor":
        return _no_improvement(skill_id, f"This session did not fit the {name} skill, so it is not a good basis "
                                         "for improving it.")
    lessons = [lesson for lesson in diagnosis["lessons"] if guard.lesson_ok(lesson["lesson"], ids)]
    dropped_lessons = len(diagnosis["lessons"]) - len(lessons)
    if not lessons:
        if dropped_lessons:
            return _no_improvement(skill_id, f"The lessons from this session were specific to its target, so none "
                                             f"could improve the {name} skill.")
        reason = guard.redact(diagnosis["no_change_reason"], ids) or "no general lesson came out of this session"
        return _no_improvement(skill_id, f"Skiller has no improvement for {name}: {reason}")

    # 8-9. Stage B: anchored edits.
    await sdk.jobs.progress("Writing the new version", {**shown, "Stage": "2 of 3"})
    tool_list = prompts.tool_names(session_tools=_session_tools(view),
                                   factory_texts=list(skill.factory.values()) + list(skill.current.values()))
    tool_set = set(tool_list)
    messages_b = prompts.messages_b(tool_list=tool_list, max_added_chars=EDIT_PROMPT_ADDED_CHARS, skill_id=skill_id,
                                    name=name, version_label=version_label, lessons=lessons, parts=skill.views())
    b = await llm_json.ask(sdk, messages_b, llm_json.check_edits, max_tokens=B_MAX_TOKENS, temperature=TEMPERATURE)
    first_edits = b["value"]["edits"]
    summary, changelog = b["value"]["summary"], b["value"]["changelog"]
    if not first_edits:
        reason = guard.redact(summary, ids) or "the model proposed no change"
        return _no_improvement(skill_id, f"Skiller has no improvement for {name}: {reason}")

    # 10-13. Apply, check, one repair round, then drop what still fails.
    _, problems, risky = _evaluate(skill, first_edits, ids=ids, tool_set=tool_set)
    fixed: list = []
    final_edits = first_edits
    if problems:
        lines = [p for i in sorted(problems) for p in problems[i]]
        repair = messages_b + [{"role": "assistant", "content": b["raw"]},
                               {"role": "user", "content": prompts.repair_message(lines)}]
        fixed = (await llm_json.ask(sdk, repair, llm_json.check_edits, max_tokens=B_MAX_TOKENS,
                                    temperature=TEMPERATURE))["value"]["edits"]
        final_edits = [e for i, e in enumerate(first_edits, 1) if i not in problems] + fixed
    result, kept, dropped_edits, more_risky = _settle(skill, final_edits, ids=ids, tool_set=tool_set)
    repaired = sum(1 for e in kept if any(e is f for f in fixed))
    edited = {pid: text for pid, text in result.parts.items() if text != skill.current[pid]}
    if not edited or proposal.unchanged(skill.current, edited):
        return _no_improvement(skill_id, f"Skiller has no improvement for {name}: none of the proposed edits passed "
                                         "its checks.")

    # 14-15. The snapshot, saved as a proposal.
    await sdk.jobs.progress("Saving the proposal", {**shown, "Stage": "3 of 3"})
    guard_counts = {"droppedLessons": dropped_lessons, "droppedEdits": dropped_edits, "repaired": repaired,
                    "riskRefusals": risky + more_risky}
    summary = guard.redact(summary, ids)
    changelog = [guard.redact(c, ids) for c in changelog]

    async def propose(current_skill: _Skill, parts: dict) -> dict:
        to_label = f"v{current_skill.from_revision + 1}"
        note = proposal.note(summary=summary, changelog=changelog, name=name, from_label=f"v{current_skill.from_revision}",
                             to_label=to_label, lessons=len(lessons), dropped=dropped_lessons + dropped_edits)
        meta_out = proposal.meta(summary=summary, changelog=changelog, lessons=lessons, guard=guard_counts,
                                 source_session_id=meta.get("id"), high_water_seq=meta.get("highWaterSeq"),
                                 session_revision=pick.get("revision"),
                                 predates_active=skill_pick.predates_active(pick, current_skill.active),
                                 model_used=model_used, skiller_version=SKILLER_VERSION)
        return await sdk.override.propose(POINT, skill_id, storable(parts), note=storable(note),
                                          meta=storable(meta_out), scope="user")

    parts = proposal.snapshot(skill.factory, skill.current, edited)
    try:
        out = await propose(skill, parts)
    except PluginError as e:
        if e.code != "factory_changed":
            raise
        # A RedAmon update changed the factory text meanwhile: redo the same edits on the new
        # text once (no new model call); the old wording must not ride past the update.
        skill = await _read_skill(sdk, skill_id)
        result, _kept, _dropped, _ = _settle(skill, kept, ids=ids, tool_set=tool_set)
        edited = {pid: text for pid, text in result.parts.items() if text != skill.current[pid]}
        if not edited:
            raise PluginError("factory_changed", f"RedAmon was updated while Skiller worked and its edits no longer "
                                                 f"fit {name}. Run Skiller again.") from None
        out = await propose(skill, proposal.snapshot(skill.factory, skill.current, edited))

    label = out.get("proposedRevisionLabel") or f"v{skill.from_revision + 1}"
    return {"status": "proposed", "skillId": skill_id,
            "reviewProposal": {"extensionPoint": POINT, "changeSetId": out["changeSetId"]},
            "message": f"Skiller proposes {label} of {name}."}
