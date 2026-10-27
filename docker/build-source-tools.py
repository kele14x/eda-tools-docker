"""Build pinned tools inside the target-architecture Linux container."""

import json
import os
import platform
import subprocess
import sys
import tarfile
from pathlib import Path

from runtime_checksums import file_sha256, tree_sha256

ROOT = Path('/opt/eda')
MANIFESTS = ROOT / 'manifests'
SOURCE_CONFIG = json.loads((MANIFESTS / 'source-build.json').read_text())


def run(*args, cwd=None):
    subprocess.run(list(map(str, args)), cwd=cwd, check=True)


def manifest(tool):
    return json.loads((MANIFESTS / f'{tool}-install.json').read_text())


def download(url, destination, sha256):
    destination = Path(destination)
    if destination.is_file() and file_sha256(destination) == sha256:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    run('curl', '--fail', '--location', '--connect-timeout', '20',
        '--retry', '5', '--retry-all-errors', '--output', destination, url)
    actual = file_sha256(destination)
    if actual != sha256:
        raise SystemExit(f'Checksum mismatch for {url}: {actual} != {sha256}')


def checkout(tool, info, commit):
    source = Path('/src') / tool
    source.mkdir(parents=True, exist_ok=True)
    run('git', 'init', source)
    run('git', 'remote', 'add', 'origin', info['repository'], cwd=source)
    run('git', 'fetch', '--depth=1', 'origin', commit, cwd=source)
    run('git', 'checkout', '--detach', 'FETCH_HEAD', cwd=source)
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    if actual != commit:
        raise SystemExit(f'Unexpected {tool} source commit: {actual}')
    run('git', 'submodule', 'update', '--init', '--recursive', '--depth=1', cwd=source)
    return source


def record(tool, info, build):
    # Keep source identities, but never label native outputs with x86 checksums.
    for key in ('source_image', 'source_tag', 'archive', 'archive_prefix', 'archive_size_bytes',
                'download_url', 'sha256', 'binary_sha256', 'abc_binary_sha256',
                'runtime_tree_sha256', 'source_directory', 'build_directory'):
        info.pop(key, None)
    info.update(platform='linux-arm64' if platform.machine() == 'aarch64' else 'linux-x64',
                variant='source-build', installed_directory=str(ROOT / tool), build=build)
    if tool == 'xls':
        info['binary_sha256'] = {p.name: file_sha256(p) for p in (ROOT / tool).iterdir()
                                 if p.is_file() and os.access(p, os.X_OK)}
    else:
        binary = ROOT / tool / 'bin' / tool
        info.update(binary=str(binary), binary_sha256=file_sha256(binary))
    if tool == 'yosys':
        info['abc_binary_sha256'] = file_sha256(ROOT / 'yosys/bin/yosys-abc')
        info['runtime_data_directory'] = '/opt/eda/yosys/share/yosys'
        info['cmake_options'] = build['cmake_options']
    if tool == 'openroad':
        info['runtime_tree_sha256'] = tree_sha256(ROOT / tool)
    (MANIFESTS / f'{tool}-install.json').write_text(json.dumps(info, indent=2) + '\n')


def bazel(source, jobs, *args):
    # A persistent output root also preserves in-progress work after a failed RUN.
    run('bazelisk', '--batch', '--output_user_root=/home/builder/.cache/bazel',
        '--host_jvm_args=-Xmx2048m', 'build', '-c', 'opt', f'--jobs={jobs}',
        '--local_resources=memory=6144', '--local_resources=cpu=' + str(jobs),
        '--repository_cache=/home/builder/.cache/bazel-repository-cache',
        '--disk_cache=/home/builder/.cache/bazel-disk-cache', *args, cwd=source)


def main():
    tool = sys.argv[1]
    if tool == 'bootstrap':
        arch = sys.argv[2]
        version = SOURCE_CONFIG['bazelisk_version']
        destination = Path('/var/cache/eda') / f'bazelisk-{version}-linux-{arch}'
        download(f'https://github.com/bazelbuild/bazelisk/releases/download/v{version}/bazelisk-linux-{arch}',
                 destination, SOURCE_CONFIG['bazelisk_sha256'][arch])
        run('install', '-m', '755', destination, '/usr/local/bin/bazelisk')
        return
    if tool == 'libraries':
        physical = manifest('nangate45-physical')
        base = ('https://raw.githubusercontent.com/The-OpenROAD-Project/OpenROAD-flow-scripts/'
                + physical['commit'] + '/' + physical['source_path'])
        for filename, checksum in physical['files'].items():
            download(base + '/' + filename, ROOT / 'nangate45-physical' / filename, checksum)
        liberty = manifest('nangate45')
        download(base + '/lib/' + liberty['filename'], ROOT / 'nangate45' / liberty['filename'],
                 liberty['sha256'])
        return

    jobs = int(sys.argv[2])
    if jobs < 1:
        raise SystemExit('BUILD_JOBS must be positive')
    info = manifest(tool)
    if tool == 'openroad':
        source = checkout(tool, info, info['commit'])
        patch = Path('/opt/build/openroad-parser-eof.patch')
        run('git', 'apply', '--check', patch, cwd=source)
        run('git', 'apply', patch, cwd=source)
        info['patches'] = [{'file': patch.name, 'sha256': file_sha256(patch)}]
        run('git', 'tag', info['version'], cwd=source)
        sta_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                                            cwd=source / 'src/sta', text=True).strip()
        if sta_commit != info['embedded_opensta']['source_submodule_commit']:
            raise SystemExit(f'Unexpected embedded OpenSTA revision: {sta_commit}')
        gui = sys.argv[3] == 'ON'
        build_platform = 'gui' if gui else 'cli'
        options = ['--config=release', f'--//:platform={build_platform}']
        if gui:
            # Qt 6.9.1 omits getenv's header on ARM64, including in its build-time tools.
            qt_simd = r'external/qt-bazel.*/src/corelib/global/qsimd[.]cpp$@-include,stdlib.h'
            options += ['--per_file_copt=' + qt_simd, '--host_per_file_copt=' + qt_simd]
            if platform.machine() == 'aarch64':
                # Qt links Ubuntu 24.04's GUI libraries, not the toolchain's glibc 2.28 stubs.
                native_platform = '@llvm//platforms:linux_aarch64_gnu.2.39'
                options += ['--platforms=' + native_platform,
                            '--host_platform=' + native_platform,
                            '--extra_execution_platforms=' + native_platform]
        bazel(source, jobs, *options, '//:tarfile')
        install = ROOT / tool
        (install / 'bin').mkdir(parents=True)
        with tarfile.open(source / 'bazel-bin/packaging/openroad.tar') as archive:
            archive.extractall(install / 'bin', filter='data')
        run(install / 'bin/openroad', '-version')
        record(tool, info, {'system': 'bazel', 'configuration': 'release', 'gui': gui,
                            'jobs': jobs, 'bazel_options': options,
                            'bazel_version': (source / '.bazelversion').read_text().strip()})
    elif tool == 'yosys':
        archive_path = Path('/tmp/yosys.tar.gz')
        source_checksum = info['sha256']
        download(info['download_url'], archive_path, source_checksum)
        source = Path('/src/yosys')
        source.mkdir()
        with tarfile.open(archive_path) as archive:
            archive.extractall(source, filter='data')
        options = info['cmake_options'] | {'CMAKE_INSTALL_PREFIX': str(ROOT / tool)}
        flags = [f'-D{key}={"ON" if value is True else "OFF" if value is False else value}'
                 for key, value in options.items()]
        run('cmake', '-S', source, '-B', source / 'build', '-G', 'Ninja', *flags)
        run('cmake', '--build', source / 'build', '--parallel', jobs)
        run('cmake', '--install', source / 'build', '--strip')
        record(tool, info, {'system': 'cmake', 'jobs': jobs, 'source_archive_sha256': source_checksum,
                            'cmake_options': options})
    elif tool == 'xls':
        info['commit'] = SOURCE_CONFIG['xls_commit']
        source = checkout(tool, info, info['commit'])
        arch = 'arm64' if platform.machine() == 'aarch64' else 'x64'
        bazel(source, jobs, '//dist:xls_dist_tar', '--//dist:os=linux', f'--//dist:arch={arch}',
              '--//dist:version=' + info['release'])
        archive_path = source / 'bazel-bin/dist' / f'xls-{info["release"]}-linux-{arch}.tar.gz'
        run('tar', '-xzf', archive_path, '-C', ROOT)
        (ROOT / f'xls-{info["release"]}-linux-{arch}').rename(ROOT / tool)
        record(tool, info, {'system': 'bazel', 'configuration': 'opt', 'jobs': jobs,
                            'bazel_version': (source / '.bazelversion').read_text().strip()})
    else:
        raise SystemExit(f'Unknown tool: {tool}')


if __name__ == '__main__':
    main()
