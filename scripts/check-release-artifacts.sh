#!/usr/bin/env bash
set -euo pipefail

dist_dir=$(cd "${1:?Pass the directory containing the built wheel and sdist}" && pwd)
repo_root=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo_root"
expected_version=$(uv run --locked --no-sync python -c \
  'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
artifact_dir=$(mktemp -d)
trap 'rm -rf -- "$artifact_dir"' EXIT

for artifact in "$dist_dir/"*.whl "$dist_dir/"*.tar.gz; do
    [[ -f "$artifact" ]]
    venv="$artifact_dir/$(basename "$artifact").venv"
    UV_PROJECT_ENVIRONMENT="$venv" uv sync --locked --no-dev --no-install-project
    uv pip install --python "$venv/bin/python" --no-deps "$artifact"
    (
        cd "$artifact_dir"
        [[ $("$venv/bin/mentat" --version) == "mentat $expected_version" ]]
        "$venv/bin/mentat" showcase polaris --help > /dev/null
        "$venv/bin/python" - "$expected_version" <<'PY'
from importlib.metadata import distribution
from pathlib import Path
import sys

from mentat.showcases.polaris.spec import PACK

dist = distribution("hypertrial-mentat")
assert dist.version == sys.argv[1]
assert dist.metadata["License-Expression"] == "MIT"
assert "LICENSE" in dist.metadata.get_all("License-File")
licenses = [dist.locate_file(path) for path in dist.files if str(path).endswith("/licenses/LICENSE")]
assert len(licenses) == 1 and "Hypertrial" in licenses[0].read_text()
assert PACK.is_relative_to(Path(sys.prefix))
for path in ("contract.json", "public.json", "lean/Main.lean", "runtime/Dockerfile"):
    assert (PACK / path).is_file(), path
PY
    )
done
