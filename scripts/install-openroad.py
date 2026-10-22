#!/usr/bin/env python3
"""Install the pinned OpenROAD runtime and Nangate45 physical files locally."""

import argparse
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'docker'))
from runtime_checksums import file_sha256, tree_sha256


def load_manifest(name):
    return json.loads((ROOT / 'config/tools' / f'{name}-install.json').read_text())


def check(actual, expected, description):
    if actual != expected:
        raise RuntimeError(f'SHA-256 mismatch for {description}: {actual} != {expected}')


def activate(destination, stable):
    if stable.is_symlink():
        if stable.resolve() == destination.resolve():
            return
    elif stable.exists():
        raise RuntimeError(f'Refusing to replace a non-symlink: {stable}')
    temporary = stable.with_name(stable.name + '.tmp')
    temporary.symlink_to(destination.name)
    temporary.replace(stable)


def install_runtime(manifest):
    destination = ROOT / manifest['installed_directory']
    stable = ROOT / 'tools/openroad'
    if not destination.exists():
        archive = ROOT / 'downloads' / manifest['archive']
        archive.parent.mkdir(parents=True, exist_ok=True)
        if not archive.exists():
            # Anonymous Docker Hub token; no Docker daemon or account is needed.
            with urllib.request.urlopen(
                    'https://auth.docker.io/token?service=registry.docker.io'
                    '&scope=repository:openroad/ubuntu24.04:pull', timeout=60) as response:
                token = json.load(response)['token']
            request = urllib.request.Request(manifest['download_url'],
                                             headers={'Authorization': 'Bearer ' + token})
            temporary = archive.with_suffix(archive.suffix + '.tmp')
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open('wb') as output:
                shutil.copyfileobj(response, output)
            check(file_sha256(temporary), manifest['sha256'], archive.name)
            temporary.replace(archive)
        check(file_sha256(archive), manifest['sha256'], archive.name)
        with tempfile.TemporaryDirectory(prefix='openroad-install-', dir=ROOT / 'tools') as scratch:
            staging = Path(scratch) / 'runtime'
            staging.mkdir()
            prefix = PurePosixPath(manifest['archive_prefix'])
            with tarfile.open(archive, 'r:gz') as source:
                for member in source:
                    try:
                        relative = PurePosixPath(member.name).relative_to(prefix)
                    except ValueError:
                        continue
                    if '..' in relative.parts:
                        raise RuntimeError(f'Unsafe archive path: {member.name}')
                    target = staging.joinpath(*relative.parts)
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif member.isfile():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with source.extractfile(member) as data, target.open('xb') as output:
                            shutil.copyfileobj(data, output)
                        target.chmod(member.mode & 0o777)
                    else:
                        raise RuntimeError(f'Unsupported archive entry: {member.name}')
            check(file_sha256(staging / 'bin/openroad'), manifest['binary_sha256'], 'openroad')
            check(tree_sha256(staging), manifest['runtime_tree_sha256'], 'OpenROAD runtime')
            staging.rename(destination)
    check(tree_sha256(destination), manifest['runtime_tree_sha256'], 'OpenROAD runtime')
    activate(destination, stable)
    print(f'Installed and verified {stable}', flush=True)


def install_platform(manifest):
    destination = ROOT / manifest['installed_directory']
    cache = ROOT / manifest['download_directory']
    base = (f"https://raw.githubusercontent.com/The-OpenROAD-Project/OpenROAD-flow-scripts/"
            f"{manifest['commit']}/{manifest['source_path']}/")
    # Timing and physical cells must be from the same library.
    library = ROOT / load_manifest('nangate45')['library_path']
    check(file_sha256(library), manifest['liberty_sha256'], str(library))
    if not destination.exists():
        with tempfile.TemporaryDirectory(prefix='nangate45-install-', dir=ROOT / 'tools') as scratch:
            staging = Path(scratch) / 'platform'
            for name, expected in manifest['files'].items():
                cached = cache / name
                cached.parent.mkdir(parents=True, exist_ok=True)
                if not cached.exists():
                    with urllib.request.urlopen(base + name, timeout=60) as response:
                        data = response.read()
                    temporary = cached.with_suffix(cached.suffix + '.tmp')
                    temporary.write_bytes(data)
                    check(file_sha256(temporary), expected, name)
                    temporary.replace(cached)
                check(file_sha256(cached), expected, name)
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(cached, target)
            staging.rename(destination)
    for name, expected in manifest['files'].items():
        check(file_sha256(destination / name), expected, name)
    activate(destination, ROOT / manifest['stable_directory'])
    print(f'Installed and verified {destination}', flush=True)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    (ROOT / 'tools').mkdir(exist_ok=True)
    install_runtime(load_manifest('openroad'))
    install_platform(load_manifest('nangate45-physical'))


if __name__ == '__main__':
    main()
