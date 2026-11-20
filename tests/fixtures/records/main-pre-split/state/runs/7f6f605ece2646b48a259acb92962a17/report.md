# Mentat verification evidence

- Run `7f6f605ece2646b48a259acb92962a17`
- Result **incomplete**
- Candidate `207cdd83a456d62fcfff04643876de0572dd9c5b`
- Approved contract `72925afb86b5298a55284efd310e3feb613b4c9e6e45fc654a596c81ac8ebcbe`

- Contract base `207cdd83a456d62fcfff04643876de0572dd9c5b`
- Runner `host`
- Claim outcomes · 0 pass, 0 violation, 1 incomplete, 0 skipped

## Qualification failure

Claim `clamp`
- Check `upper-equivalent` · **violation** · Mutation survived
  - Case `identity` · pass
  - Case `lower` · pass
  - Case `upper` · pass
  - [cases.json](./mutation-003.check-001.artifact-001.cases.json)

## Qualification gap

Claim `clamp`
- Check `identity-exit` · **incomplete** · Mutation check evidence was missing or could not be retained
  - Case `identity` · missing
  - Case `lower` · missing
  - Case `upper` · missing

## Claims

### `clamp` · incomplete

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
- Source mutation (error) `identity-exit` · **incomplete** · Mutation check evidence was missing or could not be retained
  - Case `identity` · missing
  - Case `lower` · missing
  - Case `upper` · missing
- Expected failing case: `identity`
- Source mutation (killed) `upper-bound` · **pass** · Expected case failed
  - Case `identity` · pass
  - Case `lower` · pass
  - Case `upper` · fail
  - [cases.json](./mutation-002.check-001.artifact-001.cases.json)
- Expected failing case: `upper`
- Source mutation (survived) `upper-equivalent` · **violation** · Mutation survived
  - Case `identity` · pass
  - Case `lower` · pass
  - Case `upper` · pass
  - [cases.json](./mutation-003.check-001.artifact-001.cases.json)
- Expected failing case: `upper`

## Reproduce

    cd /home/runner/work/mentat/mentat && PATH=/work/src/main-pre-split/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin /work/src/main-pre-split/.venv/bin/python -P -c 'from mentat.cli import app; app()' --state /work/run/main-pre-split/state verify --repo /work/run/main-pre-split/repos/qualified --base 207cdd83a456d62fcfff04643876de0572dd9c5b --rev 207cdd83a456d62fcfff04643876de0572dd9c5b --runner host --json

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
