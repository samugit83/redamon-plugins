"""The committed session fixtures are exactly what fixtures/sessions_build.py writes."""

import importlib.util

import pytest

from support import FIXTURES, SESSIONS, all_sessions

spec = importlib.util.spec_from_file_location("sessions_build", FIXTURES / "sessions_build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def test_every_built_session_is_committed_and_nothing_else():
    assert sorted(build.SESSIONS) == all_sessions()


@pytest.mark.parametrize("name", sorted(build.SESSIONS))
def test_fixture_matches_its_builder(name):
    assert (SESSIONS / f"{name}.json").read_text(encoding="utf-8") == build.render(name), \
        "re-run: python skiller/tests/fixtures/sessions_build.py"


@pytest.mark.parametrize("name", sorted(build.SESSIONS))
def test_synthetic_data_only(name):
    # Master plan P3: documentation domains and address ranges only.
    import re

    text = (SESSIONS / f"{name}.json").read_text(encoding="utf-8")
    for host in re.findall(r"\b(?:[a-z0-9-]+\.)+(?:com|org|net|io)\b", text):
        assert host.endswith(("example.com", "example.org", "example.net")), host
    for ip in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text):
        assert ip.startswith(("192.0.2.", "198.51.100.", "203.0.113.")), ip
