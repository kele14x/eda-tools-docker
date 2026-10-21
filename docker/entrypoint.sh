#!/usr/bin/env bash
set -euo pipefail

if [[ "${0##*/}" == xls ]]; then
    set -- xls "$@"
fi

if [[ "${1:-}" == xls ]]; then
    shift
    if [[ $# -eq 0 || "$1" == --help || "$1" == -h ]]; then
        echo 'Usage: xls <XLS tool> [arguments...]' >&2
        echo 'Example: xls interpreter_main src/design.x' >&2
        if [[ $# -eq 0 ]]; then exit 2; else exit 0; fi
    fi
    if [[ "$1" == */* || ! -f "/opt/eda/xls/$1" || ! -x "/opt/eda/xls/$1" ]]; then
        echo "XLS tool not found: $1" >&2
        exit 2
    fi
fi

if [[ $# -eq 0 ]]; then
    set -- bash
fi
tool="$1"
shift
case "$tool" in
    interpreter_main|ir_converter_main)
        has_stdlib=false
        for argument in "$@"; do
            case "$argument" in
                --dslx_stdlib_path|--dslx_stdlib_path=*) has_stdlib=true ;;
            esac
        done
        if [[ "$has_stdlib" == false ]]; then
            set -- --dslx_stdlib_path=/opt/eda/xls/xls/dslx/stdlib "$@"
        fi
        ;;
esac
exec "$tool" "$@"
