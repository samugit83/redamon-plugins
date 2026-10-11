"""Skiller's tests import the plugin the way a worker does: the platform SDK first, then the
plugin folder (so `engine` is a package), and nothing else the plugin may use at run time.
tools/run_tests.sh sets REDAMON_PLATFORM_SDK to the lowest synced platform the range accepts."""

import os
import pathlib
import sys

PLUGIN = pathlib.Path(__file__).resolve().parents[1]
REPO = PLUGIN.parent
TESTS = pathlib.Path(__file__).resolve().parent


def _sdk() -> str:
    if os.environ.get("REDAMON_PLATFORM_SDK"):
        return os.environ["REDAMON_PLATFORM_SDK"]
    sys.path.insert(0, str(REPO / "tools"))
    import semver

    return str(REPO / semver.lowest_sdk(PLUGIN, REPO))


for path in (str(TESTS), str(PLUGIN), _sdk()):
    if path not in sys.path:
        sys.path.insert(0, path)
