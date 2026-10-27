# Yosys toolchain

The Docker image uses an unmodified Yosys 0.69 source release, with the
bundled sv-elab/slang frontend and ABC enabled.
`config/tools/yosys-install.json` pins the source archive checksum, source
and dependency revisions, and build options. The source image compiles
Yosys inside Docker and records the installed executable checksums in
`/opt/eda/manifests/yosys-install.json`.

`docker/bin/yosys` runs the packaged tool in Docker and preserves the calling
directory. After installing the launchers with
`./scripts/install-docker-tools.sh`, use `yosys` from any project:

```bash
yosys -V
yosys -p 'help read_slang'
```

The image includes ABC and Yosys's runtime data files. Select another image
with `EDA_IMAGE` or another Docker context with `EDA_DOCKER_CONTEXT`.

## SystemVerilog input

Use `read_slang` explicitly for SystemVerilog features such as unpacked
array ports. `read_verilog -sv` selects Yosys's older parser.

```bash
yosys -p 'read_slang --top my_top design.sv; synth -top my_top; check -assert'
```

## Build and validate in Docker

From this project on macOS, build the native ARM64 source image and run
the standalone tool tests:

```bash
./scripts/build-docker-tools.sh
python3 scripts/test-docker-tools.py
./docker/bin/yosys -V
./docker/bin/yosys -p 'help read_slang'
```

`docker/build-source-tools.py` downloads and verifies the official release
archive, which contains Yosys, ABC, sv-elab, slang, and the other bundled
libraries. It applies the manifest's CMake options and installs Yosys under
`/opt/eda/yosys` inside the image. The revision options supply source and
dependency metadata when building from the archive without Git submodule
checkouts. Executable checksums are recorded automatically after the build.

The integration flow checks slang array ports with a SAT proof, bundled
runtime data, ABC technology mapping with Nangate45, and OpenSTA timing/power.
See [ARM64 Docker](arm64-docker.md) for build caching and memory controls,
[WSL containers](wsl-containers.md) for an AMD64 source image on Windows,
and [Docker tools](docker-tools.md) for selecting either architecture on macOS.
