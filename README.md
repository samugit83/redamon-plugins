# RedAmon plugins

The plugins of [RedAmon](https://github.com/samugit83/redamon), each in its own folder, and the
tools and workflows that release them. RedAmon itself holds the plugin **platform** (the Plugin
Runner, the Gateway, the Plugin Manager and the screens that review what a plugin proposes); this
repository holds the plugin **code**. Nothing here runs until an administrator installs a plugin
from the Plugins page of a RedAmon instance with the plugin platform switched on.

| Plugin | What it does |
|---|---|
| [`skiller/`](skiller/README.md) | Reads a finished agent chat session and proposes a better, session-agnostic version of the built-in skill it used. A person approves every version. |

## Layout

```
README.md  LICENSE  .gitignore
.github/CODEOWNERS            every plugin folder, tools/, .github/ and the catalog need the owner's review
.github/workflows/            ci.yml, release.yml, catalog-refresh.yml
.platform-sdk/<version>/      synced copy of the platform contract, one folder per platform version
tools/                        validate, pack, catalog, sign, gen_key, semver, sync_sdk, run_tests
catalog.json, catalog.json.sig   written by the release workflow only (absent until the first release)
<plugin>/                     one folder per plugin
```

A plugin folder:

```
<plugin>/
  plugin.json      the manifest: the whole contract with the platform
  README.md        what the plugin does, the data it reads and stores, its limits
  CHANGELOG.md     one "## [x.y.z]" section per released version
  engine/          Python actions (engine/__init__.py, engine/<module>.py)
  vendor/          optional: bundled pure-Python dependencies
  ui/              optional: screen descriptions (JSON) drawn by RedAmon
  migrations/      optional: NNNN_up.py / NNNN_down.py data migrations
  tests/           pytest, run against the platform SDK's fakes (never packed)
  eval/            optional: evaluation harness (never packed)
```

Plugins never import from each other. Plugin code may import only the Python standard library,
`redamon_plugin_sdk` and its own `vendor/` folder: the Runner starts each worker with
`python -I -S`, so nothing else exists at run time. `tools/validate.py` refuses anything else.

## The platform contract (`.platform-sdk/`)

The SDK is owned by the RedAmon repository, because it *is* the contract. This repository keeps an
unedited copy per platform version, made by:

```bash
tools/sync_sdk.sh <path-to-redamon> [git-ref]
```

It copies the SDK package (`redamon_plugin_sdk`, with `FakeSdk`, `ScriptedLlm` and
`make_context` for tests), its contract cases, the JSON schemas of the manifest, the screens and
the catalog, and the version-range cases, from a git ref (never a working tree), and writes
`VERSION`, `SOURCE` (the RedAmon commit) and `SHA256SUMS`. A test fails when a file was edited by
hand. A new platform version adds a folder; old folders stay, so a plugin is always tested on the
lowest platform version its `platform` range accepts.

## Tools

All tools are Python 3.11 scripts. Run them in a container, as the tests do (see "Tests").

| Tool | Does |
|---|---|
| `tools/validate.py <plugin>` | Checks the manifest and every `ui/*.json` against the platform schemas plus the rules a schema cannot hold (contributions name declared actions, `llm` needs `llm:invoke`, a core screen needs its permission, screen depth), the folder layout, that each entry is an `async def`, the imports (standard library, SDK, own `vendor/` only; never another plugin), the `platform` range, and a `CHANGELOG.md` section for the manifest version. |
| `tools/pack.py <plugin>` | Writes `dist/<id>-<version>.tar.gz`: a deterministic POSIX ustar archive (sorted entries, zero times and owners, modes 0644/0755, no links) without `tests/`, `eval/` or caches. Two packs of one folder are byte-identical. Prints the sha256 and size. |
| `tools/catalog.py add-release\|refresh\|local` | Adds a release to `catalog.json` (fields from the manifest and the archive), re-signs an unchanged catalog with a new expiry, or writes a one-release catalog for an offline install. `serial` only grows; `expires` defaults to 90 days. |
| `tools/sign.py <catalog.json>` | Ed25519 over the exact bytes, key from the environment variable `CATALOG_SIGNING_KEY` (PEM); writes `<catalog>.sig` = `{"keyId", "sig"}`. |
| `tools/gen_key.py --out <dir>` | A new Ed25519 key pair, written outside every git repository (`0600`); prints the public key and its key id. |
| `tools/semver.py` | The version-range grammar of the platform (`>=1.0.0 <2.0.0`: comparators `>= > <= < =` on full `x.y.z`), held equal to RedAmon's by the shared cases. |

## Tests

```bash
tools/run_tests.sh all        # the tools, then every plugin, each in its own pytest process
tools/run_tests.sh tools      # the tools only
tools/run_tests.sh skiller    # one plugin
tools/run_tests.sh skiller/tests/test_edits_apply.py::test_anchor_must_be_unique
```

The script never runs Python on the host: it builds, once per `tools/requirements-tools.txt`, an
image from `python:3.11-slim` with the pinned tools and runs itself inside it with this
repository mounted read-only. Plugin tests use the SDK's `FakeSdk` (an in-memory Gateway that
applies its main refusals) and `ScriptedLlm` (queued model answers, every prompt recorded), so
they need no network, no RedAmon and no model.

## Releases

A release is a tag `<pluginId>-v<version>`, for example `skiller-v1.0.0`. The tag must point at a
commit where `<plugin>/plugin.json` has that version and `CHANGELOG.md` has its section. For the
tag, `release.yml`:

1. validates the plugin and runs its tests on the lowest synced platform version its range accepts;
2. packs only that folder and computes its sha256;
3. creates the GitHub release with the archive;
4. in the protected environment `release` (a reviewer approves): adds the release to
   `catalog.json`, increments `serial`, sets `expires` to 90 days, signs it, and commits
   `catalog.json` and `catalog.json.sig` to `main`.

Two tags pushed together queue in one concurrency group (`catalog`), so they never race on the
catalog. `catalog-refresh.yml` re-signs the unchanged catalog monthly (and by hand) with a new
expiry and `serial` + 1, after the same approval; left unapproved, the catalog expires: installed
plugins keep running, but installs and updates stop until a fresh catalog is signed. RedAmon warns
14 days before.

Every action in the workflows is pinned to a full commit SHA.

## Keys

| Key | Where |
|---|---|
| Production private key | only the `CATALOG_SIGNING_KEY` secret (with `CATALOG_SIGNING_KEY_ID`) of the `release` environment, plus the owner's offline backup |
| Production public key | RedAmon's keyring (`webapp/src/lib/plugins/keyring.ts`), shipped in a RedAmon release |
| Development keys | made by `tools/gen_key.py` outside every repository; trusted by a local RedAmon only through `PLUGINS_EXTRA_TRUSTED_KEYS` |

No private key is ever in this repository: `.gitignore` refuses `*.pem`, `*.key` and `.env*`, and
a test fails if key material appears in any file.

## Developing a plugin against a local RedAmon

There is no "load a folder" mode: a plugin is installed the same way as in production, from a
signed catalog, here an offline one signed with a development key.

```bash
python tools/gen_key.py --out ~/.config/redamon-plugins/dev        # once
python tools/validate.py <plugin> && python tools/pack.py <plugin>
python tools/catalog.py local <plugin> --archive dist/<plugin>-<version>.tar.gz \
  --url https://github.com/<owner>/redamon-plugins/releases/download/<plugin>-v<version>/<plugin>-<version>.tar.gz \
  --out dist/catalog.json
CATALOG_SIGNING_KEY="$(cat ~/.config/redamon-plugins/dev/signing-key.pem)" python tools/sign.py dist/catalog.json
```

Then, on the RedAmon host: set `PLUGINS_ENABLED=true` and
`PLUGINS_EXTRA_TRUSTED_KEYS=[{"keyId":"…","publicKey":"…"}]` (the values `gen_key.py` printed)
in `.env`, run `./redamon.sh up`, and use **Install from file** on the Plugins page with the
archive, `catalog.json` and `catalog.json.sig`. The page warns while an extra key is trusted;
remove it when you are done. (Run these commands in `python:3.11-slim` too, for example
`docker run --rm -v "$PWD:/repo" -w /repo python:3.11-slim python tools/pack.py <plugin>`; only
`sign.py` and `gen_key.py` need `cryptography`, which the test image has.)

## Adding a plugin

1. Create `<plugin>/` with the layout above; the id matches `^[a-z][a-z0-9-]{1,39}$`.
2. Write `plugin.json` (the manifest is described in RedAmon's `docs/readmes/README.PLUGINS.md`
   §6.3, and its schema is `.platform-sdk/<version>/schemas/manifest.schema.json`), ask for the
   fewest permissions that work, and write the README: what it reads, what it stores,
   what a person must approve.
3. Write tests with `FakeSdk` and `ScriptedLlm`; `tools/run_tests.sh <plugin>` and
   `tools/validate.py <plugin>` must pass.
4. Add the folder to `.github/CODEOWNERS`.
5. Merge to `main`, then push the tag `<plugin>-v<version>`.

## Licence

MIT, see [LICENSE](LICENSE).
