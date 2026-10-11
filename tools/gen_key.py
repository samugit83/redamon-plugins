#!/usr/bin/env python3
"""Generates an Ed25519 catalog signing key outside every git repository.

    python tools/gen_key.py --out ~/.config/redamon-plugins/dev

Writes `<out>/signing-key.pem` (mode 0600, in a 0700 folder) and `<out>/public-key.json`
(`{keyId, publicKey}`), and prints the public half: the key id, the base64 raw public key and
the `PLUGINS_EXTRA_TRUSTED_KEYS` value a local RedAmon needs to trust a development key. The
private key is never printed. It refuses a folder inside a git repository (a key must never be
committed) and a folder that already holds a key.

The production key is made the same way by its owner, stored as the `CATALOG_SIGNING_KEY`
secret of the `release` environment, and its public key added to RedAmon's keyring.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from sign import key_id, raw_public_key


def _inside_git_repository(path: pathlib.Path) -> pathlib.Path | None:
    for parent in (path, *path.parents):
        if (parent / ".git").exists():
            return parent
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Generate an Ed25519 catalog signing key outside every repository.")
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args(argv)
    out = args.out.expanduser().resolve()

    repo = _inside_git_repository(out)
    if repo is not None:
        print(f"gen_key: {out} is inside a git repository ({repo}); keys are written outside every repository",
              file=sys.stderr)
        raise SystemExit(2)
    pem_path = out / "signing-key.pem"
    if pem_path.exists():
        print(f"gen_key: {out} already holds a key; choose another folder (a key is never overwritten)", file=sys.stderr)
        raise SystemExit(2)

    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(out, 0o700)
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    fd = os.open(pem_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    os.chmod(pem_path, 0o600)

    raw = raw_public_key(key)
    public = {"keyId": key_id(raw), "publicKey": base64.b64encode(raw).decode("ascii")}
    (out / "public-key.json").write_text(json.dumps(public, indent=2) + "\n", encoding="utf-8")
    print(f"private key: {pem_path} (0600; never commit it, never print it)")
    print(f"keyId: {public['keyId']}")
    print(f"publicKey: {public['publicKey']}")
    print(f"PLUGINS_EXTRA_TRUSTED_KEYS={json.dumps([public], separators=(',', ':'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
