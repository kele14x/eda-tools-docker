# Native ARM64 Docker on Apple Silicon

The source image targets **Linux ARM64** inside OrbStack's Linux VM. Its
XLS, Yosys/slang/ABC, and OpenROAD/OpenSTA executables run on the Apple
Silicon CPU without translating AMD64 instructions. It is not a macOS
binary image. Removing translation may improve routing performance; the
gain depends on the design, routing stage, thread count, and available RAM.
Icarus Verilog, Verilator, and the C++ build tools needed for simulation are
native Ubuntu packages, downloaded during `fetch` and installed offline.

Build from this checkout with OrbStack running:

```bash
./scripts/build-docker-tools.sh --progress=plain
```

This creates the default `local/eda-tools:arm64` using `docker/Dockerfile.source`
and tags the same image as `local/eda-tools:v1` for compatibility. AMD64 uses
the same Dockerfile. No host tool installations are required beyond Docker,
`curl`, and Python 3.

`EDA_XLS_INSTALL_MODE` defaults to `auto`: ARM64 builds XLS from source, while
AMD64 uses the checksum-pinned XLS release. Set `EDA_XLS_INSTALL_MODE=source`
to compile XLS from source on AMD64 too (`release` is AMD64 only).

The first build compiles large C++ projects, including LLVM for XLS. Allow
hours and tens of GB of disk for `downloads/` and Docker. Four compile jobs
are the default. Set `EDA_BUILD_JOBS=2` to reduce peak memory use or raise it
if the VM has sufficient RAM. The tools build sequentially.

## Fetch online, build offline

The default command runs two steps in order:

```bash
./scripts/build-docker-tools.sh fetch --progress=plain
EDA_BUILD_JOBS=2 ./scripts/build-docker-tools.sh build --progress=plain
```

**Step 1, `fetch`**, is the only step that uses the network. It writes every
build input into the project's `downloads/` folder and does not compile any
EDA tool:

```text
downloads/
  linux-arm64/              # one folder per architecture (linux-amd64 on AMD64)
    ubuntu-base.tar.gz      # checksum-pinned Ubuntu 24.04 root filesystem
    bazelisk                # checksum-pinned Bazel launcher
    apt/                    # every Ubuntu .deb plus the apt index
    yosys.tar, yosys.json   # source with submodules
    xls.tar, xls.json       # source plus Bazel binary and repositories
    openroad.tar, ...       # only with EDA_OPENROAD_ENABLED=ON
    xls-release/            # only when XLS uses the AMD64 release
  common/                   # ASAP7 and Nangate45 files
  fetch-cache/              # partial downloads kept for fetch retries
```

Each `<tool>.json` records the archive's size, SHA-256, and the inputs it was
prepared from (pins, patch, Bazel registry, GUI setting, architecture). Rerunning
`fetch` skips anything already complete and matching, and resumes interrupted
downloads. Fetch runs short-lived containers with `downloads/` bind-mounted and
removes its temporary `-fetch` images when it finishes or fails; no Docker image
holds fetched state. Run it as a regular user, not root.

**Step 2, `build`**, needs only `downloads/` and this repository. It refuses to
start if any input is missing or outdated, then runs a single `docker build`
with `--network=none --pull=false`; every `RUN` in the Dockerfile also has
`--network=none`, Bazel uses `--nofetch`, and apt installs with
`--no-download`. There are no BuildKit cache mounts and no intermediate images
to keep, so as long as `downloads/` is kept, `build` can be rerun at any time
without the network. Compilation parallelism can change without fetching again.

OpenROAD, GUI, and XLS settings are environment variables read by both steps,
so pass the same values to each:

```bash
EDA_OPENROAD_ENABLED=ON EDA_OPENROAD_GUI=ON ./scripts/build-docker-tools.sh fetch
EDA_OPENROAD_ENABLED=ON EDA_OPENROAD_GUI=ON ./scripts/build-docker-tools.sh build
```

If they differ, `build` fails immediately with "rerun fetch" instead of trying
to download. Repeat `fetch` after changing pins, the apt package lists, or the
OpenROAD patch; only the affected inputs are downloaded again.

A fetch failure is a download/dependency-resolution problem. An offline failure
reporting a missing or outdated prefetched input means `fetch` must be rerun;
any other offline failure is a compiler or build problem, not the network.

Bazel fetching prints its command and inherited options, plus a heartbeat every
30 seconds even when Bazel itself is silent. A heartbeat means the process is
alive, not that a download is progressing. Each Bazel fetch has a 1,800-second
limit, including Bazelisk startup; on timeout its process group is terminated
(with a 10-second grace period), and downloaded repository-cache entries are
saved under `downloads/fetch-cache/` for the next attempt. Failures and timeouts
print the last 16 KiB of Bazel's Java log when available. To change the limit:

```bash
EDA_BAZEL_FETCH_TIMEOUT=600 ./scripts/build-docker-tools.sh fetch --progress=plain
```

Because each tool is packed into `downloads/` as soon as it is fetched, a retry
after an XLS failure skips the already prepared libraries, Yosys, and OpenROAD.

OpenROAD builds without the Qt GUI by default (`EDA_OPENROAD_GUI=OFF`). This keeps the
browser viewer working: `-web` is wired up by the non-Qt `makeGui.cpp` stub, so
`scripts/view-smoke-test.sh` and layout inspection are unaffected. Only the native
Qt display and `save_image` are unavailable. Set `EDA_OPENROAD_GUI=ON` for
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
The Ubuntu base filesystem is checksum-pinned; apt packages are resolved during
`fetch` and the offline build installs exactly those files, so a kept
`downloads/` folder rebuilds the same package set. A fresh `fetch` may pick up
newer Ubuntu updates, so the image is pinned at the tool-source level rather
than bit-for-bit reproducible.
Simulator and C++ toolchain package versions are recorded separately in
`/opt/eda/manifests/simulation-packages.txt` inside the image.

To build and select AMD64 with the same source Dockerfile:

```bash
EDA_PLATFORM=linux/amd64 ./scripts/build-docker-tools.sh
export EDA_IMAGE=local/eda-tools:amd64
unset EDA_PLATFORM
```
