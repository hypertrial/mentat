# Mentat verification evidence

- Run `4d71e2a16ed9410ba1aeb99dea985b9b`
- Result **pass**
- Candidate `67ce9a6e051450a4de6dd913907005d6f8e759b0`
- Approved contract `508e5cc4c8a1d5b36211f05d6202cd46a0be673e5650f94def70a267e74e240e`

- Contract base `67ce9a6e051450a4de6dd913907005d6f8e759b0`
- Runner `host`
- Claim outcomes · 1 pass, 0 violation, 0 incomplete, 0 skipped

## Claims

### `clamp` · pass

Map inputs to 0 through 2 and preserve inputs already in range.
- Assumption · Inputs are integers from -3 through 3.

- Formal (z3) `clamp-model` · **pass** · Z3 returned unsat
  - Expected `"unsat"`
  - Observed `"unsat"`
- Behavior `clamp-cases` · **pass** · Check passed
  - Command · pass · exit 0
  - Case `identity` · pass
  - Case `lower` · pass
  - Case `upper` · pass
  - [cases.json](./check-001.artifact-001.cases.json)
- Negative control (qualified) `clamp-cases` · **violation** · Check reported a violation
  - Command · violation · exit 10
  - Case `identity` · pass
  - Case `lower` · pass
  - Case `upper` · fail
  - [cases.json](./check-001.negative-control.artifact-001.cases.json)
- Source mutation (killed) `lower-bound` · **pass** · Expected case failed
  - Case `identity` · pass
  - Case `lower` · fail
  - Case `upper` · pass
  - [cases.json](./mutation-001.check-001.artifact-001.cases.json)
- Expected failing case: `lower`
- Source mutation (killed) `upper-bound` · **pass** · Expected case failed
  - Case `identity` · pass
  - Case `lower` · pass
  - Case `upper` · fail
  - [cases.json](./mutation-002.check-001.artifact-001.cases.json)
- Expected failing case: `upper`

## Reproduce

    cd /home/runner/work/mentat/mentat && PATH=/work/src/main-pre-split/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin /work/src/main-pre-split/.venv/bin/python -P -c 'from mentat.cli import app; app()' --state /work/run/main-pre-split/state verify --repo /work/run/main-pre-split/repos/qualified --base 67ce9a6e051450a4de6dd913907005d6f8e759b0 --rev 67ce9a6e051450a4de6dd913907005d6f8e759b0 --runner host --json

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
