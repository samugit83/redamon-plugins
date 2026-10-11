"""Every value specific to the session, so the leak guard can keep it out of a playbook (TD-27).

Collected from every string of the transcript (what people wrote, thoughts, tool arguments and
outputs, findings) and from its title and id: hostnames, IPv4 and IPv6 addresses, URLs, e-mail
addresses, URL paths of two or more segments, query parameter names and values, tokens (hex of
16 or more, base64-like of 24 or more, JWT), and flag-shaped strings.

Not identifiers: anything the skill's factory or current text already contains (it is the
playbook's own wording), and a short generic list (loopback, the unspecified addresses, the
cloud metadata address, the bare documentation domains). The documentation address ranges and
subdomains of the documentation domains are deliberately not generic: no real session reaches
them, so exempting them would change nothing, while the synthetic sessions of the tests and
the evaluation use them for the targets the guard must catch.

Matching is case-insensitive and bounded: a value matches a whole token, also as the tail of a
longer hostname (`api.<host>`) or after `@`, never inside a longer word.
"""

from __future__ import annotations

import ipaddress
import re
import urllib.parse
from dataclasses import dataclass, field

from .transcript_view import as_text

GENERIC = frozenset({
    "localhost", "127.0.0.1", "0.0.0.0", "::1", "::", "169.254.169.254",
    "example.com", "example.org", "example.net",
})

# Extensions that make `name.ext` a file name rather than a host.
FILE_EXTENSIONS = frozenset("""
php php3 php4 php5 phtml html htm xhtml shtml js mjs cjs ts jsx tsx json xml txt md rst py pyc sh bash
asp aspx ashx asmx jsp jspx do action cgi pl rb erb css scss less map png jpg jpeg gif svg ico webp bmp
md5 conf cfg ini env log bak old orig swp tmp sql db sqlite mdb zip gz tgz tar rar 7z war jar ear class
exe dll so bin pem key crt cer der p12 pfx csr csv tsv pdf doc docx xls xlsx ppt pptx yml yaml toml lock
properties inc tpl twig vue git htaccess htpasswd config dat out err
""".split())
# Technology names written like hosts.
NOT_HOSTS = frozenset({"asp.net", "ado.net", "vb.net", "node.js", "vue.js", "next.js", "nuxt.js", "react.js",
                       "express.js", "d3.js", "chart.js", "three.js", "socket.io", "e.g", "i.e"})

_HOST_RE = re.compile(r"(?<![\w.-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,23})(?![\w-]|\.[\w-])",
                      re.I)
_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]{1,15}://[^\s\"'<>`\\]+", re.I)
_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]{1,64}@(?:[a-z0-9-]{1,63}\.)+[a-z]{2,24}(?![\w-])", re.I)
_IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.]|\.\d)")
_IPV6_RE = re.compile(r"(?<![\w:])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?![\w:])", re.I)
_PATH_RE = re.compile(r"(?<![\w.:/~-])(/[\w.~%+-]+(?:/[\w.~%+-]+)+/?)")
_HEX_RE = re.compile(r"(?<![0-9a-f])[0-9a-f]{16,}(?![0-9a-f])", re.I)
_B64_RE = re.compile(r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{24,}={0,2}(?![A-Za-z0-9+/_=-])")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*")
_FLAG_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_]{1,19}\{[^{}\s]{4,100}\}")
_QUERY_RE = re.compile(r"[?&]([A-Za-z_][\w.\[\]-]{0,63})=([^&#\s\"'<>]*)")
# A parameter name or value that is also a plain short word ("id", "page", "test", "1") says
# nothing about the target, and refusing it would refuse ordinary prose.
_PLAIN_NAME = re.compile(r"^[a-z]{1,7}$")
_PLAIN_VALUE = re.compile(r"^(?:[a-z]{1,5}|\d{1,6}|true|false|null|none)$", re.I)


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def _generic(value: str) -> bool:
    v = value.lower().strip("[]")
    if v in GENERIC:
        return True
    if _is_ip(v):
        ip = ipaddress.ip_address(v)
        return ip.is_loopback or ip.is_unspecified or v == "169.254.169.254"
    return False


def _host_ok(host: str) -> bool:
    h = host.lower().rstrip(".")
    if h in NOT_HOSTS or _is_ip(h):
        return False
    tld = h.rsplit(".", 1)[-1]
    return tld not in FILE_EXTENSIONS and not tld.isdigit()


def _strings(value, out: list, depth: int = 0) -> None:
    if depth > 64:
        return
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            out.append(as_text(k))
            _strings(v, out, depth + 1)
    elif isinstance(value, list):
        for v in value:
            _strings(v, out, depth + 1)


def _view_strings(view) -> list:
    out: list = []
    _strings(view.meta.get("title"), out)
    _strings(view.objective, out)
    for h in view.prelude_humans:
        out += [h.text, h.subject]
    for t in view.turns:
        out += [t.thought, t.reasoning]
        for c in t.calls:
            _strings(c.args, out)
            out += [c.summary, c.raw]
            _strings(c.findings, out)
        for d in t.deep_thinks:
            out += [d["trigger"], d["analysis"]]
        out += [h.text for h in t.humans] + [h.subject for h in t.humans] + list(t.errors)
    for team in view.fireteams:
        out.append(team.rationale)
        for m in team.members.values():
            out += [m.name, m.task, m.error] + [text for _, text in m.thoughts]
            for c in m.calls:
                _strings(c.args, out)
                out += [c.summary, c.raw]
    for s in view.searches:
        out.append(s.objective)
        _strings(s.trajectory, out)
    out.append(view.ending.get("text", ""))
    return [s for s in out if s]


@dataclass
class Identifiers:
    by_class: dict = field(default_factory=dict)

    @property
    def values(self) -> list:
        return sorted({v for vs in self.by_class.values() for v in vs})

    def find(self, text: str) -> list:
        """The identifiers that occur in `text`, each once, in a stable order."""
        if not text:
            return []
        hits = []
        for value in self.values:
            pattern = rf"(?<![A-Za-z0-9_-]){re.escape(value)}(?![A-Za-z0-9_-]|\.[A-Za-z0-9])"
            if re.search(pattern, text, re.I):
                hits.append(value)
        return hits


def extract(view, exclude_texts=(), extra_text=()) -> Identifiers:
    """The session's identifiers, minus the playbook's own wording and the generic list."""
    found: dict = {k: set() for k in ("host", "ip", "url", "email", "path", "param", "token", "flag", "meta")}

    def add(kind: str, value: str) -> None:
        value = value.strip().strip(".,;:)(]['\"")
        if len(value) >= 2 and not _generic(value):
            found[kind].add(value)

    for meta_key in ("id", "title"):
        value = view.meta.get(meta_key)
        if isinstance(value, str) and len(value.strip()) >= 4:
            add("meta", value)

    for text in _view_strings(view) + list(extra_text):
        for url in _URL_RE.findall(text):
            add("url", url)
            parts = urllib.parse.urlsplit(url)
            if parts.hostname:
                (add("ip", parts.hostname) if _is_ip(parts.hostname) else add("host", parts.hostname))
            if parts.path.count("/") >= 2 and parts.path.strip("/").count("/") >= 1:
                add("path", parts.path.rstrip("/"))
        for name, value in _QUERY_RE.findall(text):
            if not _PLAIN_NAME.match(name):
                add("param", name)
            decoded = urllib.parse.unquote(value)
            if decoded and not _PLAIN_VALUE.match(decoded):
                add("param", value)
        for email in _EMAIL_RE.findall(text):
            add("email", email)
        for host in _HOST_RE.findall(text):
            if _host_ok(host):
                add("host", host)
        for ip in _IPV4_RE.findall(text):
            if _is_ip(ip):
                add("ip", ip)
        for candidate in _IPV6_RE.findall(text):
            if ":" in candidate and _is_ip(candidate):
                add("ip", candidate)
        for path in _PATH_RE.findall(text):
            add("path", path.rstrip("/"))
        for token in _JWT_RE.findall(text):
            add("token", token)
        for token in _HEX_RE.findall(text):
            add("token", token)
        for token in _B64_RE.findall(text):
            if re.search(r"\d", token) and re.search(r"[A-Za-z]", token) and not _HEX_RE.fullmatch(token):
                add("token", token)
        for flag in _FLAG_RE.findall(text):
            add("flag", flag)

    excluded = "\n".join(t for t in exclude_texts if t).lower()
    if excluded:
        for kind in found:
            found[kind] = {v for v in found[kind] if v.lower() not in excluded}
    return Identifiers(by_class={k: sorted(v) for k, v in found.items()})
