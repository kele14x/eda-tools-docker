# EDA Tools Docker

Shared Linux installations of Google XLS, Yosys with slang/ABC,
OpenROAD with embedded OpenSTA, and Nangate45 timing/physical libraries,
plus Icarus Verilog and Verilator in Docker for RTL simulation and linting. This project owns
the tool binaries, download cache, installation manifests, Docker image, launchers,
tool documentation, and integration tests.

## Layout

```text
eda-tools-docker/
├── Dockerfile        # Ubuntu 24.04 amd64 runtime image
├── docker/           # Image entrypoint and Mac Docker launchers
├── config/tools/     # Pinned versions, build options, and checksums
├── scripts/          # Image build, smoke test/viewer, and launcher installation
├── tests/smoke/      # DSLX adder and Nangate45 synthesis/routing fixtures
├── tests/tools/      # OpenSTA activity and OpenROAD placement tests
├── docs/             # Docker usage and source rebuild instructions
├── tools/            # Installed runtimes and libraries (ignored)
├── downloads/        # Source/release archives and checksums (ignored)
└── build/            # Test logs and generated artifacts (ignored)
```

## Windows with WSL containers

Use `wslc` to build a native AMD64 image. It compiles OpenROAD/Yosys from
source and automatically downloads the checksum-pinned XLS release.
See [WSL containers](docs/wsl-containers.md) for PowerShell build commands,
XLS source-build selection, and the full smoke test.

## Docker on macOS

The default toolchain is native Linux ARM64 for Apple Silicon. Clone the
repository, then build from the pinned sources with OrbStack running and
install the launchers:

```bash
git clone git@github.com:kele14x/eda-tools-docker.git
cd eda-tools-docker
./scripts/build-docker-tools.sh --progress=plain
python3 scripts/test-docker-tools.py --expected-architecture arm64 \
  --output-dir build/docker-tools-arm64
./scripts/run-smoke-test.sh
./scripts/install-docker-tools.sh
```

This builds `local/eda-tools:arm64` and gives the same image the compatibility
tag `local/eda-tools:v1`. The launchers select ARM64 without environment overrides.
The first source build is substantial; see [ARM64 Docker](docs/arm64-docker.md)
for memory controls, caching, provenance, and image selection. Reinstall existing
launchers to remove their old AMD64 constraint.

To package the existing AMD64 binaries instead:

```bash
python3 scripts/install-openroad.py
EDA_PLATFORM=linux/amd64 ./scripts/build-docker-tools.sh
EDA_IMAGE=local/eda-tools:amd64 ./scripts/run-smoke-test.sh
EDA_IMAGE=local/eda-tools:amd64 python3 scripts/test-docker-tools.py
```

The optional AMD64 packaging build uses `local/eda-tools:amd64`.
The launcher installer places `eda`, `xls`, `yosys`, `sta`, `openroad`,
`iverilog`, `vvp`, and `verilator`
in `~/.local/bin`. The installed commands
preserve the calling project's working directory and do not depend on this checkout.
Ensure that directory is on `PATH`; use `rehash` in an existing zsh after
installation.

From any hardware project:

```bash
xls interpreter_main src/design.x
yosys -p 'read_slang --top top src/top.sv; synth -top top'
sta -exit timing.tcl
openroad -no_init -exit -threads 2 place.tcl
iverilog -g2012 -s tb -o simulation.vvp src/design.sv tests/tb.sv
vvp simulation.vvp
verilator --lint-only --top-module top src/top.sv
```

The image contains the DSLX standard library, Yosys runtime data and ABC,
OpenROAD with embedded OpenSTA, `/opt/eda/nangate45/NangateOpenCellLibrary_typical.lib`,
and the Nangate45 LEF/RC files under `/opt/eda/nangate45-physical/`.
Both Docker variants include Ubuntu's Icarus Verilog (`iverilog`/`vvp`),
Verilator, and a C++ compiler, Make, and zlib headers for building simulations.
Simulator package versions are recorded at build time in
`/opt/eda/manifests/simulation-packages.txt`; these packages follow Ubuntu
24.04 repositories rather than pinned upstream source commits.
Select another image using `EDA_IMAGE`, another Docker context using
`EDA_DOCKER_CONTEXT`, or a common parent mount using `EDA_PROJECT_ROOT`.
See [the Docker guide](docs/docker-tools.md) for full usage and sharing.

## XLS-to-routing smoke test

Run `./scripts/run-smoke-test.sh` to check the existing Docker image with the
small adder in `tests/smoke/simple_add.x`. The launcher works from any
directory and runs the whole flow in one container using the selected image. It uses this
checkout's Docker launcher and does not require globally installed wrappers.

The test checks addition, zero, overflow, and 100 commutativity cases with
seed 42 using the DSLX interpreter, IR interpreter, and LLVM JIT. It then
converts and optimizes XLS IR and generates one-stage pipelined Verilog and
a module signature. With the default input/output registers, the signature
reports latency two and initiation interval one.

Icarus Verilog and Verilator each compile and run the generated RTL against
`tests/smoke/simple_add_tb.sv`. Both must pass six addition cases, including
zero and 32-bit overflow, and produce a VCD waveform. A mismatch or timeout
fails the smoke test; Verilator also exercises C++ compilation and tracing.

Yosys maps the generated adder to Nangate45 cells. OpenROAD creates a
floorplan, places pins and cells, synthesizes a clock tree for the 96 pipeline
flops, and performs global and detailed signal/clock routing on metal2–metal6.
The test requires legal placement, zero detailed-routing DRCs, and routes on
all multi-pin signal/clock nets. A fresh OpenROAD process reloads the routed
database and checks the pipeline flops, clock buffers, and routing again.

Artifacts go to `build/simple_add/`:

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
GDS export, and signoff are separate flow work. Select another image or
context using `EDA_IMAGE` or `EDA_DOCKER_CONTEXT`.

To view the routed layout, run `./scripts/view-smoke-test.sh` and open
[http://localhost:8080](http://localhost:8080). It loads the DEF and Nangate45
libraries in OpenROAD's browser GUI. Stop it with
`./scripts/view-smoke-test.sh --stop`.

## Pinned installations

| Tool | Installation |
| --- | --- |
| Google XLS | `v0.0.0-10788-g6e55d7763`; ARM64 source build, optional Linux x64 release |
| Yosys | 0.69, commit `143eb14f9cc55d6f8927e68523b0c9d2166ed02c`, slang and ABC enabled |
| OpenSTA | 3.1.0 embedded in OpenROAD; source submodule `84c4d7e133d3b1a54352c97c6a5df3140c71c78c` |
| OpenROAD | `26Q4-878-g273fa45460`; ARM64 source build with parser EOF patch, optional pinned amd64 runtime |
| Nangate45 | Typical corner, 1.1 V, 25 C; SHA-256 pinned in its manifest |
| Nangate45 physical | LEF and RC files from OpenROAD-flow-scripts commit `31ae82dc3149d5e66386317d564f47c672ddb1d8` |

Manifest paths are relative to this project. Versioned installations and
their stable symlinks are under `tools/`. Downloads and built runtimes are
local artifacts; keep the manifests, scripts, Docker setup, and documentation
in version control.

## Docker execution and tool tests

All tools run inside Docker through the launchers in `docker/bin/`, which
preserve your working directory. The launcher installer copies them into
`~/.local/bin` for use from other projects. Source rebuild instructions are
in [Yosys](docs/yosys.md). OpenROAD installation and physical library usage
are covered in [OpenROAD](docs/openroad.md). The `sta` command forwards to OpenROAD; existing
timing scripts need LEF inputs as described in [timing and power](docs/opensta.md).

The standalone Docker test script covers the toolchain from an unrelated
temporary project, including simulation of XLS-generated RTL with both Icarus
and Verilator, waveform output, placement of an XLS/Yosys-generated adder,
and OpenDB reload. The OpenSTA activity and OpenROAD placement tests also
use Docker and the image's bundled Nangate45 libraries:

```bash
python3 -m unittest discover -s tests/tools -p 'test_*.py' -v
```

## License

This repository's original code is licensed under the [MIT License](LICENSE).
Third-party tools and libraries retain their respective licenses.
