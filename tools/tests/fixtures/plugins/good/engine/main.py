import asyncio
import os.path
from collections import OrderedDict

import tinylib
from redamon_plugin_sdk import PluginError

from engine import helper
from . import helper as relative_helper


async def run(ctx, params):
    await asyncio.sleep(0)
    if not isinstance(params, dict):
        raise PluginError("bad_params", "params must be an object")
    return {"shape": helper.shape(params), "same": relative_helper.shape(params), "tiny": tinylib.VALUE,
            "ordered": list(OrderedDict(a=1)), "base": os.path.basename("/x/y")}


async def list_items(ctx, params):
    return {"items": []}
