#!/usr/bin/env bash
# Copies the platform contract from a RedAmon checkout into .platform-sdk/<version>/:
# the SDK package, its contract cases, the exported JSON schemas and the semver cases.
#
#   tools/sync_sdk.sh <path-to-redamon> [git-ref]      (ref defaults to HEAD)
#
# Files come from the git ref, not the working tree, so uncommitted edits in the RedAmon
# checkout never reach the copy. The copy is never edited by hand: SHA256SUMS lists every
# file and tools/tests/test_sdk_copy.py checks it. One folder per platform version, never
# deleted, so a plugin can be tested on the lowest platform its range accepts.
set -euo pipefail

src="${1:?usage: tools/sync_sdk.sh <path-to-redamon> [git-ref]}"
ref="${2:-HEAD}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

die() {
  echo "sync_sdk: $*" >&2
  exit 1
}

show() {
  git -C "$src" show "$ref:$1"
}

commit="$(git -C "$src" rev-parse --verify "$ref^{commit}")" || die "$src has no commit $ref"

# The three places that state the platform version must agree.
sdk_version="$(show services/plugin_runner/sdk/redamon_plugin_sdk/__init__.py | sed -n 's/^PLATFORM_VERSION = "\(.*\)"$/\1/p')"
runner_version="$(show services/plugin_runner/platform_version.py | sed -n 's/^PLATFORM_VERSION = "\(.*\)"$/\1/p')"
webapp_version="$(show webapp/src/lib/plugins/platformVersion.ts | sed -n "s/^export const PLUGIN_PLATFORM_VERSION = '\(.*\)'$/\1/p")"
[[ "$sdk_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "no PLATFORM_VERSION in the SDK at $ref"
[[ "$sdk_version" == "$runner_version" && "$sdk_version" == "$webapp_version" ]] ||
  die "platform versions disagree at $ref: sdk=$sdk_version runner=$runner_version webapp=$webapp_version"

mkdir -p "$ROOT/.platform-sdk"
tmp="$(mktemp -d "$ROOT/.platform-sdk/.sync.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/redamon_plugin_sdk" "$tmp/schemas"

copy_dir() {
  local from="$1" to="$2" pattern="$3" name
  while IFS= read -r name; do
    [[ "$name" == $pattern ]] || continue
    show "$from/$name" > "$tmp/$to/$name"
  done < <(git -C "$src" ls-tree --name-only "$ref" "$from/" | sed "s|^$from/||")
}

copy_dir services/plugin_runner/sdk/redamon_plugin_sdk redamon_plugin_sdk '*.py'
copy_dir webapp/src/lib/plugins/schemas/json schemas '*.schema.json'
show services/plugin_runner/sdk/contract_cases.json > "$tmp/contract_cases.json"
show webapp/src/lib/plugins/__fixtures__/semver-cases.json > "$tmp/semver-cases.json"
printf '%s\n' "$sdk_version" > "$tmp/VERSION"
printf '%s\n' "$commit" > "$tmp/SOURCE"
(
  cd "$tmp"
  find . -type f ! -name SHA256SUMS | sed 's|^\./||' | LC_ALL=C sort | while IFS= read -r f; do
    sha256sum "$f"
  done > SHA256SUMS
)

dest="$ROOT/.platform-sdk/$sdk_version"
rm -rf "$dest"
mv "$tmp" "$dest"
trap - EXIT
echo "synced platform $sdk_version from ${commit:0:12} into .platform-sdk/$sdk_version"
