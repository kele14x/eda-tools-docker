#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
exec "$project_dir/docker/bin/eda" bash "$project_dir/scripts/smoke-test.sh"
