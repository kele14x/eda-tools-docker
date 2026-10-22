"""Content checksums for a relocatable runtime, independent of timestamps/owners."""

import hashlib
import json
from pathlib import Path


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(root):
    root = Path(root).resolve()
    inventory = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            # A portable runtime must not depend on paths in the source image.
            path.resolve(strict=True).relative_to(root)
            inventory[relative] = {'symlink': str(path.readlink())}
        elif path.is_file():
            inventory[relative] = {'sha256': file_sha256(path)}
    encoded = json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(encoded).hexdigest()
