"""RedAmon plugin SDK: what plugin code imports (README.PLUGINS.md §7.3, §7.7).

An action is `async def run(ctx, params) -> dict`; `ctx.sdk` makes every Gateway call,
each awaited. Standard library only: workers start with `python -I -S` and see no
site-packages, so a plugin's own pure-Python dependencies go in its `vendor/` folder.
"""

from .client import GatewayTransport, Sdk
from .context import Context
from .errors import PluginError

# The platform contract this copy of the SDK implements; equal to the runner's
# services/plugin_runner/platform_version.py (tests/test_sdk_contract.py).
PLATFORM_VERSION = "1.0.0"


class _Delete:
    """The type of DELETE; there is one instance."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "DELETE"


# What a data migration's `migrate_row(row)` returns to delete the row it was given
# (migrations/NNNN_up.py or NNNN_down.py; TD-28). The runner compares by identity.
DELETE = _Delete()

__all__ = ["Context", "DELETE", "GatewayTransport", "PLATFORM_VERSION", "PluginError", "Sdk"]
