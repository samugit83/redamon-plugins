#!/usr/bin/env python3
"""Runs Skiller on the evaluation sessions through a local RedAmon and scores each proposal.

    docker run --rm --network host -u "$(id -u):$(id -g)" \\
      -v "$PWD:/repo:ro" -v "<redamon>/.env:/env/.env:ro" -v "<results dir>:/out" -w /repo/skiller/eval \\
      python:3.11-slim python run_eval.py --user <user id> --project <project id> --label <model> --out /out/<model>.json

For every session in eval/sessions/: seed it as a new conversation of the project (the internal
key), start "Improve skill from this session" (a session cookie minted from AUTH_SECRET, as the
e2e specs do), wait for the job, read the proposal's review model, score it, then delete the
proposal and the conversation (unless --keep). The user must own the project and have a model
chosen for Skiller in Models by feature. The two secrets are read from the .env file and never
printed; nothing here talks to anything but the RedAmon URL given.

Metrics (README.md): JSON valid at the first try, edits applied, template parts valid, leaks of
the planted identifiers (must be 0), planted mistakes covered by a lesson, added characters.
"""

from __future__ import annotations

import argparse
import base64
import difflib
import hashlib
import hmac
import json
import pathlib
import re
import secrets
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from engine import templates  # noqa: E402  (eval runs beside the plugin's code, never packed)

TERMINAL = {"succeeded", "failed", "cancelled"}
JOB_WAIT_SECONDS = 720


# -- secrets and auth ---------------------------------------------------------------------------

def read_env(path: pathlib.Path, keys) -> dict:
    """The named keys of a .env file; their values are never printed."""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key.strip() in keys:
            out[key.strip()] = value.strip().strip("'\"")
    missing = [k for k in keys if not out.get(k) or out[k] == "changeme"]
    if missing:
        raise SystemExit(f"run_eval: {', '.join(missing)} missing from {path}")
    return out


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def mint_token(user_id: str, secret: str, role: str = "admin", ttl: int = 3600, now: int | None = None) -> str:
    """The app's session cookie: HS256 over {sub, role, iat, exp} (testing/e2e/tests/auth.ts)."""
    now = int(time.time()) if now is None else now
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps({"sub": user_id, "role": role, "iat": now, "exp": now + ttl},
                                 separators=(",", ":")).encode())
    data = f"{header}.{payload}"
    sig = _b64url(hmac.new(secret.encode(), data.encode(), hashlib.sha256).digest())
    return f"{data}.{sig}"


# -- what is sent ---------------------------------------------------------------------------------

def seed_body(spec: dict, project_id: str, user_id: str) -> dict:
    """The conversation rows of a session, in order (the server assigns sequence numbers)."""
    events = spec["transcript"]["events"]
    return {"projectId": project_id, "userId": user_id,
            "messages": [{"type": e["type"], "data": e["data"]} for e in events]}


# -- scoring --------------------------------------------------------------------------------------

def added_text(review: dict) -> str:
    """The lines a proposal adds, over all parts (a line diff of before and after)."""
    out = []
    for part in review.get("parts") or []:
        before, after = (part.get("before") or "").splitlines(), (part.get("after") or "").splitlines()
        for line in difflib.ndiff(before, after):
            if line.startswith("+ "):
                out.append(line[2:])
    return "\n".join(out)


def leaks(spec: dict, review: dict) -> list:
    """Planted identifiers found anywhere a proposal could carry them to a reviewer or the agent."""
    meta = review.get("meta") or {}
    haystack = "\n".join([added_text(review), review.get("note") or "", json.dumps(meta.get("lessons") or []),
                          json.dumps(meta.get("summary") or ""), json.dumps(meta.get("changelog") or [])])
    # A whole identifier, with the boundaries of Skiller's Identifiers.find: `lang` is a leak in
    # "the lang parameter", not in "language".
    return [v for v in spec["plantedIdentifiers"]
            if re.search(rf"(?<![A-Za-z0-9_-]){re.escape(v)}(?![A-Za-z0-9_-]|\.[A-Za-z0-9])", haystack, re.I)]


def map_lessons(spec: dict, lessons: list) -> dict:
    """Planted mistake id -> the lessons holding one of its rubric keywords at the start of a word
    ("execut" matches "execution"; "hang" does not match "change")."""
    out = {m["id"]: [] for m in spec["plantedMistakes"]}
    for lesson in lessons:
        text = (lesson.get("lesson") or "").lower()
        for mistake in spec["plantedMistakes"]:
            if any(re.search(r"(?<![a-z0-9])" + re.escape(k.lower()), text) for k in mistake["keywords"]):
                out[mistake["id"]].append(lesson.get("id"))
    return out


def template_problems(review: dict) -> list:
    problems = []
    for part in review.get("parts") or []:
        if part.get("kind") == "template" and part.get("after") != part.get("before"):
            problems += [f"{part['partId']}: {p}" for p in
                         templates.check("template", part.get("before") or "", part.get("placeholders") or [],
                                         part.get("after") or "")]
    return problems


def score(spec: dict, job: dict, review: dict | None) -> dict:
    result = (job or {}).get("result") or {}
    stats = result.get("stats") or {}
    calls = stats.get("modelCalls") or {}
    row = {
        "skillId": spec["skillId"],
        "jobStatus": (job or {}).get("status"),
        "outcome": result.get("status") or ((job or {}).get("error") or {}).get("code"),
        "message": result.get("message") or ((job or {}).get("error") or {}).get("message"),
        "jsonFirstTry": {"diagnose": calls.get("diagnose") == 1,
                         "edit": calls.get("edit") in (0, 1) and calls.get("edit") is not None},
        "modelCalls": calls,
        "editsApplied": stats.get("edits"), "editsDropped": stats.get("droppedEdits"),
        "lessonsKept": stats.get("lessons"), "lessonsDropped": stats.get("droppedLessons"),
        "repaired": stats.get("repaired"),
    }
    if review is None:
        row.update({"templateValid": None, "leaks": [], "mistakesCovered": {}, "addedChars": 0, "riskFlags": 0})
        return row
    lessons = (review.get("meta") or {}).get("lessons") or []
    covered = map_lessons(spec, lessons)
    row.update({
        "templateValid": not template_problems(review),
        "leaks": leaks(spec, review),
        "mistakesCovered": covered,
        "mistakesCoveredCount": sum(1 for ids in covered.values() if ids),
        "addedChars": sum(len(p.get("after") or "") - len(p.get("before") or "") for p in review.get("parts") or []),
        "riskFlags": sum(len(p.get("riskFlags") or []) for p in review.get("parts") or []),
    })
    return row


# -- the API --------------------------------------------------------------------------------------

class Api:
    def __init__(self, base_url: str, *, cookie: str, internal_key: str, timeout: float = 30.0):
        self.base = base_url.rstrip("/")
        self._cookie = cookie
        self._key = internal_key
        self.timeout = timeout

    def call(self, method: str, path: str, body=None, *, internal: bool = False) -> tuple:
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if internal:
            headers["X-Internal-Key"] = self._key
        else:
            headers["Cookie"] = f"redamon-auth={self._cookie}"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                status = resp.status
        except urllib.error.HTTPError as e:
            raw, status = e.read(), e.code
        try:
            return status, json.loads(raw.decode() or "null")
        except ValueError:
            return status, None


USER_SKILL_PLACEHOLDER = "__USER_SKILL_ID__"


def with_user_skill(spec: dict, skill_id: str) -> dict:
    """The spec with the created user skill's id in place of the placeholder (label and record)."""
    text = json.dumps(spec).replace(USER_SKILL_PLACEHOLDER, skill_id)
    return json.loads(text)


def run_one(api: Api, spec: dict, *, project_id: str, user_id: str, keep: bool, log) -> dict:
    params: dict = {}
    skill_id = None
    if spec.get("userSkill"):
        own = spec["userSkill"]
        body = {"name": f"{own['name']} {secrets.token_hex(3)}", "description": own["description"],
                "content": own["content"]}
        status, created = api.call("POST", f"/api/users/{user_id}/attack-skills", body)
        if status != 201:
            raise RuntimeError(f"creating the user skill failed: HTTP {status} {created}")
        skill_id = created["id"]
        spec = with_user_skill(spec, skill_id)
        # The person's pick in RedAmon's skill picker; the action re-derives it from the session.
        params = {"pick": {"kind": "session.skill", "ref": spec["skillId"]}}
    session_id = f"session_eval_{secrets.token_hex(6)}" if skill_id else f"session_eval_{spec['skillId']}_{secrets.token_hex(6)}"
    status, seeded = api.call("POST", f"/api/conversations/by-session/{session_id}/messages",
                              seed_body(spec, project_id, user_id), internal=True)
    if status != 201:
        if skill_id and not keep:
            api.call("DELETE", f"/api/users/{user_id}/attack-skills/{skill_id}")
        raise RuntimeError(f"seeding {spec['skillId']} failed: HTTP {status} {seeded}")
    conversation_id = seeded["conversationId"]
    review = None
    job = None
    try:
        status, started = api.call("POST", "/api/plugins/skiller/actions/analyze_session",
                                   {"context": {"projectId": project_id, "sessionId": session_id}, "params": params})
        if status not in (200, 202):
            return dict(score(spec, {"status": "refused", "error": started}, None), httpStatus=status)
        job_id = started["jobId"]
        deadline = time.monotonic() + JOB_WAIT_SECONDS
        last = None
        while time.monotonic() < deadline:
            _, job = api.call("GET", f"/api/plugins/jobs/{job_id}")
            progress = (job or {}).get("progress") or {}
            if progress.get("message") != last:
                last = progress.get("message")
                log(f"  {spec['skillId']}: {last} {json.dumps(progress.get('fields') or {})}")
            if (job or {}).get("status") in TERMINAL:
                break
            time.sleep(2)
        result = (job or {}).get("result") or {}
        proposal = result.get("reviewProposal")
        if proposal:
            _, review = api.call("GET", f"/api/plugins/overrides/change-sets/{proposal['changeSetId']}")
            if not keep:
                api.call("DELETE", f"/api/plugins/overrides/change-sets/{proposal['changeSetId']}")
        return score(spec, job or {}, review)
    finally:
        if not keep:
            api.call("DELETE", f"/api/conversations/{conversation_id}")
            if skill_id:
                api.call("DELETE", f"/api/users/{user_id}/attack-skills/{skill_id}")


def table(rows: list) -> str:
    lines = ["| skill | outcome | JSON first try (A/B) | edits applied / dropped | template valid | leaks | "
             "mistakes covered | added chars |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        first = r["jsonFirstTry"]
        lines.append(f"| {r['skillId']} | {r['outcome']} | {first['diagnose']}/{first['edit']} | "
                     f"{r['editsApplied']}/{r['editsDropped']} | {r['templateValid']} | {len(r['leaks'])} | "
                     f"{r.get('mistakesCoveredCount', 0)}/4 | {r['addedChars']} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run Skiller on the evaluation sessions through a local RedAmon.")
    ap.add_argument("--base-url", default="http://localhost:3000")
    ap.add_argument("--env-file", type=pathlib.Path, default=pathlib.Path("/env/.env"))
    ap.add_argument("--user", required=True, help="the user who owns the project and chose Skiller's model")
    ap.add_argument("--project", required=True)
    ap.add_argument("--label", required=True, help="the model's name, for the report")
    ap.add_argument("--sessions", nargs="*", type=pathlib.Path, default=sorted((HERE / "sessions").glob("*.json")))
    ap.add_argument("--out", type=pathlib.Path, default=None)
    ap.add_argument("--keep", action="store_true", help="keep the proposals and conversations")
    args = ap.parse_args(argv)

    env = read_env(args.env_file, ("AUTH_SECRET", "INTERNAL_API_KEY"))
    api = Api(args.base_url, cookie=mint_token(args.user, env["AUTH_SECRET"], ttl=2 * 3600),
              internal_key=env["INTERNAL_API_KEY"])
    rows = []
    for path in args.sessions:
        spec = json.loads(path.read_text(encoding="utf-8"))
        print(f"{spec['skillId']}: seeding and analysing", flush=True)
        rows.append(run_one(api, spec, project_id=args.project, user_id=args.user, keep=args.keep,
                            log=lambda m: print(m, flush=True)))
    report = {"label": args.label, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "results": rows}
    if args.out:
        args.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(table(rows))
    return 0 if all(not r["leaks"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
