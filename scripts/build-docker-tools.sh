#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
platform="${EDA_PLATFORM:-linux/amd64}"
case "$platform" in
    linux/amd64) dockerfile="$project_dir/Dockerfile"; default_image=local/eda-tools:v1 ;;
    linux/arm64) dockerfile="$project_dir/docker/Dockerfile.source"; default_image=local/eda-tools:arm64 ;;
    *) echo "Unsupported EDA_PLATFORM: $platform (use linux/amd64 or linux/arm64)" >&2; exit 2 ;;
esac
exec docker --context "${EDA_DOCKER_CONTEXT:-orbstack}" build \
    --platform "$platform" --file "$dockerfile" \
    --tag "${EDA_IMAGE:-$default_image}" \
    --build-arg "BUILD_JOBS=${EDA_BUILD_JOBS:-4}" \
    "$@" "$project_dir"
