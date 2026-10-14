"""Builds the synthetic sessions in fixtures/sessions/ (run: python sessions_build.py).

Every session is what `sdk.session.read()` hands a plugin: `{meta, events, truncated, untrusted}`,
events in the order the agent persists them (agentic/websocket_api.py, streaming.py: on each
think yield `phase_update`, then the previous step's `tool_complete`, then `thinking`, then the
new `tool_start`), with `phase_update` rows deduplicated the way the Gateway does
(webapp/src/lib/plugins/transcript.ts: only a change of something other than
`iteration_count` makes a new row).

Synthetic data only (master plan P3): hosts under example.com / example.org, addresses in
192.0.2.0/24, 198.51.100.0/24 and 2001:db8::/32. Each historical payload shape of the persisted
events since chat persistence began (2026-02-21) has a session here; `tests/test_digest.py`
reads all of them.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parent / "sessions"
MISSING = object()

# Planted session identifiers (tests/test_identifiers.py and test_leak_guard.py look for them).
HOST = "shop.example.com"
API_HOST = "api.shop.example.com"
INTRANET = "intranet.example.org"
IP4 = "192.0.2.10"
IP4_B = "198.51.100.23"
IP6 = "2001:db8::17"
EMAIL = "alice.ops@shop.example.com"
URL = f"http://{HOST}/api/v1/products?product_id=42&sessionToken=Zq81xLmP"
PATH = "/api/v1/products"
HEX_TOKEN = "9f86d081884c7d659a2feaa0c55ad015"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0LXVzZXIifQ.c2lnbmF0dXJlLXN5bnRoZXRpYw"
FLAG = "FLAG{synthetic_sqli_flag_7a1}"
SESSION_ID = "session_5e1f7c2a9b3d4e60"
TITLE = "Assess shop.example.com product search"

# A person's own skill (user_skill:<id>), and the text the agent rendered from it: the agent
# records the first 16 hex of the SHA-256 of that text (agentic skill_part_seam.py).
USER_SKILL_ID = "cuserflow0000000000000001"
USER_SKILL_NAME = "My SQLi flow v2"
USER_SKILL_TEXT = ("# My SQLi flow\n"
                   "1. Find the parameters of each form with execute_curl.\n"
                   "2. Send a single quote to each and compare the error with the baseline.\n"
                   "3. Run sqlmap on the parameter that broke: kali_shell with sqlmap -u '<url>' -p <param> --batch.\n"
                   "Do not test hosts outside the scope.\n")


def user_record(text: str = USER_SKILL_TEXT, skill_id: str = USER_SKILL_ID) -> dict:
    import hashlib

    return {"skillId": f"user_skill:{skill_id}", "kind": "user",
            "contentSha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]}


class Session:
    def __init__(self, sid: str, title: str, *, start: str = "2026-10-10T09:00:00Z", phase_shape: str = "current"):
        self.sid = sid
        self.title = title
        self.t = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
        self.rows: list = []
        self.seq = 0
        self.phase = "informational"
        self.iteration = 0
        self.label = ""
        self.workflow = None
        self.phase_shape = phase_shape   # current | no_workflow | no_changeset | no_label
        self.pending_complete = None
        self.started = start

    # -- low level ------------------------------------------------------------
    def event(self, kind: str, data, seconds: int = 7):
        self.seq += 1
        self.t += dt.timedelta(seconds=seconds)
        self.rows.append({"seq": self.seq, "type": kind, "at": self.t.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "data": data})

    def phase_update(self):
        data = {"current_phase": self.phase, "iteration_count": self.iteration}
        if self.phase_shape != "no_label":
            data["attack_path_type"] = self.label
        if self.phase_shape == "current":
            data["skill_workflow"] = copy.deepcopy(self.workflow)
        elif self.phase_shape == "no_changeset":
            wf = copy.deepcopy(self.workflow)
            if wf:
                wf.pop("changeSetId", None)
            data["skill_workflow"] = wf
        self.event("phase_update", data, seconds=1)

    def flush_complete(self):
        if self.pending_complete is not None:
            self.event("tool_complete", self.pending_complete, seconds=2)
            self.pending_complete = None

    # -- the agent's turns ----------------------------------------------------
    def user(self, text: str):
        self.event("user_message", {"content": text})
        self.label = ""
        self.workflow = None
        self.phase_update()

    def classify(self, label: str, workflow=MISSING):
        """The skill of the next turns: a label, and the playbook record (None: not rendered)."""
        self.label = label
        if workflow is MISSING:
            workflow = {"skillId": label, "revision": 1, "changeSetId": None} if label else None
        self.workflow = workflow

    def turn(self, thought: str, tool: str | None = None, args: dict | None = None, *, ok: bool = True,
             raw: str = "", summary: str = "", findings=None, ms: int = 900, phase: str | None = None,
             tier: str | None = "green", stall: int = 0, action: str = "use_tool", shape: str = "current",
             reasoning: str = "", step_id: bool = True):
        self.iteration += 1
        if phase:
            self.phase = phase
        self.phase_update()
        self.flush_complete()
        thinking = {"iteration": self.iteration, "phase": self.phase, "thought": thought,
                    "reasoning": reasoning or f"Reasoning for turn {self.iteration}.", "action": action,
                    "input_tokens": 4200, "output_tokens": 310, "productivity_score": 0.7,
                    "productivity_tier": tier, "stall": stall}
        if shape in ("2026-02-21", "2026-04-19-early"):
            for key in ("action", "input_tokens", "output_tokens", "productivity_score", "productivity_tier", "stall"):
                thinking.pop(key)
            if shape == "2026-04-19-early":
                thinking["action"] = action
        elif shape == "2026-04-19":
            for key in ("productivity_score", "productivity_tier", "stall"):
                thinking.pop(key)
        self.event("thinking", thinking)
        if tool:
            sid = f"step-{self.iteration:04d}-{tool}"
            start = {"tool_name": tool, "tool_args": args or {}}
            if step_id:
                start["step_id"] = sid
            self.event("tool_start", start, seconds=1)
            complete = {"tool_name": tool, "success": ok, "output_summary": summary or ("ok" if ok else "failed"),
                        "actionable_findings": findings or [], "recommended_next_steps": [],
                        "duration_ms": ms, "tool_args": args or {}, "raw_output": raw}
            if step_id:
                complete["step_id"] = sid
            if shape == "2026-02-21":
                complete.pop("duration_ms")
            self.pending_complete = complete

    def wave(self, thought: str, calls: list, *, rationale: str = "Run the probes in parallel."):
        """A plan wave: several tools in one turn (wave_id/step_index, no step_id, empty summaries)."""
        self.iteration += 1
        self.phase_update()
        self.flush_complete()
        self.event("thinking", {"iteration": self.iteration, "phase": self.phase, "thought": thought,
                                "reasoning": "", "action": "plan_tools", "input_tokens": 1, "output_tokens": 1,
                                "productivity_score": 0.6, "productivity_tier": "yellow", "stall": 0})
        wave_id = f"wave-{self.iteration}"
        self.event("plan_start", {"wave_id": wave_id, "plan_rationale": rationale, "tool_count": len(calls),
                                  "tools": [c[0] for c in calls]})
        for i, (tool, args, ok, raw) in enumerate(calls):
            self.event("tool_start", {"tool_name": tool, "tool_args": args, "wave_id": wave_id, "step_index": i}, seconds=1)
        for i, (tool, args, ok, raw) in enumerate(calls):
            self.event("tool_complete", {"tool_name": tool, "success": ok, "output_summary": "",
                                         "actionable_findings": [], "recommended_next_steps": [],
                                         "wave_id": wave_id, "step_index": i, "duration_ms": 1500,
                                         "tool_args": args, "raw_output": raw}, seconds=1)
        self.event("plan_complete", {"wave_id": wave_id, "total_steps": len(calls),
                                     "successful": sum(1 for c in calls if c[2]), "failed": sum(1 for c in calls if not c[2])})
        self.event("plan_analysis", {"wave_id": wave_id, "interpretation": "The wave finished.",
                                     "actionable_findings": [], "recommended_next_steps": []})

    def deep_think(self, trigger: str, analysis: str):
        self.event("deep_think", {"trigger_reason": trigger, "analysis": analysis,
                                  "iteration": self.iteration, "phase": self.phase})

    def guidance(self, text: str):
        self.event("guidance", {"content": text, "isGuidance": True})

    def confirm_tool(self, tool: str, args: dict, decision: str, *, reasoning: str = "Needs approval."):
        self.event("tool_confirmation_request", {"confirmation_id": f"c{self.iteration:03d}", "mode": "single",
                                                 "tools": [{"tool_name": tool, "tool_args": args, "rationale": reasoning}],
                                                 "reasoning": reasoning, "phase": self.phase, "iteration": self.iteration})
        self.event("tool_confirmation_response", {"decision": decision, "modifications": None}, seconds=30)

    def approval(self, to_phase: str, decision: str, modification=None):
        self.event("approval_request", {"from_phase": self.phase, "to_phase": to_phase,
                                        "reason": "Enough evidence to move on.", "planned_actions": [], "risks": [],
                                        "requires_approval": True})
        self.event("approval_response", {"decision": decision, "modification": modification}, seconds=20)
        if decision == "approve":
            self.phase = to_phase

    def question(self, question: str, answer: str):
        self.event("question_request", {"question": question, "context": "", "options": [], "phase": self.phase})
        self.event("answer_response", {"answer": answer}, seconds=40)

    def error(self, message: str):
        self.event("error", {"message": message, "recoverable": True})

    def report(self, text: str, *, tier: str | None = "full_report"):
        self.flush_complete()
        data = {"content": text, "phase": self.phase, "task_complete": True}
        if tier is not None:
            data["response_tier"] = tier
        self.event("assistant_message", data)
        self.event("task_complete", {"message": "Task completed successfully", "final_phase": self.phase,
                                     "total_iterations": self.iteration})

    # -- out ------------------------------------------------------------------
    def transcript(self, *, running=False) -> dict:
        self.flush_complete()
        kept, last_phase = [], None
        for row in self.rows:
            if row["type"] == "phase_update":
                key = json.dumps({k: v for k, v in row["data"].items() if k != "iteration_count"}, sort_keys=True)
                if key == last_phase:
                    continue
                last_phase = key
            kept.append(row)
        return {
            "meta": {"id": self.sid, "title": self.title, "status": "active", "currentPhase": self.phase,
                     "iterationCount": self.iteration, "createdAt": self.started,
                     "updatedAt": self.rows[-1]["at"] if self.rows else self.started,
                     "running": running, "highWaterSeq": self.seq},
            "events": kept,
            "truncated": {"keptEvents": len(kept), "droppedEvents": len(self.rows) - len(kept)},
            "untrusted": True,
        }


SQLMAP_ARGS = {"command": f"sqlmap -u '{URL}' --batch --level 5 --risk 3 --threads 10 --crawl 3"}
CURL = {"args": f"-s -i '{URL}'"}


def sqli_workflow() -> dict:
    """A modern session: the SQL injection playbook in the prompt, with planted mistakes."""
    s = Session(SESSION_ID, TITLE)
    s.user(f"Test {HOST} ({IP4}) for SQL injection in the product search. Contact {EMAIL} if anything breaks.")
    s.classify("sql_injection")
    s.turn("Map the product search endpoint first.", "execute_curl", CURL,
           raw=f"HTTP/1.1 200 OK\nServer: nginx\nSet-Cookie: sid={HEX_TOKEN}\n\n<html>{HOST} products</html>",
           summary="The endpoint answers 200 and reflects product_id.")
    s.turn("Check the parameter with a quote.", "execute_curl", {"args": f"-s '{URL}%27'"},
           raw="HTTP/1.1 500 Internal Server Error\nYou have an error in your SQL syntax near ''42'''",
           summary="A database error appears: likely injectable.")
    s.turn("Run sqlmap with a crawl to find every parameter.", "kali_shell", SQLMAP_ARGS, ok=False,
           raw="[CRITICAL] connection timed out to the target URL\n[WARNING] crawling stalled after 300 s",
           summary="sqlmap timed out while crawling.", ms=300_000, tier="yellow", stall=1)
    s.guidance("Do not crawl the whole site, test only the product_id parameter.")
    s.turn("Retry sqlmap the same way.", "kali_shell", SQLMAP_ARGS, ok=False,
           raw="[CRITICAL] connection timed out to the target URL",
           summary="sqlmap timed out again.", ms=300_000, tier="orange", stall=2)
    s.turn("Retry once more.", "kali_shell", SQLMAP_ARGS, ok=False,
           raw="[CRITICAL] connection timed out to the target URL",
           summary="sqlmap timed out a third time.", ms=300_000, tier="red", stall=3)
    s.deep_think("unproductive streak: 3 failed steps of the last 6",
                 "The crawl and the thread count make sqlmap hang; target one parameter with fewer threads.")
    s.confirm_tool("kali_shell", {"command": f"sqlmap -u '{URL}' --os-shell"}, "reject",
                   reasoning="Try to get an OS shell.")
    s.turn("Target only product_id, no crawl.", "kali_shell",
           {"command": f"sqlmap -u '{URL}' -p product_id --batch --technique=BEU --threads 1"},
           raw=f"[INFO] parameter 'product_id' is vulnerable. Back-end DBMS: MySQL >= 5.6\nretrieved: {FLAG}",
           summary="product_id is injectable (boolean, error and union based).",
           findings=[f"SQL injection in product_id at {PATH}"], ms=95_000, tier="green", phase="exploitation")
    s.turn("Confirm with a manual boolean pair.", "execute_curl", {"args": f"-s '{URL}%20AND%201=1'"},
           raw=f"HTTP/1.1 200 OK\nAuthorization: Bearer {JWT}\n\nproducts 1", summary="The boolean pair confirms it.")
    s.report(f"SQL injection confirmed in product_id on {HOST}. Flag {FLAG}.")
    return s.transcript()


def switch_midsession() -> dict:
    s = Session("session_switch000000001", "Search page tests")
    s.user(f"Check {HOST} search for XSS.")
    s.classify("xss")
    s.turn("Reflect a marker.", "execute_curl", {"args": f"-s 'http://{HOST}/search?q=zzmarker'"}, raw="zzmarker")
    s.turn("Try a script tag.", "execute_curl", {"args": f"-s 'http://{HOST}/search?q=<script>'"}, raw="&lt;script&gt;")
    s.classify("sql_injection")
    for i in range(4):
        s.turn(f"SQLi probe {i + 1}.", "execute_curl", {"args": f"-s 'http://{HOST}/search?q=a%27{i}'"},
               raw="HTTP/1.1 500\nSQL syntax error", ok=i != 0)
    s.report("Done.")
    return s.transcript()


def tie() -> dict:
    s = Session("session_tie0000000000001", "Tie")
    s.user(f"Test {HOST}.")
    s.classify("xss")
    for i in range(3):
        s.turn(f"xss {i}", "execute_curl", {"args": f"-s 'http://{HOST}/x{i}'"}, raw="ok")
    s.classify("ssrf")
    for i in range(3):
        s.turn(f"ssrf {i}", "execute_curl", {"args": f"-s 'http://{HOST}/fetch?url=http://{IP4_B}/{i}'"}, raw="ok")
    s.report("Done.")
    return s.transcript()


def user_skill_only() -> dict:
    s = Session("session_userskill000001", "Custom playbook run")
    s.user("Run my custom checklist.")
    s.classify("user_skill:cm1abc", None)
    for i in range(4):
        s.turn(f"Custom step {i}", "execute_curl", {"args": f"-s 'http://{HOST}/c{i}'"}, raw="ok")
    s.report("Done.")
    return s.transcript()


def user_skill_workflow() -> dict:
    """A user skill recorded by the agent (platform 1.1.0): every turn carries its record."""
    s = Session("session_userflow0000001", "Own SQLi flow")
    s.user(f"Use my flow on {HOST}.")
    s.classify(f"user_skill:{USER_SKILL_ID}", user_record())
    s.turn("Find the forms.", "execute_curl", {"args": f"-s 'http://{HOST}/search'"}, raw="<form>")
    s.turn("Quote the parameter.", "execute_curl", {"args": f"-s 'http://{HOST}/search?q=%27'"},
           raw="SQL syntax error", ok=True)
    s.turn("sqlmap crawl.", "kali_shell", {"command": f"sqlmap -u 'http://{HOST}/' --crawl=3 --batch"},
           raw="[WARNING] crawl stalled", ok=False, tier="red", stall=2)
    s.turn("sqlmap on one parameter.", "kali_shell", {"command": f"sqlmap -u 'http://{HOST}/search?q=1' -p q --batch"},
           raw="Parameter 'q' is vulnerable", findings=["SQL injection in parameter q"])
    s.turn("Confirm.", "kali_shell", {"command": f"sqlmap -u 'http://{HOST}/search?q=1' -p q --dbs --batch"},
           raw="available databases [2]")
    s.report("Injection confirmed.")
    return s.transcript()


def mixed_builtin_user() -> dict:
    """Three built-in turns, then four of the person's own skill."""
    s = Session("session_mixed000000001", "Mixed skills")
    s.user(f"Test {HOST}.")
    s.classify("sql_injection")
    for i in range(3):
        s.turn(f"sqli {i}", "execute_curl", {"args": f"-s 'http://{HOST}/p{i}?id=%27'"}, raw="ok")
    s.classify(f"user_skill:{USER_SKILL_ID}", user_record())
    for i in range(4):
        s.turn(f"own {i}", "kali_shell", {"command": f"sqlmap -u 'http://{HOST}/q{i}?id=1' --batch"}, raw="ok")
    s.report("Done.")
    return s.transcript()


def user_skill_label_only() -> dict:
    """Recorded before the agent recorded user skills: the label only, the record null."""
    s = Session("session_userlabel000001", "Own flow, older agent")
    s.user(f"Use my flow on {HOST}.")
    s.classify(f"user_skill:{USER_SKILL_ID}", None)
    s.turn("Recon.", "execute_curl", {"args": f"-s 'http://{HOST}/'"}, raw="ok")
    s.turn("Probe.", "execute_curl", {"args": f"-s 'http://{HOST}/?id=%27'"}, raw="error", phase="exploitation")
    s.turn("Loot.", "kali_shell", {"command": "id"}, raw="uid=33", phase="post_exploitation")
    s.report("Done.")
    return s.transcript()


def user_skill_disabled_midway() -> dict:
    """The skill was turned off after three turns: later turns keep the label but have no record."""
    s = Session("session_userdisabled0001", "Own flow turned off")
    s.user(f"Use my flow on {HOST}.")
    s.classify(f"user_skill:{USER_SKILL_ID}", user_record())
    for i in range(3):
        s.turn(f"own {i}", "execute_curl", {"args": f"-s 'http://{HOST}/o{i}'"}, raw="ok")
    s.classify(f"user_skill:{USER_SKILL_ID}", None)
    for i in range(3):
        s.turn(f"off {i}", "execute_curl", {"args": f"-s 'http://{HOST}/f{i}'"}, raw="ok")
    s.report("Done.")
    return s.transcript()


def unclassified() -> dict:
    s = Session("session_unclass00000001", "Odd request")
    s.user("Look at the banner.")
    s.classify("banner_grab-unclassified", None)
    for i in range(4):
        s.turn(f"Banner {i}", "execute_naabu", {"target": IP4}, raw="80/tcp open")
    s.report("Done.")
    return s.transcript()


def skill_not_rendered() -> dict:
    """Labelled sql_injection, but the playbook was never in the prompt (skill disabled, say)."""
    s = Session("session_norender0000001", "Disabled skill")
    s.user(f"Test {HOST} for SQL injection.")
    s.classify("sql_injection", None)
    for i in range(4):
        s.turn(f"Probe {i}", "execute_curl", {"args": f"-s 'http://{HOST}/p?id={i}%27'"}, raw="500")
    s.report("Done.")
    return s.transcript()


def too_short() -> dict:
    s = Session("session_short000000001", "Short")
    s.user(f"Test {HOST}.")
    s.classify("sql_injection")
    s.turn("Look.", "execute_curl", CURL, raw="200")
    s.turn("Done looking.", None, action="complete")
    s.report("Nothing found.", tier="summary")
    return s.transcript()


def no_tool_calls() -> dict:
    s = Session("session_notools0000001", "Talk only")
    s.user("Explain SQL injection.")
    s.classify("sql_injection")
    for i in range(4):
        s.turn(f"Thinking {i}", None, action="ask_user")
    s.report("Explained.", tier="summary")
    return s.transcript()


def fireteam() -> dict:
    s = Session("session_fireteam000001", "Fireteam run")
    s.user(f"Test {HOST} for SQL injection with specialists.")
    s.classify("sql_injection")
    s.turn("Map the endpoint.", "execute_curl", CURL, raw="200")
    s.turn("Check quoting.", "execute_curl", {"args": f"-s '{URL}%27'"}, raw="500 SQL syntax")
    s.turn("Deploy specialists for two parameters.", None, action="deploy_fireteam")
    ft = "ft-0001"
    s.event("fireteam_deployed", {"fireteam_id": ft, "iteration": s.iteration, "plan_rationale": "Split the work by parameter.",
                                  "member_count": 2, "members": [
                                      {"member_id": "m1", "name": "Boolean prober", "task": "Test product_id with boolean pairs.",
                                       "tools": ["execute_curl", "kali_shell"], "max_iterations": 5},
                                      {"member_id": "m2", "name": "Time prober", "task": "Test sort with time delays.",
                                       "tools": ["execute_curl"], "max_iterations": 5}]})
    for member, name in (("m1", "Boolean prober"), ("m2", "Time prober")):
        s.event("fireteam_member_started", {"fireteam_id": ft, "member_id": member, "name": name})
    member_args = {"command": f"sqlmap -u '{URL}' -p sort --time-sec 30 --threads 10"}
    for i in range(3):
        s.event("fireteam_tool_start", {"fireteam_id": ft, "member_id": "m2", "tool_name": "kali_shell",
                                        "tool_args": member_args, "wave_id": None, "step_index": None})
        s.event("fireteam_tool_output_chunk", {"fireteam_id": ft, "member_id": "m2", "tool_name": "kali_shell",
                                               "chunk": "[CRITICAL] timed out", "is_final": True, "wave_id": None, "step_index": None})
        s.event("fireteam_tool_complete", {"fireteam_id": ft, "member_id": "m2", "tool_name": "kali_shell",
                                           "success": False, "duration_ms": 120_000, "output_excerpt": "[CRITICAL] timed out",
                                           "wave_id": None, "step_index": None})
    s.event("fireteam_tool_start", {"fireteam_id": ft, "member_id": "m1", "tool_name": "execute_curl",
                                    "tool_args": {"args": f"-s '{URL}%20AND%201=1'"}, "wave_id": None, "step_index": None})
    s.event("fireteam_tool_complete", {"fireteam_id": ft, "member_id": "m1", "tool_name": "execute_curl",
                                       "success": True, "duration_ms": 800, "output_excerpt": "products 1",
                                       "wave_id": None, "step_index": None})
    s.event("fireteam_member_completed", {"fireteam_id": ft, "member_id": "m1", "name": "Boolean prober", "status": "success",
                                          "iterations_used": 3, "tokens_used": 9000, "input_tokens_used": 8000,
                                          "output_tokens_used": 1000, "findings_count": 1, "wall_clock_seconds": 41.5,
                                          "error_message": None})
    s.event("fireteam_member_completed", {"fireteam_id": ft, "member_id": "m2", "name": "Time prober", "status": "error",
                                          "iterations_used": 5, "tokens_used": 12000, "input_tokens_used": 11000,
                                          "output_tokens_used": 1000, "findings_count": 0, "wall_clock_seconds": 380.0,
                                          "error_message": "iteration budget exhausted"})
    s.event("fireteam_completed", {"fireteam_id": ft, "total": 2, "status_counts": {"success": 1, "error": 1},
                                   "wall_clock_seconds": 381.0})
    s.turn("Confirm what the specialists found.", "execute_curl", {"args": f"-s '{URL}%20AND%201=2'"}, raw="products 0")
    s.report("SQL injection in product_id.")
    return s.transcript()


def lats() -> dict:
    s = Session("session_lats0000000001", "Exploit path search")
    s.user(f"Exploit {HOST}.")
    s.classify("sql_injection")
    s.turn("Map it.", "execute_curl", CURL, raw="200", phase="exploitation")
    s.event("lats_start", {"search_id": f"{s.sid}:root1", "objective": "Reach data through the product search.",
                           "phase": "exploitation", "budget": {"max_rollouts": 8, "max_depth": 4}, "shadow_mode": False})
    s.event("lats_tree_update", {"search_id": f"{s.sid}:root1", "snapshot": {"nodes": [{"id": "n1"}]}})
    s.turn("Follow the search's first branch.", "execute_curl", {"args": f"-s '{URL}%20UNION%20SELECT%201'"}, raw="500")
    s.turn("Follow its second branch.", "execute_curl", {"args": f"-s '{URL}%20AND%20SLEEP(5)'"}, raw="200 after 5 s")
    s.event("lats_complete", {"search_id": f"{s.sid}:root1", "outcome": "terminal_success",
                              "best_trajectory": [{"action": "union probe", "result": "error"},
                                                  {"action": "time-based probe", "result": "5 s delay"}],
                              "metrics": {"rollouts": 6, "max_depth_reached": 3, "nodes_total": 11,
                                          "pruned_count": 2, "outcome_terminal": True}})
    s.report("Time-based injection works.")
    return s.transcript()


def waves() -> dict:
    s = Session("session_waves00000001", "Parallel probes")
    s.user(f"Test {HOST}.")
    s.classify("sql_injection")
    s.turn("Map it.", "execute_curl", CURL, raw="200")
    s.wave("Probe three parameters at once.", [
        ("execute_curl", {"args": f"-s 'http://{HOST}/p?a=1%27'"}, False, "500"),
        ("execute_curl", {"args": f"-s 'http://{HOST}/p?b=1%27'"}, True, "200"),
        ("execute_curl", {"args": f"-s 'http://{HOST}/p?c=1%27'"}, False, "500"),
    ])
    s.turn("Focus on a.", "kali_shell", {"command": f"sqlmap -u 'http://{HOST}/p?a=1' -p a --batch"}, raw="vulnerable")
    s.report("Parameter a is injectable.")
    return s.transcript()


# -- historical shapes ------------------------------------------------------------------------

def shape_2026_02_21() -> dict:
    """The first persisted shape: no skill_workflow, no action/tokens/productivity in thinking,
    no step_id or duration_ms, no response_tier, and the `cve_exploit` label before the session
    is classified (the default until 6ddb4d2a)."""
    s = Session("session_shape0221000001", "Early session", start="2026-02-22T10:00:00Z", phase_shape="no_workflow")
    s.user(f"Test {HOST} for path traversal.")
    s.classify("cve_exploit")
    s.phase_update()
    s.classify("path_traversal")
    for i in range(3):
        s.turn(f"Traversal probe {i}", "execute_curl", {"args": f"-s 'http://{HOST}/download?file=../../etc/passwd{i}'"},
               raw="root:x:0:0", shape="2026-02-21", step_id=False)
    s.report("Traversal found.", tier=None)
    return s.transcript()


def shape_2026_04_19() -> dict:
    """Fireteams arrive (members[].skills before 28a215a0, fireteam_thinking rows written), thinking
    has action and tokens but no productivity fields, no step_id, no skill_workflow."""
    s = Session("session_shape0419000001", "April session", start="2026-04-20T10:00:00Z", phase_shape="no_workflow")
    s.user(f"Test {HOST} for SQL injection.")
    s.classify("sql_injection")
    for i in range(3):
        s.turn(f"Probe {i}", "execute_curl", {"args": f"-s '{URL}{i}'"}, raw="500", shape="2026-04-19", step_id=False)
    s.event("fireteam_deployed", {"fireteam_id": "ft-old", "iteration": s.iteration, "plan_rationale": "Split.",
                                  "member_count": 1, "members": [{"member_id": "m1", "name": "Prober", "task": "Probe.",
                                                                  "skills": ["execute_curl"], "max_iterations": 3}]})
    s.event("fireteam_thinking", {"fireteam_id": "ft-old", "member_id": "m1", "name": "Prober", "iteration": 1,
                                  "phase": "informational", "thought": "Try a boolean pair.", "reasoning": "r"})
    s.event("fireteam_tool_start", {"fireteam_id": "ft-old", "member_id": "m1", "tool_name": "execute_curl",
                                    "tool_args": {"args": "-s x"}, "wave_id": None, "step_index": None})
    s.event("fireteam_tool_complete", {"fireteam_id": "ft-old", "member_id": "m1", "tool_name": "execute_curl",
                                       "success": True, "duration_ms": 0, "output_excerpt": "ok", "wave_id": None,
                                       "step_index": None})
    s.event("fireteam_member_completed", {"fireteam_id": "ft-old", "member_id": "m1", "name": "Prober", "status": "success",
                                          "iterations_used": 1, "tokens_used": 10, "findings_count": 0,
                                          "wall_clock_seconds": 3.0, "error_message": None})
    s.report("Done.")
    return s.transcript()


def shape_2026_08_05() -> dict:
    """step_id on single-tool rows and productivity fields; still no skill_workflow."""
    s = Session("session_shape0805000001", "August session", start="2026-08-06T10:00:00Z", phase_shape="no_workflow")
    s.user(f"[Chat Skill Context]\nUse the checklist.\n\n[User Query]\nTest {HOST} for XSS.")
    s.classify("xss")
    for i in range(3):
        s.turn(f"XSS probe {i}", "execute_curl", {"args": f"-s 'http://{HOST}/s?q=<b>{i}'"}, raw="<b>")
    s.turn("Post-exploitation look around.", "kali_shell", {"command": "id"}, raw="uid=33", phase="post_exploitation")
    s.report("Reflected XSS.")
    return s.transcript()


def shape_2026_10_09() -> dict:
    """skill_workflow without changeSetId (683e3110, before 7cda2a9d), and a graph-node context."""
    s = Session("session_shape1009000001", "October session", start="2026-10-09T15:00:00Z", phase_shape="no_changeset")
    s.user(f"[Graph Node Context: Endpoint]\n{PATH}\n\n[User Query]\nTest it for SQL injection.")
    s.classify("sql_injection", {"skillId": "sql_injection", "revision": 2, "changeSetId": "cs-a"})
    for i in range(3):
        s.turn(f"Probe {i}", "execute_curl", {"args": f"-s '{URL}{i}'"}, raw="500")
    s.report("Done.")
    return s.transcript()


def legacy_no_attack_path() -> dict:
    """Defensive: phase_update rows without attack_path_type (no persisted session has them)."""
    s = Session("session_noattackpath001", "No label", phase_shape="no_label")
    s.user(f"Test {HOST}.")
    for i in range(4):
        s.turn(f"Probe {i}", "execute_curl", {"args": f"-s 'http://{HOST}/{i}'"}, raw="200")
    s.report("Done.")
    return s.transcript()


def defensive() -> dict:
    """Missing and odd fields: a tool_complete without raw_output or tool_args, an event the Gateway
    cut to a preview, an event whose data is not an object, wrong types, an unknown event type."""
    s = Session("session_defensive00001", "Defensive")
    s.user(f"Test {HOST} for SQL injection.")
    s.classify("sql_injection")
    s.turn("Probe one.", "execute_curl", {"args": f"-s '{URL}'"}, raw="200")
    s.turn("Probe two.", "kali_shell", {"command": "sqlmap -u x"}, raw="x" * 10, ok=False)
    s.pending_complete.pop("raw_output")
    s.pending_complete.pop("tool_args")
    s.turn("Probe three.", "execute_curl", {"args": "-s y"}, raw="z")
    s.flush_complete()
    s.event("tool_complete", {"truncated": True, "preview": '{"tool_name":"kali_shell","raw_output":"AAAA'})
    s.event("thinking", "not an object")
    s.event("thinking", {"iteration": "4", "phase": None, "thought": ["a", "list"], "productivity_tier": 5})
    s.event("tool_start", {"tool_name": None, "tool_args": "a string"})
    s.event("something_new", {"x": 1})
    s.event("deep_think", {})
    s.event("guidance", {"content": None})
    s.report("Done.")
    return s.transcript()


SESSIONS = {
    "sqli_workflow": sqli_workflow,
    "switch_midsession": switch_midsession,
    "tie": tie,
    "user_skill_only": user_skill_only,
    "user_skill_workflow": user_skill_workflow,
    "mixed_builtin_user": mixed_builtin_user,
    "user_skill_label_only": user_skill_label_only,
    "user_skill_disabled_midway": user_skill_disabled_midway,
    "unclassified": unclassified,
    "skill_not_rendered": skill_not_rendered,
    "too_short": too_short,
    "no_tool_calls": no_tool_calls,
    "fireteam": fireteam,
    "lats": lats,
    "waves": waves,
    "shape_2026_02_21": shape_2026_02_21,
    "shape_2026_04_19": shape_2026_04_19,
    "shape_2026_08_05": shape_2026_08_05,
    "shape_2026_10_09": shape_2026_10_09,
    "legacy_no_attack_path": legacy_no_attack_path,
    "defensive": defensive,
}


def render(name: str) -> str:
    return json.dumps(SESSIONS[name](), indent=1, ensure_ascii=False) + "\n"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name in SESSIONS:
        (OUT / f"{name}.json").write_text(render(name), encoding="utf-8")
    print(f"wrote {len(SESSIONS)} sessions to {OUT}")


if __name__ == "__main__":
    main()
