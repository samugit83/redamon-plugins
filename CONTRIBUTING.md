# Contributing a plugin

This guide is for **anyone without write access** to this repository who wants to add a plugin
to RedAmon or improve one. You do not need anyone's permission to start: you work in your own
fork and open a pull request. An administrator of this repository reviews it, merges it and
releases it; nothing you push can reach a RedAmon installation without that.

Maintainers with write access follow the README instead ("Adding a plugin (maintainers)" and
"Merging a contributed plugin").

## How a contribution travels

```
your fork          pull request          administrator                    every RedAmon
---------          ------------          -------------                    -------------
build + test  -->  CI validates,   -->   reviews, asks for changes,  -->  the new release appears
locally            tests, packs          merges, tags the release,        on the Plugins page; each
                   (no secrets)          approves the signed catalog      instance's admin installs it
```

Why it works this way: **one key signs every plugin** in the catalog, and RedAmon trusts a
plugin because that signature says a maintainer reviewed it. So only an administrator can merge
to `master`, create a release tag (`<plugin>-v<version>`) or approve the protected `release`
environment that signs the catalog. A merged pull request is not a release; the release is a
separate step an administrator takes after the merge.

## Before you start

- **Read the contract.** [README.PLUGIN_SDK.md](https://github.com/samugit83/redamon/blob/master/docs/readmes/README.PLUGIN_SDK.md)
  in the RedAmon repository is everything a plugin can declare, call and show, with a complete
  small example in its §14. [`skiller/`](skiller/) is a full real plugin.
- **Know the limits.** Plugin code runs isolated, with no internet, no database and no LLM key.
  It imports only the Python standard library, `redamon_plugin_sdk` and its own `vendor/`
  folder (pure Python only). It appears only at the platform's extension points (its own page,
  the chat's gear menu, a built-in skill's row) and can change what the agent reads only by
  **proposing** text a person approves.
- **Propose it first (recommended).** For anything bigger than a small fix, open an issue in
  this repository describing what the plugin does, which permissions it needs and where it
  appears. An administrator can tell you early whether it fits, before you write the code.
- **Need something the platform cannot do?** A new extension point, SDK call or screen
  primitive is a change to RedAmon itself, not to a plugin: open an issue in
  [samugit83/redamon](https://github.com/samugit83/redamon/issues) first.
- **Tools you need:** git, bash and Docker. Every tool and test runs inside a pinned
  `python:3.11-slim` image; nothing runs on your host's Python.

## Adding a new plugin, step by step

1. **Fork and branch.** Fork `samugit83/redamon-plugins` on GitHub, clone your fork and create a
   branch:

   ```bash
   git clone https://github.com/<you>/redamon-plugins.git
   cd redamon-plugins
   git switch -c plugin/<id>
   ```

   The id matches `^[a-z][a-z0-9-]{1,39}$`, is not taken by another plugin and is not reserved
   (`staging`, `platform`, `core`, `redamon`, `plugins`, `admin`, `api`, `system`, `internal`
   and a few others; `tools/validate.py` tells you). It never changes after the first release.

2. **Create the folder** `<id>/` with the layout in the README ("Layout"):

   - `plugin.json`: `"version": "1.0.0"`, `"dataVersion": 1`, and the **fewest permissions
     that work**;
   - `engine/__init__.py` and your actions (`async def`);
   - `tests/` with pytest tests that use the SDK's `FakeSdk`, `ScriptedLlm` and `make_context`
     (copy `tests/conftest.py` from the SDK guide's example);
   - `README.md`: what the plugin does, what it reads, what it stores, and what a person must
     approve;
   - `CHANGELOG.md` with a `## [1.0.0]` section;
   - optional: `ui/` screens, `vendor/`, and a listing (`listing.json` plus a square icon, at
     most 256 KiB) for its card in RedAmon.

3. **Check it locally**, exactly as CI will:

   ```bash
   tools/run.sh validate <id>      # manifest, screens, imports, layout, changelog, listing
   tools/run_tests.sh <id>         # your tests, on the oldest platform version you support
   tools/run.sh pack <id>          # the archive a release would publish (dist/, ignored by git)
   ```

4. **Try it on your own RedAmon** (optional, recommended): install it with **Install from file**
   from a catalog you sign with a development key. The commands are in the README, "Developing a
   plugin against a local RedAmon".

5. **Register it** in the same branch:
   - add `/<id>/ @samugit83` to `.github/CODEOWNERS`;
   - add a block for your plugin under "The plugins" in `README.md`, in the shape of the ones
     there.

   Leave everything else alone: `catalog.json`, `catalog.json.sig`, `tools/`,
   `.github/workflows/`, `.platform-sdk/` and other plugins' folders are maintained by the
   administrators, and the catalog is written only by the release workflows.

6. **Open the pull request** against `master` of `samugit83/redamon-plugins` and fill in its
   checklist. CI validates, tests and packs every plugin with no secrets; GitHub may hold the
   run of a first-time contributor until an administrator starts it.

7. **Wait for an administrator.** They review the code, the permissions and the README, and may
   ask for changes: push them to the same branch. Only an administrator can merge.

8. **After the merge, an administrator releases it.** They tag `<id>-v1.0.0` on the merge
   commit and approve the `release` deployment; the workflow publishes the archive and signs
   the catalog. Within about six minutes the plugin appears on the Plugins page of every
   RedAmon, as **Not installed**: each installation's admin decides whether to install it.

## What the review checks

- The plugin asks for the fewest permissions that work, and its README says why it needs each.
- The code is readable: no obfuscation, no compiled or minified files, no code downloaded or
  generated at run time. Anything in `vendor/` is pure Python under a licence compatible with
  MIT, with its licence file kept.
- Tests and fixtures hold **no real target data**: no real hostnames, IP addresses, scan output,
  captured traffic or credentials. Use synthetic sessions and reserved names (`example.com`,
  `192.0.2.0/24`).
- The plugin does not write transcripts or target data to its log, which admins read.
- A plugin with `override:skill.text` proposes text that holds for **any** target: nothing from
  the session it learned from may enter the playbook.
- `tools/run.sh validate`, `tools/run_tests.sh` and CI are green, and the README and changelog
  match what the code does.

## Updating a plugin that is already released

The same path: a pull request, a review, a merge, then an administrator's release.

- Raise `version` in `<id>/plugin.json`: patch `x.y.Z` for a fix, minor `x.Y.0` for a feature,
  major `X.0.0` for a change that breaks what users or stored data rely on.
- Add the `## [x.y.z]` section to `<id>/CHANGELOG.md`.
- If the shape of the stored data changes, raise `dataVersion` and add
  `migrations/NNNN_up.py` and `NNNN_down.py` (SDK guide §8).
- Never change a released version: a version number is used once, and the catalog pins the
  archive's checksum. A broken release is fixed by the next patch version; an administrator can
  also **revoke** a harmful version, which stops it on every RedAmon.

A new icon or documentation link needs no new version: change `listing.json` or the icon in a
pull request, and an administrator refreshes the catalog listings after the merge.

## Security

Found a vulnerability in a plugin or in the plugin platform? Do not open a public issue: report
it privately through [GitHub Security Advisories](https://github.com/samugit83/redamon/security/advisories)
of the RedAmon repository, as its [SECURITY.md](https://github.com/samugit83/redamon/blob/master/SECURITY.md)
describes.

## Licence

This repository is MIT-licensed ([LICENSE](LICENSE)). By opening a pull request you agree that
your contribution is released under the same licence.
