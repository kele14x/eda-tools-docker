# Windows with WSL containers

Run these commands in PowerShell from the repository root with `wslc` available.
The build uses the native Linux AMD64 platform on an x64 Windows machine and
needs no host `tools/` directory or separate WSL distribution.

```powershell
wslc build -f docker/Dockerfile.source `
  --build-arg BUILD_JOBS=2 `
  --progress plain -t local/eda-tools:amd64 .
```

This builds OpenROAD/OpenSTA and Yosys/slang/ABC from the pinned sources,
then downloads the pinned Linux x64 XLS release. Its archive SHA-256 is checked
before extraction; the image records the release URL, archive checksum, and
installed executable checksums in `/opt/eda/manifests/xls-install.json`.
The final image also includes Nangate45, Icarus Verilog, and Verilator.

`XLS_INSTALL_MODE` defaults to `auto`: AMD64 downloads the pinned XLS release,
while ARM64 compiles XLS and LLVM from source with Bazel. No XLS mode flag is
needed for the normal build on either architecture. Use
`--build-arg XLS_INSTALL_MODE=source` to force a source build, or
`--build-arg XLS_INSTALL_MODE=release` to explicitly select the AMD64 release.
Release mode requires AMD64. `BUILD_JOBS=2` limits compilation parallelism,
and the existing BuildKit caches preserve completed work.

The macOS build and launcher scripts select Docker's OrbStack context. Invoke
`wslc` directly on Windows, for example:

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
