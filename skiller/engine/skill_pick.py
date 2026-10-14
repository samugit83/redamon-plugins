"""Which skill a session used: a built-in one, or one of the person's own (TD-27 step 1, RV-15).

A turn counts for a skill only when its text was in that turn's prompt. Sessions recorded since
2026-10-09 say so for built-in skills: `phase_update.skill_workflow` is `{skillId, revision,
changeSetId}` when the think step rendered a built-in playbook and null when it rendered none.
Since platform 1.1.0 the agent records a user skill it rendered too: `{skillId:
"user_skill:<id>", kind: "user", contentSha256}`.

Without such a record the skill label (`attack_path_type`) is used, with a warning:
- a built-in label outside post-exploitation (the agent never renders a playbook there), and
  only on a turn whose row has no `skill_workflow` key at all: a null record means the
  playbook was not in the prompt (the skill disabled, forbidden by the RoE, missing its tool,
  a classification fallback to `cve_exploit`);
- a user-skill label in every phase, but only in a session that holds NO user record: such a
  session was recorded before the agent recorded user skills. In a later session a user label
  without a record means the skill was off and nothing was rendered.
This is the same rule as RedAmon's own skill picker (webapp lib/plugins/sessionSkills.ts).
Fireteam members record no `skill_workflow`, so their turns never count (their work still
reaches the digest).
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections import Counter

from redamon_plugin_sdk import PluginError

# agentic/state.py KNOWN_ATTACK_PATHS; the Gateway confirms each against the agent's registry.
BUILTIN_SKILLS = (
    "cve_exploit", "brute_force_credential_guess", "phishing_social_engineering", "denial_of_service",
    "sql_injection", "xss", "ssrf", "rce", "path_traversal", "access_control", "http_request_smuggling",
    "xxe", "crypto_attack", "deserialization",
)
USER_PREFIX = "user_skill:"
BUILTIN_POINT = "skill.text"
USER_POINT = "user_skill.text"

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
USER_LABEL_WARNING = ("This session predates RedAmon's record of which user skill was in the prompt: turns were "
                      "counted by the skill label, and the skill's text may not have been in the prompt for every "
                      "labelled turn.")
SHA_CHARS = 16


def is_user_ref(ref) -> bool:
    return isinstance(ref, str) and ref.startswith(USER_PREFIX) and len(ref) > len(USER_PREFIX)


def point_of(ref: str) -> str:
    return USER_POINT if is_user_ref(ref) else BUILTIN_POINT


def target_of(ref: str) -> str:
    """The Gateway's target group: the built-in id, or the user skill's own id."""
    return ref[len(USER_PREFIX):] if is_user_ref(ref) else ref


def display_name(ref: str) -> str:
    if is_user_ref(ref):
        return "your skill"
    return DISPLAY_NAMES.get(ref, ref)


def _the_skill(ref: str) -> str:
    """How a message names the skill: a built-in one by name and id, the person's own as theirs
    (its id says nothing to them, and the picker already showed its name)."""
    return "that skill of yours" if is_user_ref(ref) else f"the {display_name(ref)} skill ({ref})"


def content_sha16(text: str) -> str:
    """The prefix the agent records for a user skill's text (agentic skill_part_seam.py)."""
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:SHA_CHARS]


def has_user_record(view) -> bool:
    return any(t.workflow_known and isinstance(t.workflow, dict) and t.workflow.get("kind") == "user"
               for t in view.turns)


def turn_skill(turn, user_records: bool = False) -> tuple:
    """(skill ref or None, source) of one turn; `user_records`: the session holds a user record."""
    wf = turn.workflow if turn.workflow_known else None
    if isinstance(wf, dict):
        skill = wf.get("skillId")
        if skill in BUILTIN_SKILLS:
            return skill, "workflow"
        if wf.get("kind") == "user" and is_user_ref(skill):
            return skill, "workflow"
        return None, "workflow"
    if is_user_ref(turn.label):
        return (None, "label") if user_records else (turn.label, "label")
    if not turn.workflow_known and turn.label in BUILTIN_SKILLS and turn.phase != "post_exploitation":
        return turn.label, "label"
    return None, "workflow" if turn.workflow_known else "label"


def counted_turns(view, ref: str) -> list:
    user_records = has_user_record(view)
    return [t for t in view.turns if turn_skill(t, user_records)[0] == ref]


def _what_it_used(view) -> str:
    labels = {t.label for t in view.turns}
    if any(is_user_ref(label) for label in labels):
        return "its user skill was never in the agent's instructions"
    if any(label.endswith("-unclassified") for label in labels):
        return "it used an unclassified request"
    if any(label in BUILTIN_SKILLS for label in labels):
        return "the built-in playbook was never in the agent's instructions"
    return "no skill was chosen for it"


def choose(view, requested=None) -> dict:
    """The skill to improve: `requested` (a built-in id or `user_skill:<id>`, the person's pick)
    when the session used it, else the one with the most turns, ties to the latest."""
    if view.phase_rows and not view.phase_rows_with_label:
        raise PluginError("session_format_unsupported", "This session was recorded in a format Skiller cannot read.")
    if not view.turns:
        raise PluginError("session_too_short", "The agent took no turn in this session, so there is nothing to learn from.")
    if not view.phase_rows:
        raise PluginError("session_format_unsupported", "This session was recorded in a format Skiller cannot read.")

    user_records = has_user_record(view)
    counts: Counter = Counter()
    last_seen: dict = {}
    sources: dict = {}
    for t in view.turns:
        skill, source = turn_skill(t, user_records)
        if skill:
            counts[skill] += 1
            last_seen[skill] = t.seq
            sources.setdefault(skill, set()).add(source)

    if requested is not None:
        known = requested in BUILTIN_SKILLS or is_user_ref(requested)
        if not known or not counts.get(requested):
            raise PluginError("skill_not_used", f"This session did not use {_the_skill(requested)}, so Skiller "
                                                "cannot improve it from this session.")
        chosen = requested
    elif not counts:
        raise PluginError("no_skill", f"This session did not use a skill Skiller can work on ({_what_it_used(view)}), "
                                      "so there is nothing for Skiller to improve.")
    else:
        chosen = max(counts, key=lambda s: (counts[s], last_seen[s]))

    user = is_user_ref(chosen)
    turns = [t for t in view.turns if turn_skill(t, user_records)[0] == chosen]
    tool_calls = sum(len(t.calls) for t in turns)
    if len(turns) < MIN_ITERATIONS or tool_calls == 0:
        raise PluginError("session_too_short",
                          f"Only {len(turns)} turn(s) with {tool_calls} tool call(s) used {_the_skill(chosen)} "
                          f"in this session; Skiller needs at least {MIN_ITERATIONS} turns with a tool call to learn "
                          "from.")

    records = [t.workflow for t in turns if t.workflow_known and isinstance(t.workflow, dict)]
    revisions = [r.get("revision") for r in records]
    revisions = [r for r in revisions if isinstance(r, int) and not isinstance(r, bool)]
    change_sets = [r.get("changeSetId") for r in records]
    shas = [r.get("contentSha256") for r in records if isinstance(r.get("contentSha256"), str)]
    source = "workflow" if sources[chosen] == {"workflow"} else ("label" if sources[chosen] == {"label"} else "mixed")
    warnings = [USER_LABEL_WARNING if user else LABEL_WARNING] if "label" in sources[chosen] else []
    return {
        "skillId": chosen,
        "kind": "user" if user else "builtin",
        "point": point_of(chosen),
        "target": target_of(chosen),
        "name": display_name(chosen),
        "iterations": len(turns),
        "toolCalls": tool_calls,
        "source": source,
        # The version the session last ran; a session can span an approval. A user skill has no
        # revisions (a new version is a new skill): the hash of the text it last ran stands in.
        "revision": None if user else (revisions[-1] if revisions else None),
        "revisions": {} if user else dict(Counter(revisions)),
        "changeSetId": None if user else (change_sets[-1] if change_sets else None),
        "contentSha256": shas[-1] if user and shas else None,
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


def predates_text(pick: dict, current_text: str) -> bool:
    """For a user skill: whether the session last ran another text than the skill holds now.
    Unknown (a session recorded before the agent hashed it) is not a difference."""
    sha = pick.get("contentSha256")
    return isinstance(sha, str) and sha != content_sha16(current_text)
