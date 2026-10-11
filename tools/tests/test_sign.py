"""tools/sign.py and tools/gen_key.py: the signature RedAmon verifies, and keys kept out of repos (TD-11, R-27)."""

import base64
import hashlib
import json
import os
import stat
import subprocess
import sys

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

import gen_key
import sign
from support import ROOT


def _pem(key) -> str:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


def _raw_public(key) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


@pytest.fixture
def key():
    return ed25519.Ed25519PrivateKey.generate()


@pytest.fixture
def catalog_file(tmp_path):
    p = tmp_path / "catalog.json"
    p.write_bytes(b'{"catalogVersion": 1, "serial": 3}\n')
    return p


def test_round_trip_over_the_exact_bytes(key, catalog_file):
    out = sign.sign_file(catalog_file, _pem(key))
    sig = json.loads(out.read_text())
    assert out.name == "catalog.json.sig"
    assert set(sig) == {"keyId", "sig"}
    raw = base64.b64decode(sig["sig"], validate=True)
    assert len(raw) == 64
    # Pure Ed25519 over the file bytes, as Node's crypto.verify(null, bytes, key, sig) checks it.
    ed25519.Ed25519PublicKey.from_public_bytes(_raw_public(key)).verify(raw, catalog_file.read_bytes())


def test_key_id_is_the_first_16_hex_of_the_raw_public_key_sha256(key, catalog_file):
    sig = json.loads(sign.sign_file(catalog_file, _pem(key)).read_text())
    assert sig["keyId"] == hashlib.sha256(_raw_public(key)).hexdigest()[:16]
    assert sign.key_id(_raw_public(key)) == sig["keyId"]


def test_a_tampered_byte_fails(key, catalog_file):
    sig = base64.b64decode(json.loads(sign.sign_file(catalog_file, _pem(key)).read_text())["sig"])
    tampered = bytearray(catalog_file.read_bytes())
    tampered[5] ^= 1
    with pytest.raises(InvalidSignature):
        ed25519.Ed25519PublicKey.from_public_bytes(_raw_public(key)).verify(sig, bytes(tampered))
    assert not sign.verify(bytes(tampered), {"keyId": "x", "sig": base64.b64encode(sig).decode()},
                           base64.b64encode(_raw_public(key)).decode())
    assert sign.verify(catalog_file.read_bytes(), {"keyId": "x", "sig": base64.b64encode(sig).decode()},
                       base64.b64encode(_raw_public(key)).decode())


def test_a_key_that_is_not_ed25519_is_refused(catalog_file):
    other = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(sign.SignError, match="not an Ed25519 private key"):
        sign.sign_file(catalog_file, _pem(other))
    with pytest.raises(sign.SignError, match="not a PEM private key"):
        sign.sign_file(catalog_file, "not a key")


def _cli(args, env_extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CATALOG_SIGNING_")}
    env.update(env_extra)
    return subprocess.run([sys.executable, str(ROOT / "tools" / "sign.py"), *args], capture_output=True, text=True, env=env)


def test_cli_reads_the_key_from_the_environment_and_never_prints_it(key, catalog_file):
    pem = _pem(key)
    out = _cli([str(catalog_file)], {"CATALOG_SIGNING_KEY": pem})
    assert out.returncode == 0, out.stderr
    body = "".join(pem.splitlines()[1:-1])
    for stream in (out.stdout, out.stderr):
        assert "PRIVATE KEY" not in stream and body[:20] not in stream
    assert (catalog_file.parent / "catalog.json.sig").is_file()


def test_cli_without_a_key_fails(catalog_file):
    out = _cli([str(catalog_file)], {})
    assert out.returncode != 0
    assert "CATALOG_SIGNING_KEY is not set" in out.stderr


def test_cli_refuses_a_key_id_that_does_not_match_the_key(key, catalog_file):
    out = _cli([str(catalog_file)], {"CATALOG_SIGNING_KEY": _pem(key), "CATALOG_SIGNING_KEY_ID": "0000000000000000"})
    assert out.returncode != 0
    assert "CATALOG_SIGNING_KEY_ID" in out.stderr
    assert not (catalog_file.parent / "catalog.json.sig").exists()


# -- gen_key ---------------------------------------------------------------------------

def test_gen_key_writes_a_private_file_and_prints_the_public_half(tmp_path, capsys):
    out_dir = tmp_path / "keys" / "dev"
    assert gen_key.main(["--out", str(out_dir)]) == 0
    printed = capsys.readouterr().out
    pem = (out_dir / "signing-key.pem").read_text()
    assert stat.S_IMODE((out_dir / "signing-key.pem").stat().st_mode) == 0o600
    assert stat.S_IMODE(out_dir.stat().st_mode) == 0o700
    public = json.loads((out_dir / "public-key.json").read_text())
    raw = base64.b64decode(public["publicKey"], validate=True)
    assert len(raw) == 32
    assert public["keyId"] == hashlib.sha256(raw).hexdigest()[:16]
    assert public["keyId"] in printed and public["publicKey"] in printed
    assert json.loads(printed.split("PLUGINS_EXTRA_TRUSTED_KEYS=", 1)[1].splitlines()[0]) == [public]
    assert "PRIVATE KEY" not in printed
    # The key it wrote signs what the public half verifies.
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    assert isinstance(key, ed25519.Ed25519PrivateKey)
    assert _raw_public(key) == raw


def test_gen_key_refuses_a_folder_inside_a_git_repository(tmp_path, capsys):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    with pytest.raises(SystemExit):
        gen_key.main(["--out", str(tmp_path / "repo" / "keys")])
    assert "inside a git repository" in capsys.readouterr().err
    assert not (tmp_path / "repo" / "keys" / "signing-key.pem").exists()


def test_gen_key_never_overwrites_a_key(tmp_path, capsys):
    out_dir = tmp_path / "k"
    assert gen_key.main(["--out", str(out_dir)]) == 0
    first = (out_dir / "signing-key.pem").read_bytes()
    with pytest.raises(SystemExit):
        gen_key.main(["--out", str(out_dir)])
    assert "already holds a key" in capsys.readouterr().err
    assert (out_dir / "signing-key.pem").read_bytes() == first
