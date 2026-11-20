# Mentat verification evidence

- Run `4760c46b03984546a1e93053e0352ffd`
- Result **incomplete**
- Candidate `5453a33090ae4cab36cfb5572567f8eb1ca1e2db`
- Approved contract `21952688f25bc704a3931375498e78e48dde05c50259d6d73819b1dd1326787c`

- Contract base `5453a33090ae4cab36cfb5572567f8eb1ca1e2db`
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

    cd /home/runner/work/mentat/mentat && PATH=/work/src/main-pre-split/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin /work/src/main-pre-split/.venv/bin/python -P -c 'from mentat.cli import app; app()' --state /work/run/main-pre-split/state verify --repo /work/run/main-pre-split/repos/quickstart --base 5453a33090ae4cab36cfb5572567f8eb1ca1e2db --rev 5453a33090ae4cab36cfb5572567f8eb1ca1e2db --runner host --json

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
      "invocation_path": "/work/src/main-pre-split/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin",
      "mentat_python": "/work/src/main-pre-split/.venv/bin/python",
      "path": "/work/src/main-pre-split/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin",
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
