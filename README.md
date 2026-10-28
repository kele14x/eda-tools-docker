# EDA Tools Docker

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platforms: linux/arm64 | linux/amd64](https://img.shields.io/badge/platforms-linux%2Farm64%20%7C%20linux%2Famd64-informational)](#quick-start)

> A reproducible Docker toolchain for digital design and verification:
> XLS/DSLX, Yosys with slang/ABC, OpenROAD with embedded OpenSTA,
> Nangate45 timing/physical libraries, and Icarus Verilog/Verilator.

This repository owns the source and release pins, Docker build, launchers,
tool documentation, and integration tests. Both ARM64 and AMD64 images are
built from `docker/Dockerfile.source`.

- **Reproducible** — source/release pins and SHA-256 checksums are versioned
  in `config/tools/`, and runtime manifests are verified during the build.
- **Native images** — Linux ARM64 and AMD64 builds avoid instruction
  translation on matching hosts.
- **Portable** — launchers work from any project directory and do not depend
  on this checkout.
- **Verified** — integration tests exercise the flow from DSLX through
  routed OpenROAD layout.

## At a glance

| Component | Included |
| --- | --- |
| Design | Google XLS (DSLX, IR, Verilog codegen) |
| Synthesis | Yosys 0.69 with slang and ABC |
| Place and route | OpenROAD with embedded OpenSTA |
| PDK | Nangate45 timing and physical libraries |
| Simulation and lint | Icarus Verilog (`iverilog`/`vvp`) and Verilator |
| Platforms | Linux ARM64 (Apple Silicon) and Linux AMD64 (Linux/WSL containers) |
| Launchers | `eda`, `xls`, `yosys`, `sta`, `openroad`, `iverilog`, `vvp`, `verilator` |

## Quick start

Requirements: Docker (OrbStack on macOS), Git, and Python 3. Windows users can build with `wslc`; see [WSL containers](docs/wsl-containers.md).

### Apple Silicon (macOS + OrbStack)

With OrbStack running, clone the repository and build from the pinned sources:

```bash
git clone https://github.com/kele14x/eda-tools-docker.git
cd eda-tools-docker
./scripts/build-docker-tools.sh --progress=plain
python3 scripts/test-docker-tools.py --expected-architecture arm64 \
  --output-dir build/docker-tools-arm64
./scripts/run-smoke-test.sh
./scripts/install-docker-tools.sh
```

This builds `local/eda-tools:arm64` and gives the same image the compatibility
tag `local/eda-tools:v1`. The launchers use this image by default without
environment overrides.

`install-docker-tools.sh` places the launchers in `~/.local/bin`, so make sure
that directory is on `PATH`. In an existing zsh session, run `rehash` after
installation. Reinstall any older launchers to remove their previous AMD64
constraint.

The first source build is substantial. See [ARM64 Docker](docs/arm64-docker.md)
for memory controls, caching, provenance, and image selection.

### AMD64 (Linux or WSL containers)

Build the AMD64 image with the same Dockerfile:

```bash
EDA_PLATFORM=linux/amd64 ./scripts/build-docker-tools.sh
EDA_IMAGE=local/eda-tools:amd64 ./scripts/run-smoke-test.sh
EDA_IMAGE=local/eda-tools:amd64 python3 scripts/test-docker-tools.py
```

Both architectures compile OpenROAD and Yosys inside Docker. XLS builds from
source on ARM64 and uses the checksum-pinned release on AMD64. Pass
`--build-arg XLS_INSTALL_MODE=source` to compile XLS on AMD64 too. No host
tool installations or downloads are required.

When using the installed launchers on AMD64, set
`EDA_IMAGE=local/eda-tools:amd64` in your environment.

On Windows, use `wslc` to build the native AMD64 image. See
[WSL containers](docs/wsl-containers.md) for PowerShell build commands, XLS
source-build selection, and the full smoke test.

## Use the toolchain

After installing the launchers, run commands from any hardware project:

```bash
xls interpreter_main src/design.x
yosys -p 'read_slang --top top src/top.sv; synth -top top'
sta -exit timing.tcl
openroad -no_init -exit -threads 2 place.tcl
iverilog -g2012 -s tb -o simulation.vvp src/design.sv tests/tb.sv
vvp simulation.vvp
verilator --lint-only --top-module top src/top.sv
```

The launchers preserve the calling project's working directory and do not
depend on this repository checkout.

### What is in the image

- The DSLX standard library, Yosys runtime data, and ABC.
- OpenROAD with embedded OpenSTA.
- Nangate45 Liberty at
  `/opt/eda/nangate45/NangateOpenCellLibrary_typical.lib`.
- Nangate45 LEF/RC files under `/opt/eda/nangate45-physical/`.
- Ubuntu's Icarus Verilog, Verilator, and the C++ compiler, Make, and zlib
  headers needed to build simulations.

Simulator package versions are recorded at build time in
`/opt/eda/manifests/simulation-packages.txt`. These packages follow Ubuntu
24.04 repositories rather than pinned upstream source commits.

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `EDA_IMAGE` | `local/eda-tools:arm64` | Image used by the launchers |
| `EDA_DOCKER_CONTEXT` | `orbstack` | Docker context |
| `EDA_PLATFORM` | Detected from the image (build: `linux/arm64`) | Override the Docker platform |
| `EDA_PROJECT_ROOT` | Current directory | Parent directory to mount; must contain the current directory |
| `EDA_BUILD_JOBS` | `4` | Parallel compile jobs during the image build |
| `OPENROAD_VIEWER_PORT` | `8080` | Port for the smoke-test layout viewer |

See [the Docker guide](docs/docker-tools.md) for full usage, sharing, and
environment details.

## XLS-to-routing smoke test

Run the complete flow against the existing Docker image using the small adder
in `tests/smoke/simple_add.x`:

```bash
./scripts/run-smoke-test.sh
```

The launcher works from any directory and runs the whole flow in one container
using the selected image. It uses this checkout's Docker launcher and does not
require globally installed wrappers.

### What it verifies

1. **DSLX correctness** — addition, zero, overflow, and 100 commutativity cases
   with seed 42, using the DSLX interpreter, IR interpreter, and LLVM JIT.
2. **RTL generation** — converts and optimizes XLS IR, then generates
   one-stage pipelined Verilog and a module signature. With the default
   input/output registers, the signature reports latency two and initiation
   interval one.
3. **RTL simulation** — Icarus Verilog and Verilator each compile and run the
   generated RTL against `tests/smoke/simple_add_tb.sv`. Both must pass six
   addition cases, including zero and 32-bit overflow, and produce a VCD
   waveform. A mismatch or timeout fails the test; Verilator also exercises
   C++ compilation and tracing.
4. **Cell mapping** — Yosys maps the generated adder to Nangate45 cells.
5. **Placement and routing** — OpenROAD creates a floorplan, places pins and
   cells, synthesizes a clock tree for the 96 pipeline flops, and performs
   global and detailed signal/clock routing on metal2–metal6. The test requires
   legal placement, zero detailed-routing DRCs, and routes on all multi-pin
   signal/clock nets.
6. **Reload check** — a fresh OpenROAD process reloads the routed database and
   verifies the pipeline flops, clock buffers, and routing again.

### Artifacts

Outputs are written to `build/simple_add/`:

| Stage | Outputs |
| --- | --- |
| XLS | IR, optimized IR, `simple_add.v`, and module signature |
| RTL simulation | `simple_add.vvp`, `iverilog.vcd`, `verilator.vcd`, and simulator build/run logs |
| Cell mapping | `simple_add.mapped.v`, `synth.ys`, `synthesis.log` |
| Placement | `placed.def`, `placed.odb` |
| Routing | `routed.def`, `routed.odb`, `routed.v`, `routed.sdc`, `route.guide` |
| Checks/reports | `routing-summary.json`, `route-drc.rpt`, `cts.rpt`, `timing.rpt`, `power.rpt`, and logs |

An empty `route-drc.rpt` means no detailed-routing violations were found.
Timing and power reports use global-route RC estimates with a 5 ns clock.

This smoke test exercises signal and clock routing; power-grid construction,
GDS export, and signoff are separate flow work.

### View the routed layout

```bash
./scripts/view-smoke-test.sh
```

Open [http://localhost:8080](http://localhost:8080) to load the DEF and
Nangate45 libraries in OpenROAD's browser GUI. Stop the viewer with:

```bash
./scripts/view-smoke-test.sh --stop
```

## Pinned sources and releases

| Tool | Source or release |
| --- | --- |
| Google XLS | `v0.0.0-10788-g6e55d7763`; ARM64 source build, pinned Linux x64 release on AMD64 by default |
| Yosys | 0.69, commit `143eb14f9cc55d6f8927e68523b0c9d2166ed02c`, slang and ABC enabled |
| OpenSTA | 3.1.0 embedded in OpenROAD; source submodule `84c4d7e133d3b1a54352c97c6a5df3140c71c78c` |
| OpenROAD | `26Q4-878-g273fa45460`; source build with parser EOF patch on both architectures |
| Nangate45 | Typical corner, 1.1 V, 25 C; SHA-256 pinned in its manifest |
| Nangate45 physical | LEF and RC files from OpenROAD-flow-scripts commit `31ae82dc3149d5e66386317d564f47c672ddb1d8` |

The manifests in `config/tools/` describe source/release inputs. Docker
installs tools and libraries under `/opt/eda/` and writes runtime manifests
with executable checksums under `/opt/eda/manifests/`. BuildKit caches keep
downloads and compilation outputs inside Docker; test outputs go to `build/`.

## Repository layout

```text
eda-tools-docker/
├── docker/           # Shared source Dockerfile, entrypoint, and launchers
├── config/tools/     # Source/release pins, build options, and input checksums
├── scripts/          # Image build, smoke test/viewer, and launcher installation
├── tests/smoke/      # DSLX adder and Nangate45 synthesis/routing fixtures
├── tests/tools/      # OpenSTA activity and OpenROAD placement tests
├── docs/             # Docker usage and source rebuild instructions
└── build/            # Test logs and generated artifacts (ignored)
```

## Docker execution and tests

All tools run inside Docker through the launchers in `docker/bin/`, which
preserve your working directory. The launcher installer copies them into
`~/.local/bin` for use from other projects.

The standalone Docker test script covers the toolchain from an unrelated
temporary project, including simulation of XLS-generated RTL with both Icarus
and Verilator, waveform output, placement of an XLS/Yosys-generated adder, and
OpenDB reload:

```bash
python3 scripts/test-docker-tools.py
```

The OpenSTA activity and OpenROAD placement tests also use Docker and the
image's bundled Nangate45 libraries:

```bash
python3 -m unittest discover -s tests/tools -p 'test_*.py' -v
```

Source rebuild instructions are in [Yosys](docs/yosys.md). OpenROAD
installation and physical library usage are covered in
[OpenROAD](docs/openroad.md). The `sta` command forwards to OpenROAD; existing
timing scripts need LEF inputs as described in
[timing and power](docs/opensta.md).

## Documentation

- [Docker usage](docs/docker-tools.md) — launchers, sharing, and environment
  variables.
- [ARM64 Docker](docs/arm64-docker.md) — native Apple Silicon builds, memory
  controls, caching, and provenance.
- [WSL containers](docs/wsl-containers.md) — Windows PowerShell builds and
  smoke tests.
- [Yosys](docs/yosys.md) — source rebuild instructions.
- [OpenROAD](docs/openroad.md) — installation and physical library usage.
- [Timing and power](docs/opensta.md) — OpenSTA through OpenROAD and LEF
  requirements.

## License

This repository's original code is licensed under the [MIT License](LICENSE).
Third-party tools and libraries retain their respective licenses.
