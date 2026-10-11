"""The version-range grammar equals the platform's (TD-13): the shared cases decide."""

import json

import pytest

import semver


def test_every_shared_case(semver_cases):
    assert len(semver_cases) > 50
    for case in semver_cases:
        if case.get("invalid") and "range" in case:
            assert not semver.is_valid_range(case["range"]), case
            with pytest.raises(semver.RangeSyntaxError):
                semver.parse_range(case["range"])
        elif case.get("invalid"):
            assert not semver.is_valid_version(case["version"]), case
            with pytest.raises(semver.RangeSyntaxError):
                semver.parse_version(case["version"])
        else:
            assert semver.satisfies(case["version"], case["range"]) is case["expected"], case


def test_a_trailing_newline_is_not_the_end_of_the_string():
    # Python's `$` matches before a final newline; the grammar must not.
    assert not semver.is_valid_version("1.0.0\n")
    assert not semver.is_valid_range(">=1.0.0\n")


@pytest.mark.parametrize("value", [None, 1, ["1.0.0"], b"1.0.0"])
def test_non_strings_are_invalid(value):
    assert not semver.is_valid_version(value)
    assert not semver.is_valid_range(value)


def _repo(tmp_path, versions, plugin_range):
    for v in versions:
        (tmp_path / ".platform-sdk" / v).mkdir(parents=True)
        (tmp_path / ".platform-sdk" / v / "VERSION").write_text(v + "\n")
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "plugin.json").write_text(json.dumps({"platform": plugin_range}))
    return tmp_path


def test_lowest_synced_platform_the_range_accepts(tmp_path):
    root = _repo(tmp_path, ["1.0.0", "1.2.0", "1.10.0", "2.0.0"], ">=1.1.0 <2.0.0")
    assert semver.lowest_sdk(root / "p", root) == ".platform-sdk/1.2.0"


def test_lowest_compares_numbers_not_text(tmp_path):
    root = _repo(tmp_path, ["1.10.0", "1.9.0"], ">=1.9.0")
    assert semver.lowest_sdk(root / "p", root) == ".platform-sdk/1.9.0"


def test_no_synced_platform_accepts_the_range(tmp_path):
    root = _repo(tmp_path, ["1.0.0"], ">=3.0.0")
    with pytest.raises(semver.RangeSyntaxError, match="no synced platform"):
        semver.lowest_sdk(root / "p", root)


def test_cli_prints_the_folder(tmp_path, capsys):
    root = _repo(tmp_path, ["1.0.0"], ">=1.0.0 <2.0.0")
    assert semver.main(["lowest-sdk", str(root / "p"), "--root", str(root)]) == 0
    assert capsys.readouterr().out.strip() == ".platform-sdk/1.0.0"
