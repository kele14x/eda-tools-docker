# OpenROAD toolchain

Both ARM64 and AMD64 build OpenROAD/OpenSTA from pinned sources inside
`docker/Dockerfile.source`. See [ARM64 Docker](arm64-docker.md) for Apple
Silicon build caching and memory controls, or [WSL containers](wsl-containers.md)
for Windows.

OpenROAD `26Q4-878-g273fa45460` is pinned to upstream commit
[`273fa45460509e46b8255684622a5da33a1c8731`](https://github.com/The-OpenROAD-Project/OpenROAD/commit/273fa45460509e46b8255684622a5da33a1c8731)
from October 22, 2026. This is an upstream development snapshot built with
`docker/openroad-parser-eof.patch` on both architectures.
OpenSTA 3.1.0 is embedded in this executable and supplies timing and power
analysis. Its source revision and runtime version are recorded under
`embedded_opensta` in the OpenROAD manifest. See [timing and power](opensta.md)
for migrating standalone OpenSTA scripts, including the required LEF inputs.

## Install and use

From this repository:

```bash
./scripts/build-docker-tools.sh
python3 scripts/test-docker-tools.py
./scripts/install-docker-tools.sh
openroad -version
```

The build defaults to ARM64. To build and select AMD64 instead:

```bash
EDA_PLATFORM=linux/amd64 ./scripts/build-docker-tools.sh
export EDA_IMAGE=local/eda-tools:amd64
```

`config/tools/openroad-install.json` pins the OpenROAD source commit and
embedded OpenSTA revision. The build verifies those revisions, applies the
parser patch, and compiles OpenROAD with Bazel. The image records build
options, the patch checksum, executable checksum, and complete runtime tree
checksum in `/opt/eda/manifests/openroad-install.json`. Development sources
and build dependencies remain in the build stages.

OpenROAD is installed at `/opt/eda/openroad` inside the image.
`docker/bin/openroad` runs it in Docker and preserves the calling project's
working directory. For example:

```bash
openroad -no_init -exit -threads 2 place.tcl
```

The main Docker launcher runs batch Tcl flows. For interactive layout
inspection, use the built-in browser viewer described below. Native Qt
display forwarding and a complete RTL-to-GDS flow are not configured here.

## View the routed adder

After running the smoke test:

```bash
./scripts/view-smoke-test.sh
```

Open [http://localhost:8080](http://localhost:8080) to pan, zoom, inspect cells
and nets, and toggle routing layers. The viewer loads `routed.def`, the
selected platform's technology/cell LEFs and Liberty, and `routed.sdc`
(ASAP7 by default; pass `--platform nangate45` for the fallback). It uses
OpenROAD's built-in web interface, so macOS does not need XQuartz.

The 3D Viewer tab expects a 3Dblox chiplet assembly. This single-die adder
DEF has no chiplets, so that tab is empty. Use Layout to inspect the cells,
metal routing, and vias.

The launcher starts a detached container with the repository mounted
read-only and publishes the viewer port on host loopback only. It reloads
the latest DEF when run again. `EDA_IMAGE` and `EDA_DOCKER_CONTEXT` select
the toolchain; `OPENROAD_VIEWER_PORT` selects another host port.

```bash
./scripts/view-smoke-test.sh --stop
```

## Timing and physical libraries

Two standard-cell platforms are installed. ASAP7 is the default for the smoke
flow; Nangate45 remains available as a comparison baseline. Both are pinned
from OpenROAD-flow-scripts commit
`31ae82dc3149d5e66386317d564f47c672ddb1d8`.

### ASAP7

`config/tools/asap7-install.json` pins the tech LEF, RVT cell LEF, five
TT/NLDM Liberty files (AO, INVBUF, OA, SIMPLE, SEQ), layer RC, extraction
rules, tapcell/track scripts, and license. The library is ASAP7 7.5-track
predictive 7 nm; it is not a production process and its PPA results are not
comparable to Nangate45.

Docker installs the files at `/opt/eda/asap7` and sets:

| Variable | File |
| --- | --- |
| `ASAP7_TECH_LEF` | `lef/asap7_tech_1x_201209.lef` |
| `ASAP7_CELLS_LEF` | `lef/asap7sc7p5t_28_R_1x_220121a.lef` |
| `ASAP7_LIBERTIES` | Five TT/RVT Liberty files under `lib/NLDM/`, space-separated |
| `ASAP7_DFF_LIBERTY` | `lib/NLDM/asap7sc7p5t_SEQ_RVT_TT_nldm_220123.lib` |
| `ASAP7_RC` | `setRC.tcl` |
| `ASAP7_RCX_RULES` | `rcx_patterns.rules` |
| `ASAP7_TRACKS` | `openRoad/make_tracks.tcl` |
| `ASAP7_TAPCELL` | `openRoad/tapcell.tcl` |
| `ASAP7_SITE` | `asap7sc7p5t` |
| `ASAP7_TIEHI_CELL` / `ASAP7_TIEHI_PORT` | `TIEHIx1_ASAP7_75t_R` / `H` |
| `ASAP7_TIELO_CELL` / `ASAP7_TIELO_PORT` | `TIELOx1_ASAP7_75t_R` / `L` |
| `ASAP7_CLOCK_BUFFER` | `BUFx2_ASAP7_75t_R` |
| `ASAP7_CLOCK_BUFFERS` | `BUFx2_ASAP7_75t_R BUFx4_ASAP7_75t_R BUFx8_ASAP7_75t_R` |

ASAP7 Liberty uses 1 ps and 1 fF units. Constraints must be written in those
units (`create_clock -period 5000` for 5 ns), unlike the Nangate45 ns/pF
libraries. `tests/smoke/simple_add_asap7.sdc` shows the conversion.

The placement site is `asap7sc7p5t`. The smoke flow routes signals on M2–M7
and clocks from M4, inserts tap/endcap cells, and uses `make_tracks.tcl`
instead of the built-in Nangate45 track generation.

### Nangate45

`config/tools/nangate45-physical-install.json` pins the technology LEF,
standard-cell LEF, layer RC Tcl, extraction rules, and license from the same
commit. The Liberty file at that revision has the same SHA-256 as this
repository's existing typical-corner library. The installer and Docker build
verify that the two installations agree.

The build downloads and verifies the physical files inside Docker and
installs them at `/opt/eda/nangate45-physical`. Docker sets these variables:

| Variable | File |
| --- | --- |
| `NANGATE45_LIBERTY` | Existing typical-corner Liberty file |
| `NANGATE45_TECH_LEF` | `lef/NangateOpenCellLibrary.tech.lef` |
| `NANGATE45_CELLS_LEF` | `lef/NangateOpenCellLibrary.macro.mod.lef` |
| `NANGATE45_RC` | `setRC.tcl` |
| `NANGATE45_RCX_RULES` | `rcx_patterns.rules` |

A Docker Tcl script can load either platform. For example:

```tcl
read_lef $::env(ASAP7_TECH_LEF)
read_lef $::env(ASAP7_CELLS_LEF)
foreach lib $::env(ASAP7_LIBERTIES) { read_liberty $lib }
read_verilog mapped.v
link_design top
source $::env(ASAP7_RC)
```

The Nangate45 placement site is `FreePDK45_38x28_10R_NP_162NW_34O`. See
`tests/smoke/openroad.tcl` for the shared floorplan, pin/cell placement, and
DEF/OpenDB export. Its optional `site`, `pin_h`, `pin_v`, `make_tracks_script`,
`tapcell_script`, and `tapcell_master` variables select platform differences.
`tests/smoke/platforms/{asap7,nangate45}.tcl` hold the settings, and
`tests/smoke/route-adder.tcl` extends the flow with clock-tree synthesis,
global routing, and detailed signal/clock routing.

Run `./scripts/run-smoke-test.sh` to exercise the XLS-generated adder through
the default ASAP7 flow, or add `--platform nangate45` for the baseline. The
test requires zero detailed-routing DRCs, verifies route coverage, and uses
`tests/smoke/check-routed-adder.tcl` in a fresh OpenROAD process to check the
saved database's 96 pipeline flops, clock buffers, and routes. Outputs and
`routing-summary.json` are retained under `build/simple_add/` (ASAP7) or
`build/simple_add-nangate45/` (Nangate45). Timing/power reports use
global-route RC estimates. Power-grid construction, signoff, and GDS
generation remain design-flow work.

## Verification and updates

The tool tests use `docker/bin/openroad` and the image's bundled Nangate45
libraries. The smoke flow defaults to ASAP7. Select the image/context with
`EDA_IMAGE` and `EDA_DOCKER_CONTEXT`.

```bash
# OpenSTA activity and OpenROAD placement through Docker
python3 -m unittest discover -s tests/tools -p 'test_*.py' -v

# XLS -> Yosys -> OpenROAD timing, power, and placement in a separate project
python3 scripts/test-docker-tools.py

# Full adder smoke flow, including clock-tree synthesis and detailed routing
./scripts/run-smoke-test.sh
```

The placement test checks 32 cells, legal placement, DEF output, and an
OpenDB round trip. The Docker suite additionally places a synthesized adder,
checks dynamic linking, and exercises launchers from paths containing spaces.
These are installation tests, not physical-design signoff.

To update OpenROAD, change its pinned source commit/version and the embedded
OpenSTA revision in `config/tools/openroad-install.json`. Check whether the
parser patch is still needed and applies cleanly, rebuild the image, and run
the integration checks before selecting it. Executable and runtime tree
checksums are generated automatically inside the image. Upstream build
instructions are in the [OpenROAD installation guide](https://openroad.readthedocs.io/en/latest/user/Build.html).
