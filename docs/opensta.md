# Timing and power through OpenROAD

OpenROAD contains the OpenSTA timing and power engine. This toolchain uses
that embedded engine for analysis and has no separate OpenSTA package or
installation manifest in its Docker image.

The pinned OpenROAD executable reports OpenSTA version **3.1.0** through
`sta::version`. The OpenROAD source pins its `src/sta` submodule to
[`84c4d7e133d3b1a54352c97c6a5df3140c71c78c`](https://github.com/The-OpenROAD-Project/OpenSTA/tree/84c4d7e133d3b1a54352c97c6a5df3140c71c78c).
The prebuilt executable reports `unknown` for `sta::git_sha1`; the OpenROAD
source commit, image digest, and executable checksum identify the build.
These details are recorded under `embedded_opensta` in
`config/tools/openroad-install.json`.

## Run timing and power analysis

Use OpenROAD for the same Liberty, SDC, timing-report, and power commands:

```bash
openroad -no_init -no_splash -exit timing.tcl
```

OpenROAD's design linker requires technology and cell LEFs as well as
Liberty. A timing-only Nangate45 script in Docker starts with:

```tcl
read_lef $::env(NANGATE45_TECH_LEF)
read_lef $::env(NANGATE45_CELLS_LEF)
read_liberty $::env(NANGATE45_LIBERTY)
read_verilog mapped.v
link_design top
create_clock -name clk -period 5 [get_ports clk]
report_checks -path_delay max
report_power
puts "OpenSTA version: [sta::version]"
```

No floorplan or placement is required for this timing/power analysis. The
Docker image sets the library variables used above.

## Migrate standalone OpenSTA callers

`sta` remains a small compatibility launcher for `openroad`. This applies
to `docker/bin/sta`, the installed Docker launcher, and `sta` inside the image.
Add the two `read_lef` lines above to existing timing scripts. The alias
does not automatically select physical libraries for the design.

`sta -version` reports the OpenROAD build. Query `sta::version` in Tcl for
the embedded OpenSTA version. OpenROAD uses `.openroad` as its initialization
file; pass `-no_init` for reproducible batch analysis.

Projects that previously extracted `opensta-install.json` or hashed
`/opt/eda/opensta/sta` should now read `embedded_opensta` from
`/opt/eda/manifests/openroad-install.json` and hash
`/opt/eda/openroad/bin/openroad`. The standalone source/download artifacts
from earlier installations are not used by the active launchers or image.

## Verify power activity

The regression tests check that a measured cell-output annotation survives
power propagation and that gated clocks retain 2, 1, 0.5, and 0 transitions
per cycle. They run through `docker/bin/openroad` with the image's bundled
Nangate45 cells. Select the image/context with `EDA_IMAGE` and `EDA_DOCKER_CONTEXT`:

```bash
python3 -m unittest discover -s tests/tools -p 'test_opensta_activity.py' -v
```

See [OpenROAD setup](openroad.md) for runtime installation and updates.
Consuming projects should record the OpenROAD and embedded OpenSTA
identities in their measurement manifests, since analysis changes can
affect timing and power estimates.
