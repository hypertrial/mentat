"""Worker input is pinned source history and explicitly trusted provisioning data."""

import hashlib
import os
import subprocess

import pytest

from mentat.autonomy_worker import prepare_bundle, provision_script


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def test_bundle_contains_exact_revision_and_no_host_config(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "remote.origin.url", "ssh://private-token@example.invalid/repo")
    (repo / "source.py").write_text("x = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "first")
    rev = git(repo, "rev-parse", "HEAD")
    (repo / "source.py").write_text("x = 2\n")
    git(repo, "commit", "-qam", "second")
    bundle = prepare_bundle(repo, rev)
    assert b"private-token" not in bundle
    path = tmp_path / "source.bundle"
    path.write_bytes(bundle)
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "-q", str(path), str(checkout)], check=True)
    assert git(checkout, "rev-parse", "HEAD") == rev
    assert (checkout / "source.py").read_text() == "x = 1\n"
    assert "private-token" not in (checkout / ".git/config").read_text()
    assert git(checkout, "rev-list", "--all", "--count") == "1"
    with pytest.raises(ValueError):
        prepare_bundle(repo, "HEAD")


def test_provision_script_embeds_baseline_and_rejects_links(tmp_path):
    project = b"[project]\nname = 'baseline'\n"
    lock = b'version = 1\n'
    (tmp_path / "pyproject.toml").write_bytes(project)
    (tmp_path / "uv.lock").write_bytes(lock)
    script = provision_script(tmp_path)
    assert hashlib.sha256(project).hexdigest().encode() in script
    assert hashlib.sha256(lock).hexdigest().encode() in script
    assert b"--locked" in script and b"--no-install-project" in script
    assert b"/opt/mentat-tools" in script
    (tmp_path / "uv.lock").unlink()
    os.symlink(tmp_path / "pyproject.toml", tmp_path / "uv.lock")
    with pytest.raises(ValueError, match="trusted baseline"):
        provision_script(tmp_path)


def test_bundle_size_limit_fails_closed(tmp_path, monkeypatch):
    import mentat.autonomy_worker as worker

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "source").write_bytes(os.urandom(4096))
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "fixture")
    rev = git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(worker, "MAX_BUNDLE", 128)
    with pytest.raises(ValueError, match="limit"):
        worker.prepare_bundle(repo, rev)


def test_bundled_provisioner_matches_operator_script():
    from pathlib import Path

    from mentat.autonomy_worker import PROVISION_SCRIPT

    assert PROVISION_SCRIPT == (Path(__file__).parents[1] / "scripts/provision-autonomy-worker").read_bytes()


def test_profile_identity_tracks_exact_baseline_and_provisioner(tmp_path):
    from mentat.autonomy_worker import PROVISION_SCRIPT, profile_identity

    (tmp_path / "pyproject.toml").write_text("[project]\n")
    (tmp_path / "uv.lock").write_text("version = 1\n")
    identity = profile_identity(tmp_path)
    assert {key: identity[key] for key in ("pyproject_sha256", "uv_lock_sha256", "provisioner_sha256")} == {
        "pyproject_sha256": hashlib.sha256(b"[project]\n").hexdigest(),
        "uv_lock_sha256": hashlib.sha256(b"version = 1\n").hexdigest(),
        "provisioner_sha256": hashlib.sha256(PROVISION_SCRIPT).hexdigest(),
    }
    assert all(value.encode() in provision_script(tmp_path) for value in identity.values())
    (tmp_path / "uv.lock").write_text("version = 2\n")
    assert profile_identity(tmp_path)["uv_lock_sha256"] != identity["uv_lock_sha256"]


@pytest.mark.parametrize('failure', [None, 'missing', 'duplicate', 'extra', 'version'])
def test_build_cache_seed_compiles_only_pinned_closure_for_both_pythons(tmp_path, failure):
    import json

    from mentat.autonomy_worker import PROVISION_SCRIPT

    root = tmp_path / 'tools'
    (root / 'downloads').mkdir(parents=True)
    (root / 'node-tools').mkdir()
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    pins = [
        'hatchling==1.29.0', 'packaging==26.3', 'pathspec==0.12.1',
        'pluggy==1.7.0', 'trove-classifiers==2025.5.9.12',
    ]
    fixture = pins[1:] if failure == 'missing' else pins + ([pins[0]] if failure == 'duplicate' else [])
    (root / 'node-tools/requirements-314.txt').write_text('\n'.join(fixture + ['playwright==1.65.2']) + '\n')
    fake_uv = bin_dir / 'uv'
    fake_uv.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
assert args[:2] == ['pip', 'compile']
assert '--no-header' in args and '--no-annotate' in args
with open(os.environ['SEED_LOG'], 'a') as f:
    f.write(json.dumps(args) + '\\n')
source = pathlib.Path(args[args.index('--no-annotate') + 1])
output = pathlib.Path(args[args.index('-o') + 1])
lines = source.read_text().splitlines()
if os.environ['SEED_FAILURE'] == 'extra': lines.append('unexpected==1')
if os.environ['SEED_FAILURE'] == 'version': lines[0] = lines[0].split('==')[0] + '==999'
output.write_text('\\n'.join(reversed(lines)) + '\\n')
''')
    fake_uv.chmod(0o755)
    script = PROVISION_SCRIPT.decode()
    stage = script.split("# Populate uv's resolver cache for isolated offline builds using only the pinned\n", 1)[1].split('"$root/.venv/bin/python" -m playwright', 1)[0]
    log = tmp_path / 'uv.log'
    env = {**os.environ, 'PATH': str(bin_dir) + os.pathsep + os.environ['PATH'],
           'SEED_LOG': str(log), 'SEED_FAILURE': failure or ''}
    result = subprocess.run(['bash', '-c', 'set -euo pipefail\nroot=$1\n' + stage,
                             'seed', str(root)], env=env, capture_output=True, text=True)
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    if failure is not None:
        assert result.returncode != 0
        assert len(calls) <= 1
        return
    assert result.returncode == 0, result.stderr
    assert len(calls) == 2
    assert [call[call.index('--python') + 1] for call in calls] == [
        str(root / '.venv/bin/python'), str(root / '.venv-3.14/bin/python')]
    assert (root / 'downloads/build-requirements.txt').read_text().splitlines() == sorted(pins)
    assert 'playwright' not in (root / 'downloads/build-requirements.txt').read_text()


@pytest.mark.parametrize('with_git', [False, True])
def test_release_artifact_checks_ignore_ambient_python_paths(tmp_path, with_git):
    import io
    import tarfile
    from pathlib import Path

    project = tmp_path / 'project'
    scripts = project / 'scripts'
    scripts.mkdir(parents=True)
    source = Path(__file__).parents[1] / 'scripts/check-release-artifacts.sh'
    (scripts / source.name).write_bytes(source.read_bytes())
    marker = tmp_path / 'checkout-module-executed'
    (project / 'tarfile.py').write_text(
        f"open({str(marker)!r}, 'w').write('executed')\n"
        "class FakeArchive:\n"
        "    def __enter__(self): return self\n"
        "    def __exit__(self, *args): pass\n"
        "    def __iter__(self): return iter([])\n"
        "def open(*args, **kwargs): return FakeArchive()\n"
    )
    dist = tmp_path / 'dist'
    dist.mkdir()
    (dist / 'fixture.whl').write_bytes(b'placeholder')
    with tarfile.open(dist / 'fixture.tar.gz', 'w:gz') as archive:
        info = tarfile.TarInfo('fixture/source.py')
        info.size = 1
        archive.addfile(info, io.BytesIO(b'x'))
        if with_git:
            info = tarfile.TarInfo('fixture/.git/config')
            info.size = 1
            archive.addfile(info, io.BytesIO(b'x'))
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    fake_uv = bin_dir / 'uv'
    fake_uv.write_text('''#!/usr/bin/env bash
set -eu
if [[ $1 == run ]]; then
  [[ $* == *'python -I -c'* ]] || exit 33
  echo 1.2.3; exit 0
fi
if [[ $1 == sync ]]; then
  mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"
  cat > "$UV_PROJECT_ENVIRONMENT/bin/mentat" <<'EOF'
#!/usr/bin/env bash
[[ -z ${PYTHONPATH+x} && -z ${PYTHONHOME+x} ]] || exit 31
if [[ ${1-} == --version ]]; then echo 'mentat 1.2.3'; fi
EOF
  cat > "$UV_PROJECT_ENVIRONMENT/bin/python" <<'EOF'
#!/usr/bin/env bash
[[ -z ${PYTHONPATH+x} && -z ${PYTHONHOME+x} ]] || exit 32
[[ ${1-} == -I && ${2-} == - ]] || exit 34
cat > /dev/null
EOF
  chmod +x "$UV_PROJECT_ENVIRONMENT/bin/mentat" "$UV_PROJECT_ENVIRONMENT/bin/python"
  exit 0
fi
[[ $1 == pip && $2 == install ]]
''')
    fake_uv.chmod(0o755)
    env = {**os.environ, 'PATH': str(bin_dir) + os.pathsep + os.environ['PATH'],
           'PYTHONPATH': '/hostile/checkout/src', 'PYTHONHOME': '/hostile/python'}
    result = subprocess.run(['bash', str(scripts / source.name), str(dist)],
                            env=env, capture_output=True, text=True)
    if with_git:
        assert result.returncode != 0
        assert 'Git metadata' in result.stderr
    else:
        assert result.returncode == 0, result.stderr
    assert not marker.exists()
