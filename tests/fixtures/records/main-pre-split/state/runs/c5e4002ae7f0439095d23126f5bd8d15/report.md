# Mentat verification evidence

- Run `c5e4002ae7f0439095d23126f5bd8d15`
- Result **violation**
- Candidate `b39895d30775dae88c03822deecca22bd697b226`
- Approved contract `b77c461730646a438828d5fcf1716cd89eb3a5954f0e9be13e5de3d48005e820`

- Contract base `b39895d30775dae88c03822deecca22bd697b226`
- Runner `host`
- Claim outcomes · 0 pass, 1 violation, 0 incomplete, 0 skipped

## Counterexample

Claim `clamp`
- Check `clamp-model` · **violation** · Z3 returned sat, expected unsat
  - Expected `"unsat"`
  - Observed `"sat"`

Behavior checks were skipped because formal checks did not pass.

## Claims

### `clamp` · violation

Map inputs to 0 through 2 and preserve inputs already in range.
- Assumption · Inputs are integers from -3 through 3.

- Formal (z3) `clamp-model` · **violation** · Z3 returned sat, expected unsat
  - Expected `"unsat"`
  - Observed `"sat"`
- Behavior `clamp-cases` · **skipped** · Formal checks did not pass
- Source mutation (skipped) `identity-exit` · **skipped** · Formal checks did not pass
  - Case `identity` · missing
  - Case `lower` · missing
  - Case `upper` · missing
- Expected failing case: `identity`
- Source mutation (skipped) `upper-bound` · **skipped** · Formal checks did not pass
  - Case `identity` · missing
  - Case `lower` · missing
  - Case `upper` · missing
- Expected failing case: `upper`
- Source mutation (skipped) `upper-equivalent` · **skipped** · Formal checks did not pass
  - Case `identity` · missing
  - Case `lower` · missing
  - Case `upper` · missing
- Expected failing case: `upper`

## Reproduce

    cd /home/runner/work/mentat/mentat && PATH=/work/src/main-pre-split/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin /work/src/main-pre-split/.venv/bin/python -P -c 'from mentat.cli import app; app()' --state /work/run/main-pre-split/state verify --repo /work/run/main-pre-split/repos/qualified --base b39895d30775dae88c03822deecca22bd697b226 --rev b39895d30775dae88c03822deecca22bd697b226 --runner host --json

### Recorded host environment

    {
      "invocation_directory": "/home/runner/work/mentat/mentat",
      "invocation_path": "/work/src/main-pre-split/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin",
      "mentat_python": "/work/src/main-pre-split/.venv/bin/python",
      "path": "/work/src/main-pre-split/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin",
      "runner": "host"
    }

Replay uses the tools currently installed at the recorded locations.
