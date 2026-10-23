# Yosys toolchain

The project uses an unmodified Yosys 0.69 source release built in the
`ubuntu24` OrbStack VM, with the bundled sv-elab/slang frontend and ABC
enabled. `config/tools/yosys-install.json` pins the source archive checksum,
source and dependency revisions, build options, and installed binary checksums.

`bin/yosys` runs `tools/yosys/bin/yosys` through the VM on macOS and directly
on Linux. `tools/yosys` points to `yosys-0.69-linux-x64`. The installation
includes ABC and Yosys's runtime data files; it does not depend on the
disposable source/build directories. Set `YOSYS_VM` to select another VM.

```bash
./bin/yosys -V
./bin/yosys -p 'help read_slang'
```

## SystemVerilog input

Use `read_slang` explicitly for SystemVerilog features such as unpacked
array ports. `read_verilog -sv` selects Yosys's older parser.

```bash
./bin/yosys -p 'read_slang --top my_top design.sv; synth -top my_top; check -assert'
```

## Validation

From this project on macOS, build the image and run the standalone tool tests:

```bash
./scripts/build-docker-tools.sh
python3 scripts/test-docker-tools.py
```

The integration flow checks slang array ports with a SAT proof, bundled
runtime data, ABC technology mapping with Nangate45, and OpenSTA timing/power.

## Rebuild in Ubuntu 24.04

Run from the project root inside `ubuntu24`. The official release archive
contains Yosys, ABC, sv-elab, slang, and the other bundled libraries.

```bash
sudo apt-get install cmake g++ make ninja-build git python3 bison flex gawk \
  libffi-dev libreadline-dev tcl-dev zlib1g-dev libboost-dev libfmt-dev \
  libtommath-dev libtomlplusplus-dev
mkdir -p downloads build/tools/yosys/source
curl --fail --location --retry 3 \
  -o downloads/yosys-0.69-source.tar.gz \
  https://github.com/YosysHQ/yosys/releases/download/v0.69/yosys.tar.gz
python3 - <<'PY'
import hashlib
import json
import tarfile
from pathlib import Path
manifest = json.loads(Path('config/tools/yosys-install.json').read_text())
archive = Path('downloads') / manifest['archive']
assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest['sha256']
with tarfile.open(archive) as source:
    source.extractall(manifest['source_directory'], filter='data')
PY
cmake -S build/tools/yosys/source -B build/tools/yosys/build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$PWD/tools/yosys-0.69-linux-x64" \
  -DYOSYS_WITHOUT_SLANG=OFF -DYOSYS_WITHOUT_EDITLINE=ON \
  -DCMAKE_DISABLE_FIND_PACKAGE_Git=ON \
  -DYOSYS_CHECKOUT_INFO=143eb14f9cc55d6f8927e68523b0c9d2166ed02c \
  -DYOSYS_SLANG_REVISION=b4fd362f4c53dc8ffab6946b9f866a6ea89e366d \
  -DSLANG_REVISION=e222e7dc0250231312f14c37d47404a49df00fe2
cmake --build build/tools/yosys/build --parallel 4
cmake --install build/tools/yosys/build --strip
ln -sfn yosys-0.69-linux-x64 tools/yosys
./bin/yosys -V
./bin/yosys -p 'help read_slang'
```

The revision options supply accurate source and dependency metadata when building
from the release archive without Git submodule checkouts. They prevent CMake from
mistaking this workspace's Git revision for the bundled libraries' revisions.

Refresh the recorded executable checksums after rebuilding:

```bash
python3 - <<'PY'
import hashlib
import json
from pathlib import Path
path = Path('config/tools/yosys-install.json')
manifest = json.loads(path.read_text())
manifest['binary_sha256'] = hashlib.sha256(Path(manifest['binary']).read_bytes()).hexdigest()
manifest['abc_binary_sha256'] = hashlib.sha256(Path('tools/yosys/bin/yosys-abc').read_bytes()).hexdigest()
path.write_text(json.dumps(manifest, indent=2) + '\n')
PY
```
