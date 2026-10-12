#!/usr/bin/env bash
# Runs one tool of this repository inside the same pinned image as tools/run_tests.sh, never on
# the host: validate.py and catalog.py need jsonschema, sign.py and gen_key.py need cryptography,
# and the image has exactly the versions CI uses.
#
#   tools/run.sh validate <plugin>
#   tools/run.sh pack <plugin>                       writes dist/<id>-<version>.tar.gz
#   tools/run.sh catalog local <plugin> --archive dist/<id>-<version>.tar.gz --url <url> --out dist/catalog.json
#   KEYS_DIR=~/.config/redamon-plugins tools/run.sh gen_key --out /keys/dev
#   CATALOG_SIGNING_KEY="$(cat <pem>)" tools/run.sh sign dist/catalog.json
#
# The repository is mounted read-write (pack and catalog write dist/, which git ignores).
# KEYS_DIR, when set, is mounted at /keys: gen_key refuses to write a key inside a repository.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_IMAGE="python:3.11-slim"

tool="${1:-}"
if [[ -z "$tool" || ! -f "$ROOT/tools/$tool.py" ]]; then
  echo "usage: tools/run.sh <validate|pack|catalog|sign|gen_key|semver> [args...]" >&2
  exit 2
fi
shift

# The same image name and recipe as tools/run_tests.sh, so both share one build.
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

args=(--rm -u "$(id -u):$(id -g)" -e PYTHONDONTWRITEBYTECODE=1 -e HOME=/tmp -v "$ROOT:/repo" -w /repo)
[[ -t 1 ]] && args+=(-t)
[[ -n "${CATALOG_SIGNING_KEY:-}" ]] && args+=(-e CATALOG_SIGNING_KEY)
if [[ -n "${KEYS_DIR:-}" ]]; then
  mkdir -p "$KEYS_DIR"
  args+=(-v "$(cd "$KEYS_DIR" && pwd):/keys")
fi

exec docker run "${args[@]}" "$image" python "tools/$tool.py" "$@"
