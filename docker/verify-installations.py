"""Verify packaged files against the repository's pinned installation manifests."""

import hashlib
import json
import platform
import struct
from pathlib import Path

from runtime_checksums import tree_sha256

ROOT = Path('/opt/eda')
# OpenROAD (and its embedded OpenSTA) is optional; see OPENROAD_ENABLED in Dockerfile.source.
# When it is omitted from the image, its binary and runtime tree are absent and its checks are skipped.
OPENROAD_ENABLED = (ROOT / 'openroad/bin/openroad').is_file()


def verify(relative_path, expected):
    path = ROOT / relative_path
    with path.open('rb') as handle:
        actual = hashlib.file_digest(handle, 'sha256').hexdigest()
    if actual != expected:
        raise SystemExit(f'SHA-256 mismatch: {path}: {actual} != {expected}')
    print(f'Verified {relative_path}')


checks = [('yosys', (('yosys/bin/yosys', 'binary_sha256'),
                     ('yosys/bin/yosys-abc', 'abc_binary_sha256'))),
          ('nangate45', (('nangate45/NangateOpenCellLibrary_typical.lib', 'sha256'),))]
if OPENROAD_ENABLED:
    checks.insert(1, ('openroad', (('openroad/bin/openroad', 'binary_sha256'),)))
for manifest_name, entries in checks:
    manifest = json.loads((ROOT / 'manifests' / f'{manifest_name}-install.json').read_text())
    for filename, key in entries:
        verify(filename, manifest[key])

if OPENROAD_ENABLED:
    manifest = json.loads((ROOT / 'manifests/openroad-install.json').read_text())
    if tree_sha256(ROOT / 'openroad') != manifest['runtime_tree_sha256']:
        raise SystemExit('SHA-256 mismatch: OpenROAD runtime tree')
physical = json.loads((ROOT / 'manifests/nangate45-physical-install.json').read_text())
for filename, expected in physical['files'].items():
    verify('nangate45-physical/' + filename, expected)
verify('nangate45/NangateOpenCellLibrary_typical.lib', physical['liberty_sha256'])

asap7 = json.loads((ROOT / 'manifests/asap7-install.json').read_text())
for filename, expected in asap7['files'].items():
    verify('asap7/' + filename, expected)
verify('asap7/' + asap7['license']['filename'], asap7['license']['sha256'])

machine = platform.machine()
expected_machine = {'x86_64': 62, 'aarch64': 183}[machine]
xls_manifest = json.loads((ROOT / 'manifests/xls-install.json').read_text())
for filename, expected in xls_manifest.get('binary_sha256', {}).items():
    verify('xls/' + filename, expected)

for path in [*(ROOT / 'xls').iterdir(), *(ROOT / 'yosys/bin').iterdir(),
             ROOT / 'openroad/bin/openroad']:
    if not path.is_file():
        continue
    with path.open('rb') as handle:
        header = handle.read(20)
    if header[:4] == b'\x7fELF' and (
        header[4:6] != b'\x02\x01' or struct.unpack_from('<H', header, 18)[0] != expected_machine
    ):
        raise SystemExit(f'Expected a little-endian {machine} ELF executable: {path}')
