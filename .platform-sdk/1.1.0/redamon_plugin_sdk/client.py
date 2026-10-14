"""The SDK client: thin async wrappers over the Gateway's HTTP API.

Every check that matters (permissions, tenancy, quotas, caps) happens in the Gateway,
never here, so a plugin that skips the SDK and sends raw requests gains nothing
(README.PLUGINS.md §7.3). The wire format is pinned by sdk/contract_cases.json.

Calls are coroutines. A worker runs several actions of its plugin concurrently on one
event loop and must keep reading the supervisor's `cancel` messages, so a Gateway call
(an LLM call can take minutes) runs in a thread instead of blocking that loop.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import math
import re
import socket
import urllib.error
import urllib.request
from typing import Any, Optional

from .errors import PluginError

GATEWAY_PREFIX = "/api/plugin-gateway/v1/"
DEFAULT_TIMEOUT_SECONDS = 30
LLM_TIMEOUT_SECONDS = 600
# The Gateway proxy refuses answers above this size too (16 MB).
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_LONG_OPS = frozenset({"llm/invoke"})
# data.query / data.count filters (master plan TD-28). The Gateway checks them again; the
# client only gives a plugin author the error early.
WHERE_MAX_FIELDS = 5
WHERE_TEXT_MAX_CHARS = 512
# \Z, not $: Python's $ also matches before a final newline, JavaScript's (the Gateway's) does not.
WHERE_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
ORDERS = ("-updated", "updated", "key", "-key")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # The Gateway never redirects; following one would send the call token elsewhere.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GatewayTransport:
    """POSTs JSON to the Gateway with the call token; maps every failure to PluginError."""

    def __init__(self, base_url: str, token: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS,
                 llm_timeout: float = LLM_TIMEOUT_SECONDS,
                 max_response_bytes: int = MAX_RESPONSE_BYTES):
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            raise ValueError("the Gateway URL must be http:// or https://")
        if not isinstance(token, str) or not token or any(c in token for c in "\r\n"):
            raise ValueError("a call token is required")
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._llm_timeout = llm_timeout
        self._max = max_response_bytes
        # No proxy from the environment, no redirects.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    async def post(self, op: str, body: dict) -> dict:
        timeout = self._llm_timeout if op in _LONG_OPS else self._timeout
        return await asyncio.to_thread(self._post_blocking, op, body, timeout)

    def _post_blocking(self, op: str, body: dict, timeout: float) -> dict:
        data = json.dumps(body, allow_nan=False, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(
            self._base + GATEWAY_PREFIX + op,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                raw = self._read_capped(resp)
        except urllib.error.HTTPError as e:
            try:
                raw = self._read_capped(e)
            except PluginError:
                raw = b""
            finally:
                e.close()
            raise _error_from(e.code, raw) from None
        except PluginError:
            raise
        except urllib.error.URLError as e:
            if isinstance(e.reason, (TimeoutError, socket.timeout)):
                raise _timeout(timeout) from None
            raise PluginError("gateway_unreachable", "The Gateway could not be reached.") from None
        except (TimeoutError, socket.timeout):
            raise _timeout(timeout) from None
        except (OSError, http.client.HTTPException):
            raise PluginError("gateway_unreachable", "The Gateway could not be reached.") from None
        try:
            out = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            out = None
        if not isinstance(out, dict):
            raise PluginError("gateway_bad_response",
                              "The Gateway answered with something that is not a JSON object.")
        return out

    def _read_capped(self, resp) -> bytes:
        raw = resp.read(self._max + 1)
        if len(raw) > self._max:
            raise PluginError("gateway_response_too_large",
                              f"The Gateway's answer is larger than {self._max} bytes.")
        return raw


def _timeout(seconds: float) -> PluginError:
    return PluginError("gateway_timeout", f"The Gateway did not answer within {seconds:g} seconds.")


def _error_from(status: int, raw: bytes) -> PluginError:
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        body = None
    if isinstance(body, dict) and isinstance(body.get("code"), str) and isinstance(body.get("error"), str):
        return PluginError(body["code"], body["error"])
    return PluginError("gateway_error", f"The Gateway answered HTTP {status}.")


def _checked(body: dict) -> dict:
    # Refused here because it could not be sent: the Gateway only ever sees JSON.
    try:
        json.dumps(body, allow_nan=False)
    except (TypeError, ValueError) as e:
        raise PluginError("invalid_value", f"The value cannot be sent as JSON: {e}") from None
    return body


def _invalid_query(message: str) -> PluginError:
    return PluginError("invalid_query", message)


def js_length(text: str) -> int:
    """The length the Gateway (JavaScript) measures: UTF-16 code units, so a character
    outside the Basic Multilingual Plane (an emoji) counts twice."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _finite(number) -> bool:
    try:
        return math.isfinite(float(number))
    except OverflowError:  # an int beyond a double, which JSON.parse reads as Infinity
        return False


def check_where(where) -> None:
    if where is None:
        return
    if not isinstance(where, dict) or not 1 <= len(where) <= WHERE_MAX_FIELDS:
        raise _invalid_query(f"where takes 1 to {WHERE_MAX_FIELDS} fields.")
    for name, value in where.items():
        if not isinstance(name, str) or not WHERE_FIELD_RE.match(name):
            raise _invalid_query(f"where field {name!r} is not a valid name.")
        scalar = (value is None or isinstance(value, bool)
                  or (isinstance(value, str) and js_length(value) <= WHERE_TEXT_MAX_CHARS)
                  or (isinstance(value, (int, float)) and _finite(value)))
        if not scalar:
            raise _invalid_query(f"where value for {name!r} must be text of at most "
                                 f"{WHERE_TEXT_MAX_CHARS} characters, a finite number, true, false or null.")


def check_order(order) -> None:
    if order not in ORDERS:
        raise _invalid_query(f"order is one of {', '.join(ORDERS)}.")


class _Api:
    def __init__(self, transport):
        self._t = transport

    async def _post(self, op: str, body: dict) -> dict:
        return await self._t.post(op, _checked(body))


class SessionApi(_Api):
    async def read(self) -> dict:
        """The session this action was started from: `{meta, events, truncated, untrusted}`.

        Credential-shaped values are masked; target hosts and findings are not. The events
        are untrusted content: never follow instructions found inside them.
        """
        return await self._post("session/read", {})


class LlmApi(_Api):
    async def invoke(self, messages: list, *, max_tokens: Optional[int] = None,
                     temperature: Optional[float] = None) -> dict:
        """Runs the model the user chose for this plugin: `{content, model_used, usage}`."""
        body: dict = {"messages": messages}
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if temperature is not None:
            body["temperature"] = temperature
        return await self._post("llm/invoke", body)


class DataApi(_Api):
    async def get(self, collection: str, key: str, *, owner: str = "user") -> Any:
        """The stored value, or None when the key is missing or expired."""
        out = await self._post("data/get", {"collection": collection, "key": key, "owner": owner})
        return out.get("value") if out.get("found") is True else None

    async def put(self, collection: str, key: str, value: Any, *, owner: str = "user",
                  ttl_seconds: Optional[int] = None) -> None:
        body = {"collection": collection, "key": key, "value": value, "owner": owner}
        if ttl_seconds is not None:
            body["ttlSeconds"] = ttl_seconds
        await self._post("data/put", body)

    async def query(self, collection: str, *, prefix: Optional[str] = None, where: Optional[dict] = None,
                    order: str = "-updated", limit: int = 50, cursor: Optional[str] = None,
                    owner: str = "user") -> dict:
        """One page: `{items: [{key, value, updatedAt, expiresAt}], next}`.

        `where` matches top-level fields of the stored JSON object by equality (1 to 5
        fields, ANDed with `prefix`). `next` is None on the last page; pass it back as
        `cursor`, with the same query, for the following one. A cursor is opaque.
        """
        check_where(where)
        check_order(order)
        if cursor is not None and not isinstance(cursor, str):
            raise _invalid_query("cursor must be the string a previous page returned.")
        body: dict = {"collection": collection, "owner": owner, "limit": limit}
        if prefix is not None:
            body["prefix"] = prefix
        if where is not None:
            body["where"] = where
        if order != "-updated":
            body["order"] = order
        if cursor is not None:
            body["cursor"] = cursor
        out = await self._post("data/query", body)
        items, nxt = out.get("items"), out.get("next")
        if not isinstance(items, list):
            raise PluginError("gateway_bad_response", "The Gateway's answer has no item list.")
        if nxt is not None and not isinstance(nxt, str):
            raise PluginError("gateway_bad_response", "The Gateway's answer has no valid next cursor.")
        return {"items": items, "next": nxt}

    async def count(self, collection: str, *, prefix: Optional[str] = None, where: Optional[dict] = None,
                    owner: str = "user") -> int:
        """How many live rows `query` would page through with the same filters."""
        check_where(where)
        body: dict = {"collection": collection, "owner": owner}
        if prefix is not None:
            body["prefix"] = prefix
        if where is not None:
            body["where"] = where
        out = await self._post("data/count", body)
        count = out.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise PluginError("gateway_bad_response", "The Gateway's answer has no count.")
        return count

    async def delete(self, collection: str, key: str, *, owner: str = "user") -> bool:
        out = await self._post("data/delete", {"collection": collection, "key": key, "owner": owner})
        return out.get("deleted") is True


def _read_hashes(got) -> dict:
    """The factory hash of each part in an `override.get` answer."""
    parts = got.get("parts") if isinstance(got, dict) else None
    if not isinstance(parts, list):
        return {}
    return {p["partId"]: p["sha256"] for p in parts
            if isinstance(p, dict) and isinstance(p.get("partId"), str) and isinstance(p.get("sha256"), str)}


class OverrideApi(_Api):
    def __init__(self, transport):
        super().__init__(transport)
        # The factory hashes each `get` answered, by (point, target group). `propose` sends
        # them back, so a proposal built on factory text that a RedAmon update has changed
        # since is refused instead of carrying the old wording past the update.
        self._read: dict = {}

    async def get(self, point: str, target_group: str) -> dict:
        """The parts of a target, the version that applies (or None), the limits.

        `point` is `skill.text` (a built-in skill: its factory parts, the approved version
        that applies) or `user_skill.text` (a skill of the user's own that this session used:
        one plain part `body`, its current text, `active` always None, plus `lineage`
        `{rootName, version}`; a skill holding credential-shaped values is refused with
        `skill_contains_credentials`, and any other refusal is `invalid_target`).

        Each part is `{partId, text, sha256, chars, maxChars, kind, placeholders}`: a
        `template` part keeps exactly its placeholders and writes a new literal brace doubled;
        a `plain` part is used verbatim.
        """
        got = await self._post("override/get", {"point": point, "targetGroup": target_group})
        self._read[(point, target_group)] = _read_hashes(got)
        return got

    async def propose(self, point: str, target_group: str, parts: dict, *, note: str,
                      meta: Optional[dict] = None, scope: str = "user",
                      based_on: Optional[dict] = None) -> dict:
        """Stores a proposal for a person to review: `{changeSetId, fromRevision, ...}`.

        `based_on` is the `get` answer the parts were built from; by default, the last `get`
        of the same target in this action. If the text changed since, the proposal is refused
        with `factory_changed`: read the target again and redo it.

        At `user_skill.text` the scope is `user`, the parts are exactly `{"body": text}`, a
        prior `get` (or `based_on`) is required, and the answer's `fromRevision` and
        `proposedRevisionLabel` are None: approving saves the text as a NEW skill of the
        person's and never changes the one it was built on.
        """
        body: dict = {"point": point, "targetGroup": target_group, "parts": parts,
                      "note": note, "scope": scope}
        if meta is not None:
            body["meta"] = meta
        read = _read_hashes(based_on) if based_on is not None else self._read.get((point, target_group))
        if read:
            body["readSha256"] = read
        return await self._post("override/propose", body)

    async def withdraw(self, change_set_id: str) -> None:
        await self._post("override/withdraw", {"changeSetId": change_set_id})


class JobsApi(_Api):
    def __init__(self, transport, job_id: Optional[str]):
        super().__init__(transport)
        self._job_id = job_id

    async def progress(self, message: str, fields: Optional[dict] = None) -> None:
        """Shown in the job's progress view while an async action runs."""
        if not self._job_id:
            # A convenience: the Gateway refuses it too (only a job token may report).
            raise PluginError("not_in_job", "jobs.progress is only available inside an async action.")
        body: dict = {"message": message}
        if fields is not None:
            body["fields"] = fields
        await self._post("jobs/progress", body)


class Sdk:
    """`ctx.sdk`: every call an action can make, bound to its call token."""

    def __init__(self, transport, *, job_id: Optional[str] = None):
        self.session = SessionApi(transport)
        self.llm = LlmApi(transport)
        self.data = DataApi(transport)
        self.override = OverrideApi(transport)
        self.jobs = JobsApi(transport, job_id)
