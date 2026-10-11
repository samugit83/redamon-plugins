"""Every value specific to the session, for the leak guard (F-21, R-34)."""

import pytest

from engine import identifiers
from fixtures.sessions_build import (API_HOST, EMAIL, FLAG, HEX_TOKEN, HOST, IP4, IP4_B, JWT, PATH, SESSION_ID,
                                     TITLE)
from support import session, view


@pytest.fixture(scope="module")
def ids():
    return identifiers.extract(view("sqli_workflow"))


@pytest.mark.parametrize("value", [HOST, IP4, EMAIL, PATH, HEX_TOKEN, JWT, FLAG, SESSION_ID, TITLE,
                                   "http://shop.example.com/api/v1/products?product_id=42&sessionToken=Zq81xLmP",
                                   "sessionToken", "product_id", "Zq81xLmP"])
def test_found_in_messages_args_outputs_urls_and_meta(ids, value):
    assert value.lower() in {v.lower() for v in ids.values}, value


def test_classes(ids):
    assert HOST in ids.by_class["host"]
    assert IP4 in ids.by_class["ip"]
    assert EMAIL in ids.by_class["email"]
    assert PATH in ids.by_class["path"]
    assert JWT in ids.by_class["token"] and HEX_TOKEN in ids.by_class["token"]
    assert FLAG in ids.by_class["flag"]
    assert "product_id" in ids.by_class["param"]


def test_other_sessions_find_more_shapes():
    found = identifiers.extract(view("tie"))
    assert IP4_B in found.values
    v6 = identifiers.extract(view("sqli_workflow"), extra_text=["Reach 2001:db8::17 over IPv6."])
    assert "2001:db8::17" in v6.by_class["ip"]
    sub = identifiers.extract(view("sqli_workflow"), extra_text=[f"Then try {API_HOST}"])
    assert API_HOST in sub.values


def test_find_is_case_insensitive_and_bounded(ids):
    assert ids.find(f"Probe SHOP.EXAMPLE.COM again") == [HOST]
    assert ids.find("Use a <host> placeholder") == []
    # A substring of a longer word is not a hit.
    assert ids.find("shop.example.community") == []


def test_generic_values_are_not_identifiers():
    t = session("sqli_workflow")
    t["events"][0]["data"]["content"] += " Also localhost, 127.0.0.1, 0.0.0.0, ::1, 169.254.169.254 and example.com."
    from engine.transcript_view import TranscriptView
    found = identifiers.extract(TranscriptView.from_transcript(t))
    for generic in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "169.254.169.254", "example.com"):
        assert generic not in found.values, generic


def test_values_in_the_factory_or_current_text_are_excluded():
    factory = "Read /api/v1/products with execute_curl. Try product_id first."
    found = identifiers.extract(view("sqli_workflow"), exclude_texts=[factory, f"current: {HOST}"])
    for excluded in (PATH, "product_id", HOST):
        assert excluded not in found.values, excluded
    assert IP4 in found.values


def test_plain_words_are_not_taken_as_parameters():
    found = identifiers.extract(view("sqli_workflow"), extra_text=["http://x.example.com/a/b?id=1&page=2&q=test"])
    for word in ("id", "page", "q", "test", "1", "2"):
        assert word not in found.by_class["param"], word
