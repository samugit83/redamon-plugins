"""tools/pack.py: deterministic archives the platform's strict reader accepts (D-25, TD-12).

`strict_read` mirrors RedAmon's webapp/src/lib/plugins/archive.ts; the base repository's
packCompat.test.ts checks a real pack with archive.ts itself.
"""

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

import pack
from support import DOCS_URL, ICONS, ROOT

BLOCK = 512


def _octal(raw: bytes) -> int:
    text = raw.decode("latin1").rstrip("\0 ").lstrip(" ")
    return int(text, 8) if text else 0


def strict_read(gz: bytes, plugin_id: str) -> list:
    """The checks of archive.ts that pack.py must satisfy; returns (name, type, mode, size) rows."""
    assert gz[:2] == b"\x1f\x8b", "not gzip"
    assert gz[3] == 0, "gzip header carries optional fields (a file name or comment)"
    assert gz[4:8] == b"\0\0\0\0", "gzip mtime is not 0"
    tar = gzip.decompress(gz)
    rows, at = [], 0
    while at + BLOCK <= len(tar):
        h = tar[at:at + BLOCK]
        if h == b"\0" * BLOCK:
            break
        stored = _octal(h[148:156])
        assert stored == sum(0x20 if 148 <= i < 156 else b for i, b in enumerate(h)), "checksum"
        assert h[257:263] == b"ustar\0" and h[263:265] == b"00", "not POSIX ustar"
        typeflag = chr(h[156])
        assert typeflag in ("0", "5"), f"entry type {typeflag!r}"
        name = h[0:100].split(b"\0", 1)[0]
        prefix = h[345:500].split(b"\0", 1)[0]
        full = (prefix + b"/" + name if prefix else name).decode("utf-8")
        assert "\\" in full or True
        assert "\\" not in full and not full.startswith("/") and ".." not in full.split("/")
        assert h[157:257].strip(b"\0") == b"", "a link name"
        parts = full.rstrip("/").split("/")
        assert parts[0] == plugin_id, f"{full} is outside the top folder"
        size = _octal(h[124:136])
        mode = _octal(h[100:108])
        assert _octal(h[108:116]) == 0 and _octal(h[116:124]) == 0, "uid/gid not 0"
        assert _octal(h[136:148]) == 0, "mtime not 0"
        rows.append((full, typeflag, mode, size))
        at += BLOCK + -(-size // BLOCK) * BLOCK
    else:
        raise AssertionError("no end-of-archive block")
    assert not tar[at:].strip(b"\0"), "data after the end blocks"
    return rows


def _copy(src, dest):
    shutil.copytree(src, dest)
    return dest


def test_two_packs_of_the_same_folder_are_byte_identical(good_plugin, tmp_path):
    a = pack.pack(good_plugin, tmp_path / "a")
    # A second checkout of the same content, with other times and modes on disk.
    other = _copy(good_plugin, tmp_path / "elsewhere" / "good")
    for p in other.rglob("*"):
        os.utime(p, (1_000_000, 1_000_000))
    os.chmod(other / "engine" / "main.py", 0o755)
    b = pack.pack(other, tmp_path / "b")
    assert (tmp_path / "a" / "good-1.2.0.tar.gz").read_bytes() == (tmp_path / "b" / "good-1.2.0.tar.gz").read_bytes()
    assert a["sha256"] == b["sha256"]


def test_archive_passes_the_strict_reader_rules(good_plugin, tmp_path):
    out = pack.pack(good_plugin, tmp_path)
    gz = (tmp_path / "good-1.2.0.tar.gz").read_bytes()
    rows = strict_read(gz, "good")
    names = [r[0] for r in rows]
    assert names == sorted(names, key=lambda n: n.rstrip("/").split("/")), "entries are not sorted"
    assert names[0] == "good/"
    for name, typeflag, mode, size in rows:
        assert mode == (0o755 if typeflag == "5" else 0o644), name
    assert out["sha256"] == hashlib.sha256(gz).hexdigest()
    assert out["size"] == len(gz)


def test_tests_eval_and_caches_are_left_out(good_plugin, tmp_path):
    (good_plugin / "engine" / "__pycache__").mkdir()
    (good_plugin / "engine" / "__pycache__" / "main.cpython-311.pyc").write_bytes(b"x")
    (good_plugin / ".pytest_cache").mkdir()
    (good_plugin / ".pytest_cache" / "README.md").write_text("cache")
    pack.pack(good_plugin, tmp_path)
    names = [r[0] for r in strict_read((tmp_path / "good-1.2.0.tar.gz").read_bytes(), "good")]
    assert "good/plugin.json" in names and "good/engine/main.py" in names and "good/vendor/tinylib/__init__.py" in names
    for banned in ("good/tests/", "good/eval/", "__pycache__", ".pyc", ".pytest_cache"):
        assert not [n for n in names if banned in n], banned


def test_the_listing_is_left_out_and_changes_no_byte(good_plugin, tmp_path):
    # Presentation for the catalog only: an archive released before a plugin had a listing must
    # stay reproducible from a later checkout.
    before = pack.pack(good_plugin, tmp_path / "a")
    (good_plugin / "listing.json").write_text(json.dumps({"icon": "icon.png", "docsUrl": DOCS_URL}))
    for name, data in ICONS.items():
        (good_plugin / name).write_bytes(data)
    after = pack.pack(good_plugin, tmp_path / "b")
    assert after["sha256"] == before["sha256"]
    names = [r[0] for r in strict_read((tmp_path / "b" / "good-1.2.0.tar.gz").read_bytes(), "good")]
    assert not [n for n in names if n.split("/", 1)[1] in ("listing.json", *ICONS)]


def test_symlinks_are_refused(good_plugin, tmp_path):
    os.symlink("main.py", good_plugin / "engine" / "alias.py")
    with pytest.raises(pack.PackError, match="engine/alias.py is a symbolic link"):
        pack.pack(good_plugin, tmp_path)


def test_a_name_ustar_cannot_hold_is_refused(good_plugin, tmp_path):
    deep = good_plugin / "vendor" / ("d" * 120) / ("e" * 120)
    deep.mkdir(parents=True)
    (deep / "x.py").write_text("")
    with pytest.raises(pack.PackError, match="too long for a ustar archive"):
        pack.pack(good_plugin, tmp_path)


def test_folder_name_must_be_the_plugin_id(good_plugin, tmp_path):
    renamed = _copy(good_plugin, tmp_path / "x" / "renamed")
    with pytest.raises(pack.PackError, match="folder renamed holds plugin good"):
        pack.pack(renamed, tmp_path / "out")


def test_cli_prints_path_sha_and_size(good_plugin, tmp_path):
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "pack.py"), str(good_plugin), "--out-dir", str(tmp_path)],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    data = (tmp_path / "good-1.2.0.tar.gz").read_bytes()
    assert got == {"archive": str(tmp_path / "good-1.2.0.tar.gz"), "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
