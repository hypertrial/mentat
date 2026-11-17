#!/bin/sh
set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
root=${1:?Usage: setup.sh NEW_RUN_DIRECTORY [NEMO_GIT_SOURCE]}
source=${2:-https://github.com/knowsys/nemo.git}
before=c8396e9ad20ced0252816772223a2b23c227368d
fixed=93f219c31f227f04fbde3a1ec272343a69c4c967
context=${MENTAT_DOCKER_CONTEXT:-colima-mentat-nemo}

umask 077
mkdir -- "$root"
root=$(CDPATH='' cd -- "$root" && pwd -P)
repo="$root/nemo"
git clone --no-local --no-checkout --origin origin -- "$source" "$repo" >&2
if ! git -C "$repo" cat-file -e "$fixed^{commit}" 2>/dev/null; then
    git -C "$repo" fetch --no-tags origin "$fixed" >&2
fi
test "$(git -C "$repo" rev-parse "$fixed^1")" = "$before"
test "$(git -C "$repo" rev-parse "$fixed:Cargo.lock")" = \
    "$(git -C "$repo" rev-parse "$before:Cargo.lock")"

image=${NEMO_IMAGE:-}
if [ -z "$image" ]; then
    image=$(sh "$here/prepare-image.sh" "$repo" "$before")
fi
docker --context "$context" image inspect "$image" >/dev/null

git -C "$repo" switch -C mentat-nemo-public "$before" >&2
git -C "$repo" branch --force mentat-nemo-fixed "$fixed"
mkdir "$repo/.mentat"
cp "$here"/contract/*.json "$here"/contract/*.py "$here"/contract/*.smt2 "$repo/.mentat/"
python3 - "$repo/.mentat/contract.json" "$image" <<'PY'
import json
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
image = sys.argv[2]
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:-]*@sha256:[0-9a-f]{64}", image):
    raise SystemExit("expected a repository-qualified image digest")
contract = json.loads(path.read_text())
contract["docker_image"] = image
path.write_text(json.dumps(contract, indent=2) + "\n")
PY
git -C "$repo" add --force -- .mentat
git -C "$repo" -c user.name='Mentat Nemo Example' -c user.email='nemo@example.invalid' \
    commit --no-gpg-sign -qm 'Add public Mentat contract for mixed numeric sums'
printf 'Nemo repository %s\nMentat state %s\nDocker context %s\nRunner image %s\n' \
    "$repo" "$root/state" "$context" "$image"
printf 'Public contract base %s\nBuggy revision %s\nFixed revision %s\n' \
    "$(git -C "$repo" rev-parse HEAD)" "$before" "$fixed"
