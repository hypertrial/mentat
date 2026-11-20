# Mentat verification evidence

- Run `b57f9bdf6e1148538fd066be06003519`
- Result **violation**
- Candidate `2eee5f56be1e8dabfa891eec42674396ec442cce`
- Approved contract `afd9c613f56c336aceefbaa99a9a428ddd45af949a144a01acc723033d1c71d5`

- Contract base `2eee5f56be1e8dabfa891eec42674396ec442cce`
- Runner `host`
- Claim outcomes · 0 pass, 1 violation, 0 incomplete, 0 skipped

## Counterexample

Claim `clamp`
- Check `clamp-cli` · **violation** · Check reported a violation
  - [stdout](./check-001.stdout.log) · [counterexample.txt](./check-001.artifact-001.counterexample.txt)

## Claims

### `clamp` · violation

Map inputs to 0 through 2 and preserve inputs already in range.
- Assumption · Inputs are integers from -3 through 3.

- Formal (z3) `clamp-model` · **pass** · Z3 returned unsat
  - Expected `"unsat"`
  - Observed `"unsat"`
- Behavior `clamp-cli` · **violation** · Check reported a violation
  - [stdout](./check-001.stdout.log) · [counterexample.txt](./check-001.artifact-001.counterexample.txt)

## Reproduce

    cd /home/runner/work/mentat/mentat && PATH=/work/src/v0.1.2/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin /work/src/v0.1.2/.venv/bin/python -P -c 'from mentat.cli import app; app()' --state /work/run/v0.1.2/state verify --repo /work/run/v0.1.2/repos/quickstart --base 2eee5f56be1e8dabfa891eec42674396ec442cce --rev 2eee5f56be1e8dabfa891eec42674396ec442cce --runner host --json

### Recorded host environment

    {
      "executables": {
        "/bin/sh": {
          "checks": [
            "clamp-cli"
          ],
          "path": "/bin/sh",
          "status": "available"
        }
      },
      "invocation_directory": "/home/runner/work/mentat/mentat",
      "invocation_path": "/work/src/v0.1.2/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin",
      "mentat_python": "/work/src/v0.1.2/.venv/bin/python",
      "path": "/work/src/v0.1.2/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin",
      "platform": "Linux/x86_64",
      "prerequisites": {
        "executables": {},
        "loopback": null,
        "python_distributions": {},
        "python_executable": null
      },
      "runner": "host"
    }

Replay uses the tools currently installed at the recorded locations.
