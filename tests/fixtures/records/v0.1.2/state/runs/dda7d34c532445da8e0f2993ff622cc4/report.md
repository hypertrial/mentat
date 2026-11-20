# Mentat verification evidence

- Run `dda7d34c532445da8e0f2993ff622cc4`
- Result **incomplete**
- Candidate `c80cff74277dd9cb66fdb6c0fdf71204b2d9be4c`
- Approved contract `4eb3244211aa0aa330c012ef1dc0eebedf5797da4d196c8b859f9204a4dcbcb6`

- Contract base `c80cff74277dd9cb66fdb6c0fdf71204b2d9be4c`
- Runner `host`
- Claim outcomes · 0 pass, 0 violation, 1 incomplete, 0 skipped

## Evidence gap

Claim `clamp`
- Check `clamp-cli` · **incomplete** · Check exited unexpectedly

## Claims

### `clamp` · incomplete

Map inputs to 0 through 2 and preserve inputs already in range.
- Assumption · Inputs are integers from -3 through 3.

- Formal (z3) `clamp-model` · **pass** · Z3 returned unsat
  - Expected `"unsat"`
  - Observed `"unsat"`
- Behavior `clamp-cli` · **incomplete** · Check exited unexpectedly

## Reproduce

    cd /home/runner/work/mentat/mentat && PATH=/work/src/v0.1.2/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin /work/src/v0.1.2/.venv/bin/python -P -c 'from mentat.cli import app; app()' --state /work/run/v0.1.2/state verify --repo /work/run/v0.1.2/repos/quickstart --base c80cff74277dd9cb66fdb6c0fdf71204b2d9be4c --rev c80cff74277dd9cb66fdb6c0fdf71204b2d9be4c --runner host --json

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
