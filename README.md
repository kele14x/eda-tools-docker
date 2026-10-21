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
├── bin/              # VM / Linux launchers and Docker smoke test
├── config/tools/     # Pinned versions, build options, and checksums
├── scripts/          # Image build, test, and launcher installation
├── tests/smoke/      # Small DSLX toolchain smoke-test fixture
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
./bin/run-smoke-test.sh
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

## XLS smoke test

Run `./bin/run-smoke-test.sh` to check the existing Docker image with the
small adder in `tests/smoke/simple_add.x`. The launcher works from any
directory and runs the whole flow in one amd64 container. It uses this
checkout's Docker launcher and does not require globally installed wrappers.

The test checks addition, zero, overflow, and 100 commutativity cases with
seed 42 using the DSLX interpreter, IR interpreter, and LLVM JIT. It then
converts and optimizes XLS IR and generates one-stage pipelined Verilog and
a module signature. Artifacts and `smoke-test.log` go to `build/simple_add/`.
With the default input/output registers, the signature reports latency two
and initiation interval one. Select another image or context using
`EDA_IMAGE` or `EDA_DOCKER_CONTEXT`.

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

The `xls-test` project consumes the installed Docker toolchain through small
project launchers. Its PPA flows extract manifests, binary checksums, and
Nangate45 analysis data from the selected image into `build/toolchain/`.
It has no tool installation symlinks or runtime dependency on this checkout.
Hardware designs, verification, and design build outputs remain in `xls-test`.
