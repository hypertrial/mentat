# Mentat verification evidence

- Run `6bb67f1b654f44159be2ec3f49b5cf16`
- Result **pass**
- Candidate `32bd16035cbad3984baf5970fe94e88fe2239050`
- Approved contract `c1ea1e6fe1f0e997e1bf235089cf7dad67b0eb2a001c28777f6ee03784a59dd9`

- Contract base `32bd16035cbad3984baf5970fe94e88fe2239050`
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

    cd /home/runner/work/mentat/mentat && PATH=/work/src/v0.1.2/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin /work/src/v0.1.2/.venv/bin/python -P -c 'from mentat.cli import app; app()' --state /work/run/v0.1.2/state verify --repo /work/run/v0.1.2/repos/python-src --base 32bd16035cbad3984baf5970fe94e88fe2239050 --rev 32bd16035cbad3984baf5970fe94e88fe2239050 --runner host --json

### Recorded host environment

    {
      "executables": {
        "python3": {
          "checks": [
            "clamp-tests"
          ],
          "path": "/work/src/v0.1.2/.venv/bin/python3",
          "status": "available"
        }
      },
      "invocation_directory": "/home/runner/work/mentat/mentat",
      "invocation_path": "/work/src/v0.1.2/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin",
      "mentat_python": "/work/src/v0.1.2/.venv/bin/python",
      "path": "/work/src/v0.1.2/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin",
      "platform": "Linux/x86_64",
      "prerequisites": {
        "executables": {
          "python3": {
            "checks": [
              "clamp-tests"
            ],
            "path": "/work/src/v0.1.2/.venv/bin/python3",
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
        "python_executable": "/work/src/v0.1.2/.venv/bin/python3"
      },
      "runner": "host"
    }

Replay uses the tools currently installed at the recorded locations.
