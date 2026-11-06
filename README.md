# EDA Tools Docker

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platforms: linux/arm64 | linux/amd64](https://img.shields.io/badge/platforms-linux%2Farm64%20%7C%20linux%2Famd64-informational)](#quick-start)

> A reproducible Docker toolchain for digital design and verification:
> XLS/DSLX, Yosys with slang/ABC, ASAP7 7 nm predictive and Nangate45
> timing/physical libraries, and Icarus Verilog/Verilator. OpenROAD with
> embedded OpenSTA (place-and-route and STA) is opt-in via
> `EDA_OPENROAD_ENABLED=ON`.

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
  synthesis and, when OpenROAD is enabled, routed layout.

## At a glance

| Component | Included |
| --- | --- |
| Design | Google XLS (DSLX, IR, Verilog codegen) |
| Synthesis | Yosys 0.69 with slang and ABC |
| Place and route | OpenROAD with embedded OpenSTA (opt-in, `EDA_OPENROAD_ENABLED=ON`) |
| PDK | ASAP7 TT/RVT (default smoke platform) and Nangate45 timing/physical libraries |
| Simulation and lint | Icarus Verilog (`iverilog`/`vvp`) and Verilator |
| Platforms | Linux ARM64 (Apple Silicon) and Linux AMD64 (Linux/WSL containers) |
| Launchers | `eda`, `xls`, `yosys`, `iverilog`, `vvp`, `verilator`; plus `sta` and `openroad` when OpenROAD is enabled |

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

The build has two steps, which the command above runs in order:

1. `./scripts/build-docker-tools.sh fetch` — the only online step. It downloads
   every input (Ubuntu base, apt packages, sources, Bazel dependencies,
   libraries) into `downloads/`.
2. `./scripts/build-docker-tools.sh build` — fully offline (`--network=none`).
   It uses only `downloads/` and this repository to produce the image.

Keep `downloads/` and you never need step 1 again; network problems can only
appear in step 1.

`install-docker-tools.sh` places the launchers in `~/.local/bin`, so make sure
that directory is on `PATH`. In an existing zsh session, run `rehash` after
installation. Reinstall any older launchers to remove their previous AMD64
constraint.

The first source build is substantial. See [ARM64 Docker](docs/arm64-docker.md)
for the `downloads/` layout, memory controls, provenance, and image selection.

### AMD64 (Linux or WSL containers)

Build the AMD64 image with the same Dockerfile:

```bash
EDA_PLATFORM=linux/amd64 ./scripts/build-docker-tools.sh
EDA_IMAGE=local/eda-tools:amd64 ./scripts/run-smoke-test.sh
EDA_IMAGE=local/eda-tools:amd64 python3 scripts/test-docker-tools.py
```

Both architectures compile Yosys inside Docker. XLS builds from
source on ARM64 and uses the checksum-pinned release on AMD64. Set
`EDA_XLS_INSTALL_MODE=source` to compile XLS on AMD64 too. No host
tool installations are required.

When using the installed launchers on AMD64, set
`EDA_IMAGE=local/eda-tools:amd64` in your environment.

On Windows, use `wslc` to build the native AMD64 image. See
[WSL containers](docs/wsl-containers.md) for PowerShell build commands, XLS
source-build selection, and the full smoke test.

### OpenROAD is opt-in

OpenROAD is the only tool that needs a multi-gigabyte source, submodule, and
Bazel fetch, and on a slow or restricted link that fetch is the usual failure
point. The default image therefore omits it, shipping XLS, Yosys with
slang/ABC, the ASAP7 and Nangate45 libraries, Icarus Verilog, and Verilator —
so synthesis, cell mapping, and RTL simulation work out of the box. To add
OpenROAD (place and route) and its embedded OpenSTA (timing), set
`EDA_OPENROAD_ENABLED=ON` for **both** the fetch and build phases:

```bash
EDA_OPENROAD_ENABLED=ON ./scripts/build-docker-tools.sh fetch --progress=plain
EDA_OPENROAD_ENABLED=ON ./scripts/build-docker-tools.sh build --progress=plain
```

The value must match between steps: `fetch` only downloads OpenROAD when it is
enabled, and an enabled `build` stops with "rerun fetch" if `downloads/` lacks
it. When OpenROAD is absent, the `openroad` and `sta` commands are not
installed. The smoke test and `scripts/test-docker-tools.py` detect the missing
binary and skip their placement, routing, and STA checks; the OpenROAD and
OpenSTA tests under `tests/tools/` skip as well.

## Use the toolchain

After installing the launchers, run commands from any hardware project:

```bash
xls interpreter_main src/design.x
yosys -p 'read_slang --top top src/top.sv; synth -top top'
iverilog -g2012 -s tb -o simulation.vvp src/design.sv tests/tb.sv
vvp simulation.vvp
verilator --lint-only --top-module top src/top.sv
# With an image built using EDA_OPENROAD_ENABLED=ON:
sta -exit timing.tcl
openroad -no_init -exit -threads 2 place.tcl
```

The launchers preserve the calling project's working directory and do not
depend on this repository checkout. The `sta` and `openroad` launchers require
an image built with `EDA_OPENROAD_ENABLED=ON`.

### What is in the image

- The DSLX standard library, Yosys runtime data, and ABC.
- Two standard-cell platforms: ASAP7 TT/RVT under `/opt/eda/asap7/` (the
  default smoke-flow platform) and Nangate45 under `/opt/eda/nangate45/` and
  `/opt/eda/nangate45-physical/`. `ASAP7_*` and `NANGATE45_*` environment
  variables point at the Liberty, LEF, RC, tapcell, and track files.
- Ubuntu's Icarus Verilog, Verilator, and the C++ compiler, Make, and zlib
  headers needed to build simulations.
- OpenROAD with embedded OpenSTA, only when built with
  `EDA_OPENROAD_ENABLED=ON`.

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
| `EDA_OPENROAD_ENABLED` | `OFF` | Set to `ON` to fetch and build OpenROAD/OpenSTA (P&R and timing) |
| `EDA_OPENROAD_GUI` | `OFF` | Set to `ON` to fetch and build OpenROAD with the Qt GUI |
| `EDA_XLS_INSTALL_MODE` | `auto` | `source`, or `release` (AMD64 only); `auto` builds source on ARM64 |
| `EDA_BAZEL_FETCH_TIMEOUT` | `1800` | Seconds allowed for each Bazel dependency fetch |
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
4. **Cell mapping** — Yosys maps the generated adder to ASAP7 TT/RVT cells by
   default. Pass `--platform nangate45` to run the same flow with the
   Nangate45 baseline instead.
5. **Placement and routing** (only when the image includes OpenROAD, built
   with `EDA_OPENROAD_ENABLED=ON`) — OpenROAD creates a floorplan, inserts tap
   cells, places pins and cells, synthesizes a clock tree for the 96 pipeline
   flops, and performs global and detailed signal/clock routing on M2–M7 with
   clock routing from M4. The test requires legal placement, zero
   detailed-routing DRCs, and routes on all multi-pin signal/clock nets.
6. **Reload check** (OpenROAD only) — a fresh OpenROAD process reloads the
   routed database and verifies the pipeline flops, clock buffers, and routing
   again.

On the default image, which omits OpenROAD, the smoke test stops after cell
mapping and skips steps 5 and 6.

### Artifacts

Outputs are written to `build/simple_add/` for ASAP7 and
`build/simple_add-nangate45/` for the Nangate45 fallback:

| Stage | Outputs |
| --- | --- |
| XLS | IR, optimized IR, `simple_add.v`, and module signature |
| RTL simulation | `simple_add.vvp`, `iverilog.vcd`, `verilator.vcd`, and simulator build/run logs |
| Cell mapping | `simple_add.mapped.v`, `synth.ys`, `synthesis.log` |
| Placement | `placed.def`, `placed.odb` |
| Routing | `routed.def`, `routed.odb`, `routed.v`, `routed.sdc`, `route.guide` |
| Checks/reports | `routing-summary.json`, `route-drc.rpt`, `cts.rpt`, `timing.rpt`, `power.rpt`, and logs |

The Placement, Routing, and Checks/reports rows are produced only when the
image includes OpenROAD (`EDA_OPENROAD_ENABLED=ON`); the default image stops
after Cell mapping.

An empty `route-drc.rpt` means no detailed-routing violations were found.
Timing and power reports use global-route RC estimates with a 5 ns clock;
ASAP7 constraints are expressed in its picosecond Liberty units.

This smoke test exercises signal and clock routing; power-grid construction,
GDS export, and signoff are separate flow work.

### View the routed layout

```bash
./scripts/view-smoke-test.sh
```

Open [http://localhost:8080](http://localhost:8080) to load the DEF and the
selected platform's libraries in OpenROAD's browser GUI; use
`--platform nangate45` to view the Nangate45 artifacts. Stop the viewer with:

```bash
./scripts/view-smoke-test.sh --stop
```

The viewer requires an image built with `EDA_OPENROAD_ENABLED=ON` and routed
artifacts from a matching smoke test.

## Pinned sources and releases

| Tool | Source or release |
| --- | --- |
| Google XLS | `v0.0.0-10788-g6e55d7763`; ARM64 source build, pinned Linux x64 release on AMD64 by default |
| Yosys | 0.69, commit `143eb14f9cc55d6f8927e68523b0c9d2166ed02c`, slang and ABC enabled |
| OpenSTA | 3.1.0 embedded in OpenROAD; source submodule `84c4d7e133d3b1a54352c97c6a5df3140c71c78c` |
| OpenROAD | `26Q4-878-g273fa45460`; source build with parser EOF patch, opt-in via `EDA_OPENROAD_ENABLED=ON` |
| Nangate45 | Typical corner, 1.1 V, 25 C; SHA-256 pinned in its manifest |
| Nangate45 physical | LEF and RC files from OpenROAD-flow-scripts commit `31ae82dc3149d5e66386317d564f47c672ddb1d8` |
| ASAP7 | TT corner RVT, 0.70 V, 0 C, NLDM; LEF and scripts from the same OpenROAD-flow-scripts commit |
| ASAP7 license | `asap7sc7p5t_28` commit `e5bd25da72cd297b8c0ec6f77600e0cbaa1124d0`, BSD-3-Clause |

The manifests in `config/tools/` describe source/release inputs. Docker
installs tools and libraries under `/opt/eda/` and writes runtime manifests
with executable checksums under `/opt/eda/manifests/`. Build inputs are
downloaded into `downloads/`; test outputs go to `build/`.

## Repository layout

```text
eda-tools-docker/
├── docker/           # Shared source Dockerfile, entrypoint, and launchers
├── config/tools/     # Source/release pins, build options, and input checksums
├── config/apt/       # Ubuntu package lists for the runtime and build stages
├── scripts/          # Image build, smoke test/viewer, and launcher installation
├── tests/smoke/      # DSLX adder and ASAP7/Nangate45 synthesis/routing fixtures
├── tests/tools/      # OpenSTA activity and OpenROAD placement tests
├── docs/             # Docker usage and source rebuild instructions
├── downloads/        # Fetched build inputs; keep to rebuild offline (ignored)
└── build/            # Test logs and generated artifacts (ignored)
```

## Docker execution and tests

All tools run inside Docker through the launchers in `docker/bin/`, which
preserve your working directory. The launcher installer copies them into
`~/.local/bin` for use from other projects.

The standalone Docker test script covers the toolchain from an unrelated
temporary project, including simulation of XLS-generated RTL with both Icarus
and Verilator and waveform output; on an image with OpenROAD it also checks
placement of an XLS/Yosys-generated adder and OpenDB reload, and skips those
steps otherwise:

```bash
python3 scripts/test-docker-tools.py
```

The OpenSTA activity and OpenROAD placement tests also use Docker and the
image's bundled Nangate45 libraries, and skip automatically when OpenROAD is
absent; the smoke flow defaults to ASAP7 and can select Nangate45 explicitly:

```bash
python3 -m unittest discover -s tests/tools -p 'test_*.py' -v
```

Source rebuild instructions are in [Yosys](docs/yosys.md). OpenROAD
installation and physical library usage are covered in
[OpenROAD](docs/openroad.md). The `sta` command forwards to OpenROAD; existing
timing scripts need LEF inputs as described in
[timing and power](docs/opensta.md). The `sta` and `openroad` launchers
require an image built with `EDA_OPENROAD_ENABLED=ON`.

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
