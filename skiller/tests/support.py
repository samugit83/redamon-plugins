"""Shared helpers for Skiller's tests: the synthetic sessions and the fake SDK."""

import copy
import json
import pathlib

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
SESSIONS = FIXTURES / "sessions"

_cache: dict = {}


def session(name: str) -> dict:
    """A fresh copy of a synthetic session, as `sdk.session.read()` returns it."""
    if name not in _cache:
        _cache[name] = json.loads((SESSIONS / f"{name}.json").read_text(encoding="utf-8"))
    return copy.deepcopy(_cache[name])


def all_sessions() -> list:
    return sorted(p.stem for p in SESSIONS.glob("*.json"))


def view(name: str):
    from engine.transcript_view import TranscriptView

    return TranscriptView.from_transcript(session(name))
