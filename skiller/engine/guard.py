"""The code backstop before a person reviews a version (TD-27 step 5, RV-32).

Checked on the text an edit adds (never the factory wording around it):
- `identifier`: any value specific to the session (identifiers.py);
- `tool`: an agent tool name that neither the playbook nor the allowed list names;
- `risk_send`: an instruction to send, upload, post or exfiltrate data somewhere;
- `risk_location`: a URL, host or address the playbook does not already name, even one the
  session never saw, because an injection can name a place to be found later;
- `risk_scope`: wording that ignores, overrides, relaxes or reinterprets the scope guardrail,
  the Rules of Engagement, authorization or a confirmation step;
- `heading`: the reserved markers and section headings the agent's own validator refuses
  (agentic/prompts/skill_parts.py `reserved_errors`, mirrored), plus heading lines naming the
  other sections the agent places after the playbook;
- `hidden`: invisible or control characters a reviewer could not see;
- `safety_removed`: a replacement that drops "do not", scope or confirmation wording.
The target's scope is enforced per tool call only by prompt text, so these rules are what
stands between an injected instruction and the review (01_current_state.md §7.2).
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from dataclasses import dataclass

from . import identifiers as ids_mod

# -- the agent's reserved strings (skill_parts.py), matched the way it matches them ------------
RESERVED_MARKERS = (
    (re.compile(r"<<<\s*untrusted_", re.IGNORECASE), "<<<UNTRUSTED_"),
    (re.compile(r"<<<\s*end_untrusted_", re.IGNORECASE), "<<<END_UNTRUSTED_"),
    (re.compile(r"<<\s*redamon_cache_[a-z0-9_]*\s*>>", re.IGNORECASE), "<<REDAMON_CACHE_"),
)
RESERVED_HEADINGS = (
    "SCOPE GUARDRAIL", "RULES OF ENGAGEMENT (MANDATORY)", "RoE TIMING WARNINGS", "UNPRODUCTIVE STREAK DETECTED",
    "PRODUCTIVITY HINT", "Prior Productivity Claim Was Downgraded", "USER GUIDANCE (IMPORTANT)", "SKILL FIT CHECK",
)
_ROE_LINE = re.compile(r"^(?P<lead>[\s#>*_=|-]*)(?P<words>rules[\W_]+of[\W_]+engagement)\b", re.IGNORECASE)
_SETEXT = re.compile(r"^\s*(=+|-+)\s*$")
_LOOKALIKES = str.maketrans(dict(zip(
    "АВСЕНІЈКМОРЅТХУаеһіјорсѕхуԁԛԝΑΒΕΖΗΙΚΜΝΟΡΤΥΧαικνορτυχ",
    "ABCEHIJKMOPSTXYaehijopcsxydqwABEZHIKMNOPTYXaikvoptux",
)))
# Sections the agent places after the playbook that its validator does not reserve (the phase
# 07 code study): a heading line naming one would impersonate it.
_EXTRA_SECTIONS = ("deep think", "productivity audit", "response uniformity anomaly", "previous tool output",
                   "plan wave outputs", "per step response classification", "user guidance", "scope guardrail",
                   "skill fit check", "rules of engagement", "current state", "your task", "important rules")
_HIDDEN = {"Cf", "Co", "Cs", "Zl", "Zp"}

# -- risk patterns -------------------------------------------------------------------------------
_SEND_VERBS = r"(?:send|sends|sending|upload|uploads|post|posts|forward|transmit|leak|deliver|share|e-?mail|mail|beacon|sync)"
_LOOT = (r"(?:findings?|results?|data|dumps?|credentials?|creds|secrets?|tokens?|keys?|outputs?|loot|reports?|"
         r"cookies?|passwords?|hashes|files?|everything|information|evidence|flags?|tables?)")
# Where data would go: off the target, to a place someone else reads.
_ELSEWHERE = (r"(?:url|urls|host|hosts|server|address|domain|webhook|collector|listener|bucket|storage|channel|inbox|"
              r"e-?mail|you (?:discover|find|control)|external|remote|outside|attacker|your own|controlled|third[- ]party|"
              r"<host>|<url>|https?://|dns|pastebin|paste site)")
_RISK_SEND = [
    re.compile(r"\bexfiltrat\w*", re.I),
    re.compile(rf"\b{_SEND_VERBS}\b[^.\n]{{0,80}}?\b{_LOOT}\b[^.\n]{{0,80}}?\b(?:to|into|onto|at|via|over)\b[^.\n]{{0,60}}?{_ELSEWHERE}", re.I),
]
_WEAKEN = (r"(?:ignore|ignoring|disregard|override|bypass|relax|loosen|skip|skipping|reinterpret|circumvent|"
           r"work around|set aside|forget|disable|turn off|waive)")
# The scope itself, never "out-of-scope hosts" or "scope findings".
_SCOPE = r"(?<![-\w])(?<!of )scope\b(?![- ](?:hosts?|targets?|findings?|results?|items?|domains?|assets?))"
_GUARDS = (rf"(?:{_SCOPE}|guardrail|rules of engagement|\broe\b|engagement authori[sz]ation|confirmation|approval|"
           r"human review|safety (?:checks?|rules?|limits?))")
_RISK_SCOPE = [
    re.compile(rf"\b{_WEAKEN}\b[^.\n]{{0,60}}?{_GUARDS}", re.I),
    re.compile(r"\b(?:you may|you can|it is (?:ok|okay|fine|acceptable|allowed)|feel free|allowed to|permitted to|go ahead)"
               r"\b[^.\n]{0,60}\b(?:outside|beyond|out of)\b[^.\n]{0,20}\bscope\b", re.I),
    re.compile(r"\b(?:even if|even when|regardless of|whatever)\b[^.\n]{0,40}(?:\bscope\b|rules of engagement|\broe\b|"
               r"\bforbid\w*|\bprohibit\w*|\bnot (?:authori[sz]ed|allowed|permitted))", re.I),
    re.compile(r"\b(?:without|no need to|don't|do not|never)\b[^.\n]{0,20}\b(?:ask\w*|wait\w* for|request\w*|seek\w*)\b"
               r"[^.\n]{0,30}\b(?:confirmation|approval|permission)\b", re.I),
    re.compile(r"\bout[- ]of[- ]scope\b[^.\n]{0,40}\b(?:is|are)\b[^.\n]{0,10}\b(?:fine|allowed|ok|okay|acceptable)\b", re.I),
]
_SAFETY_CUES = re.compile(r"\b(?:do not|don't|never|must not|scope|rules of engagement|authori[sz]\w*|lockout|"
                          r"rate limit\w*|confirm\w*|approv\w*)\b", re.I)
# Agent tool names (agentic/project_settings.py TOOL_PHASE_MAP and the tool-name families).
_TOOL_RE = re.compile(r"\b((?:execute|kali|metasploit|msf|query|web|proxy|fs|job)_[a-z0-9_]+|cve_intel|shodan|"
                      r"google_dork|tradecraft_lookup)\b")
_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]{1,15}://[^\s\"'<>`)\]]+", re.I)
_IP_CANDIDATE = re.compile(r"(?<![\w.:])(?:\d{1,3}(?:\.\d{1,3}){3}|(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4})(?![\w:]|\.\d)", re.I)


@dataclass
class Violation:
    rule: str
    detail: str

    def problem(self) -> str:
        return {
            "identifier": f"session-specific value {self.detail}; use a neutral placeholder such as <host>, <param> or <path>",
            "tool": f"tool {self.detail} is not in the playbook or the allowed tool list",
            "risk_send": f"the text tells the agent to send data somewhere ({self.detail})",
            "risk_location": f"the text names a location the playbook does not name ({self.detail})",
            "risk_scope": f"the text weakens the scope, the Rules of Engagement or a confirmation step ({self.detail})",
            "heading": f"the text imitates a prompt section the agent owns ({self.detail})",
            "hidden": f"the text holds invisible characters ({self.detail})",
            "safety_removed": f"the replacement drops safety wording ({self.detail})",
        }.get(self.rule, self.detail)


def _as_read(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf").translate(_LOOKALIKES)


def _letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.casefold())


def reserved(text: str) -> list:
    """skill_parts.reserved_errors, as the agent applies it at propose and at use."""
    out = []
    read = _as_read(text)
    for pattern, marker in RESERVED_MARKERS:
        if pattern.search(read):
            out.append(f"the reserved marker {marker}")
    letters = _letters(read)
    out += [f"the heading {h!r}" for h in RESERVED_HEADINGS if _letters(h) in letters]
    lines = read.splitlines()
    for i, line in enumerate(lines):
        m = _ROE_LINE.match(line)
        underlined = i + 1 < len(lines) and _SETEXT.match(lines[i + 1])
        if m and (re.search(r"[#*_=|]", m.group("lead")) or m.group("words").isupper() or underlined):
            out.append("a Rules of Engagement heading")
            break
    for line in lines:
        stripped = line.strip()
        decorated = stripped.startswith(("#", ">", "**", "__", "==", "|"))
        shouting = len(re.sub(r"[^A-Za-z]", "", stripped)) >= 6 and stripped.upper() == stripped
        if decorated or shouting:
            words = " ".join(re.findall(r"[a-z]+", stripped.casefold()))
            for section in _EXTRA_SECTIONS:
                if section in words:
                    out.append(f"a {section!r} heading")
                    break
    return list(dict.fromkeys(out))


def _hidden(text: str, known: str) -> list:
    def chars(t):
        return {ch for ch in t if unicodedata.category(ch) in _HIDDEN or (unicodedata.category(ch) == "Cc" and ch not in "\t\n\r")}
    return [f"U+{ord(ch):04X}" for ch in sorted(chars(text) - chars(known))]


def _generic_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.strip("[]"))
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified or str(ip) == "169.254.169.254"


def _locations(text: str, known: str) -> list:
    known_l = known.lower()
    found = []
    for url in _URL_RE.findall(text):
        if url.lower().rstrip(".,;:") not in known_l:
            found.append(url)
    plain = _URL_RE.sub(" ", text)
    for m in ids_mod._HOST_RE.finditer(plain):
        host = m.group(1)
        if plain[m.end():m.end() + 1] == "(":
            continue  # a call in a code example (requests.get(...)), not a host
        if ids_mod._host_ok(host) and host.lower() not in ids_mod.GENERIC and host.lower() not in known_l:
            found.append(host)
    for candidate in _IP_CANDIDATE.findall(text):
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if not _generic_ip(candidate) and candidate.lower() not in known_l:
            found.append(candidate)
    return list(dict.fromkeys(found))


def risks(text: str, known: str = "") -> list:
    out = []
    for pattern in _RISK_SEND:
        m = pattern.search(text)
        if m:
            out.append(Violation("risk_send", m.group(0)[:80]))
            break
    for location in _locations(text, known):
        out.append(Violation("risk_location", location))
    for pattern in _RISK_SCOPE:
        m = pattern.search(text)
        if m:
            out.append(Violation("risk_scope", m.group(0)[:80]))
            break
    return out


def check_added(text: str, *, ids, known_text: str, tool_names) -> list:
    """Every violation in text an edit adds. `known_text` is the factory and current wording."""
    out = [Violation("identifier", v) for v in ids.find(text)]
    known_lower = known_text.lower()
    allowed = {t.lower() for t in tool_names}
    for tool in dict.fromkeys(_TOOL_RE.findall(text)):
        if tool.lower() not in allowed and not re.search(rf"\b{re.escape(tool)}\b", known_lower):
            out.append(Violation("tool", tool))
    out += risks(text, known_text)
    out += [Violation("heading", r) for r in reserved(text) if r not in reserved(known_text)]
    hidden = _hidden(text, known_text)
    if hidden:
        out.append(Violation("hidden", ", ".join(hidden[:5])))
    return out


def check_removed(removed: str, replacement: str) -> list:
    """A replacement that drops safety wording ("do not", scope, confirmation) without keeping it."""
    lost = {m.lower() for m in _SAFETY_CUES.findall(removed)} - {m.lower() for m in _SAFETY_CUES.findall(replacement)}
    return [Violation("safety_removed", ", ".join(sorted(lost)))] if lost else []


def lesson_ok(lesson: str, ids) -> bool:
    """A lesson may reach the edit stage only without identifiers, a place to send data, or a
    weakened guard."""
    return not ids.find(lesson) and not risks(lesson)


def redact(text: str, ids) -> str:
    """Free text from the model (summary, changelog, reasons) with the session's identifiers removed."""
    out = text or ""
    for value in sorted(ids.find(out), key=len, reverse=True):
        out = re.sub(rf"(?<![A-Za-z0-9_-]){re.escape(value)}(?![A-Za-z0-9_-]|\.[A-Za-z0-9])", "<redacted>", out,
                     flags=re.I)
    return out
