"""A session, as `sdk.session.read()` returns it, turned into the agent's turns.

The events are the agent's persisted chat rows (RedAmon agentic/websocket_api.py writers,
orchestrator_helpers/streaming.py order). On each think step the agent writes `phase_update`,
then the previous step's `tool_complete`, then `thinking`, then the new `tool_start`, so a turn
starts at a `thinking` row and owns the tool calls started after it. The Gateway hands
`phase_update` rows over deduplicated (a row only when something other than the iteration
count changed), so the skill of a turn is that of the latest `phase_update` before it.

Every field is read defensively: payloads changed over time (no `action` or token counts in
thinking before 2026-04-19, no `step_id` before 2026-08-05, no `skill_workflow` before
2026-10-09, ...), and a field that is missing or of the wrong type yields an empty value and a
counted warning, never an exception. Nothing here is stored anywhere.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

from redamon_plugin_sdk import PluginError

# A user query may carry a chat skill's or a graph node's context first (35ca19be, 30815346).
_USER_QUERY_RE = re.compile(r"^\[(?:Chat Skill Context|Graph Node Context[^\]]*)\].*?\n\[User Query\]\n", re.S)

PHASES = ("informational", "exploitation", "post_exploitation")


@dataclass
class Call:
    tool: str
    args: Any
    seq: int
    ok: Optional[bool] = None
    ms: Optional[int] = None
    summary: str = ""
    raw: str = ""
    findings: list = field(default_factory=list)
    wave: Optional[str] = None
    step_index: Optional[int] = None
    step_id: Optional[str] = None
    member: Optional[str] = None
    completed: bool = False


@dataclass
class Human:
    kind: str
    text: str
    seq: int
    # What a decision was about: the tools of a confirmation, the phases of an approval.
    subject: str = ""


@dataclass
class Turn:
    n: int
    iteration: int
    phase: str
    thought: str
    reasoning: str
    action: str
    tier: str
    stall: Optional[int]
    seq: int
    at: str
    label: str
    workflow_known: bool
    workflow: Optional[dict]
    calls: list = field(default_factory=list)
    deep_thinks: list = field(default_factory=list)
    humans: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    fireteams: list = field(default_factory=list)
    searches: list = field(default_factory=list)
    last_at: str = ""


@dataclass
class Member:
    id: str
    name: str
    task: str
    tools: list
    thoughts: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    status: str = ""
    iterations_used: Optional[int] = None
    findings_count: Optional[int] = None
    error: str = ""


@dataclass
class Fireteam:
    id: str
    turn: Optional[int]
    iteration: Optional[int]
    rationale: str
    members: dict = field(default_factory=dict)


@dataclass
class Search:
    id: str
    turn: Optional[int]
    objective: str = ""
    phase: str = ""
    budget: Any = None
    outcome: str = ""
    trajectory: list = field(default_factory=list)
    metrics: Any = None


def ensure_finished(meta: dict) -> None:
    """Refuses a session the agent may still be writing (OQ-16, RV-14).

    `running` comes from the agent's live session list, not the conversation's raw flag,
    which stays true after an agent restart; 'unverified' means the agent could not be asked.
    """
    running = meta.get("running") if isinstance(meta, dict) else None
    if running is True:
        raise PluginError("session_running", "The agent is still working in this session. Try again when it stops.")
    if running == "unverified":
        raise PluginError("session_state_unverified",
                          "RedAmon could not confirm that the agent has stopped in this session. Try again in a moment.")


def as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def as_int(value) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


_UNSTORABLE = re.compile("[\x00\ud800-\udfff]")


def storable(value):
    """`value` without what the Gateway refuses as `invalid_text`: U+0000 and unpaired surrogates."""
    if isinstance(value, str):
        return _UNSTORABLE.sub("", value)
    if isinstance(value, dict):
        return {storable(k): storable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [storable(v) for v in value]
    return value


def user_query(content: str) -> str:
    """The person's own words of a user message, without a skill's or graph node's context."""
    return _USER_QUERY_RE.sub("", content, count=1).strip()


class TranscriptView:
    def __init__(self) -> None:
        self.meta: dict = {}
        self.turns: list = []
        self.prelude_humans: list = []
        self.fireteams: list = []
        self.searches: list = []
        self.warnings: Counter = Counter()
        self.phase_rows = 0
        self.phase_rows_with_label = 0
        self.objective = ""
        self.ending: dict = {"kind": "stopped", "tier": "", "text": ""}
        self.first_at = ""
        self.last_at = ""
        self.dropped_events = 0
        # The request a person's answer refers to (the response rows do not repeat it).
        self._last_confirmation = ("a tool", "")
        self._last_approval = "a phase change"
        self._last_question = ""

    # -- reading helpers -------------------------------------------------------
    def _get(self, data: dict, kind: str, key: str, default=None, *, expected=True):
        if key in data:
            return data[key]
        if expected:
            self.warnings[f"{kind}.{key}"] += 1
        return default

    def _text(self, data, kind, key, expected=True) -> str:
        value = self._get(data, kind, key, "", expected=expected)
        if value is not None and not isinstance(value, str):
            self.warnings[f"{kind}.{key} not text"] += 1
        return as_text(value)

    # -- building ----------------------------------------------------------------
    @classmethod
    def from_transcript(cls, transcript: dict) -> "TranscriptView":
        v = cls()
        if not isinstance(transcript, dict):
            v.warnings["transcript: not an object"] += 1
            return v
        meta = transcript.get("meta")
        v.meta = meta if isinstance(meta, dict) else {}
        truncated = transcript.get("truncated")
        if isinstance(truncated, dict):
            v.dropped_events = as_int(truncated.get("droppedEvents")) or 0
        events = transcript.get("events")
        if not isinstance(events, list):
            v.warnings["transcript.events"] += 1
            events = []
        v._walk(events)
        return v

    def _walk(self, events: list) -> None:
        label, workflow_known, workflow, phase = "", False, None, "informational"
        pending: list = []
        fireteams: dict = {}
        searches: dict = {}
        turn: Optional[Turn] = None

        def humans_target() -> list:
            return turn.humans if turn else self.prelude_humans

        for raw in events:
            if not isinstance(raw, dict):
                self.warnings["event: not an object"] += 1
                continue
            kind = raw.get("type") if isinstance(raw.get("type"), str) else ""
            seq = as_int(raw.get("seq")) or 0
            at = raw.get("at") if isinstance(raw.get("at"), str) else ""
            data = raw.get("data")
            if at:
                self.first_at = self.first_at or at
                self.last_at = at
                if turn:
                    turn.last_at = at
            if not isinstance(data, dict):
                self.warnings[f"{kind}: not an object"] += 1
                continue
            if data.get("truncated") is True and "preview" in data:
                # The Gateway replaces an event above 64 KB with the start of its JSON.
                self.warnings["events cut by RedAmon"] += 1
                continue

            if kind == "phase_update":
                self.phase_rows += 1
                phase = self._text(data, kind, "current_phase") or phase
                if "attack_path_type" in data:
                    self.phase_rows_with_label += 1
                    label = as_text(data["attack_path_type"])
                else:
                    self.warnings["phase_update.attack_path_type"] += 1
                workflow_known = "skill_workflow" in data
                wf = data.get("skill_workflow")
                workflow = wf if isinstance(wf, dict) else None
            elif kind == "thinking":
                iteration = as_int(self._get(data, kind, "iteration"))
                phase_here = self._text(data, kind, "phase") or phase
                turn = Turn(
                    n=len(self.turns) + 1, iteration=iteration if iteration is not None else len(self.turns) + 1,
                    phase=phase_here, thought=self._text(data, kind, "thought"),
                    reasoning=self._text(data, kind, "reasoning", expected=False),
                    action=self._text(data, kind, "action", expected=False),
                    tier=as_text(data.get("productivity_tier")) if isinstance(data.get("productivity_tier"), str) else "",
                    stall=as_int(data.get("stall")), seq=seq, at=at, label=label,
                    workflow_known=workflow_known, workflow=workflow, last_at=at)
                self.turns.append(turn)
            elif kind == "tool_start":
                call = Call(tool=self._text(data, kind, "tool_name") or "unknown", args=data.get("tool_args", {}),
                            seq=seq, wave=data.get("wave_id") if isinstance(data.get("wave_id"), str) else None,
                            step_index=as_int(data.get("step_index")),
                            step_id=data.get("step_id") if isinstance(data.get("step_id"), str) else None)
                if turn is None:
                    self.warnings["tool_start before any turn"] += 1
                else:
                    turn.calls.append(call)
                pending.append(call)
            elif kind == "tool_complete":
                self._complete(data, pending, turn, seq)
            elif kind == "deep_think":
                entry = {"trigger": self._text(data, kind, "trigger_reason"), "analysis": self._text(data, kind, "analysis")}
                if turn:
                    turn.deep_thinks.append(entry)
            elif kind == "user_message":
                text = user_query(self._text(data, kind, "content"))
                self.objective = self.objective or text
                humans_target().append(Human("message", text, seq))
            elif kind == "guidance":
                humans_target().append(Human("guidance", self._text(data, kind, "content"), seq))
            elif kind == "tool_confirmation_request":
                tools = data.get("tools") if isinstance(data.get("tools"), list) else []
                names = [as_text(t.get("tool_name")) for t in tools if isinstance(t, dict)]
                self._last_confirmation = (", ".join(n for n in names if n) or "a tool",
                                           self._text(data, kind, "reasoning", expected=False))
            elif kind == "tool_confirmation_response":
                decision = self._text(data, kind, "decision")
                tools, why = self._last_confirmation
                verb = {"reject": "tool_rejected", "modify": "tool_modified"}.get(decision, "tool_approved")
                humans_target().append(Human(verb, why, seq, subject=tools))
            elif kind == "approval_request":
                self._last_approval = f"{as_text(data.get('from_phase'))} -> {as_text(data.get('to_phase'))}"
            elif kind == "approval_response":
                decision = self._text(data, kind, "decision")
                verb = {"abort": "phase_rejected", "modify": "phase_modified"}.get(decision, "phase_approved")
                text = self._last_approval
                modification = as_text(data.get("modification"))
                humans_target().append(Human(verb, modification, seq, subject=text))
            elif kind == "question_request":
                self._last_question = self._text(data, kind, "question")
            elif kind == "answer_response":
                question = self._last_question
                answer = self._text(data, kind, "answer")
                humans_target().append(Human("answer", f"{question} -> {answer}" if question else answer, seq))
            elif kind == "error":
                message = self._text(data, kind, "message")
                if turn:
                    turn.errors.append(message)
                self.ending = {"kind": "error", "tier": "", "text": message}
            elif kind == "assistant_message":
                self.ending = {"kind": "report", "tier": self._text(data, kind, "response_tier", expected=False),
                               "text": self._text(data, kind, "content")}
            elif kind.startswith("fireteam_"):
                self._fireteam(kind, data, fireteams, turn, seq)
            elif kind in ("lats_start", "lats_complete"):
                self._search(kind, data, searches, turn)
            # plan_*, todo_update, lats_tree_update, file_ready, task_complete and any new type
            # carry nothing the analysis needs.

        for call in pending:
            if not call.completed:
                self.warnings["tool_start without a tool_complete"] += 1
        self.fireteams = list(fireteams.values())
        self.searches = list(searches.values())

    def _complete(self, data: dict, pending: list, turn: Optional[Turn], seq: int) -> None:
        kind = "tool_complete"
        tool = self._text(data, kind, "tool_name") or "unknown"
        step_id = data.get("step_id") if isinstance(data.get("step_id"), str) else None
        wave = data.get("wave_id") if isinstance(data.get("wave_id"), str) else None
        step_index = as_int(data.get("step_index"))
        open_calls = [c for c in pending if not c.completed]
        match = None
        if step_id:
            match = next((c for c in open_calls if c.step_id == step_id), None)
        if match is None and wave is not None:
            match = next((c for c in open_calls if c.wave == wave and c.step_index == step_index), None)
        if match is None:
            match = next((c for c in open_calls if c.tool == tool), None)
        if match is None:
            self.warnings["tool_complete without a tool_start"] += 1
            match = Call(tool=tool, args=data.get("tool_args", {}), seq=seq)
            if turn:
                turn.calls.append(match)
        match.completed = True
        success = data.get("success")
        match.ok = success if isinstance(success, bool) else None
        if not isinstance(success, bool):
            self.warnings["tool_complete.success"] += 1
        match.ms = as_int(data.get("duration_ms"))
        match.summary = self._text(data, kind, "output_summary")
        match.raw = self._text(data, kind, "raw_output")
        if "tool_args" not in data:
            self.warnings["tool_complete.tool_args"] += 1
        elif not match.args:
            match.args = data["tool_args"]
        findings = data.get("actionable_findings")
        match.findings = [as_text(f) for f in findings] if isinstance(findings, list) else []

    def _fireteam(self, kind: str, data: dict, fireteams: dict, turn: Optional[Turn], seq: int) -> None:
        fid = as_text(data.get("fireteam_id")) or "fireteam"
        team = fireteams.get(fid)
        if team is None:
            team = Fireteam(id=fid, turn=turn.n if turn else None, iteration=as_int(data.get("iteration")),
                            rationale="")
            fireteams[fid] = team
            if turn:
                turn.fireteams.append(fid)
        if kind == "fireteam_deployed":
            team.rationale = self._text(data, kind, "plan_rationale")
            members = data.get("members") if isinstance(data.get("members"), list) else []
            for m in members:
                if not isinstance(m, dict):
                    continue
                # members[].skills was renamed members[].tools on 2026-05-12 (28a215a0).
                tools = m.get("tools", m.get("skills", []))
                mid = as_text(m.get("member_id")) or f"m{len(team.members) + 1}"
                team.members[mid] = Member(id=mid, name=as_text(m.get("name")), task=as_text(m.get("task")),
                                           tools=[as_text(t) for t in tools] if isinstance(tools, list) else [])
            return
        mid = as_text(data.get("member_id"))
        if not mid:
            return
        member = team.members.setdefault(mid, Member(id=mid, name=as_text(data.get("name")), task="", tools=[]))
        if kind == "fireteam_thinking":
            member.thoughts.append((as_int(data.get("iteration")), self._text(data, kind, "thought")))
        elif kind == "fireteam_tool_start":
            member.calls.append(Call(tool=self._text(data, kind, "tool_name") or "unknown",
                                     args=data.get("tool_args", {}), seq=seq, member=mid))
        elif kind == "fireteam_tool_output_chunk":
            open_call = next((c for c in reversed(member.calls) if not c.completed), None)
            if open_call is not None:
                open_call.raw += as_text(data.get("chunk"))
        elif kind == "fireteam_tool_complete":
            tool = self._text(data, kind, "tool_name") or "unknown"
            call = next((c for c in member.calls if not c.completed and c.tool == tool), None)
            if call is None:
                call = Call(tool=tool, args={}, seq=seq, member=mid)
                member.calls.append(call)
            call.completed = True
            success = data.get("success")
            call.ok = success if isinstance(success, bool) else None
            call.ms = as_int(data.get("duration_ms"))
            call.summary = self._text(data, kind, "output_excerpt", expected=False)
        elif kind == "fireteam_member_completed":
            member.status = self._text(data, kind, "status")
            member.iterations_used = as_int(data.get("iterations_used"))
            member.findings_count = as_int(data.get("findings_count"))
            member.error = as_text(data.get("error_message"))

    def _search(self, kind: str, data: dict, searches: dict, turn: Optional[Turn]) -> None:
        sid = as_text(data.get("search_id")) or "search"
        search = searches.get(sid)
        if search is None:
            search = Search(id=sid, turn=turn.n if turn else None)
            searches[sid] = search
            if turn:
                turn.searches.append(sid)
        if kind == "lats_start":
            search.objective = self._text(data, kind, "objective")
            search.phase = self._text(data, kind, "phase", expected=False)
            search.budget = data.get("budget")
        else:
            search.outcome = self._text(data, kind, "outcome")
            trajectory = data.get("best_trajectory")
            search.trajectory = trajectory if isinstance(trajectory, list) else []
            search.metrics = data.get("metrics")
