"""Install the checksum-pinned Linux x64 XLS release inside the image build."""

import json
import os
import platform
import subprocess
import sys
import tarfile
from pathlib import Path

from runtime_checksums import file_sha256


ROOT = Path('/opt/eda')
MANIFEST = ROOT / 'manifests/xls-install.json'
DOWNLOADS = Path('/downloads/platform/xls-release')


def main(action='install'):
    if action not in ('fetch', 'check', 'install'):
        raise SystemExit('Expected fetch, check or install')
    if platform.machine() != 'x86_64':
        raise SystemExit('XLS_INSTALL_MODE=release requires Linux AMD64; use source on ARM64')
    info = json.loads(MANIFEST.read_text())
    if info['platform'] != 'linux-x64':
        raise SystemExit('Expected a pinned Linux x64 XLS release manifest')
    archive_path = DOWNLOADS / info['archive']
    if not archive_path.is_file() or file_sha256(archive_path) != info['sha256']:
        if action != 'fetch':
            raise SystemExit(f'Missing or corrupt prefetched XLS release: {archive_path}; rerun fetch')
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            'curl', '--fail', '--location', '--connect-timeout', '20',
            '--retry', '5', '--retry-all-errors', '--speed-limit', '1024', '--speed-time', '60',
            '--output', str(archive_path), info['download_url'],
        ], check=True)
    actual = file_sha256(archive_path)
    if actual != info['sha256']:
        raise SystemExit(f'XLS release checksum mismatch: {actual} != {info["sha256"]}')
    print(f'Verified XLS release archive SHA-256: {actual}', flush=True)
    if action != 'install':
        return
    with tarfile.open(archive_path) as archive:
        archive.extractall(ROOT, filter='data')
    installation = ROOT / 'xls'
    (ROOT / archive_path.name.removesuffix('.tar.gz')).rename(installation)
    info.update(
        variant='binary-release', installed_directory=str(installation),
        binary_sha256={path.name: file_sha256(path) for path in installation.iterdir()
                       if path.is_file() and os.access(path, os.X_OK)},
    )
    MANIFEST.write_text(json.dumps(info, indent=2) + '\n')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'install')
