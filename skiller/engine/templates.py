"""The template rules of RedAmon's agent validator, checked before a proposal leaves Skiller.

Mirrors agentic/prompts/skill_parts.py `_template_errors` (TD-26): a template part parses as a
format string; its field names are plain lower-case names with no conversion or format spec;
it keeps exactly the factory placeholders, each at most twice as often as the factory text
uses it; literal braces are doubled. A plain part is used verbatim and has no such rule. The
messages are written for the repair round.
"""

from __future__ import annotations

import re
import string

_PLACEHOLDER_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")


def _fields(text: str):
    return list(string.Formatter().parse(text))


def _counts(text: str) -> dict:
    counts: dict = {}
    try:
        for _literal, name, _spec, _conv in _fields(text):
            if name:
                counts[name] = counts.get(name, 0) + 1
    except ValueError:
        pass
    return counts


def infer_kind(factory_text: str) -> tuple:
    """(kind, placeholders) when the platform does not say: a text that parses as a format
    string with plain field names is taken for a template. Only a fallback: plain parts that
    happen to parse (`%{http_code}`) are taken for templates, which only asks for doubled braces."""
    try:
        fields = _fields(factory_text)
    except ValueError:
        return "plain", []
    names = [name for _l, name, _s, _c in fields if name is not None]
    if not names or not all(_PLACEHOLDER_NAME.match(n) for n in names):
        return "plain", []
    return "template", sorted(set(names))


def check(kind: str, factory_text: str, placeholders, value: str) -> list:
    if kind != "template":
        return []
    expected = set(placeholders or [])
    try:
        fields = _fields(value)
    except ValueError as e:
        return [f"single brace in a template part ({e}); write {{{{ or }}}} for a literal brace"]
    problems, names, used = [], set(), {}
    for _literal, name, spec, conversion in fields:
        if name is None:
            continue
        if not _PLACEHOLDER_NAME.match(name):
            problems.append(f"single brace in a template part: {{{name}}} is not a placeholder (a plain lower-case "
                            "name); write {{ and }} for literal braces")
            continue
        if conversion:
            problems.append(f"placeholder {{{name}}} must not use a conversion (!{conversion})")
        if spec:
            problems.append(f"placeholder {{{name}}} must not use a format spec (:{spec})")
        names.add(name)
        used[name] = used.get(name, 0) + 1
    factory_counts = _counts(factory_text)
    for name, count in sorted(used.items()):
        if name in expected and count > 2 * factory_counts.get(name, 1):
            problems.append(f"placeholder {{{name}}} appears {count} times; at most {2 * factory_counts.get(name, 1)}")
    for name in sorted(expected - names):
        problems.append(f"placeholder {{{name}}} removed")
    for name in sorted(names - expected):
        problems.append(f"placeholder {{{name}}} added; write {{{{{name}}}}} if it is literal text")
    if not problems:
        try:
            for fill in ("", "x"):
                value.format(**{name: fill for name in expected})
        except Exception as e:  # noqa: BLE001 - any render failure refuses the value, as the agent does
            problems.append(f"the template does not render ({e})")
    return problems
