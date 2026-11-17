#!/bin/sh
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo=${1:?Usage: setup.sh NEW_REPO_PATH [ICEBERG_GIT_SOURCE]}
source=${2:-https://github.com/apache/iceberg-go.git}
before=410b789ed781852bbb4376e4f5cb57e76da12c56
fixed=3fb910983b9674efc6092418af90a1c2b821dd42
if [ -e "$repo" ] || [ -L "$repo" ]; then printf 'Destination already exists: %s\n' "$repo" >&2; exit 1; fi
parent=$(CDPATH='' cd -- "$(dirname -- "$repo")" && pwd)
repo="$parent/$(basename -- "$repo")"
cache="${repo}-go-cache"
if [ -e "$cache" ] || [ -L "$cache" ]; then printf 'Cache already exists: %s\n' "$cache" >&2; exit 1; fi
command -v go >/dev/null
command -v mentat >/dev/null
git clone --no-local --no-checkout -- "$source" "$repo" >&2
test "$(git -C "$repo" rev-parse "$before^{commit}")" = "$before"
test "$(git -C "$repo" rev-parse "$fixed^{commit}")" = "$fixed"
test "$(git -C "$repo" rev-parse "$fixed^1")" = "$before"
git -C "$repo" -c core.hooksPath=/dev/null switch -C mentat-iceberg-base "$fixed" >&2
git -C "$repo" branch --force mentat-iceberg-before "$before"
git -C "$repo" branch --force mentat-iceberg-fixed "$fixed"
mkdir -p "$cache/mod" "$cache/build" "$repo/.mentat"
for file in "$here"/contract/*; do
    [ ! -f "$file" ] || cp "$file" "$repo/.mentat/"
done
python3 - "$repo/.mentat/contract.json" "$cache/mod" "$cache/build" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1]); doc = json.loads(p.read_text())
for check in doc['behavior']:
    check['argv'] = [arg.replace('__MOD_CACHE__', sys.argv[2]).replace('__BUILD_CACHE__', sys.argv[3]) for arg in check['argv']]
p.write_text(json.dumps(doc, indent=2) + '\n')
PY
mentat claims render --repo "$repo"
git -C "$repo" add --force -- .mentat
git -C "$repo" -c user.name='Mentat Iceberg Example' -c user.email='iceberg@example.invalid' -c core.hooksPath=/dev/null commit --no-gpg-sign -qm 'Add Iceberg compaction contract'
printf 'Repository %s\nContract base %s\nBefore %s\nFixed %s\nCache %s\n' "$repo" "$(git -C "$repo" rev-parse HEAD)" "$before" "$fixed" "$cache"
