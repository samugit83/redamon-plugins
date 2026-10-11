"""The digest of a session that the diagnosis stage reads (TD-27 step 2).

Four parts, all inside one untrusted fence:
- facts: phases and turns, the skill timeline, duration, how the session ended, the objective,
  and how many fields could not be read;
- signals, computed here and not by a model: failed calls grouped by tool and error, identical
  calls repeated, failure streaks, productivity tiers and stalls, deep-think triggers, every
  human intervention, the turn of the first finding, informational turns before exploitation, a
  tool histogram; over the chosen skill's turns and the fireteams deployed in them (labelled by
  member);
- the timeline of the turns under the chosen skill, one line each;
- the parallel specialists (fireteams) and the exploit-path search (LATS start and outcome only).

The text comes from the target and its tools, so the whole digest is fenced with a fresh random
nonce and any marker-looking text inside is neutralised, as agentic/prompt_safety.py does. A
`summary` with counts and turn numbers only (no text from the session) is safe outside the fence.

Over budget: unflagged turns shrink to one line, then the middle unflagged turns are dropped (and
counted), then the field limits of every turn are halved, and as a last resort the timeline is cut.
"""

from __future__ import annotations

import json
import re
import secrets
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import skill_pick
from .transcript_view import as_text

DIGEST_MAX_CHARS = 120_000
FLAG_TIERS = ("orange", "red", "critical")
STALL_FLAG = 3
STREAK_MIN = 3
LIMITS = {"thought": 400, "args": 300, "summary": 600, "findings": 200, "deep": 1_000, "human": 500, "error": 300}

_MARKER_PREFIX_RE = re.compile(r"<<<\s*(END_)?UNTRUSTED_", re.IGNORECASE)
_ZWSP = "​"
_TOOL_NAME_RE = re.compile(r"[^A-Za-z0-9_.:-]")
_SPACES = re.compile(r"\s+")
_DIGITS = re.compile(r"\d+")


def neutralize(text: str) -> str:
    """Breaks any `<<<UNTRUSTED_` / `<<<END_UNTRUSTED_` look-alike (prompt_safety._neutralize_markers)."""
    return _MARKER_PREFIX_RE.sub(lambda m: f"<{_ZWSP}<{_ZWSP}<" + (m.group(1) or "") + "UNTRUSTED_", text)


def fence(body: str, nonce: str, label: str = "SESSION") -> str:
    return (f"<<<UNTRUSTED_{label} id={nonce}>>>\n"
            f"{neutralize(body)}\n"
            f"<<<END_UNTRUSTED_{label} id={nonce}>>>")


def cut(text, limit: int) -> str:
    text = _SPACES.sub(" ", as_text(text)).strip()
    return text if len(text) <= limit else text[:limit] + "…"


def tool_name(name) -> str:
    """A tool name as shown outside the fence: only name-like characters."""
    return _TOOL_NAME_RE.sub("", as_text(name))[:64] or "unknown"


def normalized_args(args) -> str:
    if isinstance(args, (dict, list)):
        try:
            text = json.dumps(args, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = as_text(args)
    else:
        text = as_text(args)
    return _SPACES.sub(" ", text).strip()


def error_text(call) -> str:
    """What a failed call said, comparable across calls (first line, numbers folded)."""
    for source in (call.raw, call.summary):
        for line in as_text(source).splitlines():
            if line.strip():
                return _DIGITS.sub("#", _SPACES.sub(" ", line).strip())[:160]
    return "(no output)"


@dataclass
class Digest:
    text: str
    nonce: str
    signals: dict
    summary: str
    omitted: int = 0
    warnings: list = field(default_factory=list)


# -- signals ----------------------------------------------------------------------------------

def _call_stream(turns, fireteams):
    """(turn iteration, member id or None, call) in order: the root's calls, then each member's."""
    for t in turns:
        for c in t.calls:
            yield t.iteration, None, c
    for team, turn_iter in fireteams:
        for m in team.members.values():
            for c in m.calls:
                yield turn_iter, m.id, c


def compute_signals(turns, fireteams) -> dict:
    failed = defaultdict(lambda: {"count": 0, "turns": []})
    repeated = defaultdict(lambda: {"count": 0, "turns": []})
    histogram = defaultdict(lambda: {"calls": 0, "failed": 0})
    streaks, run = [], defaultdict(list)

    def close(member):
        r = run[member]
        if len(r) >= STREAK_MIN:
            streaks.append({"length": len(r), "fromTurn": r[0][0], "toTurn": r[-1][0],
                            "tools": sorted({tool for _, tool in r}), "member": member})
        run[member] = []

    for turn_iter, member, call in _call_stream(turns, fireteams):
        name = tool_name(call.tool)
        histogram[name]["calls"] += 1
        key = (name, normalized_args(call.args), member)
        repeated[key]["count"] += 1
        repeated[key]["turns"].append(turn_iter)
        if call.ok is False:
            histogram[name]["failed"] += 1
            f = failed[(name, error_text(call), member)]
            f["count"] += 1
            f["turns"].append(turn_iter)
            run[member].append((turn_iter, name))
        elif call.ok is True:
            close(member)
    for member in list(run):
        close(member)

    productivity = {tier: 0 for tier in FLAG_TIERS}
    productivity.update({"maxStall": 0, "stalledTurns": []})
    deep, humans, errors = [], [], []
    first_finding, informational, seen_exploitation = None, 0, False
    for t in turns:
        if t.tier in FLAG_TIERS:
            productivity[t.tier] += 1
        if t.stall is not None:
            productivity["maxStall"] = max(productivity["maxStall"], t.stall)
            if t.stall >= STALL_FLAG:
                productivity["stalledTurns"].append(t.iteration)
        deep += [{"turn": t.iteration, "trigger": cut(d["trigger"], 200)} for d in t.deep_thinks]
        humans += [{"turn": t.iteration, "kind": h.kind, "text": cut(_human_text(h), 300)} for h in t.humans
                   if h.kind not in ("tool_approved", "phase_approved")]
        errors += [{"turn": t.iteration, "message": cut(e, LIMITS["error"])} for e in t.errors]
        if first_finding is None and any(c.findings for c in t.calls):
            first_finding = t.iteration
        if t.phase == "exploitation":
            seen_exploitation = True
        elif t.phase == "informational" and not seen_exploitation:
            informational += 1
    return {
        "failedCalls": sorted(({"tool": k[0], "error": k[1], "member": k[2], **v} for k, v in failed.items()),
                              key=lambda x: (-x["count"], x["turns"][0])),
        "repeatedCalls": sorted(({"tool": k[0], "args": cut(k[1], 200), "member": k[2], **v}
                                 for k, v in repeated.items() if v["count"] >= 2),
                                key=lambda x: (-x["count"], x["turns"][0])),
        "failureStreaks": streaks,
        "productivity": productivity,
        "deepThinks": deep,
        "humanInterventions": humans,
        "firstFindingTurn": first_finding,
        "informationalTurnsBeforeExploitation": informational if seen_exploitation else None,
        "toolHistogram": dict(sorted(histogram.items(), key=lambda kv: (-kv[1]["calls"], kv[0]))),
        "errors": errors,
    }


def _turns_list(turns) -> str:
    unique = list(dict.fromkeys(turns))
    return ", ".join(f"#{t}" for t in unique[:12]) + (", …" if len(unique) > 12 else "")


def summarize(signals: dict) -> str:
    """The signals as counts and turn numbers: nothing the target or a tool wrote."""
    who = lambda m: f" (member {tool_name(m)})" if m else ""  # noqa: E731
    lines = ["COMPUTED SIGNALS (counts and turn numbers; the details are in the session digest)"]
    for f in signals["failedCalls"][:10]:
        lines.append(f"- failed calls: {f['tool']} x{f['count']}{who(f['member'])} ({_turns_list(f['turns'])})")
    for r in signals["repeatedCalls"][:10]:
        lines.append(f"- repeated calls: {r['tool']} x{r['count']}{who(r['member'])} ({_turns_list(r['turns'])})")
    for s in signals["failureStreaks"]:
        lines.append(f"- failure streak: {s['length']} in a row{who(s['member'])} (#{s['fromTurn']} to #{s['toTurn']})")
    p = signals["productivity"]
    lines.append(f"- productivity warnings: orange {p['orange']}, red {p['red']}, critical {p['critical']}; "
                 f"longest stall {p['maxStall']} turns")
    if signals["deepThinks"]:
        lines.append(f"- deep-think steps: {_turns_list([d['turn'] for d in signals['deepThinks']])}")
    kinds = Counter(h["kind"] for h in signals["humanInterventions"])
    if kinds:
        lines.append("- human interventions: " + ", ".join(f"{k} x{n}" for k, n in sorted(kinds.items())))
    lines.append(f"- first actionable finding: {('#' + str(signals['firstFindingTurn'])) if signals['firstFindingTurn'] else 'none'}")
    if signals["informationalTurnsBeforeExploitation"] is not None:
        lines.append(f"- informational turns before exploitation: {signals['informationalTurnsBeforeExploitation']}")
    lines.append("- tools: " + ", ".join(f"{t} {v['calls']} ({v['failed']} failed)"
                                        for t, v in list(signals["toolHistogram"].items())[:15]))
    return "\n".join(lines)


# -- text sections ----------------------------------------------------------------------------

def _facts(view, pick: dict, skill_turns: list, warnings: list) -> list:
    phases = Counter(t.phase for t in skill_turns)
    lines = ["SESSION FACTS",
             f"- skill: {pick.get('name') or skill_pick.display_name(pick['skillId'])} ({pick['skillId']}); turns "
             f"counted from {'the record of the playbook in the prompt' if pick.get('source') == 'workflow' else 'the skill label'}",
             f"- turns under this skill: {len(skill_turns)} of {len(view.turns)}"
             + (f" (#{skill_turns[0].iteration} to #{skill_turns[-1].iteration})" if skill_turns else ""),
             "- by phase: " + (", ".join(f"{p} {n}" for p, n in sorted(phases.items())) or "none")]
    if pick.get("others"):
        lines.append("- other skills in the session: " + ", ".join(
            f"{o['skillId']} ({o['iterations']} turns)" for o in pick["others"]))
    if pick.get("revision") is not None:
        lines.append(f"- playbook version the session ran: v{pick['revision']}")
    if view.first_at and view.last_at:
        lines.append(f"- duration: {view.first_at} to {view.last_at}")
    ending = view.ending
    if ending["kind"] == "report":
        lines.append(f"- ended: final report ({ending['tier'] or 'tier unknown'})")
    elif ending["kind"] == "error":
        lines.append(f"- ended: error: {cut(ending['text'], LIMITS['error'])}")
    else:
        lines.append("- ended: stopped before a final report")
    if view.objective:
        lines.append(f"- objective: {cut(view.objective, LIMITS['human'])}")
    if view.dropped_events:
        lines.append(f"- {view.dropped_events} events were not handed over (repeated phase updates always are; "
                     "a very long session also loses its middle)")
    if view.warnings:
        total = sum(view.warnings.values())
        detail = ", ".join(f"{k}: {n}" for k, n in sorted(view.warnings.items())[:12])
        lines.append(f"- reading warnings: {total} fields or events could not be read ({detail})")
    lines += [f"- warning: {w}" for w in warnings]
    return lines


def _signal_lines(signals: dict) -> list:
    who = lambda m: f" [member {m}]" if m else ""  # noqa: E731
    lines = ["SIGNALS (computed from the events)"]
    for f in signals["failedCalls"]:
        lines.append(f"- failed: {f['tool']} x{f['count']}{who(f['member'])} ({_turns_list(f['turns'])}): {f['error']}")
    for r in signals["repeatedCalls"]:
        lines.append(f"- repeated: {r['tool']} x{r['count']}{who(r['member'])} same arguments "
                     f"({_turns_list(r['turns'])}): {r['args']}")
    for s in signals["failureStreaks"]:
        lines.append(f"- failure streak: {s['length']} in a row{who(s['member'])} (#{s['fromTurn']} to #{s['toTurn']}, "
                     f"{', '.join(s['tools'])})")
    p = signals["productivity"]
    lines.append(f"- productivity: orange {p['orange']}, red {p['red']}, critical {p['critical']}; longest stall "
                 f"{p['maxStall']} turns" + (f" (stalled: {_turns_list(p['stalledTurns'])})" if p["stalledTurns"] else ""))
    for d in signals["deepThinks"]:
        lines.append(f"- deep think at #{d['turn']}: {d['trigger']}")
    for h in signals["humanInterventions"]:
        lines.append(f"- person at #{h['turn']} ({h['kind']}): {h['text']}")
    for e in signals["errors"]:
        lines.append(f"- error at #{e['turn']}: {e['message']}")
    lines.append(f"- first actionable finding: {('#' + str(signals['firstFindingTurn'])) if signals['firstFindingTurn'] else 'none'}")
    if signals["informationalTurnsBeforeExploitation"] is not None:
        lines.append(f"- informational turns before exploitation: {signals['informationalTurnsBeforeExploitation']}")
    lines.append("- tools: " + ", ".join(f"{t} {v['calls']} ({v['failed']} failed)" for t, v in signals["toolHistogram"].items()))
    return lines


_HUMAN_LABELS = {
    "message": "message", "guidance": "guidance", "answer": "answer",
    "tool_rejected": "rejected tool call", "tool_modified": "modified tool call",
    "phase_rejected": "rejected phase change", "phase_modified": "modified phase change",
}


def _human_text(h) -> str:
    return f"{h.subject}: {h.text}" if h.subject and h.text else (h.subject or h.text)


def _call_text(c, scale: float) -> str:
    lim = lambda k: max(20, int(LIMITS[k] * scale))  # noqa: E731
    status = "ok" if c.ok else ("failed" if c.ok is False else "unknown")
    ms = f" ({c.ms} ms)" if c.ms is not None else ""
    out = f"action: {tool_name(c.tool)} {cut(normalized_args(c.args), lim('args'))} → {status}{ms}"
    if c.summary:
        out += f" | summary: {cut(c.summary, lim('summary'))}"
    elif c.raw:
        out += f" | output: {cut(c.raw, lim('summary'))}"
    if c.findings:
        out += f" | findings: {cut('; '.join(c.findings), lim('findings'))}"
    return out


def _turn_lines(t, scale: float, one_line: bool) -> list:
    if one_line:
        calls = ", ".join(f"{tool_name(c.tool)} → {'ok' if c.ok else 'failed' if c.ok is False else '?'}" for c in t.calls)
        return [f"#{t.iteration} [{t.phase}] " + (f"action: {calls}" if calls else f"({t.action or 'no tool'})")]
    lim = lambda k: max(20, int(LIMITS[k] * scale))  # noqa: E731
    head = f"#{t.iteration} [{t.phase}] thought: {cut(t.thought, lim('thought'))}"
    parts = [head] + [_call_text(c, scale) for c in t.calls]
    if not t.calls and t.action:
        parts.append(f"decision: {cut(t.action, 40)}")
    lines = [" | ".join(parts)]
    if t.tier in FLAG_TIERS or (t.stall or 0) >= STALL_FLAG:
        lines.append(f"   productivity: {t.tier or 'unknown'}, stall {t.stall if t.stall is not None else '?'}")
    for d in t.deep_thinks:
        lines.append(f"   deep think ({cut(d['trigger'], 200)}): {cut(d['analysis'], lim('deep'))}")
    for h in t.humans:
        if h.kind in ("tool_approved", "phase_approved"):
            continue
        label = _HUMAN_LABELS.get(h.kind, h.kind) + (f" {cut(h.subject, 80)}" if h.subject else "")
        lines.append(f"   person ({label}): {cut(h.text, lim('human')) or '(no comment)'}")
    for e in t.errors:
        lines.append(f"   error: {cut(e, lim('error'))}")
    return lines


def _flagged(t, signals: dict) -> bool:
    if t.deep_thinks or t.errors or t.tier in FLAG_TIERS or (t.stall or 0) >= STALL_FLAG:
        return True
    if any(h.kind not in ("tool_approved", "phase_approved") for h in t.humans):
        return True
    if any(c.ok is False or c.findings for c in t.calls):
        return True
    repeated = {n for r in signals["repeatedCalls"] if r["member"] is None for n in r["turns"]}
    return t.iteration in repeated


def _timeline(turns, signals: dict, budget: int) -> tuple:
    """(lines, omitted count, whether it had to cut text) within `budget` characters."""
    flags = {t.n: _flagged(t, signals) for t in turns}

    def render(scale, shrink_unflagged, keep):
        lines, omitted, gap = [], 0, False
        for t in turns:
            if t.n not in keep:
                omitted += 1
                if not gap:
                    lines.append("__GAP__")
                    gap = True
                continue
            gap = False
            lines += _turn_lines(t, scale, shrink_unflagged and not flags[t.n])
        out = []
        for ln in lines:
            out.append(f"… {omitted} unflagged turns left out here …" if ln == "__GAP__" else ln)
        return out, omitted

    def size(lines):
        return sum(len(ln) + 1 for ln in lines)

    everyone = {t.n for t in turns}
    for scale, shrink in ((1.0, False), (1.0, True)):
        lines, omitted = render(scale, shrink, everyone)
        if size(lines) <= budget:
            return lines, omitted, False
    # Drop unflagged turns from the middle outwards, keeping the first and last ones longest.
    unflagged = [t.n for t in turns if not flags[t.n]]
    order = sorted(unflagged, key=lambda n: abs(n - (unflagged[len(unflagged) // 2] if unflagged else 0)))
    keep = set(everyone)
    for n in order:
        keep.discard(n)
        lines, omitted = render(1.0, True, keep)
        if size(lines) <= budget:
            return lines, omitted, False
    for scale in (0.5, 0.25):
        lines, omitted = render(scale, True, keep)
        if size(lines) <= budget:
            return lines, omitted, True
    text = "\n".join(lines)[: max(0, budget - 40)]
    return text.splitlines() + ["… the timeline was cut to fit …"], omitted, True


def _fireteam_lines(teams) -> list:
    if not teams:
        return []
    lines = ["PARALLEL SPECIALISTS (fireteam members run the same playbook with fewer tools)"]
    for team, turn_iter in teams:
        lines.append(f"fireteam {cut(team.id, 40)} deployed at #{turn_iter}: {cut(team.rationale, 400)}")
        for m in team.members.values():
            lines.append(f' member {cut(m.id, 20)} "{cut(m.name, 60)}" task: {cut(m.task, 300)} '
                         f"tools: {', '.join(tool_name(t) for t in m.tools)}")
            for it, text in m.thoughts:
                lines.append(f"   thought #{it if it is not None else '?'}: {cut(text, 300)}")
            for c in m.calls:
                status = "ok" if c.ok else ("failed" if c.ok is False else "unknown")
                ms = f" ({c.ms} ms)" if c.ms is not None else ""
                lines.append(f"   call {tool_name(c.tool)} {cut(normalized_args(c.args), 200)} → {status}{ms}: "
                             f"{cut(c.summary or c.raw, 300)}")
            if m.status:
                tail = f"; {cut(m.error, 200)}" if m.error else ""
                lines.append(f"   finished: {cut(m.status, 30)}, {m.iterations_used} iterations, "
                             f"{m.findings_count} findings{tail}")
    return lines


def _search_lines(searches) -> list:
    if not searches:
        return []
    lines = ["EXPLOIT-PATH SEARCH (guided by the same playbook)"]
    for s, turn_iter in searches:
        lines.append(f"search at #{turn_iter}: objective: {cut(s.objective, 300)}; phase {cut(s.phase, 30)}; "
                     f"budget {cut(s.budget, 120)}")
        lines.append(f" outcome: {cut(s.outcome or 'not finished', 60)}; metrics {cut(s.metrics, 200)}")
        for i, step in enumerate(s.trajectory[:12], 1):
            lines.append(f"  {i}) {cut(step, 200)}")
    return lines


def build(view, pick: dict, *, budget: int = DIGEST_MAX_CHARS, nonce: str | None = None) -> Digest:
    nonce = nonce or secrets.token_hex(8)
    numbers = set(pick.get("turns") or [])
    skill_turns = [t for t in view.turns if t.n in numbers] if numbers else [
        t for t in view.turns if skill_pick.turn_skill(t)[0] == pick.get("skillId")]
    by_n = {t.n: t for t in view.turns}
    teams = [(team, by_n[team.turn].iteration) for team in view.fireteams if team.turn in numbers]
    searches = [(s, by_n[s.turn].iteration) for s in view.searches if s.turn in numbers]

    signals = compute_signals(skill_turns, teams)
    warnings = list(pick.get("warnings") or [])
    head = _facts(view, pick, skill_turns, warnings) + [""] + _signal_lines(signals) + [""]
    tail_lines = _fireteam_lines(teams)
    tail_lines += ([""] if tail_lines else []) + _search_lines(searches)

    wrapper = len(fence("", nonce)) + 2
    title = "TIMELINE (turns under the skill)"

    def assemble(head_text: str, tail_text: str) -> tuple:
        room = budget - wrapper - len(head_text) - len(tail_text) - len(title) - 4
        timeline, omitted, cut_text = _timeline(skill_turns, signals, max(0, room))
        body = "\n".join([head_text, title] + timeline + ([""] if tail_text else []) + [tail_text])
        return body, omitted, cut_text

    head_text, tail_text = "\n".join(head), "\n".join(tail_lines)
    body, omitted, cut_text = assemble(head_text, tail_text)
    if cut_text:
        # Facts and signals, and the specialists and the search, each keep at most a quarter
        # when the timeline cannot fit beside them otherwise.
        quarter = max(500, (budget - wrapper) // 4)
        if len(head_text) > quarter:
            head_text = head_text[: quarter - 30] + "\n… signals cut to fit …"
        if len(tail_text) > quarter:
            tail_text = tail_text[: quarter - 30] + "\n… cut to fit …"
        body, omitted, _ = assemble(head_text, tail_text)
    text = fence(body.rstrip("\n"), nonce)
    if len(text) > budget:
        # Neutralising markers can add a few characters; never exceed the budget.
        text = fence(body[: max(0, len(body) - (len(text) - budget) - 40)] + "\n… cut …", nonce)
    return Digest(text=text, nonce=nonce, signals=signals, summary=summarize(signals), omitted=omitted,
                  warnings=warnings)
