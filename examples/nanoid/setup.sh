#!/bin/sh
set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo=${1:?Usage: setup.sh NEW_REPO_PATH [NANOID_GIT_SOURCE]}
source=${2:-https://github.com/ai/nanoid.git}
before=3167e108865702c0539d8e552365ed840ac9d8d9
fixed=58193a1d3aab9877315c42e995eb94667e0b1a4a

if [ -e "$repo" ] || [ -L "$repo" ]; then
    printf 'Destination already exists: %s\n' "$repo" >&2
    exit 1
fi
command -v node >/dev/null
command -v mentat >/dev/null
git clone --no-local --no-checkout -- "$source" "$repo" >&2
test "$(git -C "$repo" rev-parse "$fixed^{commit}")" = "$fixed"
test "$(git -C "$repo" rev-parse "$fixed^1")" = "$before"
git -C "$repo" branch mentat-nanoid-before "$before"
git -C "$repo" branch mentat-nanoid-fixed "$fixed"
git -C "$repo" -c core.hooksPath=/dev/null switch -C mentat-nanoid-base "$fixed" >&2
mkdir "$repo/.mentat"
cp "$here"/contract/* "$repo/.mentat/"
mentat claims render --repo "$repo"
git -C "$repo" add --force -- .mentat
git -C "$repo" -c user.name='Mentat Nano ID Example' -c user.email='nanoid@example.invalid' \
    -c core.hooksPath=/dev/null commit --no-gpg-sign -qm 'Add Nano ID size contract'
printf 'Repository %s\nContract base %s\nBefore %s\nFixed %s\n' \
    "$repo" "$(git -C "$repo" rev-parse HEAD)" "$before" "$fixed"
