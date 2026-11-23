"""Trusted controller inputs for a disposable autonomy worker.

Provisioning inputs must come from an operator-approved baseline, not a candidate.
A bundle contains only Git objects and one target ref, never host configuration.
"""

import base64
import hashlib
import os
import tempfile
from pathlib import Path

from . import gitio, process

MAX_BUNDLE = 64_000_000
MAX_BASELINE_FILE = 2_000_000

# Kept in sync with scripts/provision-autonomy-worker so installed wheels need no checkout.
PROVISION_SCRIPT = b'#!/usr/bin/env bash\n# Run as root only on the dedicated Ubuntu 24.04 ARM64 worker.\nset -euo pipefail\n[[ $(id -u) == 0 && $(uname -m) == aarch64 ]] || { echo \'Requires a root-owned ARM64 worker\' >&2; exit 1; }\n: "${MENTAT_BASELINE_PROJECT:?trusted baseline project required}"\n: "${MENTAT_BASELINE_LOCK:?trusted baseline lock required}"\n: "${MENTAT_BASELINE_PROJECT_SHA256:?baseline digest required}"\n: "${MENTAT_BASELINE_LOCK_SHA256:?baseline digest required}"\n: "${MENTAT_PROVISIONER_SHA256:?trusted provisioner digest required}"\n: "${MENTAT_PY314_REQUIREMENTS:?trusted Python requirements required}"\n: "${MENTAT_NPM_MANIFEST:?trusted npm manifest required}"\n: "${MENTAT_NPM_LOCK:?trusted npm lock required}"\n: "${MENTAT_PY314_REQUIREMENTS_SHA256:?trusted Python digest required}"\n: "${MENTAT_NPM_MANIFEST_SHA256:?trusted npm digest required}"\n: "${MENTAT_NPM_LOCK_SHA256:?trusted npm digest required}"\n[[ ! -e /opt/mentat-tools ]] || { echo \'Refusing to replace /opt/mentat-tools\' >&2; exit 1; }\nmkdir -m 755 /opt/mentat-tools\nok=0\ncleanup() { if [[ $ok != 1 ]]; then rm -rf -- /opt/mentat-tools; fi; }\ntrap cleanup EXIT\nroot=/opt/mentat-tools\nmkdir -p "$root/baseline" "$root/python" "$root/cache" "$root/bin" "$root/downloads" "$root/node" "$root/node-tools"\nprintf \'%s\' "$MENTAT_BASELINE_PROJECT" | base64 -d > "$root/baseline/pyproject.toml"\nprintf \'%s\' "$MENTAT_BASELINE_LOCK" | base64 -d > "$root/baseline/uv.lock"\nprintf \'%s  %s\\n\' "$MENTAT_BASELINE_PROJECT_SHA256" "$root/baseline/pyproject.toml" | sha256sum -c -\nprintf \'%s  %s\\n\' "$MENTAT_BASELINE_LOCK_SHA256" "$root/baseline/uv.lock" | sha256sum -c -\nfor entry in PY314_REQUIREMENTS:requirements-314.txt NPM_MANIFEST:package.json NPM_LOCK:package-lock.json; do\n  key=${entry%%:*}; file=${entry#*:}; value=MENTAT_$key; digest=MENTAT_${key}_SHA256\n  printf \'%s\' "${!value}" | base64 -d > "$root/node-tools/$file"\n  printf \'%s  %s\\n\' "${!digest}" "$root/node-tools/$file" | sha256sum -c -\ndone\napt-get update\nDEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl git xz-utils libatomic1\n# Digests: GitHub uv 0.11.13 release asset and Node 26.10.0 official SHASUMS.\ncurl --fail --silent --show-error --location --retry 3 --proto \'=https\' --tlsv1.2 \\\n  https://github.com/astral-sh/uv/releases/download/0.11.13/uv-aarch64-unknown-linux-gnu.tar.gz -o "$root/downloads/uv.tar.gz"\nprintf \'%s  %s\\n\' 12366407dc1fdba5179b10bd69c11ebfc2eff25791366089c0b2f5701056efc5 "$root/downloads/uv.tar.gz" | sha256sum -c -\ntar -xzf "$root/downloads/uv.tar.gz" -C "$root/downloads"\ninstall -m 755 "$root/downloads/uv-aarch64-unknown-linux-gnu/uv" "$root/bin/uv"\ncurl --fail --silent --show-error --location --retry 3 --proto \'=https\' --tlsv1.2 \\\n  https://nodejs.org/dist/v26.10.0/node-v26.10.0-linux-arm64.tar.xz -o "$root/downloads/node.tar.xz"\nprintf \'%s  %s\\n\' 7a6353f63eb3d04765004b4adf172616243e4522434635cb1d26288658b04ab5 "$root/downloads/node.tar.xz" | sha256sum -c -\ntar -xJf "$root/downloads/node.tar.xz" --strip-components=1 -C "$root/node"\nexport PATH="$root/bin:$root/node/bin:/usr/bin:/bin"\nexport UV_PYTHON_INSTALL_DIR="$root/python" UV_CACHE_DIR="$root/cache" PLAYWRIGHT_BROWSERS_PATH="$root/browsers"\nuv python install 3.12 3.14\nuv venv --python "$(uv python find 3.14)" "$root/.venv-3.14"\nuv pip install --python "$root/.venv-3.14/bin/python" --no-deps -r "$root/node-tools/requirements-314.txt"\nnpm ci --prefix "$root/node-tools" --ignore-scripts --no-audit --no-fund\nUV_PROJECT_ENVIRONMENT="$root/.venv" uv sync --project "$root/baseline" --locked --extra polaris --no-install-project\nuv pip install --python "$root/.venv/bin/python" \'playwright==1.65.2\' \'hatchling==1.29.0\'\n"$root/.venv/bin/python" -m playwright install --with-deps chromium\nbrowser=$("$root/.venv/bin/python" -c \'from playwright.sync_api import sync_playwright; p=sync_playwright().start(); print(p.chromium.executable_path); p.stop()\')\n[[ -x "$browser" ]] || { echo \'Chromium installation unavailable\' >&2; exit 1; }\nln -s "$browser" "$root/bin/chromium"\nln -s "$(uv python find 3.14)" "$root/bin/python3.14"\n"$root/.venv/bin/python" -c \'import sys; assert sys.version_info[:2] == (3, 12); import pytest, z3, typer, rich, hypothesis, hatchling, playwright\'\n"$root/.venv-3.14/bin/python" -c \'import sys; assert sys.version_info[:2] == (3, 14); import coverage, greenlet, hatchling, packaging, pathspec, playwright, pluggy, trove_classifiers, typing_extensions\'\nNODE_PATH="$root/node-tools/node_modules" "$root/node/bin/node" -e \'for (const p of ["istanbul-lib-coverage", "istanbul-lib-instrument", "istanbul-lib-report", "istanbul-reports"]) require(p)\'\n"$root/bin/uv" --version\n"$root/node/bin/node" --version\n"$root/bin/chromium" --version\nrm -rf -- "$root/downloads"\npython3 - "$root/profile.json" <<\'PY\'\nimport json, os, sys\nwith open(sys.argv[1], \'w\', encoding=\'utf-8\') as f:\n    json.dump({\'pyproject_sha256\': os.environ[\'MENTAT_BASELINE_PROJECT_SHA256\'],\n               \'uv_lock_sha256\': os.environ[\'MENTAT_BASELINE_LOCK_SHA256\'],\n               \'provisioner_sha256\': os.environ[\'MENTAT_PROVISIONER_SHA256\'],\n               \'py314_requirements_sha256\': os.environ[\'MENTAT_PY314_REQUIREMENTS_SHA256\'],\n               \'npm_manifest_sha256\': os.environ[\'MENTAT_NPM_MANIFEST_SHA256\'],\n               \'npm_lock_sha256\': os.environ[\'MENTAT_NPM_LOCK_SHA256\']}, f, sort_keys=True)\n    f.write(\'\\n\')\nPY\nchown -R root:root "$root"\nchmod -R go-w "$root"\nok=1\n'


def prepare_bundle(repo: Path, rev: str) -> bytes:
    """Package a full immutable commit and its reachable history in a bounded bundle."""
    repo = repo.resolve()
    gitio.verify_revision(repo, rev)
    common = gitio.run(repo, "rev-parse", "--path-format=absolute", "--git-common-dir", limit=4096).strip().decode()
    objects = Path(common) / "objects"
    if not objects.is_dir():
        raise ValueError("Git object directory unavailable")
    env = gitio.git_env()
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(objects)})
    with tempfile.TemporaryDirectory(prefix="mentat-bundle-") as tmp:
        bare = Path(tmp) / "objects.git"
        fmt = "sha1" if len(rev) == 40 else "sha256"
        for command in (["git", "init", "--bare", "--quiet", f"--object-format={fmt}", str(bare)],
                        ["git", "-C", str(bare), "update-ref", "refs/heads/target", rev],
                        ["git", "-C", str(bare), "symbolic-ref", "HEAD", "refs/heads/target"]):
            code, _, _ = process.run(command, env=env, output_limit=4096, timeout=60, stderr=False)
            if code:
                raise ValueError("cannot prepare isolated Git bundle")
        try:
            code, bundle, _ = process.run(
                ["git", "-C", str(bare), "bundle", "create", "-", "HEAD", "refs/heads/target"],
                env=env, output_limit=MAX_BUNDLE, timeout=120, stderr=False,
            )
        except (RuntimeError, TimeoutError) as exc:
            raise ValueError("Git bundle exceeded limits or timed out") from exc
        if code or not bundle.startswith((b"# v2 git bundle\n", b"# v3 git bundle\n")):
            raise ValueError("cannot create self-contained Git bundle")
        return bundle


def _baseline(repo: Path) -> list[tuple[str, bytes]]:
    files = []
    for name in ("pyproject.toml", "uv.lock"):
        path = repo / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BASELINE_FILE:
            raise ValueError(f"invalid trusted baseline {name}")
        files.append((name, path.read_bytes()))
    return files


def worker_assets() -> list[tuple[str, str, bytes]]:
    # These files ship in the Mentat wheel and never come from the candidate.
    from importlib.resources import files

    root = files("mentat").joinpath("worker_assets")
    return [(key, profile_key, root.joinpath(name).read_bytes()) for key, profile_key, name in (
        ("MENTAT_PY314_REQUIREMENTS", "py314_requirements_sha256", "requirements-314.txt"),
        ("MENTAT_NPM_MANIFEST", "npm_manifest_sha256", "package.json"),
        ("MENTAT_NPM_LOCK", "npm_lock_sha256", "package-lock.json"),
    )]


def profile_identity(repo: Path) -> dict[str, str]:
    """Identify the trusted baseline and the embedded immutable provisioner."""
    files = dict(_baseline(repo))
    return {
        "pyproject_sha256": hashlib.sha256(files["pyproject.toml"]).hexdigest(),
        "uv_lock_sha256": hashlib.sha256(files["uv.lock"]).hexdigest(),
        "provisioner_sha256": hashlib.sha256(PROVISION_SCRIPT).hexdigest(),
        **{profile_key: hashlib.sha256(data).hexdigest() for _, profile_key, data in worker_assets()},
    }


def provision_script(repo: Path) -> bytes:
    """Embed lockfiles from a baseline independently trusted by the controller."""
    files = _baseline(repo)
    prefix = [b"#!/usr/bin/env bash\n"]
    for name, data in files:
        key = "MENTAT_BASELINE_PROJECT" if name == "pyproject.toml" else "MENTAT_BASELINE_LOCK"
        prefix.append(f"export {key}='{base64.b64encode(data).decode()}'\n".encode())
        prefix.append(f"export {key}_SHA256='{hashlib.sha256(data).hexdigest()}'\n".encode())
    for key, _, data in worker_assets():
        prefix.append(f"export {key}='{base64.b64encode(data).decode()}'\n".encode())
        prefix.append(f"export {key}_SHA256='{hashlib.sha256(data).hexdigest()}'\n".encode())
    prefix.append(f"export MENTAT_PROVISIONER_SHA256='{hashlib.sha256(PROVISION_SCRIPT).hexdigest()}'\n".encode())
    return b"".join(prefix) + PROVISION_SCRIPT
