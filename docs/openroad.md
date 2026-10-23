# OpenROAD toolchain

For native Apple Silicon execution, [build the Linux ARM64 source image](arm64-docker.md).
It builds the same pinned OpenROAD/OpenSTA sources inside Docker. The installation
instructions below describe the existing AMD64 binary image.

OpenROAD `26Q4-878-g273fa45460` is pinned to upstream commit
[`273fa45460509e46b8255684622a5da33a1c8731`](https://github.com/The-OpenROAD-Project/OpenROAD/commit/273fa45460509e46b8255684622a5da33a1c8731)
from October 22, 2026. This is an upstream development snapshot, packaged from
the official `openroad/ubuntu24.04` amd64 image. No local patches are applied.
OpenSTA 3.1.0 is embedded in this executable and supplies timing and power
analysis. Its source revision and runtime version are recorded under
`embedded_opensta` in the OpenROAD manifest. See [timing and power](opensta.md)
for migrating standalone OpenSTA scripts, including the required LEF inputs.

## Install and use

From this repository:

```bash
python3 scripts/install-openroad.py
EDA_PLATFORM=linux/amd64 ./scripts/build-docker-tools.sh
EDA_IMAGE=local/eda-tools:amd64 python3 scripts/test-docker-tools.py
./scripts/install-docker-tools.sh
EDA_IMAGE=local/eda-tools:amd64 openroad -version
```

The installer downloads the 63 MB runtime layer from Docker Hub using an
anonymous registry token. It does not require a Docker daemon or account.
`config/tools/openroad-install.json` pins the source image digest, runtime
layer checksum, source commit, binary checksum, and complete installed tree
checksum. Only `OpenROAD/install` is extracted; development sources and
build dependencies are excluded. Cached downloads are verified before reuse,
and an existing installation with a mismatched checksum is rejected.

The versioned runtime lives under `tools/`, with `tools/openroad` as its
stable symlink. `docker/bin/openroad` runs the packaged tool in Docker and
preserves the calling project's working directory. For example:

```bash
openroad -no_init -exit -threads 2 place.tcl
```

The main Docker launcher runs batch Tcl flows. For interactive layout
inspection, use the built-in browser viewer described below. Native Qt
display forwarding and a complete RTL-to-GDS flow are not configured here.

## View the routed adder

After running the smoke test:

```bash
./bin/view-smoke-test.sh
```

Open [http://localhost:8080](http://localhost:8080) to pan, zoom, inspect cells
and nets, and toggle routing layers. The viewer loads `routed.def`, the
Nangate45 technology/cell LEFs and Liberty, and `routed.sdc`. It uses
OpenROAD's built-in web interface, so macOS does not need XQuartz.

The 3D Viewer tab expects a 3Dblox chiplet assembly. This single-die adder
DEF has no chiplets, so that tab is empty. Use Layout to inspect the cells,
metal routing, and vias.

The launcher starts a detached container with the repository mounted
read-only and publishes the viewer port on host loopback only. It reloads
the latest DEF when run again. `EDA_IMAGE` and `EDA_DOCKER_CONTEXT` select
the toolchain; `OPENROAD_VIEWER_PORT` selects another host port.

```bash
./bin/view-smoke-test.sh --stop
```

The native Qt GUI is also compiled into the binary and can be launched
with `-gui` on a Linux desktop with an X display. The saved `routed.odb`
can be opened directly with `openroad -gui -db routed.odb`; reading a DEF
requires loading its technology and cell LEFs first.

## Nangate45 physical files

`config/tools/nangate45-physical-install.json` pins the technology LEF,
standard-cell LEF, layer RC Tcl, extraction rules, and license from
OpenROAD-flow-scripts commit `31ae82dc3149d5e66386317d564f47c672ddb1d8`.
The Liberty file at that revision has the same SHA-256 as this repository's
existing typical-corner library. The installer and Docker build verify that
the two installations agree.

The physical files are installed at `tools/nangate45-physical` and packaged
at `/opt/eda/nangate45-physical`. Docker sets these variables:

| Variable | File |
| --- | --- |
| `NANGATE45_LIBERTY` | Existing typical-corner Liberty file |
| `NANGATE45_TECH_LEF` | `lef/NangateOpenCellLibrary.tech.lef` |
| `NANGATE45_CELLS_LEF` | `lef/NangateOpenCellLibrary.macro.mod.lef` |
| `NANGATE45_RC` | `setRC.tcl` |
| `NANGATE45_RCX_RULES` | `rcx_patterns.rules` |

A Docker Tcl script can load them with:

```tcl
read_lef $::env(NANGATE45_TECH_LEF)
read_lef $::env(NANGATE45_CELLS_LEF)
read_liberty $::env(NANGATE45_LIBERTY)
read_verilog mapped.v
link_design top
source $::env(NANGATE45_RC)
```

The placement site is `FreePDK45_38x28_10R_NP_162NW_34O`. See
`tests/smoke/openroad.tcl` for the shared floorplan, pin/cell placement, and
DEF/OpenDB export. `tests/smoke/route-adder.tcl` extends it with clock-tree
synthesis, global routing, and detailed signal/clock routing on metal2–metal6.
The 5 ns adder constraints are in `tests/smoke/simple_add.sdc`.

Run `./bin/run-smoke-test.sh` to exercise the XLS-generated adder through
this flow. It requires zero detailed-routing DRCs, verifies route coverage,
and uses `tests/smoke/check-routed-adder.tcl` in a fresh OpenROAD process to
check the saved database's 96 pipeline flops, clock buffers, and routes.
Outputs and `routing-summary.json` are retained under `build/simple_add/`.
Timing/power reports use global-route RC estimates. Power-grid construction,
signoff, and GDS generation remain design-flow work.

## Direct Linux / OrbStack execution

`bin/openroad` runs through `ubuntu24` on macOS, or directly on Linux.
Set `OPENROAD_VM` to select another OrbStack machine. The binary targets
Ubuntu 24.04 x86-64 and requires X11 runtime libraries even in batch mode.
Docker installs these automatically. For direct execution, install them
inside the Linux environment (from the repository root):

```bash
sudo apt-get update
python3 - <<'PY'
import json
import subprocess
from pathlib import Path
manifest = json.loads(Path('config/tools/openroad-install.json').read_text())
subprocess.run(['sudo', 'apt-get', 'install', '-y', '--no-install-recommends',
                *manifest['runtime_dependencies']['apt']], check=True)
PY
./bin/openroad -version
```

The VM launcher does not set the Docker library environment variables; use
the corresponding absolute paths under `tools/` in direct-execution scripts.

## Verification and updates

```bash
# Direct VM / Linux tool tests
python3 -m unittest discover -s tests/tools -p 'test_*.py' -v

# The same tests through Docker
OPENROAD_TEST_BINARY="$PWD/docker/bin/openroad" \
  python3 -m unittest discover -s tests/tools -p 'test_*.py' -v

# XLS -> Yosys -> OpenROAD timing, power, and placement in a separate project
python3 scripts/test-docker-tools.py

# Full adder smoke flow, including clock-tree synthesis and detailed routing
./bin/run-smoke-test.sh
```

The placement test checks 32 cells, legal placement, DEF output, and an
OpenDB round trip. The Docker suite additionally places a synthesized adder,
checks dynamic linking, and exercises launchers from paths containing spaces.
These are installation tests, not physical-design signoff.

To update OpenROAD, select an immutable amd64 image digest, identify its
installation layer, verify its contents, refresh the manifest checksums,
and update the versioned paths in `Dockerfile` and `.dockerignore`. Run the
installer and integration checks before selecting the new image. Do not
replace the pinned image with `latest`. Upstream source-build instructions
are in the [OpenROAD installation guide](https://openroad.readthedocs.io/en/latest/user/Build.html).
