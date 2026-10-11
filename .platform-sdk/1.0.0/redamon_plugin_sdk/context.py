"""The `ctx` an action receives: who and where it runs for, and the SDK bound to its call."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .client import Sdk


@dataclass(frozen=True)
class Context:
    """Set by the platform for one action call; a plugin never builds its own.

    `project_id`, `session_id` and `conversation_id` are set only when the action was
    started from a project or a session; `job_id` only inside an async action.
    """

    plugin_id: str
    version: str
    user_id: str
    project_id: Optional[str]
    session_id: Optional[str]
    conversation_id: Optional[str]
    job_id: Optional[str]
    sdk: "Sdk"
