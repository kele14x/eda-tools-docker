#!/usr/bin/env bash
# Two-step image build:
#   fetch  (online)  download every input into ./downloads; the only step that can hit the network.
#   build  (offline) build the image from ./downloads with networking disabled.
# Keep ./downloads and later builds need no fetch.
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
mode=all
case "${1:-}" in
    fetch|build|all) mode="$1"; shift ;;
    -h|--help)
        printf 'Usage: %s [all|fetch|build] [docker build options]\n' "$0"
        printf 'fetch: download every input into downloads/ (network).\n'
        printf 'build: build the image from downloads/ only (no network).\n'
        printf 'all:   fetch, then build (default).\n'
        exit 0 ;;
esac
platform="${EDA_PLATFORM:-linux/arm64}"
case "$platform" in
    linux/amd64) arch=amd64 ;;
    linux/arm64) arch=arm64 ;;
    *) echo "Unsupported EDA_PLATFORM: $platform (use linux/amd64 or linux/arm64)" >&2; exit 2 ;;
esac
image="${EDA_IMAGE:-local/eda-tools:$arch}"
docker=(docker --context "${EDA_DOCKER_CONTEXT:-orbstack}")
dockerfile="$project_dir/docker/Dockerfile.source"
# These select what fetch downloads, so fetch and build must see the same values.
openroad_enabled="${EDA_OPENROAD_ENABLED:-OFF}"
openroad_gui="${EDA_OPENROAD_GUI:-OFF}"
xls_mode="${EDA_XLS_INSTALL_MODE:-auto}"
fetch_timeout="${EDA_BAZEL_FETCH_TIMEOUT:-1800}"
jobs="${EDA_BUILD_JOBS:-4}"
for setting in "EDA_OPENROAD_ENABLED=$openroad_enabled" "EDA_OPENROAD_GUI=$openroad_gui"; do
    case "${setting#*=}" in
        ON|OFF) ;;
        *) echo "Unsupported $setting (use ON or OFF)" >&2; exit 2 ;;
    esac
done
case "$xls_mode:$arch" in
    auto:*|source:*|release:amd64) ;;
    release:arm64) echo 'EDA_XLS_INSTALL_MODE=release requires linux/amd64; use source on ARM64' >&2; exit 2 ;;
    *) echo "Unsupported EDA_XLS_INSTALL_MODE=$xls_mode (use auto, source or release)" >&2; exit 2 ;;
esac
for setting in "EDA_BAZEL_FETCH_TIMEOUT=$fetch_timeout" "EDA_BUILD_JOBS=$jobs"; do
    if [[ ! "${setting#*=}" =~ ^[1-9][0-9]*$ ]]; then
        echo "Unsupported $setting (use a positive integer)" >&2; exit 2
    fi
done

downloads="$project_dir/downloads"
platform_dir="$downloads/linux-$arch"
common_dir="$downloads/common"
fetch_cache="$downloads/fetch-cache"
build_args=(--platform "$platform" --file "$dockerfile" --network=none --pull=false
            --build-context "platform=$platform_dir" --build-context "common=$common_dir"
            --build-arg "BUILDER_UID=$(id -u)" --build-arg "BUILDER_GID=$(id -g)")

pin() {
    python3 -c 'import json, sys
value = json.load(open(sys.argv[1]))
for key in sys.argv[2:]:
    value = value[key]
print(value)' "$project_dir/config/tools/$1" "${@:2}"
}

sha256() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1
}

verified() {
    [[ -f "$1" && "$(sha256 "$1")" == "$2" ]]
}

rootfs_url="$(pin ubuntu-base.json url)"
rootfs_url="${rootfs_url//\{arch\}/$arch}"
rootfs_sha="$(pin ubuntu-base.json sha256 "$arch")"
bazelisk_version="$(pin source-build.json bazelisk_version)"
bazelisk_url="https://github.com/bazelbuild/bazelisk/releases/download/v$bazelisk_version/bazelisk-linux-$arch"
bazelisk_sha="$(pin source-build.json bazelisk_sha256 "$arch")"

apt_request() {
    { printf '%s\n' "$rootfs_sha"; cat "$project_dir"/config/apt/*.txt "$project_dir/docker/fetch-apt-packages.sh"; } \
        | sha256 -
}

download() {
    local url="$1" destination="$2" checksum="$3"
    if verified "$destination" "$checksum"; then
        return
    fi
    mkdir -p "$(dirname "$destination")"
    printf 'FETCH: %s\n' "$url" >&2
    if ! curl --fail --location --connect-timeout 20 --retry 5 --retry-all-errors \
            --speed-limit 1024 --speed-time 60 --continue-at - \
            --output "$destination.partial" "$url" \
       && ! verified "$destination.partial" "$checksum"; then
        echo "Download failed: $url; rerun fetch to resume $destination.partial" >&2
        exit 1
    fi
    if ! verified "$destination.partial" "$checksum"; then
        rm -f "$destination.partial"
        echo "Checksum mismatch for $url; rerun fetch" >&2
        exit 1
    fi
    mv "$destination.partial" "$destination"
}

remove_fetch_images() {
    "${docker[@]}" image rm "$image-fetch-base" "$image-fetch" >/dev/null 2>&1 || true
}

fetch() {
    if [[ "$(id -u)" == 0 ]]; then
        echo 'Run fetch as a regular user: downloads/ is written with your UID, and Bazel refuses root.' >&2
        exit 2
    fi
    mkdir -p "$platform_dir" "$common_dir" "$fetch_cache"
    download "$rootfs_url" "$platform_dir/ubuntu-base.tar.gz" "$rootfs_sha"
    download "$bazelisk_url" "$platform_dir/bazelisk" "$bazelisk_sha"
    trap remove_fetch_images EXIT
    local request
    request="$(apt_request)"
    if [[ "$(cat "$platform_dir/apt/complete" 2>/dev/null || true)" != "$request" ]]; then
        printf 'FETCH: apt packages for %s\n' "$platform" >&2
        "${docker[@]}" build "${build_args[@]}" "$@" --target rootfs --tag "$image-fetch-base" "$project_dir"
        "${docker[@]}" run --rm --platform "$platform" \
            --mount "type=bind,source=$platform_dir,target=/downloads/platform" \
            --mount "type=bind,source=$project_dir/config/apt,target=/opt/build/apt,readonly" \
            --mount "type=bind,source=$project_dir/docker/fetch-apt-packages.sh,target=/opt/build/fetch-apt-packages.sh,readonly" \
            --env "APT_REQUEST=$request" --env "HOST_UID=$(id -u)" --env "HOST_GID=$(id -g)" \
            "$image-fetch-base" sh /opt/build/fetch-apt-packages.sh
    fi
    "${docker[@]}" build "${build_args[@]}" "$@" --target builder --tag "$image-fetch" "$project_dir"
    local items=(libraries yosys)
    if [[ "$openroad_enabled" == ON ]]; then
        items+=(openroad)
    fi
    items+=(xls)
    for item in "${items[@]}"; do
        "${docker[@]}" run --rm --platform "$platform" \
            --mount "type=bind,source=$platform_dir,target=/downloads/platform" \
            --mount "type=bind,source=$common_dir,target=/downloads/common" \
            --mount "type=bind,source=$fetch_cache,target=/downloads/fetch-cache" \
            --env "BAZEL_FETCH_TIMEOUT=$fetch_timeout" --env "OPENROAD_GUI=$openroad_gui" \
            --env "XLS_INSTALL_MODE=$xls_mode" \
            "$image-fetch" python3 /opt/build/build-source-tools.py fetch "$item"
    done
    remove_fetch_images
    trap - EXIT
    printf 'FETCH COMPLETE: every build input is in %s\n' "$downloads" >&2
}

require_fetched() {
    local missing=()
    verified "$platform_dir/ubuntu-base.tar.gz" "$rootfs_sha" || missing+=(ubuntu-base.tar.gz)
    verified "$platform_dir/bazelisk" "$bazelisk_sha" || missing+=(bazelisk)
    [[ "$(cat "$platform_dir/apt/complete" 2>/dev/null || true)" == "$(apt_request)" ]] || missing+=(apt)
    [[ -d "$common_dir" ]] || missing+=(../common)
    if (( ${#missing[@]} )); then
        echo "Missing or outdated downloads in $platform_dir: ${missing[*]}; run $0 fetch first." >&2
        exit 1
    fi
}

if [[ "$mode" != build ]]; then
    fetch "$@"
fi
if [[ "$mode" == fetch ]]; then
    exit 0
fi
require_fetched
tag_args=(--tag "$image")
if [[ "$platform" == linux/arm64 && "$image" == local/eda-tools:arm64 ]]; then
    tag_args+=(--tag local/eda-tools:v1)
fi
printf 'OFFLINE BUILD: %s from %s (network disabled)\n' "$image" "$downloads" >&2
exec "${docker[@]}" build "${build_args[@]}" \
    --build-arg "OPENROAD_ENABLED=$openroad_enabled" --build-arg "OPENROAD_GUI=$openroad_gui" \
    --build-arg "XLS_INSTALL_MODE=$xls_mode" --build-arg "BUILD_JOBS=$jobs" "$@" \
    --target runtime "${tag_args[@]}" "$project_dir"
