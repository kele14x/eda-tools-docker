#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
docker_cmd=(docker --context "${EDA_DOCKER_CONTEXT:-orbstack}")
container=eda-tools-docker-openroad-viewer
label_key=local.eda-tools-docker.viewer
port="${OPENROAD_VIEWER_PORT:-8080}"

if [[ $# -gt 1 || ( $# -eq 1 && "$1" != --stop ) ]]; then
    echo "Usage: $0 [--stop]" >&2
    echo 'Default: view the routed smoke-test adder at http://localhost:8080.' >&2
    echo 'Environment: EDA_IMAGE, EDA_PLATFORM, EDA_DOCKER_CONTEXT, OPENROAD_VIEWER_PORT.' >&2
    if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then exit 0; else exit 2; fi
fi
if [[ ! "$port" =~ ^[0-9]+$ ]] || (( 10#$port < 1024 || 10#$port > 65535 )); then
    echo "OPENROAD_VIEWER_PORT must be between 1024 and 65535: $port" >&2
    exit 2
fi

# Also find viewers launched before the repository rename.
for viewer in "$container" xls-tools-openroad-viewer; do
    viewer_label_key="$label_key"
    if [[ "$viewer" != "$container" ]]; then
        viewer_label_key=local.xls-tools.viewer
    fi
    if "${docker_cmd[@]}" container inspect "$viewer" >/dev/null 2>&1; then
        label="$("${docker_cmd[@]}" inspect --format "{{index .Config.Labels \"$viewer_label_key\"}}" "$viewer")"
        if [[ "$label" != "$viewer" ]]; then
            echo "Refusing to stop an unrelated container: $viewer" >&2
            exit 1
        fi
        "${docker_cmd[@]}" stop "$viewer" >/dev/null
    fi
done
if [[ "${1:-}" == --stop ]]; then
    echo 'Stopped the OpenROAD smoke-test viewer.'
    exit 0
fi

for artifact in routed.def routed.sdc; do
    if [[ ! -s "$project_dir/build/simple_add/$artifact" ]]; then
        echo "Missing $artifact; run $project_dir/bin/run-smoke-test.sh first." >&2
        exit 2
    fi
done

run_args=(run --detach --rm --init)
image="${EDA_IMAGE:-local/eda-tools:arm64}"
platform="${EDA_PLATFORM:-}"
if [[ -z "$platform" ]]; then
    platform="$("${docker_cmd[@]}" image inspect --format '{{.Os}}/{{.Architecture}}' "$image" 2>/dev/null || true)"
fi
if [[ -n "$platform" ]]; then
    run_args+=(--platform "$platform")
fi
"${docker_cmd[@]}" "${run_args[@]}" \
    --name "$container" --label "$label_key=$container" \
    --user "$(id -u):$(id -g)" \
    --mount "type=bind,source=$project_dir,target=$project_dir,readonly" \
    --workdir "$project_dir" \
    --publish "127.0.0.1:$port:8080" \
    "$image" openroad -no_init -no_splash -threads 2 \
    -web -web_port 8080 -web_bind 0.0.0.0 \
    "$project_dir/tests/smoke/view-adder.tcl" >/dev/null

url="http://localhost:$port"
for attempt in {1..30}; do
    if curl --fail --silent --max-time 1 "$url/" >/dev/null; then
        echo "OpenROAD layout viewer: $url"
        echo "Stop with: $project_dir/bin/view-smoke-test.sh --stop"
        exit 0
    fi
    sleep 0.2
done
echo "Viewer did not become ready; inspect it with: docker logs $container" >&2
exit 1
