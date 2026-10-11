"""Which built-in skill a session used (TD-27 step 1, RV-15).

A turn counts for a skill only when its playbook was in that turn's prompt. Sessions recorded
since 2026-10-09 say so: `phase_update.skill_workflow` is `{skillId, revision, changeSetId}`
when the think step rendered a built-in playbook and null when it rendered none. Older
sessions only carry the skill label (`attack_path_type`), which stays set when the playbook
was not rendered (the skill disabled, forbidden by the RoE, missing its tool, a classification
fallback to `cve_exploit`); the label is used then, with a warning, and never for
post-exploitation turns, where the agent never renders a playbook. Fireteam members record no
`skill_workflow`, so their turns never count (their work still reaches the digest).
"""

from __future__ import annotations

import datetime as dt
from collections import Counter

from redamon_plugin_sdk import PluginError

# agentic/state.py KNOWN_ATTACK_PATHS; the Gateway confirms each against the agent's registry.
BUILTIN_SKILLS = (
    "cve_exploit", "brute_force_credential_guess", "phishing_social_engineering", "denial_of_service",
    "sql_injection", "xss", "ssrf", "rce", "path_traversal", "access_control", "http_request_smuggling",
    "xxe", "crypto_attack", "deserialization",
)

# The chat drawer's labels (webapp KNOWN_ATTACK_PATH_CONFIG), for messages before the
# Gateway's displayName is known.
DISPLAY_NAMES = {
    "cve_exploit": "CVE (MSF)",
    "brute_force_credential_guess": "Credential Testing",
    "phishing_social_engineering": "Social Engineering Simulation",
    "denial_of_service": "Availability Testing",
    "sql_injection": "SQL Injection",
    "xss": "Cross-Site Scripting",
    "ssrf": "Server-Side Request Forgery",
    "rce": "Remote Code Execution",
    "deserialization": "Insecure Deserialization",
    "path_traversal": "Path Traversal / LFI / RFI",
    "access_control": "Broken Access Control",
    "http_request_smuggling": "HTTP Request Smuggling / Desync",
    "xxe": "XML External Entity",
    "crypto_attack": "Cryptographic Attacks",
}

MIN_ITERATIONS = 3
LABEL_WARNING = ("This session predates RedAmon's record of which playbook was in the prompt: turns were "
                 "counted by the skill label, and the playbook may not have been in the prompt for every "
                 "labelled turn.")


def display_name(skill_id: str) -> str:
    return DISPLAY_NAMES.get(skill_id, skill_id)


def turn_skill(turn) -> tuple:
    """(skill id or None, source) of one turn."""
    if turn.workflow_known:
        wf = turn.workflow or {}
        skill = wf.get("skillId")
        return (skill if skill in BUILTIN_SKILLS else None), "workflow"
    if turn.label in BUILTIN_SKILLS and turn.phase != "post_exploitation":
        return turn.label, "label"
    return None, "label"


def counted_turns(view, skill_id: str) -> list:
    return [t for t in view.turns if turn_skill(t)[0] == skill_id]


def _what_it_used(view) -> str:
    labels = {t.label for t in view.turns}
    if any(label.startswith("user_skill:") for label in labels):
        return "it used a user skill"
    if any(label.endswith("-unclassified") for label in labels):
        return "it used an unclassified request"
    if any(label in BUILTIN_SKILLS for label in labels):
        return "the built-in playbook was never in the agent's instructions"
    return "no skill was chosen for it"


def choose(view, requested=None) -> dict:
    if view.phase_rows and not view.phase_rows_with_label:
        raise PluginError("session_format_unsupported", "This session was recorded in a format Skiller cannot read.")
    if not view.turns:
        raise PluginError("session_too_short", "The agent took no turn in this session, so there is nothing to learn from.")
    if not view.phase_rows:
        raise PluginError("session_format_unsupported", "This session was recorded in a format Skiller cannot read.")

    counts: Counter = Counter()
    last_seen: dict = {}
    sources: dict = {}
    for t in view.turns:
        skill, source = turn_skill(t)
        if skill:
            counts[skill] += 1
            last_seen[skill] = t.seq
            sources.setdefault(skill, set()).add(source)

    if requested is not None:
        if requested not in BUILTIN_SKILLS or not counts.get(requested):
            raise PluginError("skill_not_used", f"This session did not use the {display_name(requested)} skill "
                                                f"({requested}), so Skiller cannot improve it from this session.")
        chosen = requested
    elif not counts:
        raise PluginError("no_builtin_skill", f"This session did not use a built-in skill ({_what_it_used(view)}), "
                                              "so there is nothing for Skiller to improve.")
    else:
        chosen = max(counts, key=lambda s: (counts[s], last_seen[s]))

    turns = counted_turns(view, chosen)
    tool_calls = sum(len(t.calls) for t in turns)
    if len(turns) < MIN_ITERATIONS or tool_calls == 0:
        raise PluginError("session_too_short",
                          f"Only {len(turns)} turn(s) with {tool_calls} tool call(s) used the {display_name(chosen)} "
                          f"skill ({chosen}) in this session; Skiller needs at least {MIN_ITERATIONS} turns with "
                          "a tool call to learn from.")

    revisions = [t.workflow.get("revision") for t in turns if t.workflow_known and t.workflow]
    revisions = [r for r in revisions if isinstance(r, int) and not isinstance(r, bool)]
    change_sets = [t.workflow.get("changeSetId") for t in turns if t.workflow_known and t.workflow]
    source = "workflow" if sources[chosen] == {"workflow"} else ("label" if sources[chosen] == {"label"} else "mixed")
    warnings = [LABEL_WARNING] if "label" in sources[chosen] else []
    return {
        "skillId": chosen,
        "name": display_name(chosen),
        "iterations": len(turns),
        "toolCalls": tool_calls,
        "source": source,
        # The version the session last ran; a session can span an approval.
        "revision": revisions[-1] if revisions else None,
        "revisions": dict(Counter(revisions)),
        "changeSetId": change_sets[-1] if change_sets else None,
        "others": [{"skillId": s, "iterations": n} for s, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
                   if s != chosen],
        "startedAt": turns[0].at or None,
        "endedAt": turns[-1].last_at or turns[-1].at or None,
        "turns": [t.n for t in turns],
        "warnings": warnings,
    }


def _when(text):
    if not isinstance(text, str) or not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def predates_active(pick: dict, active) -> bool:
    """Whether the session ran another version than the one that applies now (RV-25)."""
    active_revision = active.get("revision") if isinstance(active, dict) else None
    if pick.get("revision") is not None:
        return pick["revision"] != (active_revision or 1)
    if not isinstance(active, dict):
        return False
    decided, ended = _when(active.get("decidedAt")), _when(pick.get("endedAt"))
    return bool(decided and ended and decided > ended)
