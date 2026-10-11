"""The GitHub workflows: what they run, and the controls around the signing key (D-25, D-26)."""

import re

import pytest
import yaml

from support import ROOT

WORKFLOWS = ROOT / ".github" / "workflows"
NAMES = ("ci.yml", "release.yml", "catalog-refresh.yml")
PINNED_USES = re.compile(r"^\s*-?\s*uses:\s*([\w.-]+/[\w./-]+)@([0-9a-f]{40})\s+#\s*v\d+\.\d+\.\d+\s*$")


def _load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _triggers(wf):
    # YAML 1.1 reads the bare key `on` as the boolean True.
    return wf.get("on", wf.get(True))


def _steps(wf):
    for job_name, job in wf["jobs"].items():
        for step in job.get("steps", []):
            yield job_name, job, step


@pytest.mark.parametrize("name", NAMES)
def test_parses_and_has_jobs(name):
    wf = _load(name)
    assert _triggers(wf), f"{name} has no trigger"
    assert wf["jobs"], f"{name} has no job"


@pytest.mark.parametrize("name", NAMES)
def test_actions_are_pinned_to_a_full_commit_sha_with_the_version_noted(name):
    lines = [ln for ln in (WORKFLOWS / name).read_text(encoding="utf-8").splitlines() if re.search(r"\buses:", ln)]
    assert lines, f"{name} uses no action"
    for ln in lines:
        assert PINNED_USES.match(ln), f"{name}: not pinned to a commit SHA with a '# vX.Y.Z' note: {ln.strip()}"


@pytest.mark.parametrize("name", NAMES)
def test_containers_are_pinned_by_digest(name):
    for job in _load(name)["jobs"].values():
        image = job.get("container")
        if image is not None:
            image = image["image"] if isinstance(image, dict) else image
            assert re.fullmatch(r"python:3\.11-slim@sha256:[0-9a-f]{64}", image), image


@pytest.mark.parametrize("name", NAMES)
def test_the_default_token_is_read_only(name):
    assert _load(name).get("permissions") == {"contents": "read"}


@pytest.mark.parametrize("name", NAMES)
def test_no_expression_is_expanded_inside_a_shell_script(name):
    # A tag name or a secret expanded into `run:` becomes shell code or a log line; both go
    # through `env:` instead.
    for job_name, _, step in _steps(_load(name)):
        script = step.get("run", "")
        assert "${{" not in script, f"{name}/{job_name}: '{step.get('name')}' expands an expression in its script"
        assert not re.search(r"\bset\s+-[a-z]*x", script), f"{name}/{job_name}: tracing would print secrets"


@pytest.mark.parametrize("name", NAMES)
def test_secrets_only_in_the_protected_environment_and_only_through_env(name):
    wf = _load(name)
    for job_name, job in wf["jobs"].items():
        text = yaml.safe_dump(job)
        if "secrets." not in text:
            continue
        assert job.get("environment") == "release", f"{name}/{job_name} reads a secret outside the release environment"
        for step in job.get("steps", []):
            outside_env = {k: v for k, v in step.items() if k != "env"}
            assert "secrets." not in yaml.safe_dump(outside_env), f"{name}/{job_name}: a secret outside env:"


def _signing_job(wf):
    jobs = [j for j in wf["jobs"].values() if j.get("environment") == "release"]
    assert len(jobs) == 1, "exactly one job runs in the release environment"
    return jobs[0]


@pytest.mark.parametrize("name", ["release.yml", "catalog-refresh.yml"])
def test_signing_runs_in_the_protected_environment_one_at_a_time(name):
    job = _signing_job(_load(name))
    assert job.get("concurrency") == {"group": "catalog", "cancel-in-progress": False}
    assert job.get("permissions") == {"contents": "write"}
    env = {}
    for step in job["steps"]:
        env.update(step.get("env", {}))
    assert env.get("CATALOG_SIGNING_KEY") == "${{ secrets.CATALOG_SIGNING_KEY }}"
    assert env.get("CATALOG_SIGNING_KEY_ID") == "${{ secrets.CATALOG_SIGNING_KEY_ID }}"
    scripts = "\n".join(step.get("run", "") for step in job["steps"])
    assert "tools/sign.py catalog.json" in scripts
    assert "git push" in scripts and "catalog.json.sig" in scripts


def test_release_runs_on_plugin_tags_and_tests_before_it_signs():
    wf = _load("release.yml")
    assert _triggers(wf) == {"push": {"tags": ["*-v*"]}}
    signing = _signing_job(wf)
    build_name = signing["needs"]
    build = wf["jobs"][build_name]
    assert "environment" not in build and "secrets." not in yaml.safe_dump(build)
    scripts = "\n".join(step.get("run", "") for step in build["steps"])
    for needed in ("tools/validate.py", "tools/run_tests.sh", "tools/pack.py"):
        assert needed in scripts, f"the build job does not run {needed}"
    publish = "\n".join(step.get("run", "") for step in signing["steps"])
    assert "sha256sum -c" in publish, "the archive released must be the one built and tested"
    assert "gh release" in publish and "tools/catalog.py add-release" in publish


def test_refresh_is_monthly_and_by_hand():
    triggers = _triggers(_load("catalog-refresh.yml"))
    assert "workflow_dispatch" in triggers
    [schedule] = triggers["schedule"]
    minute, hour, day, month, weekday = schedule["cron"].split()
    assert (day, month, weekday) == ("1", "*", "*")
    scripts = "\n".join(step.get("run", "") for step in _signing_job(_load("catalog-refresh.yml"))["steps"])
    assert "tools/catalog.py refresh" in scripts


def test_ci_validates_and_tests_everything_without_secrets():
    wf = _load("ci.yml")
    triggers = _triggers(wf)
    assert "push" in triggers and "pull_request" in triggers
    assert "secrets." not in yaml.safe_dump(wf)
    scripts = "\n".join(step.get("run", "") for _, _, step in _steps(wf))
    assert "tools/validate.py" in scripts and "tools/run_tests.sh all" in scripts
