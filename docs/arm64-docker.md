# Native ARM64 Docker on Apple Silicon

The source image targets **Linux ARM64** inside OrbStack's Linux VM. Its
XLS, Yosys/slang/ABC, and OpenROAD/OpenSTA executables run on the Apple
Silicon CPU without translating AMD64 instructions. It is not a macOS
binary image. Removing translation may improve routing performance; the
gain depends on the design, routing stage, thread count, and available RAM.
Icarus Verilog and Verilator are prepared online as native Ubuntu packages,
together with the C++ build tools needed for simulation. Their runtime filesystem
is stored in the dependency image before source compilation starts.

Build from this checkout with OrbStack running:

```bash
./scripts/build-docker-tools.sh --progress=plain
```

This creates the default `local/eda-tools:arm64` using `docker/Dockerfile.source`
and tags the same image as `local/eda-tools:v1` for compatibility. AMD64 uses
the same Dockerfile. Both builds fetch sources, releases, and checksum-pinned
ASAP7 and Nangate45 files inside Docker, with no host tool installations required.

The Dockerfile defaults to `XLS_INSTALL_MODE=auto`: ARM64 builds XLS from source,
while AMD64 downloads the checksum-pinned XLS release. To compile XLS from
source on AMD64 too, pass `--build-arg XLS_INSTALL_MODE=source`.

The first build downloads compilers/dependencies and compiles large C++
projects, including LLVM for XLS. Allow hours and tens of GB of Docker disk
space, including the prepared sources and resolved Bazel repositories. Four
compile jobs are the default. Set `EDA_BUILD_JOBS=2` to reduce peak memory use
or raise it if the VM has sufficient RAM. The tools build sequentially.

## Fetch online, build offline

The default command runs these two phases in order:

```bash
./scripts/build-docker-tools.sh fetch --progress=plain
EDA_BUILD_JOBS=2 ./scripts/build-docker-tools.sh build --progress=plain
```

`fetch` stops at the Dockerfile's `dependencies` target and saves
`local/eda-tools:arm64-deps` (AMD64: `local/eda-tools:amd64-deps`). It prepares
system packages, sources with recursive submodules, patches, Bazelisk and the
projects' pinned Bazel binaries, target-specific Bazel repositories, XLS release
archives when selected, and ASAP7/Nangate45 files. It also stores the complete
runtime filesystem, including simulator packages. It does not compile the EDA
tools. Bazel dependency rules can still execute setup programs during fetching.

`build` requires that local image with the requested architecture and skips all
online Docker stages. Every compilation, extraction, and final verification
`RUN` has `--network=none`; Bazel also uses `--nofetch`, with the upstream remote
action cache disabled. Aspect telemetry is disabled in both phases. There is no
automatic online fallback. No external Dockerfile frontend is needed. Use Docker's local
image-store builder, as the wrapper does by default; a remote builder needs the
prepared image loaded into its own store before going offline.

GUI selection, XLS mode, registries, and tool pins are captured during `fetch`:

```bash
./scripts/build-docker-tools.sh fetch --build-arg OPENROAD_GUI=ON
./scripts/build-docker-tools.sh build
```

Repeat `fetch` after changing these inputs or the source-build helper; they are
not changed by passing new GUI/XLS flags only to `build`. Compilation parallelism
can change without fetching again. Override `EDA_DEPENDENCIES_IMAGE` to keep
separate prepared variants. Download caches speed up retries; resolved sources,
Bazel repositories, and Bazel binaries live in image layers, not disposable
cache mounts. Only Bazel action outputs use an offline BuildKit cache mount.
Clearing that cache loses reusable compiled outputs but not the prepared inputs.

A fetch failure is a download/dependency-resolution problem. An offline failure
reporting an absent repository, archive, or source is a prefetch-completeness
problem; compiler/linker errors can then be investigated separately. Network
isolation alone does not prove an error is in the source code.

Bazel fetching prints its command and inherited options, plus a heartbeat every
30 seconds even when Bazel itself is silent. A heartbeat means the process is
alive, not that a download is progressing. Each Bazel fetch has a 1,800-second
limit, including Bazelisk startup; on timeout its process group is terminated
(with a 10-second grace period), and downloaded repository-cache entries are
saved for retry. Failures and timeouts print the last 16 KiB of Bazel's Java log
when available. Compilation has no fetch timeout. To change the fetch limit:

```bash
./scripts/build-docker-tools.sh fetch --build-arg BAZEL_FETCH_TIMEOUT=600 --progress=plain
```

For an XLS-only retry after OpenROAD and Yosys fetching succeeded, save the
completed stage before editing the fetch helper, then reuse that local image:

```bash
docker --context orbstack build --platform linux/arm64 -f docker/Dockerfile.source \
  --target yosys-fetch -t local/eda-tools:arm64-fetch-checkpoint .
./scripts/build-docker-tools.sh all --progress=plain \
  --build-arg XLS_FETCH_BASE_IMAGE=local/eda-tools:arm64-fetch-checkpoint \
  --build-arg BAZEL_FETCH_TIMEOUT=600
```

The checkpoint fixes the architecture, OpenROAD/Yosys pins, GUI settings, and
Bazel registries; use it only when those inputs are unchanged. Omit
`XLS_FETCH_BASE_IMAGE` for a normal complete fetch.

OpenROAD builds without the Qt GUI by default (`OPENROAD_GUI=OFF`). This keeps the
browser viewer working: `-web` is wired up by the non-Qt `makeGui.cpp` stub, so
`scripts/view-smoke-test.sh` and layout inspection are unaffected. Only the native
Qt display and `save_image` are unavailable. Pass `--build-arg OPENROAD_GUI=ON` for
a Qt build; it adds the Qt prebuilts and the ARM64 Qt workarounds. OpenROAD uses its
pinned Bazel release configuration (optimized with ThinLTO), Yosys uses CMake
Release, and XLS uses Bazel `opt`.

## Validate and select the image

```bash
python3 scripts/test-docker-tools.py --expected-architecture arm64 \
  --output-dir build/docker-tools-arm64
./scripts/run-smoke-test.sh
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
its bundled dependencies, and the ASAP7 and Nangate45 libraries. `config/tools/source-build.json`
adds the full source commit for the existing XLS release and SHA-256 hashes
for the Bazel bootstrap launcher. Bazel reads each project's pinned
`.bazelversion` and dependency definitions. Upstream instructions:
[OpenROAD builds](https://openroad.readthedocs.io/en/latest/user/Build.html)
and [XLS builds](https://google.github.io/xls/).

Source builds generate their own `/opt/eda/manifests/*-install.json` inside
the image, recording native executable hashes, build options, and source
identities. The repository manifests describe build inputs; the image's
output hashes describe this build and are not prepublished upstream checksums.
Ubuntu apt dependencies are resolved at build time, so the build is pinned
at the tool-source level rather than bit-for-bit reproducible.
Simulator and C++ toolchain package versions are recorded separately in
`/opt/eda/manifests/simulation-packages.txt` inside the image.

To build and select AMD64 with the same source Dockerfile:

```bash
EDA_PLATFORM=linux/amd64 ./scripts/build-docker-tools.sh
export EDA_IMAGE=local/eda-tools:amd64
unset EDA_PLATFORM
```
