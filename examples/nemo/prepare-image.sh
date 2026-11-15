#!/bin/sh
set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo=${1:?Usage: prepare-image.sh NEMO_REPO BUGGY_COMMIT}
revision=${2:?Specify the full buggy Nemo commit ID}
context=${MENTAT_DOCKER_CONTEXT:-colima-mentat-nemo}
nightly=${NEMO_NIGHTLY:-nightly-2026-08-12}
registry_image=registry:2
build_dir=$(mktemp -d "${TMPDIR:-/tmp}/mentat-nemo-image.XXXXXX")
registry=''

docker_nemo() {
    docker --context "$context" "$@"
}

cleanup() {
    if [ -n "$registry" ]; then
        docker_nemo rm -f -v "$registry" >/dev/null
    fi
    rm -rf -- "$build_dir"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

test "$(git -C "$repo" rev-parse --verify "$revision^{commit}")" = "$revision"
git -C "$repo" archive --format=tar --output="$build_dir/source.tar" "$revision"
mkdir "$build_dir/source"
tar -xf "$build_dir/source.tar" -C "$build_dir/source"

# Use a per-build loopback registry so parallel setups cannot overwrite each other's tags.
registry=$(docker_nemo run -d -p 127.0.0.1::5000 "$registry_image")
registry_endpoint=$(docker_nemo port "$registry" 5000/tcp)
image_name="localhost:${registry_endpoint##*:}/mentat-nemo"

printf 'Nemo runner Rust %s\n' "$nightly" >&2
docker_nemo build --platform linux/arm64 \
    --build-arg "NEMO_NIGHTLY=$nightly" -f "$here/Dockerfile" \
    -t "$image_name:local" "$build_dir/source" >&2

# Register a repository-qualified digest in the local daemon.
for attempt in 1 2 3 4 5; do
    if docker_nemo push "$image_name:local" >&2; then
        break
    fi
    test "$attempt" -lt 5
    sleep 1
done
image=$(docker_nemo image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' \
    "$image_name:local" | awk -v name="$image_name@sha256:" 'index($0, name) == 1 {print; exit}')
test -n "$image"
docker_nemo image inspect "$image" >/dev/null
printf '%s\n' "$image"
