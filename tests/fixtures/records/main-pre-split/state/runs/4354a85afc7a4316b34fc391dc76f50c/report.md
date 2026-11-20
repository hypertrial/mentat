# Mentat verification evidence

- Run `4354a85afc7a4316b34fc391dc76f50c`
- Result **violation**
- Candidate `e800ee5763387bc9dfc0c8273b43356e1e889ca9`
- Approved contract `891b652faf705cd1878eebbd4545f3746e80b5d929f0a528533b5eddf2556702`

- Contract base `e800ee5763387bc9dfc0c8273b43356e1e889ca9`
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
- Behavior `clamp-cli` · **skipped** · Formal checks did not pass

## Reproduce

    cd /home/runner/work/mentat/mentat && PATH=/work/src/main-pre-split/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin /work/src/main-pre-split/.venv/bin/python -P -c 'from mentat.cli import app; app()' --state /work/run/main-pre-split/state verify --repo /work/run/main-pre-split/repos/quickstart --base e800ee5763387bc9dfc0c8273b43356e1e889ca9 --rev e800ee5763387bc9dfc0c8273b43356e1e889ca9 --runner host --json

### Recorded host environment

    {
      "invocation_directory": "/home/runner/work/mentat/mentat",
      "invocation_path": "/work/src/main-pre-split/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin",
      "mentat_python": "/work/src/main-pre-split/.venv/bin/python",
      "path": "/work/src/main-pre-split/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin",
      "runner": "host"
    }

Replay uses the tools currently installed at the recorded locations.
