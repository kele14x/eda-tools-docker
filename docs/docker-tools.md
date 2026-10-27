# Shared Docker tools on macOS

The default image is `local/eda-tools:arm64`, built from source for Apple
Silicon: see [ARM64 Docker](arm64-docker.md). Build it with
`./scripts/build-docker-tools.sh`. The same native image is tagged
`local/eda-tools:v1` for compatibility. The launchers support both
architectures; reinstall older copies that force AMD64.

Both architectures use `docker/Dockerfile.source` on Ubuntu 24.04. It compiles
Yosys 0.69 with slang/ABC and OpenROAD `26Q4-878-g273fa45460` with embedded
OpenSTA 3.1.0 from pinned sources, and downloads the pinned Nangate45
timing/physical libraries. XLS and its DSLX standard library are built from
source on ARM64; AMD64 uses the checksum-pinned XLS release by default.
Pass `--build-arg XLS_INSTALL_MODE=source` to compile XLS on AMD64 too.
The build records executable and OpenROAD tree checksums in runtime
manifests and verifies them, along with the pinned LEF/RC/license files,
before completing the image. Host tool installations are not required.

Both architectures also install Icarus Verilog (`iverilog` and `vvp`), Verilator,
`build-essential`, and zlib development headers from Ubuntu 24.04. Keeping
the C++ compiler and Make in the runtime image lets Verilator build executable
simulations with `--binary` or `--cc --exe --build`. Package versions are
recorded in `/opt/eda/manifests/simulation-packages.txt` during the build.
These simulator packages use Ubuntu's versions, which may lag upstream releases;
they are not source-pinned like XLS, Yosys, and OpenROAD.

The binaries execute in OrbStack's Linux Docker environment. The Mac commands
are Bash launchers, and also work from Python, Make, and IDEs when their
installation directory is on `PATH`. A running OrbStack Docker engine is required.

## Build, test, and install

From this repository on macOS:

```bash
./scripts/build-docker-tools.sh
./scripts/run-smoke-test.sh
python3 scripts/test-docker-tools.py
./scripts/install-docker-tools.sh
```

Defaults are image `local/eda-tools:arm64`, Docker context `orbstack`, and
installation directory `~/.local/bin`. The launcher installer copies eight
executable scripts (`eda`, `xls`, `yosys`, `sta`, `openroad`, `iverilog`, `vvp`,
and `verilator`); those scripts
do not reference this checkout, so moving the repository does not break the installed commands.
It only replaces files marked as belonging to this setup, and refuses to
overwrite unrelated commands. Re-run it after changing the launchers.

If needed, add `export PATH="$HOME/.local/bin:$PATH"` to your shell startup
file. Put it before Homebrew in `PATH` to select this Yosys instead of Homebrew's.
For a running zsh, use `rehash` after installing. Existing shell aliases or
functions with these names also take precedence over executables.

To verify the installed commands rather than the repository copies:

```bash
python3 scripts/test-docker-tools.py --bin-dir "$HOME/.local/bin" \
  --output-dir build/docker-tools-installed
```

The integration checks create a temporary project outside this repository,
including spaces in its path. They check the actual image architecture,
dynamic libraries, DSLX stdlib imports, unit tests and quickchecks with the
IR interpreter and LLVM JIT, IR conversion/optimization, pipelined Verilog,
Icarus and Verilator simulation of that RTL (including overflow and VCD traces),
Verilator lint and C++ compilation,
slang array ports with a SAT proof, ABC cell mapping, OpenSTA timing/power,
OpenROAD floorplanning/placement and DEF/OpenDB export/reload, stdin, output
files, exit statuses, and parent-directory mounts. Verilator's compilation
uses a separate temporary path without spaces, as required by its Makefiles.
Logs, generated artifacts, and `results.json` are retained in the output directory.

## XLS-to-routing smoke test

Run `./scripts/run-smoke-test.sh` from this checkout, or invoke that script by
absolute path from another directory. It runs `tests/smoke/simple_add.x`
through DSLX tests and quickchecks, both IR evaluators, IR optimization,
Verilog generation, Icarus/Verilator simulation, Yosys/ABC mapping to Nangate45,
and OpenROAD placement, clock-tree synthesis, global routing, and detailed
routing in one Docker container. It does not build the image; use the build
command above first if the image is unavailable.

Both simulators compile and run `tests/smoke/simple_add_tb.sv` against the
generated RTL, checking six sums including zero and 32-bit overflow with
the two-cycle pipeline latency. Each run must report `RTL_SIMULATION_PASS`
and produce a nonempty VCD waveform. Verilator builds a C++ executable with
timing and tracing enabled. Waveforms are saved as `iverilog.vcd` and
`verilator.vcd` alongside the simulator build/run logs in `build/simple_add/`.

The source fixture and smoke-test scripts live in this toolchain project.
Generated IR, Verilog, the module signature, mapped cells, placed/routed
DEF and OpenDB files, route guides, constraints, reports, and logs are written
under `build/simple_add/`. `routing-summary.json` records cell, flop, clock
buffer, and routed-net counts. The test requires zero detailed-routing DRCs,
checks routing coverage, and reloads the routed database in a fresh process.
An empty `route-drc.rpt` records a clean detailed-routing result.

The 5 ns timing and power reports use global-route RC estimates. Supply-grid
routing, GDS export, and signoff are outside this smoke test. The standalone
`test-docker-tools.py` integration suite additionally exercises slang, the
embedded OpenSTA activity/timing flow, and launcher behavior from an unrelated
temporary project.

## Use from any project

```bash
cd /path/to/your/project
xls interpreter_main --compare=jit src/design.x
mkdir -p build
xls ir_converter_main --top=add src/design.x > build/design.ir
xls opt_main build/design.ir > build/design.opt.ir
yosys -p 'read_slang --top top src/top.sv; synth -top top; check -assert'
sta -exit timing.tcl
openroad -no_init -exit -threads 2 place.tcl
```

`xls` supplies `--dslx_stdlib_path` for `interpreter_main` and
`ir_converter_main`, unless the caller supplies that option explicitly.
Use `eda <command> ...` for any other executable in the image, or `eda bash`
for an interactive Linux shell. Interactive terminals receive a TTY; pipes
and output redirection do not. Each invocation creates a disposable container.
For flows containing many small commands, run the whole script with
`eda bash scripts/flow.sh` to use one container.

### RTL simulation and linting

Compile a testbench with Icarus, then execute it with its `vvp` runtime:

```bash
iverilog -g2012 -s tb -o simulation.vvp src/design.sv tests/tb.sv
vvp simulation.vvp
```

Lint RTL or compile a SystemVerilog testbench with Verilator:

```bash
verilator --lint-only --top-module top src/top.sv
verilator --binary --timing --trace --top-module tb -j 2 src/design.sv tests/tb.sv
eda ./obj_dir/Vtb
```

The generated executable is a Linux binary; run it through `eda` on macOS.
Verilator's generated Makefiles require project/build paths without spaces.
Use `$dumpfile`/`$dumpvars` in the testbench to write a VCD trace to the mounted
project. Rebuild the image and rerun the launcher installer to add these commands
to an existing installation. Upstream usage references:
[Icarus compile/run workflow](https://steveicarus.github.io/iverilog/usage/getting_started.html)
and [Verilator installation requirements](https://verilator.org/guide/latest/install.html).

### Project paths and library inputs

The launcher bind-mounts the current physical working directory at the same
absolute path inside the container. Relative paths resolve from your current
project; absolute paths under the mounted directory work as well. Output files
appear on the Mac and commands run with your Mac UID/GID.

By default, files outside the current directory are not mounted. When invoking
from a subdirectory, or using sibling projects or libraries, explicitly mount
a common parent:

```bash
EDA_PROJECT_ROOT=/path/to/workspace yosys -s synth.ys
```

`EDA_PROJECT_ROOT` must contain the current physical directory. The mounted
tree is writable. Paths containing commas are not supported by Docker's
`--mount` syntax used by these launchers. Symlink targets outside the mounted
tree also require choosing a parent that includes the target.

The bundled Liberty file is available inside the container at
`/opt/eda/nangate45/NangateOpenCellLibrary_typical.lib`. OpenROAD timing scripts use:

```tcl
read_lef $::env(NANGATE45_TECH_LEF)
read_lef $::env(NANGATE45_CELLS_LEF)
read_liberty $::env(NANGATE45_LIBERTY)
```

`sta` is a compatibility alias for OpenROAD, which embeds OpenSTA. The image
has no standalone OpenSTA binary or manifest. Existing timing scripts need
the LEF-loading commands above; see [timing and power](opensta.md) for migration
and analyzer provenance.

All tool commands use the Docker launchers in `docker/bin/`; the installer
copies them into `~/.local/bin` by default. See [OpenROAD](openroad.md) for
physical-library variables and placement examples.
The Docker OpenROAD launcher supports batch flows. To inspect the routed
smoke-test adder interactively, run `./scripts/view-smoke-test.sh` and open
[http://localhost:8080](http://localhost:8080). This uses OpenROAD's browser
viewer with a read-only project mount; stop it with
`./scripts/view-smoke-test.sh --stop`. Native Qt display forwarding is not configured.

## Select or distribute a toolchain

Set `EDA_IMAGE` to select another image, and `EDA_DOCKER_CONTEXT` to select
another Docker context. These variables also apply to the build and test
scripts. For example, build and check another tag before selecting it:

```bash
EDA_IMAGE=local/eda-tools:v2 ./scripts/build-docker-tools.sh
EDA_IMAGE=local/eda-tools:v2 python3 scripts/test-docker-tools.py
EDA_IMAGE=local/eda-tools:v2 yosys -V
```

To share with another machine, tag and push the image to a registry you use,
then install the launchers there and set `EDA_IMAGE` to that registry tag or
digest. Projects can pin a digest for an immutable tool selection. No image
is published by the build or installation scripts.

Build AMD64 with `EDA_PLATFORM=linux/amd64 ./scripts/build-docker-tools.sh`;
its tag is `local/eda-tools:amd64`. ARM64 defaults to `local/eda-tools:arm64`.
Both use the same source Dockerfile and input manifests. To update a tool,
change its source/release pins in `config/tools/`, build a new image, and
run the integration tests. Runtime executable checksums are generated
automatically. The Ubuntu base tag and runtime package versions can receive
updates between builds; pin a base digest and package snapshot if rebuilds
must be identical. See the [native build guide](arm64-docker.md) for build
caching and memory controls.

To uninstall the Mac launchers, remove `eda`, `xls`, `yosys`, `sta`, `openroad`,
`iverilog`, `vvp`, and `verilator` from the chosen installation directory.
This does not remove the Docker image.
