#!/usr/bin/env python3
"""Signs a catalog the way RedAmon verifies it (TD-11, README.PLUGINS.md §6.4).

    CATALOG_SIGNING_KEY="<PEM>" python tools/sign.py catalog.json [--out catalog.json.sig]

Writes `{"keyId", "sig"}`: `sig` is the base64 Ed25519 signature over the exact bytes of the
file (RedAmon checks it with Node's `crypto.verify(null, bytes, key, sig)` before parsing
anything), and `keyId` is the first 16 hex characters of the SHA-256 of the raw 32-byte public
key. The private key comes only from the environment and is never printed; when
`CATALOG_SIGNING_KEY_ID` is also set, it must be the key's id, so a release cannot be signed
with a key the keyring does not expect.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import pathlib
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


class SignError(Exception):
    pass


def key_id(raw_public: bytes) -> str:
    return hashlib.sha256(raw_public).hexdigest()[:16]


def raw_public_key(key: ed25519.Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def load_private_key(pem: str) -> ed25519.Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except (ValueError, TypeError):
        # The error text of the parser could quote the key; never pass it on.
        raise SignError("the signing key is not a PEM private key without a passphrase") from None
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise SignError("the signing key is not an Ed25519 private key")
    return key


def sign_bytes(data: bytes, pem: str, expected_key_id: str | None = None) -> dict:
    key = load_private_key(pem)
    kid = key_id(raw_public_key(key))
    if expected_key_id and expected_key_id != kid:
        raise SignError(f"CATALOG_SIGNING_KEY_ID is {expected_key_id}, but the key's id is {kid}")
    return {"keyId": kid, "sig": base64.b64encode(key.sign(data)).decode("ascii")}


def sign_file(path, pem: str, out=None, expected_key_id: str | None = None) -> pathlib.Path:
    path = pathlib.Path(path)
    sig = sign_bytes(path.read_bytes(), pem, expected_key_id)
    out = pathlib.Path(out) if out else path.with_name(path.name + ".sig")
    out.write_text(json.dumps(sig) + "\n", encoding="utf-8")
    return out


def verify(data: bytes, sig: dict, public_key_b64: str) -> bool:
    """The verifier's check, for tests and for checking a catalog before committing it."""
    try:
        raw = base64.b64decode(public_key_b64, validate=True)
        signature = base64.b64decode(sig["sig"], validate=True)
        ed25519.Ed25519PublicKey.from_public_bytes(raw).verify(signature, data)
        return True
    except (InvalidSignature, ValueError, KeyError, TypeError, binascii.Error):
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Sign a catalog with the Ed25519 key in CATALOG_SIGNING_KEY.")
    ap.add_argument("catalog", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args(argv)
    pem = os.environ.get("CATALOG_SIGNING_KEY", "")
    if not pem.strip():
        print("sign: CATALOG_SIGNING_KEY is not set (the PEM text of the Ed25519 private key)", file=sys.stderr)
        return 2
    try:
        out = sign_file(args.catalog, pem, args.out, os.environ.get("CATALOG_SIGNING_KEY_ID") or None)
    except (SignError, OSError) as e:
        print(f"sign: {e}", file=sys.stderr)
        return 1
    print(f"signed {args.catalog} with key {json.loads(out.read_text())['keyId']}: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
