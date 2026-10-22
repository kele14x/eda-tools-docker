"""Verify packaged files against the repository's pinned installation manifests."""

import hashlib
import json
import struct
from pathlib import Path

from runtime_checksums import tree_sha256

ROOT = Path('/opt/eda')


def verify(relative_path, expected):
    path = ROOT / relative_path
    with path.open('rb') as handle:
        actual = hashlib.file_digest(handle, 'sha256').hexdigest()
    if actual != expected:
        raise SystemExit(f'SHA-256 mismatch: {path}: {actual} != {expected}')
    print(f'Verified {relative_path}')


for manifest_name, checks in (
    ('yosys', (('yosys/bin/yosys', 'binary_sha256'),
               ('yosys/bin/yosys-abc', 'abc_binary_sha256'))),
    ('openroad', (('openroad/bin/openroad', 'binary_sha256'),)),
    ('nangate45', (('nangate45/NangateOpenCellLibrary_typical.lib', 'sha256'),)),
):
    manifest = json.loads((ROOT / 'manifests' / f'{manifest_name}-install.json').read_text())
    for filename, key in checks:
        verify(filename, manifest[key])

manifest = json.loads((ROOT / 'manifests/openroad-install.json').read_text())
if tree_sha256(ROOT / 'openroad') != manifest['runtime_tree_sha256']:
    raise SystemExit('SHA-256 mismatch: OpenROAD runtime tree')
physical = json.loads((ROOT / 'manifests/nangate45-physical-install.json').read_text())
for filename, expected in physical['files'].items():
    verify('nangate45-physical/' + filename, expected)
verify('nangate45/NangateOpenCellLibrary_typical.lib', physical['liberty_sha256'])

for path in [*(ROOT / 'xls').iterdir(), *(ROOT / 'yosys/bin').iterdir(),
             ROOT / 'openroad/bin/openroad']:
    if not path.is_file():
        continue
    with path.open('rb') as handle:
        header = handle.read(20)
    if header[:4] == b'\x7fELF' and (
        header[4:6] != b'\x02\x01' or struct.unpack_from('<H', header, 18)[0] != 62
    ):
        raise SystemExit(f'Expected a little-endian x86-64 ELF executable: {path}')
