"""Local minimum gates remain authoritative over repository proposals."""

import json

import pytest

from mentat.autonomy import default_config, state_path
from mentat.autonomy_policy import check_policy, load_policy_path, pin_policy


def test_known_repository_pins_all_minimum_gates_outside_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src/mentat").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "src/mentat/proof_checker.py").touch()
    (repo / "scripts/verify").touch()
    config = default_config(repo)
    policy = pin_policy(repo, tmp_path / "state", config)
    path = state_path(repo, tmp_path / "state")
    assert not path.is_relative_to(repo)
    assert policy["mandatory_local_checks"] == [["scripts/verify"], ["scripts/check-autonomy-package"]]
    assert policy["mandatory_worker_checks"] == [["scripts/verify-worker"]]
    assert policy["auto_merge"] is True
    assert load_policy_path(path) == policy
    for key in ("local_checks", "worker_checks"):
        changed = {**config, key: [["true"]]}
        with pytest.raises(ValueError, match="removes a required"):
            check_policy(changed, policy)


def test_unknown_repo_cannot_grant_itself_merge_authority(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    config = default_config(repo)
    assert not config["auto_merge"]
    policy = pin_policy(repo, tmp_path / "state", config)
    assert not policy["auto_merge"]
    proposed = {**config, "auto_merge": True, "local_checks": [["true"]]}
    with pytest.raises(ValueError, match="not authorized"):
        check_policy(proposed, policy)
    with pytest.raises(ValueError, match="not authorized"):
        pin_policy(repo, tmp_path / "state", proposed)


def test_policy_file_rejects_oversize_symlink_and_bad_plan(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    config = default_config(repo)
    path = state_path(repo, tmp_path / "state")
    policy = pin_policy(repo, tmp_path / "state", config)
    file = path.parent / "policy.json"
    file.write_text("x" * 65537)
    with pytest.raises(ValueError):
        load_policy_path(path)
    file.unlink()
    target = tmp_path / "target"
    target.write_text(json.dumps(policy))
    file.symlink_to(target)
    with pytest.raises(ValueError):
        load_policy_path(path)
    file.unlink()
    policy["mandatory_local_checks"] = [[""]]
    file.write_text(json.dumps(policy))
    with pytest.raises(ValueError, match="invalid pinned"):
        load_policy_path(path)
