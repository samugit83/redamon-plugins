"""The evaluation harness, offline: its sessions, auth, scoring, and one run against a fake RedAmon (R-36)."""

import base64
import hashlib
import hmac
import http.server
import importlib.util
import json
import pathlib
import re
import threading

import pytest

from engine import skill_pick
from engine.transcript_view import TranscriptView

EVAL = pathlib.Path(__file__).resolve().parents[1] / "eval"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, EVAL / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load("build_sessions")
run_eval = _load("run_eval")
SESSIONS = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((EVAL / "sessions").glob("*.json"))}


def test_sessions_match_their_builder():
    assert sorted(SESSIONS) == sorted(build.SESSIONS)
    for name in build.SESSIONS:
        assert (EVAL / "sessions" / f"{name}.json").read_text(encoding="utf-8") == build.render(name), name


@pytest.mark.parametrize("name", sorted(build.SESSIONS))
def test_each_session_plants_the_four_mistakes_and_uses_its_skill(name):
    spec = SESSIONS[name]
    assert [m["kind"] for m in spec["plantedMistakes"]] == ["hang", "repeat", "verify", "guidance"]
    text = json.dumps(spec["transcript"])
    for value in spec["plantedIdentifiers"]:
        assert value in text, value
    pick = skill_pick.choose(TranscriptView.from_transcript(spec["transcript"]))
    assert pick["skillId"] == spec["skillId"] and pick["iterations"] >= 3


@pytest.mark.parametrize("name", sorted(build.SESSIONS))
def test_synthetic_data_only(name):
    text = json.dumps(SESSIONS[name])
    for host in re.findall(r"\b(?:[a-z0-9-]+\.)+(?:com|org|net|io)\b", text):
        assert host.endswith(("example.com", "example.org", "example.net")), host
    for ip in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text):
        assert ip.startswith(("192.0.2.", "198.51.100.", "203.0.113.")), ip


def test_token_is_the_apps_hs256_cookie():
    token = run_eval.mint_token("user-1", "s3cret", ttl=60, now=1_000)
    header, payload, sig = token.split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
    assert json.loads(base64.urlsafe_b64decode(pad(payload))) == {"sub": "user-1", "role": "admin", "iat": 1000,
                                                                   "exp": 1060}
    expected = base64.urlsafe_b64encode(hmac.new(b"s3cret", f"{header}.{payload}".encode(), hashlib.sha256).digest())
    assert sig == expected.decode().rstrip("=")


def test_env_is_read_without_printing(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("# comment\nAUTH_SECRET='abc123'\nINTERNAL_API_KEY=k-9\nOTHER=x\n")
    assert run_eval.read_env(env, ("AUTH_SECRET", "INTERNAL_API_KEY")) == {"AUTH_SECRET": "abc123",
                                                                           "INTERNAL_API_KEY": "k-9"}
    assert capsys.readouterr().out == ""
    env.write_text("AUTH_SECRET=changeme\n")
    with pytest.raises(SystemExit) as e:
        run_eval.read_env(env, ("AUTH_SECRET", "INTERNAL_API_KEY"))
    assert "changeme" not in str(e.value) and "AUTH_SECRET, INTERNAL_API_KEY" in str(e.value)


def test_seed_body_keeps_the_order():
    spec = SESSIONS["sql_injection"]
    body = run_eval.seed_body(spec, "p1", "u1")
    assert body["projectId"] == "p1" and body["userId"] == "u1"
    assert [m["type"] for m in body["messages"]] == [e["type"] for e in spec["transcript"]["events"]]


REVIEW = {
    "parts": [{"partId": "tools", "kind": "template", "placeholders": ["sqli_level"],
               "before": "Step 1.\nUse {sqli_level}.\n",
               "after": "Step 1.\nWhen a crawl stalls, test one parameter with one thread.\nUse {sqli_level}.\n",
               "riskFlags": []}],
    "note": "Steer sqlmap.", "meta": {"lessons": [
        {"id": "L1", "lesson": "When a crawl stalls, test one parameter with one thread."},
        {"id": "L2", "lesson": "When probes are blocked, change the payload family instead of repeating it."}]},
}


def test_scoring():
    spec = SESSIONS["sql_injection"]
    job = {"status": "succeeded", "result": {"status": "proposed", "message": "m", "stats": {
        "modelCalls": {"diagnose": 1, "edit": 2, "repair": 0}, "lessons": 2, "droppedLessons": 0, "edits": 1,
        "droppedEdits": 0, "repaired": 0}}}
    row = run_eval.score(spec, job, REVIEW)
    assert row["outcome"] == "proposed" and row["templateValid"] is True and row["leaks"] == []
    assert row["jsonFirstTry"] == {"diagnose": True, "edit": False}
    assert row["mistakesCovered"]["M1"] == ["L1"] and "L2" in row["mistakesCovered"]["M2"]
    assert row["addedChars"] == len("When a crawl stalls, test one parameter with one thread.\n")


def test_leaks_are_found_in_added_text_note_and_meta():
    spec = SESSIONS["sql_injection"]
    leaky = json.loads(json.dumps(REVIEW))
    leaky["parts"][0]["after"] += "Then probe store.example.org.\n"
    leaky["note"] = "seen at 198.51.100.40"
    assert set(run_eval.leaks(spec, leaky)) == {"store.example.org", "198.51.100.40"}
    broken = json.loads(json.dumps(REVIEW))
    broken["parts"][0]["after"] = "Use the level.\n"
    assert run_eval.template_problems(broken)



def test_a_leak_is_a_whole_identifier_not_part_of_a_word():
    # The planted parameter `lang` must count where the text names it, but not inside
    # "language": the same boundaries as Skiller's own Identifiers.find (found by the first
    # real-model run, 2026-10-11, where the substring match could not tell them apart).
    spec = SESSIONS["xss"]
    assert "lang" in spec["plantedIdentifiers"]

    def leaked(text):
        review = json.loads(json.dumps(REVIEW))
        review["parts"][0]["after"] += text + "\n"
        return "lang" in run_eval.leaks(spec, review)

    for clean in ("Check the page language before encoding.", "Use slang-free payload names.",
                  "A lang-specific filter", "langs are not relevant"):
        assert not leaked(clean), clean
    for leaky in ("Inject into the lang parameter.", "Try ?lang=<payload> first.", "the `lang` value",
                  "Probe LANG and q together.", "params: q, lang."):
        assert leaked(leaky), leaky

class _FakeRedAmon(http.server.BaseHTTPRequestHandler):
    calls: list = []
    job_polls = 0

    def log_message(self, *args):
        pass

    def _answer(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _record(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        type(self).calls.append({"method": self.command, "path": self.path, "body": body,
                                 "key": self.headers.get("X-Internal-Key"), "cookie": self.headers.get("Cookie")})

    def do_POST(self):
        self._record()
        if self.path == "/api/users/u1/attack-skills":
            return self._answer(201, {"id": "cevalskill0000000000000001", "name": self.headers.get("X-Name") or "x"})
        if self.path.startswith("/api/conversations/by-session/"):
            return self._answer(201, {"count": 3, "conversationId": "conv-1"})
        if self.path == "/api/plugins/skiller/actions/analyze_session":
            return self._answer(202, {"jobId": "job-1"})
        return self._answer(404, {})

    def do_GET(self):
        self._record()
        if self.path == "/api/plugins/jobs/job-1":
            type(self).job_polls += 1
            if type(self).job_polls < 2:
                return self._answer(200, {"status": "running", "progress": {"message": "Reading the session",
                                                                            "fields": {"Stage": "1 of 3"}}})
            return self._answer(200, {"status": "succeeded", "result": {
                "status": "proposed", "reviewProposal": {"extensionPoint": "skill.text", "changeSetId": "cs-1"},
                "stats": {"modelCalls": {"diagnose": 1, "edit": 1, "repair": 0}, "lessons": 2, "edits": 1}}})
        if self.path == "/api/plugins/overrides/change-sets/cs-1":
            return self._answer(200, REVIEW)
        return self._answer(404, {})

    def do_DELETE(self):
        self._record()
        return self._answer(200, {"ok": True})


def test_one_run_against_a_fake_redamon(monkeypatch):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeRedAmon)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(run_eval.time, "sleep", lambda s: None)
    try:
        api = run_eval.Api(f"http://127.0.0.1:{server.server_address[1]}", cookie="tok", internal_key="ik")
        logs = []
        row = run_eval.run_one(api, SESSIONS["xss"], project_id="p1", user_id="u1", keep=False, log=logs.append)
    finally:
        server.shutdown()
    calls = _FakeRedAmon.calls
    seed = calls[0]
    assert seed["path"].startswith("/api/conversations/by-session/session_eval_xss_") and seed["key"] == "ik"
    assert seed["cookie"] is None and seed["body"]["projectId"] == "p1"
    action = calls[1]
    assert action["key"] is None and action["cookie"] == "redamon-auth=tok"
    assert action["body"]["context"]["sessionId"] == seed["path"].split("/")[4]
    deletes = [c["path"] for c in calls if c["method"] == "DELETE"]
    assert deletes == ["/api/plugins/overrides/change-sets/cs-1", "/api/conversations/conv-1"]
    assert row["outcome"] == "proposed" and row["leaks"] == [] and row["templateValid"] is True
    assert any("Reading the session" in line for line in logs)


def test_a_user_skill_run_creates_the_skill_picks_it_and_cleans_up(monkeypatch):
    _FakeRedAmon.calls, _FakeRedAmon.job_polls = [], 0
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeRedAmon)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(run_eval.time, "sleep", lambda s: None)
    try:
        api = run_eval.Api(f"http://127.0.0.1:{server.server_address[1]}", cookie="tok", internal_key="ik")
        run_eval.run_one(api, SESSIONS["user_skill"], project_id="p1", user_id="u1", keep=False, log=lambda m: None)
    finally:
        server.shutdown()
    calls = _FakeRedAmon.calls
    create, seed, action = calls[0], calls[1], calls[2]
    assert create["path"] == "/api/users/u1/attack-skills" and create["cookie"] == "redamon-auth=tok"
    assert create["body"]["content"] == SESSIONS["user_skill"]["userSkill"]["content"]
    seeded = json.dumps(seed["body"])
    assert run_eval.USER_SKILL_PLACEHOLDER not in seeded and "user_skill:cevalskill0000000000000001" in seeded
    assert action["body"]["params"] == {"pick": {"kind": "session.skill", "ref": "user_skill:cevalskill0000000000000001"}}
    deletes = [c["path"] for c in calls if c["method"] == "DELETE"]
    assert deletes[-1] == "/api/users/u1/attack-skills/cevalskill0000000000000001"


def test_the_user_skill_session_records_the_text_it_names():
    spec = SESSIONS["user_skill"]
    pick = skill_pick.choose(TranscriptView.from_transcript(spec["transcript"]))
    assert pick["kind"] == "user" and pick["contentSha256"] == skill_pick.content_sha16(spec["userSkill"]["content"])
