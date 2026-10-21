# Shared Docker tools on macOS

The root `Dockerfile` packages the existing pinned Linux amd64 installations
on Ubuntu 24.04. It includes XLS and the DSLX standard library, Yosys 0.69
with slang and ABC, OpenSTA 3.1.0, and the Nangate45 typical Liberty library.
It copies the installation manifests and checks the recorded Yosys, ABC,
OpenSTA, and Liberty SHA-256 hashes during the image build. XLS's release
archive checksum is recorded in its manifest; individual XLS executable
checksums are not supplied by that manifest.

The binaries execute in OrbStack's Linux Docker environment. The Mac commands
are Bash launchers, and also work from Python, Make, and IDEs when their
installation directory is on `PATH`. A running OrbStack Docker engine is required.

## Build, test, and install

From this repository on macOS:

```bash
./scripts/build-docker-tools.sh
./bin/run-smoke-test.sh
python3 scripts/test-docker-tools.py
./scripts/install-docker-tools.sh
```

Defaults are image `local/eda-tools:v1`, Docker context `orbstack`, and
installation directory `~/.local/bin`. The installer copies four executable
scripts (`eda`, `xls`, `yosys`, and `sta`); those scripts do not reference
this checkout, so moving the repository does not break the installed commands.
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
slang array ports with a SAT proof, ABC cell mapping, OpenSTA timing/power,
stdin, output files, exit statuses, and parent-directory mounts. Logs,
generated artifacts, and `results.json` are retained in the output directory.

## XLS smoke test

Run `./bin/run-smoke-test.sh` from this checkout, or invoke that launcher by
absolute path from another directory. It runs `tests/smoke/simple_add.x`
through DSLX tests and quickchecks, both IR evaluators, IR optimization,
and Verilog generation in one Docker container. It does not build the image;
use the build command above first if the image is unavailable.

The source fixture and smoke-test scripts live in this toolchain project.
Generated IR, Verilog, the module signature, and the test log are written
under `build/simple_add/`. The standalone `test-docker-tools.py` integration
suite additionally exercises Yosys, ABC, OpenSTA, and launcher behavior.

## Use from any project

```bash
cd /path/to/your/project
xls interpreter_main --compare=jit src/design.x
mkdir -p build
xls ir_converter_main --top=add src/design.x > build/design.ir
xls opt_main build/design.ir > build/design.opt.ir
yosys -p 'read_slang --top top src/top.sv; synth -top top; check -assert'
sta -exit timing.tcl
```

`xls` supplies `--dslx_stdlib_path` for `interpreter_main` and
`ir_converter_main`, unless the caller supplies that option explicitly.
Use `eda <command> ...` for any other executable in the image, or `eda bash`
for an interactive Linux shell. Interactive terminals receive a TTY; pipes
and output redirection do not. Each invocation creates a disposable container.
For flows containing many small commands, run the whole script with
`eda bash scripts/flow.sh` to use one container.

The launcher bind-mounts the current physical working directory at the same
absolute path inside the container. Relative paths resolve from your current
project; absolute paths under the mounted directory work as well. Output files
appear on the Mac and commands run with your Mac UID/GID.

By default, files outside the current directory are not mounted. When invoking
from a subdirectory, or using sibling projects or libraries, explicitly mount
a common parent:

```bash
EDA_PROJECT_ROOT=/Users/kele/Workspaces yosys -s synth.ys
```

`EDA_PROJECT_ROOT` must contain the current physical directory. The mounted
tree is writable. Paths containing commas are not supported by Docker's
`--mount` syntax used by these launchers. Symlink targets outside the mounted
tree also require choosing a parent that includes the target.

The bundled Liberty file is available inside the container at
`/opt/eda/nangate45/NangateOpenCellLibrary_typical.lib`. OpenSTA scripts can use:

```tcl
read_liberty $::env(NANGATE45_LIBERTY)
```

The installed global commands use Docker. This project also provides
`./bin/xls`, `./bin/yosys`, and `./bin/sta` for direct execution through
the `ubuntu24` OrbStack machine on macOS, or directly on Linux. These
VM launchers preserve the calling directory; select another machine with
`XLS_VM`, `YOSYS_VM`, or `OPENSTA_VM`. The Docker installer uses the
separate launchers in `docker/bin/`.

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

The current image packages already-built binaries, rather than rebuilding
them from source. To update a tool, rebuild or download the desired amd64
installation, update its manifest, update the explicit versioned paths in
`Dockerfile` and `.dockerignore`, and build and test a new tag. The Ubuntu
base tag and runtime package versions can receive updates between builds;
pin a base digest and package snapshot if rebuilds must be identical.

To uninstall the Mac launchers, remove `eda`, `xls`, `yosys`, and `sta` from
the chosen installation directory. This does not remove the Docker image.
