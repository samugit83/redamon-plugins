"""Test doubles for plugin authors: FakeSdk, ScriptedLlm and make_context.

FakeSdk is the real `Sdk` class over an in-memory Gateway, so a plugin's calls go through
the same request building and answer mapping as in production, and the shared contract
cases (sdk/contract_cases.json) run against both. It applies the Gateway's main refusals
(permissions, owner, reserved collections, unknown parts, five pending proposals) but not
every quota: passing here is necessary, not sufficient.

The user's own skills (`user_skill.text`) are added with `FakeSdk.add_user_skill`. The fake
checks a proposed text with a subset of the agent's rules (empty, NUL, length, the reserved
headings and markers) and a skill's text for credentials with a subset of the Gateway's
detector, with the same messages; the real ones refuse more.

    from redamon_plugin_sdk.testing import FakeSdk, ScriptedLlm, make_context

    llm = ScriptedLlm(['{"lessons": []}'])
    ctx = make_context(session_id="s1", job_id="j1", llm=llm, transcript={...})
    result = await run(ctx, {})
    assert llm.prompts[0][0]["role"] == "system"
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import time
from typing import Any, Callable, Iterable, Optional

from .client import Sdk, check_order, check_where, js_length
from .context import Context
from .errors import PluginError

ALL_PERMISSIONS = ("session:read", "llm:invoke", "override:skill.text", "override:user_skill.text")
MAX_PENDING_CHANGE_SETS = 5
USER_SKILL_POINT = "user_skill.text"
USER_SKILL_PART = "body"
# MAX_CONTENT_SIZE of webapp src/lib/userSkills.ts, in UTF-16 code units.
USER_SKILL_MAX_CHARS = 51_200
_GROUP_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}\Z")
_VERSION_SUFFIX = re.compile(r"^(.*\S)\s+v(\d{1,6})\Z", re.IGNORECASE)
# agentic prompts/skill_parts.py RESERVED_HEADINGS and RESERVED_MARKERS.
_RESERVED_HEADINGS = (
    "SCOPE GUARDRAIL", "RULES OF ENGAGEMENT (MANDATORY)", "RoE TIMING WARNINGS",
    "UNPRODUCTIVE STREAK DETECTED", "PRODUCTIVITY HINT", "Prior Productivity Claim Was Downgraded",
    "USER GUIDANCE (IMPORTANT)", "SKILL FIT CHECK",
)
# agentic/prompts/skill_parts.py _MARKER_RES, on the raw text: the agent also folds look-alike
# characters first, so it may refuse more, never less.
_RESERVED_MARKERS = (
    (re.compile(r"<<<\s*untrusted_", re.IGNORECASE), "<<<UNTRUSTED_"),
    (re.compile(r"<<<\s*end_untrusted_", re.IGNORECASE), "<<<END_UNTRUSTED_"),
    (re.compile(r"<<\s*redamon_cache_[a-z0-9_]*\s*>>", re.IGNORECASE), "<<REDAMON_CACHE_"),
)
# webapp lib/plugins/redact.ts SECRET_SHAPES (hasSecretKeyShape): key and token formats only,
# so prose such as "Basic authentication" is not refused.
_CREDENTIAL_RES = (
    # "[ ]" keeps the key header itself out of this file, which repository scans look for.
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE[ ]KEY-----"),
    re.compile(r"(?<!\w)eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\."),
    re.compile(r"(?<!\w)(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"(?<!\w)sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?<!\w)AIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"(?<!\w)(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    re.compile(r"(?<!\w)glpat-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?<!\w)xox[abposr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?<!\w)hf_[A-Za-z0-9]{30,}\b"),
    re.compile(r"(?<!\w)(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"),
)
_CREDENTIALS_MESSAGE = ("This skill holds a key or token (a private key, a JWT or an API token), so it is never "
                        "given to a plugin. Remove it from the skill first.")
_COLLECTION_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,63}\Z")
_KEY_MAX_CHARS = 512
_VALUE_MAX_BYTES = 1_048_576
_TTL_MAX_SECONDS = 10 * 365 * 24 * 3600
_QUERY_LIMIT_MAX = 200
_ROLES = ("system", "user", "assistant")


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _has_nul(value: Any) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any("\x00" in k or _has_nul(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_nul(v) for v in value)
    return False


def _serialized_size(value: Any) -> int:
    """Bytes of the value's JSON as the Gateway counts them: compact, UTF-8, unescaped."""
    if _has_nul(value):
        raise PluginError("invalid_value", "the value contains a NUL character")
    try:
        text = json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise PluginError("invalid_value", "the value cannot be written as JSON") from None
    return len(text.encode("utf-8", "surrogatepass"))


def _jsonb_equal(stored: Any, wanted: Any) -> bool:
    """Equality as Postgres compares jsonb scalars: a bool never equals a number, 1 == 1.0."""
    if wanted is None:
        return stored is None
    if isinstance(wanted, bool) or isinstance(stored, bool):
        return isinstance(wanted, bool) and isinstance(stored, bool) and stored == wanted
    if isinstance(wanted, (int, float)):
        return isinstance(stored, (int, float)) and stored == wanted
    return isinstance(stored, str) and stored == wanted


def _matches(value: Any, where: Optional[dict]) -> bool:
    if not where:
        return True
    if not isinstance(value, dict):
        return False
    return all(name in value and _jsonb_equal(value[name], wanted) for name, wanted in where.items())


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + f".{int(ts * 1000) % 1000:03d}Z"


class ScriptedLlm:
    """Answers model calls from a queue and records every prompt.

    A queued `str` becomes `{content, model_used: "scripted", usage}`; a `dict` is returned
    as it is; a `PluginError` is raised. An empty queue raises `llm_script_exhausted`, so a
    plugin that makes one call more than the test expects fails loudly.
    """

    def __init__(self, responses: Iterable = ()):
        self._queue = list(responses)
        self.prompts: list = []
        self.calls: list = []

    def queue(self, *responses) -> None:
        self._queue.extend(responses)

    def __call__(self, messages: list, max_tokens=None, temperature=None) -> dict:
        self.prompts.append(copy.deepcopy(messages))
        self.calls.append({"messages": copy.deepcopy(messages), "max_tokens": max_tokens,
                           "temperature": temperature})
        if not self._queue:
            raise PluginError("llm_script_exhausted", "ScriptedLlm has no response left for this call.")
        nxt = self._queue.pop(0)
        if isinstance(nxt, PluginError):
            raise nxt
        if isinstance(nxt, dict):
            return copy.deepcopy(nxt)
        return {"content": str(nxt), "model_used": "scripted",
                "usage": {"input_tokens": 0, "output_tokens": 0, "estimated": True}}


class FakeGateway:
    """The Gateway's operations, in memory. Use it through FakeSdk."""

    def __init__(self, *, plugin_id: str, user_id: str, project_id: Optional[str],
                 session_id: Optional[str], job_id: Optional[str], transcript: Optional[dict],
                 llm: Optional[ScriptedLlm], registry: Optional[dict],
                 permissions: Optional[Iterable[str]], clock: Callable[[], float]):
        self.plugin_id = plugin_id
        self.user_id = user_id
        self.project_id = project_id
        self.session_id = session_id
        self.job_id = job_id
        self.transcript = transcript
        self.llm = llm if llm is not None else ScriptedLlm()
        self.registry = copy.deepcopy(registry or {})
        self.permissions = set(ALL_PERMISSIONS if permissions is None else permissions)
        self.clock = clock
        self.rows: dict = {}
        self.proposals: list = []
        self.active: dict = {}
        self.user_skills: dict = {}
        self.progress: list = []
        self._seq = 0
        self._change_sets = 0

    async def post(self, op: str, body: dict) -> dict:
        handler = getattr(self, "_" + op.replace("/", "_"), None)
        if handler is None:
            raise PluginError("not_found", f"No Gateway operation {op}.")
        return handler(_json_copy(body))

    # -- helpers ---------------------------------------------------------------

    def _need(self, permission: str) -> None:
        if permission not in self.permissions:
            raise PluginError("permission_denied", f"This plugin was not granted {permission}.")

    def _owner(self, body: dict) -> tuple:
        owner = body.get("owner")
        if owner == "user":
            return ("user", self.user_id)
        if owner == "project":
            if not self.project_id:
                raise PluginError("invalid_owner", "This action was not started from a project.")
            return ("project", self.project_id)
        raise PluginError("invalid_owner", "owner must be user or project")

    def _collection(self, body: dict) -> str:
        c = body.get("collection")
        if not isinstance(c, str) or not _COLLECTION_RE.match(c):
            raise PluginError("invalid_collection", f"invalid collection {c!r}")
        if c.startswith("__"):
            raise PluginError("reserved_collection", "collections starting with __ are reserved for the platform")
        return c

    def _key(self, body: dict) -> str:
        k = body.get("key")
        if not isinstance(k, str) or not k or js_length(k) > _KEY_MAX_CHARS or "\x00" in k:
            raise PluginError("invalid_key", f"a key is 1-{_KEY_MAX_CHARS} characters, without NUL")
        return k

    def _live(self, row: dict) -> bool:
        return row["expiresAt"] is None or row["expiresAt"] > self.clock()

    def _target(self, body: dict) -> tuple:
        point = body.get("point")
        if point not in ("skill.text", USER_SKILL_POINT):
            raise PluginError("invalid_target", f"unknown extension point {point!r}")
        self._need(f"override:{point}")
        group = body.get("targetGroup")
        if group not in self.registry:
            raise PluginError("invalid_target", f"unknown skill {group!r}")
        return point, group

    def _part_max(self, entry: dict) -> int:
        return int(entry.get("maxChars") or max(4000, 2 * len(entry["text"])))

    def _skill_max(self, group: str) -> int:
        reg = self.registry[group]
        total = sum(len(p["text"]) for p in reg["parts"].values())
        return int(reg.get("skillMaxChars") or int(total * 1.5))

    def _pending(self, scope: str, group: str) -> int:
        return sum(1 for p in self.proposals
                   if p["status"] == "proposed" and p["scope"] == scope and p["targetGroup"] == group)

    # -- operations ------------------------------------------------------------

    def _session_read(self, body: dict) -> dict:
        self._need("session:read")
        if not self.session_id:
            raise PluginError("no_session", "This action was not started from a session.")
        if self.transcript is not None:
            return copy.deepcopy(self.transcript)
        return {"meta": {"id": self.session_id, "running": False, "highWaterSeq": 0}, "events": [],
                "truncated": {"keptEvents": 0, "droppedEvents": 0}, "untrusted": True}

    def _llm_invoke(self, body: dict) -> dict:
        self._need("llm:invoke")
        messages = body.get("messages")
        if (not isinstance(messages, list) or not messages
                or not all(isinstance(m, dict) and m.get("role") in _ROLES
                           and isinstance(m.get("content"), str) for m in messages)):
            raise PluginError("invalid_messages",
                              "messages is a non-empty list of {role: system|user|assistant, content}")
        return self.llm(messages, body.get("max_tokens"), body.get("temperature"))

    def _data_get(self, body: dict) -> dict:
        ident = (self._owner(body), self._collection(body), self._key(body))
        row = self.rows.get(ident)
        if row is None or not self._live(row):
            return {"found": False}
        return {"found": True, "value": copy.deepcopy(row["value"])}

    def _data_put(self, body: dict) -> dict:
        ident = (self._owner(body), self._collection(body), self._key(body))
        value = body.get("value")
        size = _serialized_size(value)
        if size > _VALUE_MAX_BYTES:
            raise PluginError("value_too_large",
                              f"a value is limited to {_VALUE_MAX_BYTES} bytes of JSON, this one has {size}")
        ttl = body.get("ttlSeconds")
        if ttl is not None and (not isinstance(ttl, int) or isinstance(ttl, bool)
                                or not 1 <= ttl <= _TTL_MAX_SECONDS):
            raise PluginError("invalid_ttl", f"ttlSeconds is a whole number from 1 to {_TTL_MAX_SECONDS}")
        now = self.clock()
        self._seq += 1
        expires = now + ttl if ttl is not None else None
        self.rows[ident] = {"value": value, "updatedAt": now, "seq": self._seq, "expiresAt": expires}
        return {"sizeBytes": size, "expiresAt": _iso(expires) if expires is not None else None}

    def _selected(self, body: dict) -> tuple:
        owner, collection = self._owner(body), self._collection(body)
        prefix = body.get("prefix")
        if prefix is None:
            prefix = ""
        if not isinstance(prefix, str) or js_length(prefix) > _KEY_MAX_CHARS or "\x00" in prefix:
            raise PluginError("invalid_query", f"prefix must be text of at most {_KEY_MAX_CHARS} characters.")
        where = body.get("where")
        try:
            check_where(where)
        except PluginError as e:
            raise PluginError("invalid_query", e.message) from None
        rows = [(k, r) for (o, c, k), r in self.rows.items()
                if o == owner and c == collection and k.startswith(prefix) and self._live(r)
                and _matches(r["value"], where)]
        return owner, collection, prefix, where, rows

    @staticmethod
    def _sort_key(order: str, key: str, row: dict) -> tuple:
        # Ties broken by the row's insertion sequence, in the same direction (TD-28).
        return (row["updatedAt"], row["seq"]) if order.endswith("updated") else (key, row["seq"])

    def _data_query(self, body: dict) -> dict:
        owner, collection, prefix, where, rows = self._selected(body)
        order = body.get("order", "-updated")
        try:
            check_order(order)
        except PluginError as e:
            raise PluginError("invalid_query", e.message) from None
        limit = body.get("limit", 50)
        limit = max(1, min(_QUERY_LIMIT_MAX, int(limit) if isinstance(limit, (int, float)) else 50))
        descending = order.startswith("-")
        rows.sort(key=lambda kr: self._sort_key(order, *kr), reverse=descending)
        # The fake's own opaque cursor: the query's fingerprint plus the last sort key.
        fingerprint = hashlib.sha256(json.dumps([collection, list(owner), prefix, where, order],
                                                sort_keys=True).encode()).hexdigest()[:16]
        cursor = body.get("cursor")
        if cursor is not None:
            try:
                decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
                if decoded["q"] != fingerprint:
                    raise ValueError
                after = tuple(decoded["after"])
            except (ValueError, KeyError, TypeError, AttributeError):
                raise PluginError("invalid_cursor", "This cursor belongs to another query.") from None
            if descending:
                rows = [kr for kr in rows if self._sort_key(order, *kr) < after]
            else:
                rows = [kr for kr in rows if self._sort_key(order, *kr) > after]
        page, more = rows[:limit], len(rows) > limit
        nxt = None
        if more:
            last = self._sort_key(order, *page[-1])
            nxt = base64.urlsafe_b64encode(json.dumps({"q": fingerprint, "after": list(last)}).encode()).decode()
        return {"items": [{"key": k, "value": copy.deepcopy(r["value"]), "updatedAt": _iso(r["updatedAt"]),
                           "expiresAt": _iso(r["expiresAt"]) if r["expiresAt"] is not None else None}
                          for k, r in page],
                "next": nxt}

    def _data_count(self, body: dict) -> dict:
        return {"count": len(self._selected(body)[4])}

    def _data_delete(self, body: dict) -> dict:
        ident = (self._owner(body), self._collection(body), self._key(body))
        row = self.rows.pop(ident, None)
        return {"deleted": row is not None and self._live(row)}

    def _user_skill(self, group) -> dict:
        """A user skill this session used, owned by the token's user; the Gateway's refusals,
        in its order (webapp lib/plugins/userSkillOverrideOps.ts)."""
        invalid = PluginError("invalid_target", "No such skill in this session.")
        if not isinstance(group, str) or not _GROUP_RE.match(group):
            raise invalid
        if not self.session_id:
            raise PluginError("no_session", "This action was not started from a session.")
        skill = self.user_skills.get(group)
        if not skill or skill["owner"] != self.user_id or not skill["usedInSession"] or skill["quarantined"]:
            raise invalid
        # Everything a plugin is given of the skill: its name, description and text.
        given = "\n".join([skill["name"], skill.get("description") or "", skill["content"]])
        if any(r.search(given) for r in _CREDENTIAL_RES):
            raise PluginError("skill_contains_credentials", _CREDENTIALS_MESSAGE)
        return skill

    def _get_user_skill(self, body: dict) -> dict:
        self._need(f"override:{USER_SKILL_POINT}")
        skill = self._user_skill(body.get("targetGroup"))
        m = _VERSION_SUFFIX.match(skill["name"])
        root, n = (m.group(1), int(m.group(2))) if m else (skill["name"], None)
        content = skill["content"]
        return {"point": USER_SKILL_POINT, "targetGroup": skill["id"], "displayName": skill["name"],
                "description": skill["description"],
                "parts": [{"partId": USER_SKILL_PART, "text": content,
                           "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                           "chars": js_length(content), "maxChars": USER_SKILL_MAX_CHARS,
                           "kind": "plain", "placeholders": []}],
                "active": None, "pendingCount": self._pending("user", skill["id"]),
                "limits": {"skillMaxChars": USER_SKILL_MAX_CHARS, "parts": {USER_SKILL_PART: USER_SKILL_MAX_CHARS}},
                "lineage": {"rootName": root, "version": skill["version"] or n or 1}}

    @staticmethod
    def _text_errors(text: str, base: str) -> list:
        """A subset of the agent's /plugins/text/validate, with its messages."""
        errors = []
        if not text.strip():
            errors.append("value is empty")
        if "\x00" in text:
            errors.append("value contains a NUL character")
        if js_length(text) > USER_SKILL_MAX_CHARS:
            errors.append(f"value is {js_length(text)} characters; the limit is {USER_SKILL_MAX_CHARS}")

        def letters(t: str) -> str:
            return re.sub(r"[^a-z]", "", t.casefold())
        for pattern, marker in _RESERVED_MARKERS:
            if pattern.search(text) and not pattern.search(base):
                errors.append(f"value contains the reserved marker {marker!r}")
        for heading in _RESERVED_HEADINGS:
            if letters(heading) in letters(text) and letters(heading) not in letters(base):
                errors.append(f"value contains the heading of the {heading!r} prompt section")
        return errors

    def _propose_user_skill(self, body: dict) -> dict:
        self._need(f"override:{USER_SKILL_POINT}")
        if body.get("scope") != "user":
            raise PluginError("invalid_proposal", "A version of your own skill is proposed in the user scope only.")
        skill = self._user_skill(body.get("targetGroup"))
        if not isinstance(body.get("note"), str):
            raise PluginError("invalid_proposal", "a note is required")
        meta = body.get("meta")
        if meta is not None and not isinstance(meta, dict):
            raise PluginError("invalid_proposal", "meta must be an object")
        sent = body.get("parts")
        if not isinstance(sent, dict):
            raise PluginError("invalid_proposal", "parts must be an object of part id to text")
        if list(sent) != [USER_SKILL_PART]:
            raise PluginError("invalid_proposal", f"A user skill has exactly one part, {USER_SKILL_PART}; "
                                                  f"this proposal names {', '.join(sent) or 'none'}.")
        text = sent[USER_SKILL_PART]
        if not isinstance(text, str):
            raise PluginError("invalid_proposal", f"the text of {skill['id']}/{USER_SKILL_PART} must be a string")
        read = body.get("readSha256")
        if not isinstance(read, dict) or not isinstance(read.get(USER_SKILL_PART), str):
            raise PluginError("invalid_proposal", f"readSha256.{USER_SKILL_PART} is required: "
                                                  "the sha256 of the text this proposal was built on")
        if read[USER_SKILL_PART] != hashlib.sha256(skill["content"].encode("utf-8")).hexdigest():
            raise PluginError("factory_changed", f'The text of "{skill["name"]}" changed since this plugin read it: '
                                                 "read it again and redo the proposal.")
        if text == skill["content"]:
            raise PluginError("invalid_proposal", f'This proposal does not change "{skill["name"]}".')
        errors = self._text_errors(text, skill["content"])
        if errors:
            raise PluginError("invalid_proposal", "; ".join(f"{skill['id']}/{USER_SKILL_PART}: {e}" for e in errors))
        if self._pending("user", skill["id"]) >= MAX_PENDING_CHANGE_SETS:
            raise PluginError("too_many_pending", f"There are already {MAX_PENDING_CHANGE_SETS} proposals waiting "
                                                  f"for a decision on {skill['id']}")
        self._change_sets += 1
        cs = {"changeSetId": f"fake-cs-{self._change_sets}", "point": USER_SKILL_POINT, "targetGroup": skill["id"],
              "parts": {USER_SKILL_PART: text}, "note": body["note"], "meta": meta, "scope": "user",
              "status": "proposed", "fromRevision": None,
              "baseText": skill["content"], "sourceName": skill["name"]}
        self.proposals.append(cs)
        return {"changeSetId": cs["changeSetId"], "fromRevision": None, "proposedRevisionLabel": None}

    def _override_get(self, body: dict) -> dict:
        if body.get("point") == USER_SKILL_POINT:
            return self._get_user_skill(body)
        point, group = self._target(body)
        reg = self.registry[group]
        active = self.active.get(("project", group)) if self.project_id else None
        active = active or self.active.get(("user", group))
        parts = [{"partId": pid, "text": e["text"],
                  "sha256": hashlib.sha256(e["text"].encode("utf-8")).hexdigest(),
                  "chars": len(e["text"]), "maxChars": self._part_max(e),
                  "kind": e.get("kind", "plain"), "placeholders": list(e.get("placeholders", []))}
                 for pid, e in reg["parts"].items()]
        return {"point": point, "targetGroup": group, "displayName": reg.get("displayName", group),
                "parts": parts, "active": copy.deepcopy(active),
                "pendingCount": self._pending("user", group),
                "limits": {"skillMaxChars": self._skill_max(group),
                           "parts": {p["partId"]: p["maxChars"] for p in parts}}}

    def _override_propose(self, body: dict) -> dict:
        # The Gateway's order and rules (webapp lib/plugins/overrideOps.ts, TD-19).
        if body.get("point") == USER_SKILL_POINT:
            return self._propose_user_skill(body)
        point, group = self._target(body)
        reg = self.registry[group]
        scope = body.get("scope")
        if scope not in ("user", "project") or (scope == "project" and not self.project_id):
            raise PluginError("invalid_proposal", "scope is user, or project inside a project")
        if not isinstance(body.get("note"), str):
            raise PluginError("invalid_proposal", "a note is required")
        meta = body.get("meta")
        if meta is not None and not isinstance(meta, dict):
            raise PluginError("invalid_proposal", "meta must be an object")
        sent = body.get("parts")
        if not isinstance(sent, dict):
            raise PluginError("invalid_proposal", "parts must be an object of part id to text")
        if not sent:
            raise PluginError("invalid_proposal", "a proposal changes at least one part")
        for pid, text in sent.items():
            if not isinstance(text, str):
                raise PluginError("invalid_proposal", f"the text of {group}/{pid} must be a string")
        unknown = [f"unknown part {group}/{pid}" for pid in sent if pid not in reg["parts"]]
        if unknown:
            raise PluginError("invalid_proposal", "; ".join(unknown))
        self._check_read_hashes(body.get("readSha256"), reg, group)
        # A proposal is a whole version: a part not sent is factory text in it, and a part
        # sent unchanged from its factory text is not a change.
        parts = {pid: t for pid, t in sent.items() if t != reg["parts"][pid]["text"]}
        if not parts:
            raise PluginError("invalid_proposal",
                              f"This proposal changes no part of {group}: every text equals the factory text.")
        for pid, text in parts.items():
            if len(text) > self._part_max(reg["parts"][pid]):
                raise PluginError("invalid_proposal", f"{group}/{pid}: the text is too long")
        active = self.active.get((scope, group))
        # What override.get showed in this context; a proposal shown anything other than
        # its own scope's active version built on no revision of that scope.
        shown = (self.active.get(("project", group)) if self.project_id else None) or self.active.get(("user", group))
        merged = {pid: e["text"] for pid, e in reg["parts"].items()}
        merged.update(parts)
        if sum(len(t) for t in merged.values()) > self._skill_max(group):
            raise PluginError("invalid_proposal", f"the whole skill {group} would exceed {self._skill_max(group)} characters")
        if self._pending(scope, group) >= MAX_PENDING_CHANGE_SETS:
            raise PluginError("too_many_pending",
                              f"There are already {MAX_PENDING_CHANGE_SETS} proposals waiting for a decision on {group}")
        self._change_sets += 1
        from_revision = active["revision"] if active and shown is active else 1
        cs = {"changeSetId": f"fake-cs-{self._change_sets}", "point": point, "targetGroup": group,
              "parts": parts, "note": body["note"], "meta": meta, "scope": scope,
              "status": "proposed", "fromRevision": from_revision}
        self.proposals.append(cs)
        return {"changeSetId": cs["changeSetId"], "fromRevision": from_revision,
                "proposedRevisionLabel": f"v{from_revision + 1}"}

    @staticmethod
    def _check_read_hashes(read, reg: dict, group: str) -> None:
        if read is None:
            return
        if not isinstance(read, dict):
            raise PluginError("invalid_proposal", "readSha256 must be an object of part id to the sha256 read")
        changed = []
        for pid, sha in read.items():
            if not isinstance(sha, str):
                raise PluginError("invalid_proposal", f"readSha256 of {group}/{pid} must be a string")
            if pid not in reg["parts"]:
                raise PluginError("invalid_proposal", f"unknown part {group}/{pid}")
            if hashlib.sha256(reg["parts"][pid]["text"].encode("utf-8")).hexdigest() != sha:
                changed.append(f"{group}/{pid}")
        if changed:
            raise PluginError("factory_changed",
                              f"The factory text of {', '.join(changed)} changed since this plugin read it "
                              "(a RedAmon update): read it again and redo the proposal.")

    def _override_withdraw(self, body: dict) -> dict:
        # The permission is that of the set's own point, checked once the set is found.
        cs_id = body.get("changeSetId")
        for cs in self.proposals:
            if cs["changeSetId"] == cs_id and cs["status"] == "proposed":
                self._need(f"override:{cs['point']}")
                cs["status"] = "withdrawn"
                return {"withdrawn": True}
        raise PluginError("not_found", f"No proposal {cs_id} of this plugin.")

    def _jobs_progress(self, body: dict) -> dict:
        if not self.job_id:
            raise PluginError("not_in_job", "jobs.progress is only available inside an async action.")
        self.progress.append({"message": body.get("message"), "fields": body.get("fields")})
        return {"ok": True}


class FakeSdk(Sdk):
    """An `Sdk` whose Gateway is in memory; inspect `proposals`, `progress`, `rows`, `llm`."""

    def __init__(self, *, plugin_id: str = "test-plugin", user_id: str = "user-1",
                 project_id: Optional[str] = None, session_id: Optional[str] = None,
                 job_id: Optional[str] = None, transcript: Optional[dict] = None,
                 llm: Optional[ScriptedLlm] = None, registry: Optional[dict] = None,
                 permissions: Optional[Iterable[str]] = None,
                 clock: Optional[Callable[[], float]] = None):
        self.gateway = FakeGateway(plugin_id=plugin_id, user_id=user_id, project_id=project_id,
                                   session_id=session_id, job_id=job_id, transcript=transcript,
                                   llm=llm, registry=registry, permissions=permissions,
                                   clock=clock or time.time)
        super().__init__(self.gateway, job_id=job_id)

    @property
    def project_id(self) -> Optional[str]:
        return self.gateway.project_id

    @property
    def job_id(self) -> Optional[str]:
        return self.gateway.job_id

    @property
    def proposals(self) -> list:
        return self.gateway.proposals

    @property
    def progress(self) -> list:
        return self.gateway.progress

    @property
    def rows(self) -> dict:
        return self.gateway.rows

    @property
    def llm_script(self) -> ScriptedLlm:
        return self.gateway.llm

    def add_user_skill(self, skill_id: str, name: str, content: str, *, used_in_session: bool = True,
                       owner: Optional[str] = None, description: Optional[str] = None,
                       version: Optional[int] = None, quarantined: bool = False) -> None:
        """A user's own skill. `used_in_session` says the session's turns used it; `owner`
        defaults to this fake's user (another owner's skill is never visible)."""
        self.gateway.user_skills[skill_id] = {
            "id": skill_id, "name": name, "content": content, "description": description,
            "owner": owner or self.gateway.user_id, "usedInSession": used_in_session,
            "version": version, "quarantined": quarantined}

    def set_active(self, target_group: str, *, revision: int, parts: dict, scope: str = "user") -> None:
        """Make a version active, as if a person had approved it."""
        self.gateway.active[(scope, target_group)] = {
            "changeSetId": f"fake-active-{scope}-{target_group}", "scope": scope, "revision": revision,
            "decidedAt": _iso(self.gateway.clock()), "pluginId": self.gateway.plugin_id,
            "parts": dict(parts)}


def make_context(*, plugin_id: str = "test-plugin", version: str = "0.0.0", user_id: str = "user-1",
                 project_id: Optional[str] = None, session_id: Optional[str] = None,
                 conversation_id: Optional[str] = None, job_id: Optional[str] = None,
                 sdk: Optional[Sdk] = None, **fake_kwargs) -> Context:
    """A `ctx` for a test; without `sdk`, a FakeSdk built from the same ids and fake_kwargs."""
    if sdk is None:
        sdk = FakeSdk(plugin_id=plugin_id, user_id=user_id, project_id=project_id,
                      session_id=session_id, job_id=job_id, **fake_kwargs)
    return Context(plugin_id=plugin_id, version=version, user_id=user_id, project_id=project_id,
                   session_id=session_id, conversation_id=conversation_id, job_id=job_id, sdk=sdk)
