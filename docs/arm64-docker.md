# Native ARM64 Docker on Apple Silicon

The source image targets **Linux ARM64** inside OrbStack's Linux VM. Its
XLS, Yosys/slang/ABC, and OpenROAD/OpenSTA executables run on the Apple
Silicon CPU without translating AMD64 instructions. It is not a macOS
binary image. Removing translation may improve routing performance; the
gain depends on the design, routing stage, thread count, and available RAM.
Icarus Verilog and Verilator are installed as native Ubuntu packages in the
final runtime stage, together with the C++ build tools needed for simulation.
Changing these packages does not invalidate the expensive source-build stages.

Build from this checkout with OrbStack running:

```bash
./scripts/build-docker-tools.sh --progress=plain
```

This creates the default `local/eda-tools:arm64` using `docker/Dockerfile.source`
and tags the same image as `local/eda-tools:v1` for compatibility. Unlike the AMD64 packaging build, it
does not need `tools/` or `downloads/`: source code and the checksum-pinned
Nangate45 files are fetched inside Docker.

The Dockerfile defaults to `XLS_INSTALL_MODE=auto`: ARM64 builds XLS from source,
while AMD64 downloads the checksum-pinned XLS release. To compile XLS from
source on AMD64 too, pass `--build-arg XLS_INSTALL_MODE=source`.

The first build downloads compilers/dependencies and compiles large C++
projects, including LLVM for XLS. Allow hours and tens of GB of Docker disk
space. Four compile jobs are the default. Set `EDA_BUILD_JOBS=2` to reduce
peak memory use or raise it if the VM has sufficient RAM. The tools build
sequentially, and Bazel downloads and compilation outputs persist in a
BuildKit cache so the same build command can resume after a failure.
Deleting Docker's build cache loses that work.

OpenROAD includes the Qt/browser viewer by default. A batch-only build is
available with `--build-arg OPENROAD_GUI=OFF`; that image does not support
`bin/view-smoke-test.sh`. OpenROAD uses its pinned Bazel release configuration
(optimized with ThinLTO), Yosys uses CMake Release, and XLS uses Bazel `opt`.

## Validate and select the image

```bash
python3 scripts/test-docker-tools.py --expected-architecture arm64 \
  --output-dir build/docker-tools-arm64
./bin/run-smoke-test.sh
OPENROAD_TEST_BINARY="$PWD/docker/bin/openroad" \
  python3 -m unittest discover -s tests/tools -p 'test_*.py' -v
./scripts/install-docker-tools.sh
```

Reinstalling the launchers is necessary if the installed copies still force
`--platform linux/amd64`. Updated launchers let Docker use the selected
image's architecture. `EDA_PLATFORM` can explicitly constrain Docker's
platform when needed, but selecting the image with `EDA_IMAGE` is enough.
With no `EDA_IMAGE`, all Docker launchers, including `iverilog`, `vvp`, and `verilator`, select
`local/eda-tools:arm64`. No shell configuration is required. Set `EDA_IMAGE`
only when selecting a different toolchain.

The integration test checks the image architecture, executable ELF headers,
runtime checksums, dynamic linking, XLS's ARM64 LLVM JIT, Icarus and Verilator
simulation of XLS-generated RTL, Verilator C++ compilation and tracing, slang and ABC,
OpenSTA timing/power, placement, and database reload. The smoke test also runs
both simulators on the adder with waveform checks, then adds clock-tree
synthesis and detailed routing with zero required DRC violations.
Smoke-test outputs replace `build/simple_add/`; keep separate copies before
comparing architectures. Benchmark the same design and thread count after
the source build has finished so compilation does not compete with routing.

## Source and output provenance

The existing manifests pin OpenROAD and its OpenSTA submodule, Yosys and
its bundled dependencies, and Nangate45. `config/tools/source-build.json`
adds the full source commit for the existing XLS release and SHA-256 hashes
for the Bazel bootstrap launcher. Bazel reads each project's pinned
`.bazelversion` and dependency definitions. Upstream instructions:
[OpenROAD builds](https://openroad.readthedocs.io/en/latest/user/Build.html)
and [XLS builds](https://google.github.io/xls/).

Source builds generate their own `/opt/eda/manifests/*-install.json` inside
the image, recording native executable hashes, build options, and source
identities. Host AMD64 manifests and binaries remain intact. These output
hashes describe this build; they are not prepublished upstream checksums.
Ubuntu apt dependencies are resolved at build time, so the build is pinned
at the tool-source level rather than bit-for-bit reproducible.
Simulator and C++ toolchain package versions are recorded separately in
`/opt/eda/manifests/simulation-packages.txt` inside the image.

To build and select the optional AMD64 image from the host installations:

```bash
EDA_PLATFORM=linux/amd64 ./scripts/build-docker-tools.sh
export EDA_IMAGE=local/eda-tools:amd64
unset EDA_PLATFORM
```
