# XLS Tools

Shared Linux amd64 installations of Google XLS, Yosys with slang/ABC,
OpenSTA, and the Nangate45 typical cell library. This project owns the tool
binaries, download cache, installation manifests, Docker image, launchers,
tool documentation, and integration tests.

## Layout

```text
xls-tools/
├── Dockerfile        # Ubuntu 24.04 amd64 runtime image
├── docker/           # Image entrypoint and Mac Docker launchers
├── bin/              # Direct OrbStack VM / Linux launchers
├── config/tools/     # Pinned versions, build options, and checksums
├── scripts/          # Image build, test, and launcher installation
├── tests/tools/      # OpenSTA activity integration tests
├── docs/             # Docker usage and source rebuild instructions
├── tools/            # Installed runtimes and libraries (ignored)
├── downloads/        # Source/release archives and checksums (ignored)
└── build/            # Test logs and generated artifacts (ignored)
```

## Docker on macOS

With OrbStack running, execute from this folder:

```bash
./scripts/build-docker-tools.sh
python3 scripts/test-docker-tools.py
./scripts/install-docker-tools.sh
```

This builds `local/eda-tools:v1` for `linux/amd64` and installs `eda`, `xls`,
`yosys`, and `sta` in `~/.local/bin`. The installed commands preserve the
calling project's working directory and do not depend on this checkout.
Ensure that directory is on `PATH`; use `rehash` in an existing zsh after
installation.

From any hardware project:

```bash
xls interpreter_main src/design.x
yosys -p 'read_slang --top top src/top.sv; synth -top top'
sta -exit timing.tcl
```

The image contains the DSLX standard library, Yosys runtime data and ABC,
OpenSTA, and `/opt/eda/nangate45/NangateOpenCellLibrary_typical.lib`.
Select another image using `EDA_IMAGE`, another Docker context using
`EDA_DOCKER_CONTEXT`, or a common parent mount using `EDA_PROJECT_ROOT`.
See [the Docker guide](docs/docker-tools.md) for full usage and sharing.

## Pinned installations

| Tool | Installation |
| --- | --- |
| Google XLS | `v0.0.0-10788-g6e55d7763` Linux x64 release |
| Yosys | 0.69, commit `143eb14f9cc55d6f8927e68523b0c9d2166ed02c`, slang and ABC enabled |
| OpenSTA | 3.1.0, commit `d1e43c6f9f4e66cb59c3d7958a4aa7d1626b4614` |
| Nangate45 | Typical corner, 1.1 V, 25 C; SHA-256 pinned in its manifest |

Manifest paths are relative to this project. Versioned installations and
their stable symlinks are under `tools/`. Downloads and built runtimes are
local artifacts; keep the manifests, scripts, Docker setup, and documentation
in version control.

## Direct VM execution and tool tests

`bin/xls`, `bin/yosys`, and `bin/sta` run the installations through `ubuntu24`
on macOS, or directly on Linux. They preserve your working directory. Set
`XLS_VM`, `YOSYS_VM`, or `OPENSTA_VM` to choose another OrbStack machine.
Source rebuild instructions are in [Yosys](docs/yosys.md) and
[OpenSTA](docs/opensta.md).

The standalone Docker test script covers all three tools from an unrelated
temporary project. To check OpenSTA activity annotations through Docker:

```bash
OPENSTA_TEST_BINARY="$PWD/docker/bin/sta" \
  python3 -m unittest discover -s tests/tools -p 'test_*.py' -v
```

## Existing xls-test project

The sibling `xls-test` project retains compatibility links for `tools/` and
`config/tools/`, plus forwarding `bin/xls`, `bin/yosys`, and `bin/sta` launchers.
Its hardware designs, design tests, and design build outputs stay there.
Tool binaries, metadata, and Docker infrastructure have a single home here.
Keep the two checkouts as siblings when using those compatibility paths.
New projects can use the installed Docker commands directly.
