# Mentat verification evidence

- Run `46575fae7d3c42e297aff4a91c8822ab`
- Result **incomplete**
- Candidate `55ddabe506153b7c079f354165fb801a234607aa`
- Approved contract `87db0852802076e874db18db6a9bee7ffef724250bce6192df5a1b69f348ac49`

- Contract base `7fd03730144abef8e7f7cdd13ca539e97d75e05a`
- Runner `host`
- Claim outcomes · 0 pass, 0 violation, 1 incomplete, 0 skipped

## Qualification gap

Claim `clamp`
- Check `lower-bound` · **incomplete** · Mutation must match exactly once
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
- Source mutation (error) `lower-bound` · **incomplete** · Mutation must match exactly once
  - Case `identity` · missing
  - Case `lower` · missing
  - Case `upper` · missing
- Expected failing case: `lower`

## Reproduce

    cd /home/runner/work/mentat/mentat && PATH=/work/src/main-pre-split/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin /work/src/main-pre-split/.venv/bin/python -P -c 'from mentat.cli import app; app()' --state /work/run/main-pre-split/state verify --repo /work/run/main-pre-split/repos/qualified --base 7fd03730144abef8e7f7cdd13ca539e97d75e05a --rev 55ddabe506153b7c079f354165fb801a234607aa --runner host --json

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
