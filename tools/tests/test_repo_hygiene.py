"""Keys and build output never enter this repository (R-27, P2)."""

import subprocess

from support import ROOT

KEY_MARKERS = ("PRIVATE KEY-----", "OPENSSH PRIVATE KEY")


def _ignore_lines() -> set:
    return {ln.strip() for ln in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()}


def test_gitignore_refuses_keys_env_files_and_build_output():
    lines = _ignore_lines()
    for pattern in ("*.pem", "*.key", ".env*", "dist/", "__pycache__/"):
        assert pattern in lines, f".gitignore lacks {pattern}"


def _tracked_or_present_files():
    try:
        git = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-co", "--exclude-standard"],
                             capture_output=True, text=True)
    except FileNotFoundError:
        git = None
    if git is not None and git.returncode == 0 and git.stdout.strip():
        return [ROOT / f for f in git.stdout.splitlines()]
    # No git in the container (or not a checkout): every file outside .git.
    return [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.relative_to(ROOT).parts]


def test_no_private_key_material_in_the_repository():
    offenders = []
    for path in _tracked_or_present_files():
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        text = path.read_bytes().decode("utf-8", "ignore")
        if any(m in text for m in KEY_MARKERS) and path.name != "test_repo_hygiene.py":
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"private key material found in {offenders}"


def test_licence_is_mit():
    assert (ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")
