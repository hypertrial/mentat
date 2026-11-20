# Mentat verification evidence

- Run `7e049a4e9d4e4004ac4d7f28b0357619`
- Result **violation**
- Candidate `c81cd056a2f94d072558bf785c7916b923064b9f`
- Approved contract `f5ee6223581c1376f7b6b9f2c11401b6c06bb87739b4f7963573e51dc28002a8`

- Contract base `c81cd056a2f94d072558bf785c7916b923064b9f`
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

    cd /home/runner/work/mentat/mentat && PATH=/work/src/v0.1.2/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin /work/src/v0.1.2/.venv/bin/python -P -c 'from mentat.cli import app; app()' --state /work/run/v0.1.2/state verify --repo /work/run/v0.1.2/repos/quickstart --base c81cd056a2f94d072558bf785c7916b923064b9f --rev c81cd056a2f94d072558bf785c7916b923064b9f --runner host --json

### Recorded host environment

    {
      "invocation_directory": "/home/runner/work/mentat/mentat",
      "invocation_path": "/work/src/v0.1.2/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin",
      "mentat_python": "/work/src/v0.1.2/.venv/bin/python",
      "path": "/work/src/v0.1.2/.venv/bin:/home/runner/work/_temp/uv-python-dir:/opt/hostedtoolcache/uv/0.11.13/x86_64:/home/runner/.local/bin:/snap/bin:/home/runner/.local/bin:/opt/pipx_bin:/home/runner/.cargo/bin:/home/runner/.config/composer/vendor/bin:/usr/local/.ghcup/bin:/home/runner/.dotnet/tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin",
      "runner": "host"
    }

Replay uses the tools currently installed at the recorded locations.
