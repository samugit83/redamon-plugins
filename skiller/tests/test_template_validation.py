"""Template parts keep exactly their placeholders, as the agent's validator demands (R-35, TD-26)."""

from engine import templates

FACTORY = "Use --level {sqli_level} --risk {sqli_risk}. Example: kali_shell({{\"command\": \"sqlmap\"}})"
PLACEHOLDERS = ["sqli_level", "sqli_risk"]


def _check(value, kind="template", factory=FACTORY, placeholders=PLACEHOLDERS):
    return templates.check(kind, factory, placeholders, value)


def test_unchanged_and_extended_templates_pass():
    assert _check(FACTORY) == []
    assert _check(FACTORY + "\nThen retry with --level {sqli_level} only. Use {{braces}} literally.") == []


def test_placeholder_removed():
    assert "placeholder {sqli_risk} removed" in _check("Use --level {sqli_level}.")


def test_placeholder_added():
    assert any("placeholder {sqli_threads} added" in p for p in _check(FACTORY + " Threads {sqli_threads}."))


def test_single_brace_in_a_template_part():
    problems = _check(FACTORY + " A JSON body {\"id\": 1}.")
    assert any("single brace in a template part" in p for p in problems)
    problems = _check(FACTORY + " a lone } here")
    assert any("single brace in a template part" in p for p in problems)


def test_doubled_braces_are_fine():
    assert _check(FACTORY + ' A JSON body {{"id": 1}}.') == []


def test_field_names_must_be_plain_and_without_spec_or_conversion():
    assert any("plain lower-case name" in p for p in _check(FACTORY + " {0}"))
    assert any("plain lower-case name" in p for p in _check(FACTORY + " {sqli_level.real}"))
    assert any("format spec" in p for p in _check(FACTORY.replace("{sqli_level}", "{sqli_level:>5}")))
    assert any("conversion" in p for p in _check(FACTORY.replace("{sqli_level}", "{sqli_level!r}")))


def test_a_placeholder_repeated_more_than_twice_the_factory_count():
    value = FACTORY + " {sqli_level} {sqli_level}"
    assert any("appears 3 times" in p for p in _check(value))


def test_plain_parts_are_verbatim():
    assert _check('A JSON body {"id": 1} and %{http_code}.', kind="plain", factory="x", placeholders=[]) == []


def test_kind_inferred_when_the_platform_does_not_say():
    # An older platform answered no kind: a factory text with placeholders and no lone brace is a template.
    assert templates.infer_kind("Use {sqli_level} and {{x}}.") == ("template", ["sqli_level"])
    assert templates.infer_kind("Use %{http_code} and {method}.") == ("template", ["http_code", "method"])
    assert templates.infer_kind('A body {"id": 1}') == ("plain", [])
    assert templates.infer_kind("No braces at all.") == ("plain", [])
