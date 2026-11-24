"""Operator opt-in binds reviewed generic-repository gates to local authority."""

import copy
import json

import pytest
from test_autonomy import repo as repo
from typer.testing import CliRunner

from mentat.autonomy import default_config, state_path
from mentat.autonomy_policy import check_policy, minimum_gates, minimum_source_checks
from mentat.cli import app


def _write_config(repo, config):
    (repo / ".mentat/workspace.json").write_text(json.dumps(config))


def _init(repo, state, *, trust=False):
    args = ["--state", str(state), "init", "--repo", str(repo)]
    if trust:
        args.append("--trust-configured-checks")
    return CliRunner().invoke(app, args)


def _policy_path(repo, state):
    return state_path(repo, state).parent / "policy.json"


def _config(repo):
    config = default_config(repo)
    config.update(auto_merge=True, local_checks=[["python", "-m", "compileall", "src"]],
                  worker_checks=[["python", "-m", "pytest", "tests"]],
                  source_checks=[{"path": "src.py", "function": "value", "inputs": {},
                                  "requires": "True", "ensures": "result >= 0"}])
    return config


def test_generic_auto_merge_refused_by_default_with_configured_checks(repo, tmp_path):
    config = _config(repo)
    _write_config(repo, config)
    state = tmp_path / "no-opt-in"
    result = _init(repo, state)
    assert result.exit_code == 2, result.output
    assert json.loads(result.stdout)["status"] == "incomplete"
    assert not _policy_path(repo, state).exists()


def test_explicit_init_pins_generic_checks_and_enforces_policy(repo, tmp_path):
    config = _config(repo)
    config["local_checks"] += [["python", "-m", "compileall", "src"], ["ruff", "check", "."]]
    config["worker_checks"] += [["python", "-m", "pytest", "tests"]]
    _write_config(repo, config)
    state = tmp_path / "opted-in"
    result = _init(repo, state, trust=True)
    assert result.exit_code == 0, result.output
    policy = json.loads(result.stdout)["local_policy"]
    assert policy["auto_merge"] is True
    assert policy["mandatory_local_checks"] == [["python", "-m", "compileall", "src"],
                                                ["ruff", "check", "."]]
    assert policy["mandatory_worker_checks"] == [["python", "-m", "pytest", "tests"]]
    assert policy["mandatory_source_checks"] == config["source_checks"]
    assert policy["schema_version"] == 2
    assert json.loads(_policy_path(repo, state).read_text()) == policy
    for field in ("local_checks", "worker_checks", "source_checks"):
        changed = copy.deepcopy(config)
        changed[field] = []
        with pytest.raises(ValueError, match="(?i)(removes|required)"):
            check_policy(changed, policy)
    reordered = copy.deepcopy(config)
    reordered["local_checks"] = list(reversed(config["local_checks"]))
    check_policy(reordered, policy)


def test_empty_checks_never_authorize_generic_auto_merge(repo, tmp_path):
    config = _config(repo)
    config.update(local_checks=[], worker_checks=[])
    _write_config(repo, config)
    state = tmp_path / "empty"
    result = _init(repo, state, trust=True)
    assert result.exit_code == 2, result.output
    assert not _policy_path(repo, state).exists()


def test_existing_policy_cannot_be_silently_replaced_by_opt_in(repo, tmp_path):
    config = _config(repo)
    config["auto_merge"] = False
    _write_config(repo, config)
    state = tmp_path / "existing"
    initial = _init(repo, state)
    assert initial.exit_code == 0, initial.output
    path = _policy_path(repo, state)
    before = path.read_bytes()
    denied = _init(repo, state, trust=True)
    assert denied.exit_code == 2, denied.output
    assert "state" in denied.stdout.lower()
    assert path.read_bytes() == before
    fresh = _init(repo, tmp_path / "new-state", trust=True)
    assert fresh.exit_code == 0, fresh.output
    assert path.read_bytes() == before


def test_repeat_enrollment_same_gates_preserves_policy_bytes(repo, tmp_path):
    config = _config(repo)
    config["local_checks"].append(["ruff", "check", "."])
    _write_config(repo, config)
    state = tmp_path / "existing-enrolled"
    first = _init(repo, state, trust=True)
    assert first.exit_code == 0, first.output
    path = _policy_path(repo, state)
    before = path.read_bytes()
    config["local_checks"] = list(reversed(config["local_checks"]))
    _write_config(repo, config)
    second = _init(repo, state, trust=True)
    assert second.exit_code == 0, second.output
    assert path.read_bytes() == before
    assert json.loads(first.stdout)["local_policy"] == json.loads(second.stdout)["local_policy"]


@pytest.mark.parametrize("profile", ["mentat", "titanic"])
def test_builtin_floors_and_source_checks_survive_opt_in(repo, tmp_path, profile):
    if profile == "mentat":
        (repo / "src/mentat").mkdir(parents=True)
        (repo / "src/mentat/proof_checker.py").touch()
        (repo / "scripts").mkdir()
        (repo / "scripts/verify").touch()
    else:
        for name in ("app.py", "verify.py", "titanic.csv"):
            (repo / name).touch()
    minimum_local, minimum_worker = minimum_gates(repo)
    minimum_source = minimum_source_checks(repo)
    config = default_config(repo)
    config["local_checks"] += minimum_local + [["extra", "local"]]
    config["worker_checks"] += minimum_worker + [["extra", "worker"]]
    _write_config(repo, config)
    result = _init(repo, tmp_path / profile, trust=True)
    assert result.exit_code == 0, result.output
    policy = json.loads(result.stdout)["local_policy"]
    assert policy["mandatory_local_checks"] == minimum_local + [["extra", "local"]]
    assert policy["mandatory_worker_checks"] == minimum_worker + [["extra", "worker"]]
    assert policy["mandatory_source_checks"] == minimum_source
    for field in ("local_checks", "worker_checks"):
        if not config[field]:
            continue
        changed = copy.deepcopy(config)
        changed[field] = [["extra", "local"]] if field == "local_checks" else [["extra", "worker"]]
        if field == "worker_checks" and not minimum_worker:
            continue
        with pytest.raises(ValueError, match="removes a required"):
            check_policy(changed, policy)


@pytest.mark.parametrize("field", ["local_checks", "worker_checks"])
def test_one_nonempty_gate_category_can_authorize(repo, tmp_path, field):
    config = _config(repo)
    config["worker_checks" if field == "local_checks" else "local_checks"] = []
    _write_config(repo, config)
    result = _init(repo, tmp_path / field, trust=True)
    assert result.exit_code == 0, result.output
    policy = json.loads(result.stdout)["local_policy"]
    assert policy["auto_merge"] is True
    assert policy["mandatory_" + field] == config[field]


def test_new_configured_gate_cannot_expand_an_existing_enrollment(repo, tmp_path):
    config = _config(repo)
    _write_config(repo, config)
    state = tmp_path / "existing-extra"
    first = _init(repo, state, trust=True)
    assert first.exit_code == 0, first.output
    path = _policy_path(repo, state)
    original = path.read_bytes()
    config["worker_checks"].append(["additional", "check"])
    _write_config(repo, config)
    rejected = _init(repo, state, trust=True)
    assert rejected.exit_code == 2, rejected.output
    assert "state" in rejected.stdout.lower()
    assert path.read_bytes() == original
