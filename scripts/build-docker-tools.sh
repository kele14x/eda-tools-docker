#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
platform="${EDA_PLATFORM:-linux/arm64}"
case "$platform" in
    linux/amd64) default_image=local/eda-tools:amd64 ;;
    linux/arm64) default_image=local/eda-tools:arm64 ;;
    *) echo "Unsupported EDA_PLATFORM: $platform (use linux/amd64 or linux/arm64)" >&2; exit 2 ;;
esac
image="${EDA_IMAGE:-$default_image}"
tag_args=(--tag "$image")
if [[ "$platform" == linux/arm64 && "$image" == local/eda-tools:arm64 ]]; then
    tag_args+=(--tag local/eda-tools:v1)
fi
exec docker --context "${EDA_DOCKER_CONTEXT:-orbstack}" build \
    --platform "$platform" --file "$project_dir/docker/Dockerfile.source" \
    "${tag_args[@]}" \
    --build-arg "BUILD_JOBS=${EDA_BUILD_JOBS:-4}" \
    "$@" "$project_dir"
