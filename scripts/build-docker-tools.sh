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
build_args=(--platform "$platform" --file "$project_dir/docker/Dockerfile.source")
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
