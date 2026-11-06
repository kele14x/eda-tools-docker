#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
mode=all
case "${1:-}" in
    fetch|build|all) mode="$1"; shift ;;
    -h|--help)
        printf 'Usage: %s [all|fetch|build] [docker build options]\n' "$0"
        printf 'fetch: prepare dependencies online; build: compile offline from the prepared image.\n'
        exit 0 ;;
esac
platform="${EDA_PLATFORM:-linux/arm64}"
case "$platform" in
    linux/amd64) default_image=local/eda-tools:amd64 ;;
    linux/arm64) default_image=local/eda-tools:arm64 ;;
    *) echo "Unsupported EDA_PLATFORM: $platform (use linux/amd64 or linux/arm64)" >&2; exit 2 ;;
esac
image="${EDA_IMAGE:-$default_image}"
dependencies_image="${EDA_DEPENDENCIES_IMAGE:-${image}-deps}"
docker=(docker --context "${EDA_DOCKER_CONTEXT:-orbstack}")
# OpenROAD/OpenSTA (P&R and timing) is opt-in: it is the only stage that needs the
# multi-gigabyte OpenROAD fetch, so the default image omits it. Set EDA_OPENROAD_ENABLED=ON to
# include it. The value must match between the fetch and build phases.
openroad_enabled="${EDA_OPENROAD_ENABLED:-OFF}"
case "$openroad_enabled" in
    ON|OFF) ;;
    *) echo "Unsupported EDA_OPENROAD_ENABLED: $openroad_enabled (use ON or OFF)" >&2; exit 2 ;;
esac
# Host-side download seed. BuildKit mounts this read-only so archives prepared ahead of time are
# copied into the persistent cache mount instead of re-downloaded. The Dockerfile's bind mount
# requires the named context to exist, but BuildKit transfers it even when OpenROAD is off, so
# point it at an empty directory then to avoid shipping a multi-hundred-MB downloads tree.
downloads_dir="${EDA_DOWNLOADS_DIR:-$project_dir/downloads}"
if [[ "$openroad_enabled" == ON ]]; then
    mkdir -p "$downloads_dir"
else
    # Reused (not mktemp) so repeated default builds do not accumulate empty directories.
    downloads_dir="$project_dir/build/empty-downloads-context"
    mkdir -p "$downloads_dir"
fi
build_args=(--platform "$platform" --file "$project_dir/docker/Dockerfile.source"
            --build-context "downloads=$downloads_dir"
            --build-arg "OPENROAD_ENABLED=$openroad_enabled")
if [[ "$mode" != build ]]; then
    printf 'FETCH: preparing %s (network enabled)\n' "$dependencies_image" >&2
    "${docker[@]}" build "${build_args[@]}" "$@" \
        --build-arg DEPENDENCIES_IMAGE=dependencies \
        --target dependencies --tag "$dependencies_image" "$project_dir"
fi
if [[ "$mode" == fetch ]]; then
    exit 0
fi
if ! prepared_platform="$("${docker[@]}" image inspect --format '{{.Os}}/{{.Architecture}}' "$dependencies_image")"; then
    echo "Missing dependency image $dependencies_image; run $0 fetch first." >&2
    exit 1
fi
if [[ "$prepared_platform" != "$platform" ]]; then
    echo "Dependency image platform $prepared_platform does not match $platform; rerun fetch." >&2
    exit 1
fi
tag_args=(--tag "$image")
if [[ "$platform" == linux/arm64 && "$image" == local/eda-tools:arm64 ]]; then
    tag_args+=(--tag local/eda-tools:v1)
fi
printf 'OFFLINE BUILD: using %s (network disabled)\n' "$dependencies_image" >&2
exec "${docker[@]}" build "${build_args[@]}" \
    --build-arg "BUILD_JOBS=${EDA_BUILD_JOBS:-4}" "$@" \
    --build-arg "DEPENDENCIES_IMAGE=$dependencies_image" \
    --pull=false --network=none --target runtime "${tag_args[@]}" "$project_dir"
