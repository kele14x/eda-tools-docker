#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec docker --context "${EDA_DOCKER_CONTEXT:-orbstack}" build \
    --platform linux/amd64 \
    --tag "${EDA_IMAGE:-local/eda-tools:v1}" \
    "$@" "$project_dir"
