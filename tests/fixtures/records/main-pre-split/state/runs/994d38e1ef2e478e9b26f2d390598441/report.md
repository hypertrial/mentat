# Mentat verification evidence

- Run `994d38e1ef2e478e9b26f2d390598441`
- Result **violation**
- Candidate `a489b4ec8d98696c2ade9d720b5c9e103bca595c`
- Approved contract `2763c44bcbde6d3c5c8b98dc10aaf602ef00190a8a1a2128bdf976571f91331a`

- Contract base `a489b4ec8d98696c2ade9d720b5c9e103bca595c`
- Runner `host`
- Claim outcomes · 0 pass, 1 violation, 0 incomplete, 0 skipped

## Qualification failure

Claim `clamp`
- Check `upper-equivalent` · **violation** · Mutation survived
  - Case `identity` · pass
  - Case `lower` · pass
  - Case `upper` · pass
  - [cases.json](./mutation-001.check-001.artifact-001.cases.json)

## Claims

### `clamp` · violation

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
- Source mutation (survived) `upper-equivalent` · **violation** · Mutation survived
  - Case `identity` · pass
  - Case `lower` · pass
  - Case `upper` · pass
  - [cases.json](./mutation-001.check-001.artifact-001.cases.json)
- Expected failing case: `upper`

## Reproduce

    cd /home/runner/work/mentat/mentat && PATH=/work/src/main-pre-split/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin /work/src/main-pre-split/.venv/bin/python -P -c 'from mentat.cli import app; app()' --state /work/run/main-pre-split/state verify --repo /work/run/main-pre-split/repos/qualified --base a489b4ec8d98696c2ade9d720b5c9e103bca595c --rev a489b4ec8d98696c2ade9d720b5c9e103bca595c --runner host --json

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
