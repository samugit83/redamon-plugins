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
.github/workflows/            ci.yml, release.yml, revoke.yml, catalog-resign.yml
.platform-sdk/<version>/      synced copy of the platform contract, one folder per platform version
tools/                        validate, pack, catalog, sign, gen_key, semver, sync_sdk, run_tests
catalog.json, catalog.json.sig   written only by the release, revoke and re-sign workflows, never by hand
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
| `tools/catalog.py add-release\|revoke\|unrevoke\|resign\|local` | Adds a release to `catalog.json` (fields from the manifest and the archive); lists a released version in `revoked` with its reason, or lifts that; raises only the serial so the unchanged catalog can be signed again; or writes a one-release catalog for an offline install. `serial` only grows. A catalog has no expiry. |
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
tag, `release.yml` runs two jobs:

1. **build** (no secret): checks that the tag matches the manifest, validates the plugin, runs its
   tests on the lowest synced platform version its range accepts, and packs only that folder
   (deterministic archive, sha256);
2. **publish**, in the protected environment `release`, **after a reviewer approves**: creates the
   GitHub release with the archive, adds it to `catalog.json`, increments `serial`, signs the
   catalog, and commits `catalog.json` and `catalog.json.sig` to `master` as
   `github-actions[bot]` ("chore(catalog): add <plugin> <version>").

Two tags pushed together queue in one concurrency group (`catalog`), so they never race on the
catalog. Every action in the workflows is pinned to a full commit SHA. The first release,
`skiller-v1.0.0`, ran this way on 2026-10-11: build 20 s, then the publish job waited for the
approval and took 13 s; the raw catalog URL served the new catalog 17 s after the bot's commit.

### Releasing a new version of a plugin

1. `git pull` first: the release bot commits the catalog to `master`, so your copy is behind
   after every release.
2. Make the change, with tests.
3. Raise `version` in `<plugin>/plugin.json`: patch `x.y.Z` for a fix, minor `x.Y.0` for a
   feature, major `X.0.0` for a change that breaks what users or stored data rely on.
4. Add `## [x.y.z]` to `<plugin>/CHANGELOG.md`.
5. Only if the shape of the plugin's stored data changes: raise `dataVersion` and add
   `migrations/NNNN_up.py` and `NNNN_down.py`.
6. Only if the plugin needs a newer platform feature: `tools/sync_sdk.sh <path-to-redamon>
   <git-ref>` and raise the lower bound of `platform`. Installations on an older RedAmon keep the
   previous release, because RedAmon installs the newest release its platform version accepts.
7. `tools/run_tests.sh <plugin>` and `tools/validate.py <plugin>` (in the container, see "Tests").
8. Commit and push `master`; wait for the CI run to be green.
9. Tag that commit and push the tag:
   `git tag <plugin>-v<x.y.z> && git push origin <plugin>-v<x.y.z>`.
   Only a repository admin can create `*-v*` tags (ruleset `release tags`; GitHub reports
   "Bypassed rule violations" for the admin, which is expected).
10. Approve: GitHub e-mails the reviewer; open Actions → the "Release" run → **Review
    deployments** → tick `release` → **Approve and deploy**.
11. `git pull` to get the bot's `chore(catalog): add …` commit.
12. RedAmon installations see the new release within about six minutes (the raw CDN caches the
    catalog for up to 5 minutes, RedAmon for 60 s); an admin clicks **Update** on the plugin's
    card. Nothing updates by itself.

### Rules, and what to do when a release fails

- A version number is used once. Never move or delete a published tag or GitHub release: the
  catalog pins the archive's sha256, and installations may already hold it. A broken release is
  fixed by the next patch version; a harmful one is also revoked ("Revoking a version" below).
- **The build job fails** (tests, validation, tag and manifest disagree): nothing is published.
  A transient failure (a runner or network outage) is retried with Actions → the run → **Re-run
  failed jobs**, which rebuilds the same commit. A real failure is fixed on `master` and released
  as the next patch version; the failed tag stays and is never reused.
- **The approval is rejected or not given** (a waiting job expires after 30 days): nothing is
  published. To publish that tag later, **Re-run all jobs** and approve.
- **The publish job fails after the approval**: **Re-run failed jobs** and approve again, within
  7 days (the archive built by the first job is kept 7 days; after that, **Re-run all jobs**). A
  re-run is safe at any step: the archive is byte-identical, an existing GitHub release gets the
  same file again, and the catalog entry of that version is replaced, not duplicated.

### Revoking a version

A patch release fixes a bad version only where an admin clicks **Update**. A revocation stops it
everywhere: the version stays in the catalog and is listed in `revoked` with a reason, and every
RedAmon, at its next reconcile (every 5 minutes, plus up to 5 minutes of raw CDN cache), stops
that plugin's actions and worker, hides its menu items, stops applying the skill versions it
proposed (the agent uses the built-in text), and shows "Revoked by the catalog: <reason>" on its
card. The admin's enabled/disabled setting is not touched, and the version is never offered for
a new install.

1. Usually, first publish the fix as the next patch version (above), so admins have something
   to update to.
2. Actions → **Revoke** → **Run workflow** on `master`: action `revoke`, the plugin id, the
   released version, and the reason RedAmon will show (500 characters at most).
3. Approve the `release` deployment, as for a release. The workflow refuses a plugin or version
   the catalog does not list, an empty reason, and a revocation that changes nothing.
4. `git pull` to get the bot's `chore(catalog): revoke <plugin> <version>` commit.

A mistaken revocation is lifted the same way with action `unrevoke`; RedAmon serves the version
again at its next reconcile. Locally, the same change is
`python tools/catalog.py revoke <plugin> <version> --reason "…"` (or `unrevoke`), but only the
workflow can sign it.

### Re-signing the catalog

A catalog never expires (the owner's decision of 2026-10-11): there is no routine re-signing and
no monthly approval. **Catalog re-sign** (Actions → Run workflow, then approve) signs the
unchanged catalog again with `serial` + 1. It is needed only after a key rotation (RedAmon's
`docs/readmes/README.PLUGINS.md` §6.4), so the catalog carries the new key at once.

### Repository settings

These are set in the GitHub UI by the owner; the token used for pushes cannot change them.

| Setting | Value |
|---|---|
| Visibility | public (installations download release assets without credentials) |
| Environment `release` | required reviewer: the owner; deployment branches and tags: `master` and `*-v*`; secrets `CATALOG_SIGNING_KEY` (PEM) and `CATALOG_SIGNING_KEY_ID` |
| Ruleset `release tags` | `refs/tags/*-v*`: creation, update, deletion and force push restricted; bypass: Repository admin |
| Ruleset `protect master` | default branch: deletion and force push restricted. It must not require pull requests, status checks or signed commits, or restrict updates: the release bot pushes the catalog to `master` directly |
| Actions → Workflow permissions | read-only (the default); each job declares its own, and only the catalog-committing jobs ask for `contents: write` |

## Keys

| Key | Where |
|---|---|
| Production private key | only the `CATALOG_SIGNING_KEY` secret (with `CATALOG_SIGNING_KEY_ID`) of the `release` environment, plus the owner's offline backup |
| Production public key | id `ee4a2df9065d36b1`, in RedAmon's keyring (`webapp/src/lib/plugins/keyring.ts`), shipped in a RedAmon release |
| Development keys | made by `tools/gen_key.py` outside every repository; trusted by a local RedAmon only through `PLUGINS_EXTRA_TRUSTED_KEYS` |

No private key is ever in this repository: `.gitignore` refuses `*.pem`, `*.key` and `.env*`, and
a test fails if key material appears in any file. Rotating the production key starts with a
RedAmon release that trusts the new key; the steps are in RedAmon's
`docs/readmes/README.PLUGINS.md` §6.4.

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

1. `git pull`, then create `<plugin>/` with the layout above; the id matches
   `^[a-z][a-z0-9-]{1,39}$` and is never reused for another plugin.
2. Write `plugin.json` (the manifest is described in RedAmon's `docs/readmes/README.PLUGINS.md`
   §6.3, and its schema is `.platform-sdk/<version>/schemas/manifest.schema.json`), starting at
   version `1.0.0` and `dataVersion` 1. Ask for the fewest permissions that work, and write the
   README: what it reads, what it stores, what a person must approve. Add `CHANGELOG.md` with
   `## [1.0.0]`.
3. Write tests with `FakeSdk` and `ScriptedLlm`; `tools/run_tests.sh <plugin>` and
   `tools/validate.py <plugin>` must pass.
4. Add the line `/<plugin>/ @samugit83` to `.github/CODEOWNERS`, and a row to the table at the
   top of this README.
5. Optional, recommended: try it on a local RedAmon with a development key ("Developing a plugin
   against a local RedAmon" above).
6. Push `master`, then follow "Releasing a new version of a plugin" from step 8: tag
   `<plugin>-v1.0.0`, approve, pull. The plugin appears on every installation's Plugins page as
   a new card.

No RedAmon release is needed for a new plugin, unless it needs a platform feature RedAmon does
not have yet, or the signing key changes.

## Licence

MIT, see [LICENSE](LICENSE).
