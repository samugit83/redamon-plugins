#!/usr/bin/env bash
# Runs this repository's tests inside python:3.11-slim, never on the host.
#
#   tools/run_tests.sh tools          the tools' tests
#   tools/run_tests.sh <plugin>       one plugin's tests
#   tools/run_tests.sh all            the tools, then every plugin, each in its own pytest process
#   tools/run_tests.sh <path>[::test] one file or node id (a plugin's path picks that plugin's SDK)
#
# A plugin's tests run against the lowest platform SDK in .platform-sdk/ that its `platform`
# range accepts (the release workflow does the same), exported as REDAMON_PLATFORM_SDK.
#
# On the host the script builds, once per requirements set, an image with the pinned tools and
# runs itself inside it with the repository mounted read-only. CI starts it inside the
# container already (REDAMON_PLUGINS_IN_CONTAINER=1).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_IMAGE="python:3.11-slim"

if [[ "${REDAMON_PLUGINS_IN_CONTAINER:-}" != "1" ]]; then
  sum="$(sha256sum "$ROOT/tools/requirements-tools.txt" | cut -c1-12)"
  image="redamon-plugins-tools:$sum"
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "building $image from $BASE_IMAGE (pinned tools, once)" >&2
    docker build -q -t "$image" -f - "$ROOT/tools" >/dev/null <<EOF
FROM $BASE_IMAGE
COPY requirements-tools.txt /tmp/requirements-tools.txt
RUN pip install --no-cache-dir --disable-pip-version-check --no-deps -r /tmp/requirements-tools.txt
EOF
  fi
  tty=()
  [[ -t 1 ]] && tty=(-t)
  exec docker run --rm "${tty[@]}" -u "$(id -u):$(id -g)" \
    -e REDAMON_PLUGINS_IN_CONTAINER=1 -e PYTHONDONTWRITEBYTECODE=1 -e HOME=/tmp \
    -v "$ROOT:/repo:ro" -w /repo "$image" tools/run_tests.sh "$@"
fi

cd "$ROOT"
export PYTHONDONTWRITEBYTECODE=1
# The checkout is read-only in the container: no cache folder, and each plugin's tests import
# by path so two plugins' `tests` packages never collide.
PYTEST=(python -m pytest -q -p no:cacheprovider --import-mode=importlib)

plugins() {
  local d
  for d in */; do
    [[ -f "$d/plugin.json" ]] && echo "${d%/}"
  done
  return 0
}

sdk_for() {
  python tools/semver.py lowest-sdk "$1"
}

run_plugin() {
  local plugin="$1"
  shift
  if [[ ! -d "$plugin/tests" ]]; then
    echo "== $plugin: no tests/ folder (every plugin ships its tests)" >&2
    return 1
  fi
  local sdk
  sdk="$(sdk_for "$plugin")" || return 1
  echo "== $plugin (platform SDK $sdk)"
  REDAMON_PLATFORM_SDK="$ROOT/$sdk" "${PYTEST[@]}" "${@:-$plugin/tests}"
}

failed=0
target="${1:-all}"
case "$target" in
  tools)
    echo "== tools"
    "${PYTEST[@]}" tools/tests || failed=1
    ;;
  all)
    echo "== tools"
    "${PYTEST[@]}" tools/tests || failed=1
    for p in $(plugins); do
      run_plugin "$p" || failed=1
    done
    ;;
  *)
    first="${target%%/*}"
    first="${first%%::*}"
    if [[ -f "$first/plugin.json" ]]; then
      if [[ "$target" == "$first" ]]; then
        run_plugin "$first" || failed=1
      else
        run_plugin "$first" "$@" || failed=1
      fi
    else
      "${PYTEST[@]}" "$@" || failed=1
    fi
    ;;
esac
exit "$failed"
