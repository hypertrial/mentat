#!/usr/bin/env bash
set -euo pipefail

# Validate the installed artifacts without importing the checkout or a caller's
# Python environment. This affects only this script and its child processes.
unset PYTHONPATH PYTHONHOME

dist_dir=$(cd "${1:?Pass the directory containing the built wheel and sdist}" && pwd)
repo_root=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo_root"
expected_version=$(uv run --locked --no-sync python -I -c \
  'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
artifact_dir=$(mktemp -d)
trap 'rm -rf -- "$artifact_dir"' EXIT

for artifact in "$dist_dir/"*.whl "$dist_dir/"*.tar.gz; do
    [[ -f "$artifact" ]]
    if [[ "$artifact" == *.tar.gz ]]; then
        python -I - "$artifact" <<'PY'
import sys
import tarfile

with tarfile.open(sys.argv[1], "r:gz") as archive:
    if any(member.name.split("/")[1:2] == [".git"] for member in archive):
        sys.exit("Source distribution contains Git metadata.")
PY
    fi
    venv="$artifact_dir/$(basename "$artifact").venv"
    UV_PROJECT_ENVIRONMENT="$venv" uv sync --locked --no-dev --no-install-project
    uv pip install --python "$venv/bin/python" --no-deps "$artifact"
    (
        cd "$artifact_dir"
        [[ $("$venv/bin/mentat" --version) == "mentat $expected_version" ]]
        "$venv/bin/mentat" showcase polaris --help > /dev/null
        "$venv/bin/python" -I - "$expected_version" <<'PY'
from importlib.metadata import distribution
from pathlib import Path
import subprocess
import sys
import tempfile

from mentat import evidence
from mentat.showcases.polaris.spec import PACK

dist = distribution("hypertrial-mentat")
assert dist.version == sys.argv[1]
assert dist.metadata["License-Expression"] == "MIT"
assert "LICENSE" in dist.metadata.get_all("License-File")
licenses = [dist.locate_file(path) for path in dist.files if str(path).endswith("/licenses/LICENSE")]
assert len(licenses) == 1 and "Hypertrial" in licenses[0].read_text()
assert PACK.is_relative_to(Path(sys.prefix))
for path in (
    "contract.json",
    "public.json",
    "lean/Check.lean",
    "lean/Main.lean",
    "lean/lakefile.toml",
    "lean/lean-toolchain",
    "runtime/Dockerfile",
    "runtime/oracle_cli.py",
    "runtime/requirements.txt",
):
    assert (PACK / path).is_file(), path
assert Path(evidence.__file__).is_relative_to(Path(sys.prefix))
with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
    root = Path(directory)
    state = evidence.state_directory(root / "state")
    run_id = "a" * 32
    evidence.save_run(
        state,
        {
            "schema_version": 2,
            "kind": "project-verification",
            "run_id": run_id,
            "revision": "b" * 40,
            "base": "c" * 40,
            "contract_digest": "d" * 64,
            "runner": "host",
            "status": "pass",
            "claims": [{"id": "wheel", "statement": "Wheel <claim>"}],
            "model_checks": [{
                "id": "model", "claim": "wheel", "kind": "z3",
                "status": "pass", "detail": "Z3 returned sat",
            }],
            "cases": [{"checks": [{
                "id": "behavior", "claim": "wheel", "status": "pass",
                "detail": "Check passed", "artifacts": {"result.txt": "result.txt"},
            }]}],
            "behavior_skipped": False,
            "reproduce": "PRIVATE_REPRODUCTION_COMMAND",
        },
        {"result.txt": b"PRIVATE_ARTIFACT_CONTENTS"},
    )
    output = root / "report.html"
    subprocess.run(
        [str(Path(sys.executable).with_name("mentat")), "--state", str(state),
         "evidence", "render", run_id, "--out", str(output)],
        check=True,
    )
    html = output.read_text()
    assert "Wheel &lt;claim&gt;" in html and run_id in html
    assert "PRIVATE_REPRODUCTION_COMMAND" not in html
    assert "PRIVATE_ARTIFACT_CONTENTS" not in html
PY
    )
done
