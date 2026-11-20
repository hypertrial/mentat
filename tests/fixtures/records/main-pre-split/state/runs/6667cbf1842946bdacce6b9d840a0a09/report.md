# Mentat verification evidence

- Run `6667cbf1842946bdacce6b9d840a0a09`
- Result **pass**
- Candidate `081e0338e59a67072acf5502305cc809d0b11c7a`
- Approved contract `900625a54d81b4afe1d79c1b0f33185dd6e776a207b0625f2c7bb70fe49e0cfa`

- Contract base `081e0338e59a67072acf5502305cc809d0b11c7a`
- Runner `host`
- Claim outcomes · 1 pass, 0 violation, 0 incomplete, 0 skipped

## Claims

### `clamp` · pass

Clamp integers to the range 0 through 2.

- Formal (z3) `clamp-model` · **pass** · Z3 returned unsat
  - Expected `"unsat"`
  - Observed `"unsat"`
- Behavior `clamp-tests` · **pass** · Check passed
  - [stdout](./check-001.stdout.log)

## Reproduce

    cd /home/runner/work/mentat/mentat && PATH=/work/src/main-pre-split/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin /work/src/main-pre-split/.venv/bin/python -P -c 'from mentat.cli import app; app()' --state /work/run/main-pre-split/state verify --repo /work/run/main-pre-split/repos/python-src --base 081e0338e59a67072acf5502305cc809d0b11c7a --rev 081e0338e59a67072acf5502305cc809d0b11c7a --runner host --json

### Recorded host environment

    {
      "executables": {
        "python3": {
          "checks": [
            "clamp-tests"
          ],
          "path": "/work/src/main-pre-split/.venv/bin/python3",
          "status": "available"
        }
      },
      "invocation_directory": "/home/runner/work/mentat/mentat",
      "invocation_path": "/work/src/main-pre-split/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin",
      "mentat_python": "/work/src/main-pre-split/.venv/bin/python",
      "path": "/work/src/main-pre-split/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin",
      "platform": "Linux/x86_64",
      "prerequisites": {
        "executables": {
          "python3": {
            "checks": [
              "clamp-tests"
            ],
            "path": "/work/src/main-pre-split/.venv/bin/python3",
            "status": "available"
          }
        },
        "loopback": null,
        "python_distributions": {
          "pytest": {
            "status": "available",
            "version": "9.2.0"
          }
        },
        "python_executable": "/work/src/main-pre-split/.venv/bin/python3"
      },
      "runner": "host"
    }

Replay uses the tools currently installed at the recorded locations.
