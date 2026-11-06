# Windows with WSL containers

Run these commands from the repository root. The build uses the native Linux
AMD64 platform on an x64 Windows machine and needs no host `tools/` directory.

The image is built in two steps by `scripts/build-docker-tools.sh`: `fetch`
downloads every input into `downloads/` (host downloads plus several short
container runs), then `build` runs one `docker build --network=none`. Because
`fetch` is a sequence of commands rather than a single `wslc build`, run the
script from a Bash shell (for example a WSL distribution) that has `curl`,
Python 3, and a Docker CLI, and point `EDA_DOCKER_CONTEXT` at that engine:

```bash
export EDA_PLATFORM=linux/amd64 EDA_DOCKER_CONTEXT=default
./scripts/build-docker-tools.sh fetch --progress=plain
EDA_BUILD_JOBS=2 ./scripts/build-docker-tools.sh build --progress=plain
```

Keep the repository on the Linux filesystem rather than `/mnt/c` so that
`downloads/` keeps Unix permissions and case-sensitive names.

Fetching downloads the Ubuntu base, all apt packages, pinned sources/submodules,
Bazel dependencies, the Linux x64 XLS release, and libraries. The offline step
builds Yosys/slang/ABC (and OpenROAD/OpenSTA when `EDA_OPENROAD_ENABLED=ON`),
then installs the prefetched XLS release. Its archive SHA-256 is checked before
extraction; the image records the release URL, archive checksum, and installed
executable checksums in `/opt/eda/manifests/xls-install.json`. Every `RUN` in
the build disables networking. The final image also includes ASAP7, Nangate45,
Icarus Verilog, and Verilator.

`EDA_XLS_INSTALL_MODE` defaults to `auto`: AMD64 uses the pinned XLS release,
while ARM64 compiles XLS and LLVM from source with Bazel. Set it to `source` to
force a source build, or `release` to explicitly select the AMD64 release.
Use the same OpenROAD/GUI/XLS settings for both steps; `build` stops with
"rerun fetch" if `downloads/` was prepared with different ones.
`EDA_BUILD_JOBS=2` limits compilation parallelism.

The resulting image can be run directly with `wslc`, for example:

```powershell
wslc run --rm local/eda-tools:amd64 yosys -V
wslc run --rm local/eda-tools:amd64 xls interpreter_main --version
```

To run the complete XLS-to-routing smoke test, mount the repository read-only
and its output directory writable:

```powershell
$repo = (Get-Location).Path
New-Item -ItemType Directory -Path build -Force | Out-Null
wslc run --rm `
  --mount "type=bind,source=$repo,target=/repo,readonly" `
  --mount "type=bind,source=$repo\build,target=/repo/build" `
  --workdir /repo local/eda-tools:amd64 bash scripts/smoke-test.sh
```

The smoke test checks XLS interpretation and JIT execution, Verilog generation,
Icarus/Verilator simulation, Yosys cell mapping, and OpenROAD placement/routing
with zero required routing DRCs. Artifacts are written to `build/simple_add/`.

The repository's `.gitattributes` keeps text files, including shell scripts,
extensionless launchers, and patches, on LF when checked out on Windows.
