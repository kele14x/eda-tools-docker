# OpenSTA toolchain

The default analyzer is an **OpenSTA development snapshot (source version 3.1.0)**,
built from unmodified upstream
commit [`d1e43c6f9f4e66cb59c3d7958a4aa7d1626b4614`](https://github.com/The-OpenROAD-Project/OpenSTA/commit/d1e43c6f9f4e66cb59c3d7958a4aa7d1626b4614),
dated September 28, 2026. `config/tools/opensta-install.json` pins its full
commit, source archive, SHA-256 checksums, and build dependencies. Its `patches`
list is empty.

`bin/sta` runs `tools/opensta/sta`, through the `ubuntu24` OrbStack VM on macOS
and directly on Linux. `tools/opensta` points to
`opensta-3.1.0-gd1e43c6f9f4e-linux-x64`. CUDD 3.0.0 is linked statically and
Eigen 3.4 supplies headers at build time. The installed executable uses the
VM's Tcl, zlib, C++ runtime, and standard system libraries; it has no runtime
dependency on files under `build/`.

## Power activity

The current upstream power analyzer retains explicit pin annotations during
propagation and uses annotated gated-clock pulse density. These were the
behaviors fixed by the project's old 2.0.17 patch. The PPA script now uses the
public `set_power_activity -pins` command and records the installed source
commit and empty patch list in each measurement manifest.

The integration tests check that a measured cell-output annotation survives
power propagation, and that gated clocks retain 2, 1, 0.5, and 0 transitions
per cycle. Both checks use the actual installed analyzer and Nangate45 cells:

```bash
python3 -m unittest discover -s tests/tools -p 'test_*.py' -v
```

Consuming projects should record tool identities in their measurement
manifests; timing and power estimates can change as analysis models evolve.

## Rebuild in Ubuntu 24.04

Run these commands from the project root inside `ubuntu24`. Start with fresh
source directories under the disposable `build/` tree. The OpenSTA checkout
uses the full pinned commit so CMake can embed the Git revision. Its verified
GitHub source archive is also cached in `downloads/`.

```bash
sudo apt-get install cmake g++ make git bison flex swig tcl8.6-dev zlib1g-dev libeigen3-dev
mkdir -p build/tools/opensta/source build/tools/cudd/source
git -C build/tools/opensta/source init
git -C build/tools/opensta/source fetch --depth=1 https://github.com/The-OpenROAD-Project/OpenSTA.git d1e43c6f9f4e66cb59c3d7958a4aa7d1626b4614
git -C build/tools/opensta/source checkout --detach FETCH_HEAD
tar -xzf downloads/cudd-3.0.0-gf54f53330364-source.tar.gz --strip-components=1 -C build/tools/cudd/source
(
  cd build/tools/cudd/source
  ./configure --enable-static --disable-shared
  make -j4
)
cmake -S build/tools/opensta/source -B build/tools/opensta/build \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTS=OFF -DUSE_TCL_READLINE=OFF \
  -DCUDD_DIR="$PWD/build/tools/cudd/source" \
  -DTCL_LIBRARY=/usr/lib/x86_64-linux-gnu/libtcl8.6.so \
  -DTCL_HEADER=/usr/include/tcl8.6/tcl.h
cmake --build build/tools/opensta/build -j4
OPENSTA_TEST_BINARY="$PWD/build/tools/opensta/build/sta" \
  python3 -m unittest discover -s tests/tools -p 'test_*.py' -v
cp build/tools/opensta/build/sta tools/opensta/sta
```

The CUDD archive is generated from its pinned Git commit with `git archive`;
all source timestamps match that snapshot. This allows its shipped generated
Autotools files to build without requiring its historical Automake 1.14.
Both archives have checksums in `downloads/SHA256SUMS`.

After installing a rebuilt binary, refresh its recorded checksum:

```bash
python3 - <<'PY'
from pathlib import Path
import hashlib
import json
path = Path('config/tools/opensta-install.json')
manifest = json.loads(path.read_text())
manifest['binary_sha256'] = hashlib.sha256(Path(manifest['binary']).read_bytes()).hexdigest()
path.write_text(json.dumps(manifest, indent=2) + '\n')
PY
./bin/sta -version
```

The GNU C++ compiler used for this build supports `std::format`, so no fmt
download is needed. The standalone build disables upstream's optional GTest
targets and runs the tool activity integration tests explicitly.
