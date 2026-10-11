"""Anchored edits applied in code, within the per-edit, per-part and whole-skill limits (R-35, R-53)."""

from engine import edits

TOOLS = ("## SQL injection\n"
         "### Step 1: Confirm the injection point (execute_curl)\n"
         "Send a single quote and compare the response with the baseline.\n"
         "### Step 2: Run sqlmap (kali_shell)\n"
         "Use --level {sqli_level} and --risk {sqli_risk}.\n"
         "Do NOT test hosts outside the scope.\n")
PARTS = {"tools": TOOLS, "payload_reference": "## Payloads\n' OR 1=1 --\n"}
LIMITS = {"skillMaxChars": 20_000, "parts": {"tools": 10_000, "payload_reference": 10_000}}


def _apply(*specs, parts=None, limits=None):
    return edits.apply(dict(parts or PARTS), list(specs), limits=limits or LIMITS)


def test_insert_after_a_line_adds_a_new_line():
    r = _apply({"part": "tools", "op": "insert_after", "anchor": "compare the response with the baseline.",
                "text": "Repeat the probe once to rule out noise."})
    assert not r.refused
    assert "with the baseline.\nRepeat the probe once to rule out noise.\n### Step 2" in r.parts["tools"]
    assert r.applied[0].added == len("Repeat the probe once to rule out noise.")


def test_insert_after_mid_line_joins_with_a_space():
    r = _apply({"part": "tools", "op": "insert_after", "anchor": "Send a single quote", "text": "(and a double quote)"})
    assert "Send a single quote (and a double quote) and compare" in r.parts["tools"]


def test_insert_before_a_line_adds_a_line_above():
    r = _apply({"part": "tools", "op": "insert_before", "anchor": "### Step 2: Run sqlmap (kali_shell)",
                "text": "Before sqlmap, note the baseline length."})
    assert "baseline.\nBefore sqlmap, note the baseline length.\n### Step 2" in r.parts["tools"]


def test_replace_exact_text():
    r = _apply({"part": "tools", "op": "replace", "anchor": "Send a single quote", "text": "Send a single and a double quote"})
    assert "Send a single and a double quote and compare" in r.parts["tools"]
    assert r.applied[0].removed == len("Send a single quote")


def test_append_at_the_end_of_the_part():
    r = _apply({"part": "payload_reference", "op": "append", "text": "' OR '1'='1"})
    assert r.parts["payload_reference"] == "## Payloads\n' OR 1=1 --\n' OR '1'='1\n"


def test_anchor_missing_duplicated_or_inexact():
    r = _apply({"part": "tools", "op": "insert_after", "anchor": "not in the playbook at all", "text": "x"})
    assert "the anchor is not in part tools" in r.refused[0].reason
    r = _apply({"part": "tools", "op": "insert_after", "anchor": "### Step", "text": "x"})
    assert "the anchor appears 2 times in part tools" in r.refused[0].reason
    r = _apply({"part": "tools", "op": "replace", "anchor": "send a single quote", "text": "x"})  # case differs
    assert r.refused and r.parts == PARTS


def test_edits_apply_in_order_on_the_evolving_text():
    r = _apply({"part": "tools", "op": "append", "text": "Extra step one."},
               {"part": "tools", "op": "insert_after", "anchor": "Extra step one.", "text": "Extra step two."})
    assert not r.refused
    assert r.parts["tools"].endswith("Extra step one.\nExtra step two.\n")


def test_unknown_part_and_empty_text():
    r = _apply({"part": "nope", "op": "append", "text": "x"}, {"part": "tools", "op": "append", "text": "  "})
    assert "unknown part nope" in r.refused[0].reason
    assert "empty" in r.refused[1].reason


def test_one_edit_is_at_most_4000_characters():
    r = _apply({"part": "tools", "op": "append", "text": "x" * 4001})
    assert "4000" in r.refused[0].reason


def test_part_growth_is_bounded():
    # max(1,500, 25 % of the part): 1,500 here.
    r = _apply({"part": "tools", "op": "append", "text": "a" * 1000},
               {"part": "tools", "op": "append", "text": "b" * 600})
    assert len(r.applied) == 1 and "add more than 1500 characters to part tools" in r.refused[0].reason
    big = "y" * 20_000
    r = _apply({"part": "big", "op": "append", "text": "c" * 4000}, {"part": "big", "op": "append", "text": "d" * 1500},
               parts={"big": big}, limits={"skillMaxChars": 100_000, "parts": {"big": 100_000}})
    assert len(r.applied) == 1 and r.refused  # 25 % of 20,000 = 5,000


def test_a_replace_removes_at_most_600_characters():
    long_line = "L" * 700
    r = _apply({"part": "p", "op": "replace", "anchor": long_line, "text": "short"},
               parts={"p": f"start\n{long_line}\nend\n"}, limits={"skillMaxChars": 10_000, "parts": {"p": 10_000}})
    assert "removes 700 characters" in r.refused[0].reason


def test_part_limit_from_the_platform():
    r = _apply({"part": "payload_reference", "op": "append", "text": "z" * 100},
               limits={"skillMaxChars": 20_000, "parts": {"tools": 10_000, "payload_reference": 60}})
    assert "the limit for part payload_reference is 60" in r.refused[0].reason


def test_whole_skill_budget_from_the_platform():
    total = sum(len(t) for t in PARTS.values())
    r = _apply({"part": "tools", "op": "append", "text": "w" * 50},
               limits={"skillMaxChars": total + 20, "parts": {"tools": 10_000, "payload_reference": 10_000}})
    assert "the whole skill would be" in r.refused[0].reason and str(total + 20) in r.refused[0].reason


def test_refusals_carry_the_edit_number_for_the_repair_round():
    r = _apply({"part": "tools", "op": "append", "text": "ok"},
               {"part": "tools", "op": "insert_after", "anchor": "missing anchor here", "text": "x"})
    assert r.refused[0].index == 2
    assert r.refused[0].problem().startswith("tools edit 2:")
