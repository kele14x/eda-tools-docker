#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -gt 1 || "${1:-}" == --help || "${1:-}" == -h ]]; then
    echo "Usage: $0 [bin-directory]" >&2
    echo 'Default: $HOME/.local/bin. Existing unrelated commands are never overwritten.' >&2
    if [[ $# -gt 1 ]]; then exit 2; else exit 0; fi
fi
bin_dir="${1:-$HOME/.local/bin}"
marker='# Managed by eda-tools-docker; installed by scripts/install-docker-tools.sh.'
# Recognize installed launchers from before the repository rename.
legacy_marker='# Managed by xls-tools Docker tools; installed by scripts/install-docker-tools.sh.'
tools=(eda xls yosys sta openroad iverilog vvp verilator)

# Preflight every destination before changing any installed commands.
for tool in "${tools[@]}"; do
    destination="$bin_dir/$tool"
    if [[ -e "$destination" || -L "$destination" ]]; then
        if [[ -L "$destination" || ! -f "$destination" ]] || ! grep -Fqx -e "$marker" -e "$legacy_marker" "$destination"; then
            echo "Refusing to overwrite an unrelated command: $destination" >&2
            exit 1
        fi
    fi
done

mkdir -p "$bin_dir"
for tool in "${tools[@]}"; do
    install -m 755 "$project_dir/docker/bin/$tool" "$bin_dir/$tool"
done
echo "Installed ${tools[*]} in $bin_dir"
case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *) echo "Add $bin_dir to PATH in your shell configuration." ;;
esac
