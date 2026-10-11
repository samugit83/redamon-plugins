#!/usr/bin/env python3
"""Checks a plugin folder before it is packed or released.

    python tools/validate.py <plugin> [--sdk-root .platform-sdk]

Prints `ok: <id> <version>` and exits 0, or one line per problem and exits 1. It applies:
- the manifest and every `ui/**/*.json` against the JSON schemas of each synced platform
  version the plugin's `platform` range accepts;
- the rules a JSON Schema cannot hold, as RedAmon applies them at install
  (`webapp/src/lib/plugins/schemas/manifest.ts`, `schemas/screen.ts`, `installChecks.ts`):
  contributions name declared actions, `llm` needs `llm:invoke`, `llmFeature` goes with
  `llm:invoke`, a core screen needs its point's permission, one of action/coreScreen, screen
  depth and size, screen references declared;
- the folder layout, each entry an `async def`, the data migrations' names and shape;
- imports: plugin code (engine/, migrations/) may import only the standard library,
  `redamon_plugin_sdk`, its own `engine` package and its `vendor/` folder, and never another
  plugin, because workers start with `python -I -S` and see nothing else;
- a `CHANGELOG.md` section for the manifest version.
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import sys

import jsonschema

import semver

ROOT = pathlib.Path(__file__).resolve().parents[1]
SDK_ROOT = ROOT / ".platform-sdk"

REQUIRED_FILES = ("plugin.json", "README.md", "CHANGELOG.md", "engine/__init__.py")
TOP_LEVEL = {"plugin.json", "README.md", "CHANGELOG.md", "LICENSE",
             "engine", "vendor", "ui", "migrations", "tests", "eval"}
CACHE_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
ENTRY_RE = re.compile(r"(engine(?:/[a-z0-9_]+)+\.py):([a-z_][a-z0-9_]*)")
MIGRATION_RE = re.compile(r"(\d{4})_(up|down)\.py")
MIGRATION_STEP_MAX = 9999
SDK_PACKAGE = "redamon_plugin_sdk"

# extensionPoints.ts
POINT_PERMISSION = {"skill.text": "override:skill.text"}
CORE_SCREEN_POINT = {"skill.text.versions": "skill.text"}

# schemas/screen.ts
SCREEN_MAX_DEPTH = 6
SCREEN_MAX_NODES = 200
SCREEN_MAX_JSON_DEPTH = 64


def _rel(plugin: pathlib.Path, path: pathlib.Path) -> str:
    return path.relative_to(plugin).as_posix()


def _is_cache(path: pathlib.Path) -> bool:
    return any(part in CACHE_NAMES for part in path.parts) or path.suffix == ".pyc"


def _json_path(error) -> str:
    return "/".join(str(p) for p in error.absolute_path) or "(root)"


def _json_depth(value, limit: int) -> int:
    """Iteratively, so a hostile file cannot exhaust the stack."""
    stack, deepest = [(value, 1)], 0
    while stack:
        node, depth = stack.pop()
        deepest = max(deepest, depth)
        if deepest > limit:
            return deepest
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)
    return deepest


def _load_json(path: pathlib.Path, label: str, errors: list):
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        errors.append(f"{label}: cannot be read ({e.__class__.__name__})")
        return None
    # A deeply nested file is refused before the recursive parser sees it.
    if _bracket_depth(text) > SCREEN_MAX_JSON_DEPTH:
        errors.append(f"{label}: nested too deep (more than {SCREEN_MAX_JSON_DEPTH} levels of JSON)")
        return None
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        errors.append(f"{label}: not valid JSON")
        return None


def _bracket_depth(text: str) -> int:
    depth = deepest = 0
    in_string = escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
            deepest = max(deepest, depth)
        elif ch in "]}":
            depth -= 1
    return deepest


# -- layout -------------------------------------------------------------------------------

def _layout(plugin: pathlib.Path) -> list:
    errors = []
    for name in REQUIRED_FILES:
        if not (plugin / name).is_file():
            errors.append(f"{name} is missing")
    for entry in sorted(plugin.iterdir()):
        if entry.name not in TOP_LEVEL and entry.name not in CACHE_NAMES:
            errors.append(f"unexpected top-level entry: {entry.name}")
    for path in sorted(plugin.rglob("*")):
        if path.is_symlink():
            errors.append(f"{_rel(plugin, path)} is a symbolic link (an archive may hold only files and folders)")
    return errors


# -- the manifest -------------------------------------------------------------------------

def _schema_errors(schema: dict, doc, label: str) -> list:
    validator = jsonschema.Draft202012Validator(schema)
    return [f"{label} {_json_path(e)}: {e.message}" for e in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))]


def _cross_field(m: dict) -> list:
    """manifest.ts superRefine, run only on a manifest the schema accepted (as zod does)."""
    errors = []
    permissions = list(m.get("permissions", []))
    if len(set(permissions)) != len(permissions):
        errors.append("plugin.json permissions: lists a permission twice")
    screens = list(m.get("screens", []))
    if len(set(screens)) != len(screens):
        errors.append("plugin.json screens: lists a screen twice")
    actions = m.get("actions", [])
    ids = [a["id"] for a in actions]
    if len(set(ids)) != len(ids):
        errors.append("plugin.json actions: two actions share an id")
    has = permissions.__contains__
    for i, a in enumerate(actions):
        if a.get("llm") and not has("llm:invoke"):
            errors.append(f"plugin.json actions/{i}/llm: an action that calls a model needs the llm:invoke permission")
    if has("llm:invoke") and "llmFeature" not in m:
        errors.append("plugin.json llmFeature: is required with the llm:invoke permission (it describes the model choice to users)")
    if "llmFeature" in m and not has("llm:invoke"):
        errors.append("plugin.json llmFeature: needs the llm:invoke permission")
    contributes = m.get("contributes", {})
    refs = []
    for i, c in enumerate(contributes.get("chat.session.actions", [])):
        where = f"plugin.json contributes/chat.session.actions/{i}"
        if ("action" in c) == ("coreScreen" in c):
            errors.append(f"{where}: give either action or coreScreen")
        if "action" in c:
            refs.append((f"{where}/action", c["action"]))
        if "coreScreen" in c:
            needed = POINT_PERMISSION[CORE_SCREEN_POINT[c["coreScreen"]]]
            if not has(needed):
                errors.append(f"{where}/coreScreen: {c['coreScreen']} needs the {needed} permission")
    for i, c in enumerate(contributes.get("project.skills.row", [])):
        refs.append((f"plugin.json contributes/project.skills.row/{i}/action", c["action"]))
    for where, action in refs:
        if action not in ids:
            errors.append(f'{where}: no action "{action}" is declared')
    return errors


# -- screens ------------------------------------------------------------------------------

def _one_of(node: dict, keys: tuple) -> bool:
    return sum(1 for k in keys if k in node) == 1


def _screen_rules(node, file: str, errors: list, refs: dict, depth: int, acc: dict) -> None:
    """schemas/screen.ts refinements and measures, and the references for the cross-check."""
    acc["depth"] = max(acc["depth"], depth)
    acc["nodes"] += 1
    kind = node.get("type")
    if kind == "page":
        if "source" in node:
            refs["actions"].add(node["source"]["action"])
        for child in node.get("children", []):
            _screen_rules(child, file, errors, refs, depth + 1, acc)
    elif kind in ("list", "table"):
        refs["actions"].add(node["source"]["action"])
        for r in node.get("rowActions", []):
            if not _one_of(r, ("screen", "action")):
                errors.append(f"{file}: a row action opens a screen or calls an action, not both")
            if "action" in r:
                refs["actions"].add(r["action"])
            if "screen" in r:
                refs["screens"].add(r["screen"])
    elif kind == "form":
        refs["actions"].add(node["submit"]["action"])
        if "resultScreen" in node:
            refs["screens"].add(node["resultScreen"])
        for f in node.get("fields", []):
            if (f.get("kind") == "select") != ("options" in f):
                errors.append(f"{file}: options are required for a select field and allowed only there")
    elif kind == "button":
        if not _one_of(node, ("action", "screen")):
            errors.append(f"{file}: a button calls an action or opens a screen, not both")
        if "action" in node:
            refs["actions"].add(node["action"])
        if "screen" in node:
            refs["screens"].add(node["screen"])
    for binding in ("content", "before", "after"):
        value = node.get(binding)
        if kind in ("markdown", "textDiff", "badge") and isinstance(value, dict) and not _one_of(value, ("text", "field")):
            errors.append(f"{file}: give either text or field")


def _screens(plugin: pathlib.Path, m: dict, schema: dict, ids: set) -> list:
    errors = []
    listed = set(m.get("screens", []))
    contributes = m.get("contributes", {})
    must_list = [contributes.get("nav", {}).get("screen")] + [a.get("resultScreen") for a in m.get("actions", [])]
    for s in filter(None, must_list):
        if s not in listed:
            errors.append(f"{s} is used but not listed in screens")
    for s in sorted(listed | set(filter(None, must_list))):
        if not (plugin / s).is_file():
            errors.append(f"{s} is listed but missing")
    ui = plugin / "ui"
    files = sorted(p for p in ui.rglob("*.json") if not _is_cache(p)) if ui.is_dir() else []
    for path in files:
        file = _rel(plugin, path)
        node = _load_json(path, file, errors)
        if node is None:
            continue
        if _json_depth(node, SCREEN_MAX_JSON_DEPTH) > SCREEN_MAX_JSON_DEPTH:
            errors.append(f"{file}: nested too deep (more than {SCREEN_MAX_JSON_DEPTH} levels of JSON)")
            continue
        found = _schema_errors(schema, node, file)
        if found:
            errors.extend(found)
            continue
        refs, acc = {"actions": set(), "screens": set()}, {"depth": 0, "nodes": 0}
        _screen_rules(node, file, errors, refs, 1, acc)
        if acc["depth"] > SCREEN_MAX_DEPTH:
            errors.append(f"{file}: nested {acc['depth']} deep; at most {SCREEN_MAX_DEPTH}")
        if acc["nodes"] > SCREEN_MAX_NODES:
            errors.append(f"{file}: {acc['nodes']} elements; at most {SCREEN_MAX_NODES}")
        for a in sorted(refs["actions"] - ids):
            errors.append(f'{file}: no action "{a}" is declared')
        for s in sorted(refs["screens"] - listed):
            errors.append(f"{file}: opens {s}, which is not listed in screens")
    return errors


# -- code: entries, imports, migrations ---------------------------------------------------

def _parse(plugin: pathlib.Path, path: pathlib.Path, errors: list):
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as e:
        errors.append(f"{_rel(plugin, path)}: syntax error at line {e.lineno}: {e.msg}")
    except (OSError, UnicodeDecodeError) as e:
        errors.append(f"{_rel(plugin, path)}: cannot be read ({e.__class__.__name__})")
    return None


def _module_function(tree: ast.Module, name: str):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _entries(plugin: pathlib.Path, m: dict, trees: dict) -> list:
    errors = []
    for i, a in enumerate(m.get("actions", [])):
        match = ENTRY_RE.fullmatch(a.get("entry", ""))
        if not match:
            continue
        file, fn = match.groups()
        where = f"plugin.json actions/{i}/entry"
        if not (plugin / file).is_file():
            errors.append(f"{where}: {file} does not exist")
            continue
        tree = trees.get(file)
        if tree is None:
            continue
        node = _module_function(tree, fn)
        if node is None:
            errors.append(f"{where}: {file} defines no function {fn}")
        elif not isinstance(node, ast.AsyncFunctionDef):
            errors.append(f"{where}: {fn} must be an async def (async def {fn}(ctx, params))")
    return errors


def _imported_names(tree: ast.Module):
    """(top-level module, line) for every absolute import, static or by a literal string."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], node.lineno
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split(".")[0], node.lineno
        elif (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)
              and isinstance(node.args[0].value, str)
              and ((isinstance(node.func, ast.Name) and node.func.id == "__import__")
                   or (isinstance(node.func, ast.Attribute) and node.func.attr == "import_module"))):
            yield node.args[0].value.split(".")[0], node.lineno


def _vendor_names(plugin: pathlib.Path) -> set:
    vendor = plugin / "vendor"
    if not vendor.is_dir():
        return set()
    return {p.stem if p.suffix == ".py" else p.name for p in vendor.iterdir()
            if (p.is_dir() and p.name not in CACHE_NAMES) or p.suffix == ".py"}


def _sibling_plugins(plugin: pathlib.Path) -> set:
    return {p.name for p in plugin.parent.iterdir()
            if p.is_dir() and p.resolve() != plugin.resolve() and (p / "plugin.json").is_file()}


def _imports(plugin: pathlib.Path, trees: dict) -> list:
    errors = []
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    allowed = stdlib | {SDK_PACKAGE, "engine"} | _vendor_names(plugin)
    siblings = _sibling_plugins(plugin)
    for file, tree in sorted(trees.items()):
        for top, line in _imported_names(tree):
            if top in allowed:
                continue
            if top in siblings:
                errors.append(f"{file}:{line}: imports from another plugin ({top}); plugins never import each other")
            else:
                errors.append(f"{file}:{line}: imports {top}, which is not the standard library, the SDK or its vendor/ folder")
    return errors


def _migrations(plugin: pathlib.Path, m: dict, trees: dict) -> list:
    errors = []
    folder = plugin / "migrations"
    present = set()
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            if path.name in CACHE_NAMES:
                continue
            match = MIGRATION_RE.fullmatch(path.name)
            if not match or not 2 <= int(match.group(1)) <= MIGRATION_STEP_MAX or not path.is_file():
                errors.append(f"migrations/{path.name} is not named NNNN_up.py or NNNN_down.py (steps 0002 to 9999)")
                continue
            present.add(path.name)
            tree = trees.get(f"migrations/{path.name}")
            fn = _module_function(tree, "migrate_row") if tree is not None else None
            if tree is not None and not isinstance(fn, ast.FunctionDef):
                errors.append(f"migrations/{path.name} must define a plain def migrate_row(row)")
    data_version = m.get("dataVersion")
    if isinstance(data_version, int) and not isinstance(data_version, bool):
        for step in range(2, min(data_version, MIGRATION_STEP_MAX) + 1):
            if f"{step:04d}_up.py" not in present:
                errors.append(f"dataVersion {data_version} needs migrations/{step:04d}_up.py")
    return errors


def _changelog(plugin: pathlib.Path, version) -> list:
    path = plugin / "CHANGELOG.md"
    if not path.is_file() or not isinstance(version, str):
        return []
    heading = re.compile(rf"^##\s+\[?{re.escape(version)}\]?(?=\s|$)", re.M)
    if not heading.search(path.read_text(encoding="utf-8")):
        return [f"CHANGELOG.md has no section for {version} (a heading '## [{version}]')"]
    return []


# -- all of it ----------------------------------------------------------------------------

def _platform_schemas(sdk_root: pathlib.Path) -> dict:
    out = {}
    for version in semver.synced_platforms(sdk_root.parent):
        folder = sdk_root / version / "schemas"
        out[version] = {name: json.loads((folder / f"{name}.schema.json").read_text(encoding="utf-8"))
                        for name in ("manifest", "screen")}
    return out


def validate(plugin, sdk_root: pathlib.Path = SDK_ROOT) -> list:
    plugin = pathlib.Path(plugin)
    if not plugin.is_dir():
        return [f"{plugin} is not a folder"]
    errors = _layout(plugin)
    if not (plugin / "plugin.json").is_file():
        return errors
    m = _load_json(plugin / "plugin.json", "plugin.json", errors)
    if not isinstance(m, dict):
        return errors + (["plugin.json is not a JSON object"] if m is not None else [])

    schemas = _platform_schemas(pathlib.Path(sdk_root))
    if not schemas:
        return errors + ["no platform SDK synced: run tools/sync_sdk.sh <path-to-redamon>"]
    rng = m.get("platform")
    accepted = [v for v in schemas if semver.is_valid_range(rng) and semver.satisfies(v, rng)]
    if semver.is_valid_range(rng) and not accepted:
        errors.append(f"plugin.json platform: no synced platform version satisfies {rng!r} (synced: {', '.join(schemas)})")
    checked = accepted or list(schemas)[:1]

    manifest_ok = True
    for version in checked:
        found = _schema_errors(schemas[version]["manifest"], m, "plugin.json")
        manifest_ok &= not found
        errors.extend(e for e in found if e not in errors)
    if manifest_ok:
        errors.extend(_cross_field(m))
    if m.get("id") != plugin.name:
        errors.append(f'plugin.json id: {json.dumps(m.get("id"))} but the folder is "{plugin.name}" '
                      "(the folder must be named after the plugin id)")

    trees = {}
    for folder in ("engine", "migrations"):
        base = plugin / folder
        for path in sorted(base.rglob("*.py")) if base.is_dir() else []:
            if _is_cache(path) or path.is_symlink():
                continue
            tree = _parse(plugin, path, errors)
            if tree is not None:
                trees[_rel(plugin, path)] = tree

    actions = [a for a in m.get("actions", []) if isinstance(a, dict)] if isinstance(m.get("actions"), list) else []
    ids = {a.get("id") for a in actions}
    errors.extend(_entries(plugin, {"actions": actions}, trees))
    errors.extend(_imports(plugin, trees))
    errors.extend(_migrations(plugin, m, trees))
    if manifest_ok:
        for version in checked:
            errors.extend(e for e in _screens(plugin, m, schemas[version]["screen"], ids) if e not in errors)
    errors.extend(_changelog(plugin, m.get("version")))
    return errors


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check a plugin folder before it is packed or released.")
    ap.add_argument("plugin", type=pathlib.Path)
    ap.add_argument("--sdk-root", type=pathlib.Path, default=SDK_ROOT)
    args = ap.parse_args(argv)
    errors = validate(args.plugin, args.sdk_root)
    if errors:
        for e in errors:
            print(f"{args.plugin.name}: {e}")
        return 1
    m = json.loads((args.plugin / "plugin.json").read_text(encoding="utf-8"))
    print(f"ok: {m['id']} {m['version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
