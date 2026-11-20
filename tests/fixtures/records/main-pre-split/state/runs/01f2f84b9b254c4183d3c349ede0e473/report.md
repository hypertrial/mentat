# Mentat verification evidence

- Run `01f2f84b9b254c4183d3c349ede0e473`
- Result **incomplete**
- Candidate `c498f7b11d6ba9057ecb26ddca1850d8e7692340`
- Approved contract `575f8a5c650133e7bdd476ecc69e7c02e066f8f8f98178664d1be1e9cf223202`

- Contract base `c498f7b11d6ba9057ecb26ddca1850d8e7692340`
- Runner `host`
- Claim outcomes · 0 pass, 0 violation, 1 incomplete, 0 skipped

## Evidence gap

Claim `clamp`
- Check `clamp-cases` · **incomplete** · Negative control did not demonstrate the expected violation
  - Command · pass · exit 0
  - Case `identity` · pass
  - Case `lower` · pass
  - Case `upper` · pass
  - [cases.json](./check-001.artifact-001.cases.json)

## Claims

### `clamp` · incomplete

Map inputs to 0 through 2 and preserve inputs already in range.
- Assumption · Inputs are integers from -3 through 3.

- Formal (z3) `clamp-model` · **pass** · Z3 returned unsat
  - Expected `"unsat"`
  - Observed `"unsat"`
- Behavior `clamp-cases` · **incomplete** · Negative control did not demonstrate the expected violation
  - Command · pass · exit 0
  - Case `identity` · pass
  - Case `lower` · pass
  - Case `upper` · pass
  - [cases.json](./check-001.artifact-001.cases.json)
- Negative control (did not qualify) `clamp-cases` · **pass** · Check passed
  - Command · pass · exit 0
  - Case `identity` · pass
  - Case `lower` · pass
  - Case `upper` · pass
  - [cases.json](./check-001.negative-control.artifact-001.cases.json)
- Source mutation (error) `upper-equivalent` · **incomplete** · Baseline check did not pass completely
  - Case `identity` · missing
  - Case `lower` · missing
  - Case `upper` · missing
- Expected failing case: `upper`

## Reproduce

    cd /home/runner/work/mentat/mentat && PATH=/work/src/main-pre-split/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin /work/src/main-pre-split/.venv/bin/python -P -c 'from mentat.cli import app; app()' --state /work/run/main-pre-split/state verify --repo /work/run/main-pre-split/repos/qualified --base c498f7b11d6ba9057ecb26ddca1850d8e7692340 --rev c498f7b11d6ba9057ecb26ddca1850d8e7692340 --runner host --json

### Recorded host environment

    {
      "executables": {
        "/bin/sh": {
          "checks": [
            "clamp-cases"
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
