# XLS Tools

Shared Linux amd64 installations of Google XLS, Yosys with slang/ABC,
OpenROAD with embedded OpenSTA, and Nangate45 timing/physical libraries. This project owns
the tool binaries, download cache, installation manifests, Docker image, launchers,
tool documentation, and integration tests.

## Layout

```text
xls-tools/
├── Dockerfile        # Ubuntu 24.04 amd64 runtime image
├── docker/           # Image entrypoint and Mac Docker launchers
├── bin/              # VM / Linux launchers and Docker smoke test
├── config/tools/     # Pinned versions, build options, and checksums
├── scripts/          # Image build, test, and launcher installation
├── tests/smoke/      # DSLX adder and Nangate45 synthesis/routing fixtures
├── tests/tools/      # OpenSTA activity and OpenROAD placement tests
├── docs/             # Docker usage and source rebuild instructions
├── tools/            # Installed runtimes and libraries (ignored)
├── downloads/        # Source/release archives and checksums (ignored)
└── build/            # Test logs and generated artifacts (ignored)
```

## Docker on macOS

With OrbStack running, execute from this folder:

```bash
python3 scripts/install-openroad.py
./scripts/build-docker-tools.sh
./bin/run-smoke-test.sh
python3 scripts/test-docker-tools.py
./scripts/install-docker-tools.sh
```

This builds `local/eda-tools:v1` for `linux/amd64` and installs `eda`, `xls`,
`yosys`, `sta`, and `openroad` in `~/.local/bin`. The installed commands
preserve the calling project's working directory and do not depend on this checkout.
Ensure that directory is on `PATH`; use `rehash` in an existing zsh after
installation.

From any hardware project:

```bash
xls interpreter_main src/design.x
yosys -p 'read_slang --top top src/top.sv; synth -top top'
sta -exit timing.tcl
openroad -no_init -exit -threads 2 place.tcl
```

The image contains the DSLX standard library, Yosys runtime data and ABC,
OpenROAD with embedded OpenSTA, `/opt/eda/nangate45/NangateOpenCellLibrary_typical.lib`,
and the Nangate45 LEF/RC files under `/opt/eda/nangate45-physical/`.
Select another image using `EDA_IMAGE`, another Docker context using
`EDA_DOCKER_CONTEXT`, or a common parent mount using `EDA_PROJECT_ROOT`.
See [the Docker guide](docs/docker-tools.md) for full usage and sharing.

## XLS-to-routing smoke test

Run `./bin/run-smoke-test.sh` to check the existing Docker image with the
small adder in `tests/smoke/simple_add.x`. The launcher works from any
directory and runs the whole flow in one amd64 container. It uses this
checkout's Docker launcher and does not require globally installed wrappers.

The test checks addition, zero, overflow, and 100 commutativity cases with
seed 42 using the DSLX interpreter, IR interpreter, and LLVM JIT. It then
converts and optimizes XLS IR and generates one-stage pipelined Verilog and
a module signature. With the default input/output registers, the signature
reports latency two and initiation interval one.

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
| Cell mapping | `simple_add.mapped.v`, `synth.ys`, `synthesis.log` |
| Placement | `placed.def`, `placed.odb` |
| Routing | `routed.def`, `routed.odb`, `routed.v`, `routed.sdc`, `route.guide` |
| Checks/reports | `routing-summary.json`, `route-drc.rpt`, `cts.rpt`, `timing.rpt`, `power.rpt`, and logs |

An empty `route-drc.rpt` means no detailed-routing violations were found.
Timing and power reports use global-route RC estimates with a 5 ns clock.
This smoke test exercises signal and clock routing; power-grid construction,
GDS export, and signoff are separate flow work. Select another image or
context using `EDA_IMAGE` or `EDA_DOCKER_CONTEXT`.

To view the routed layout, run `./bin/view-smoke-test.sh` and open
[http://localhost:8080](http://localhost:8080). It loads the DEF and Nangate45
libraries in OpenROAD's browser GUI. Stop it with
`./bin/view-smoke-test.sh --stop`.

## Pinned installations

| Tool | Installation |
| --- | --- |
| Google XLS | `v0.0.0-10788-g6e55d7763` Linux x64 release |
| Yosys | 0.69, commit `143eb14f9cc55d6f8927e68523b0c9d2166ed02c`, slang and ABC enabled |
| OpenSTA | 3.1.0 embedded in OpenROAD; source submodule `84c4d7e133d3b1a54352c97c6a5df3140c71c78c` |
| OpenROAD | `26Q4-878-g273fa45460`, official Ubuntu 24.04 amd64 runtime; image/layer digests pinned |
| Nangate45 | Typical corner, 1.1 V, 25 C; SHA-256 pinned in its manifest |
| Nangate45 physical | LEF and RC files from OpenROAD-flow-scripts commit `31ae82dc3149d5e66386317d564f47c672ddb1d8` |

Manifest paths are relative to this project. Versioned installations and
their stable symlinks are under `tools/`. Downloads and built runtimes are
local artifacts; keep the manifests, scripts, Docker setup, and documentation
in version control.

## Direct VM execution and tool tests

`bin/xls`, `bin/yosys`, `bin/sta`, and `bin/openroad` run the installations
through `ubuntu24` on macOS, or directly on Linux. They preserve your working
directory. Set `XLS_VM`, `YOSYS_VM`, `OPENSTA_VM`, or `OPENROAD_VM` to choose
another OrbStack machine.
Source rebuild instructions are in [Yosys](docs/yosys.md). OpenROAD
installation, VM dependencies, and physical library usage are covered in
[OpenROAD](docs/openroad.md). The `sta` command forwards to OpenROAD; existing
timing scripts need LEF inputs as described in [timing and power](docs/opensta.md).

The standalone Docker test script covers all three tools from an unrelated
temporary project, including placement of an XLS/Yosys-generated adder and
OpenDB reload. To check OpenSTA activity and OpenROAD placement through Docker:

```bash
OPENROAD_TEST_BINARY="$PWD/docker/bin/openroad" \
  python3 -m unittest discover -s tests/tools -p 'test_*.py' -v
```

## Existing xls-test project

The `xls-test` project consumes the installed Docker toolchain through small
project launchers. Its PPA flows extract manifests, binary checksums, and
Nangate45 analysis data from the selected image into `build/toolchain/`.
It has no tool installation symlinks or runtime dependency on this checkout.
Hardware designs, verification, and design build outputs remain in `xls-test`.
