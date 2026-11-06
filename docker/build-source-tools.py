"""Build pinned tools inside the target-architecture Linux container."""

import hashlib
import json
import os
import platform
import shlex
import signal
import subprocess
import sys
import tarfile
import time
import zlib
from pathlib import Path

from runtime_checksums import file_sha256, tree_sha256

ROOT = Path('/opt/eda')
MANIFESTS = ROOT / 'manifests'
# Abort a transfer that stays below 1 KiB/s for a minute so retries can restart it.
STALL_ARGS = ('--speed-limit', '1024', '--speed-time', '60')
DOWNLOAD_CACHE = Path('/var/cache/eda/downloads')
DISTDIR = Path('/home/builder/.cache/distdir')


def run(*args, cwd=None):
    subprocess.run(list(map(str, args)), cwd=cwd, check=True)


def fetch_with_progress(*args, cwd):
    limit = int(os.environ.get('BAZEL_FETCH_TIMEOUT', '1800'))
    if limit <= 0:
        raise SystemExit('BAZEL_FETCH_TIMEOUT must be positive (seconds)')
    command = list(map(str, args))
    print(f'FETCH COMMAND [{cwd.name}, limit {limit}s]: {shlex.join(command)}', flush=True)
    started = time.monotonic()
    with subprocess.Popen(command, cwd=cwd, start_new_session=True) as process:
        while True:
            remaining = limit - (time.monotonic() - started)
            try:
                code = process.wait(timeout=max(0, min(30, remaining)))
                break
            except subprocess.TimeoutExpired:
                elapsed = int(time.monotonic() - started)
                print(f'FETCH WAIT [{cwd.name}]: {elapsed}s elapsed, limit {limit}s; '
                      'process still running (not proof of download progress)', flush=True)
                if elapsed < limit:
                    continue
                print(f'FETCH TIMEOUT [{cwd.name}]: stopping dependency resolution', flush=True)
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                # Descendant repository commands can outlive the Bazel launcher.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                raise SystemExit(f'Bazel fetch exceeded {limit}s for {cwd.name}; '
                                 'this is a fetch timeout, not a compilation failure')
    if code:
        raise subprocess.CalledProcessError(code, command)
    print(f'FETCH COMPLETE [{cwd.name}]: {int(time.monotonic() - started)}s', flush=True)


def retry(*args, attempts=6, cwd=None):
    """Run a command, retrying transient network failures that abort the transfer."""
    for attempt in range(1, attempts + 1):
        try:
            run(*args, cwd=cwd)
            return
        except subprocess.CalledProcessError as exc:
            if attempt == attempts:
                raise exc


def manifest(tool):
    return json.loads((MANIFESTS / f'{tool}-install.json').read_text())


def download(url, destination, sha256):
    destination = Path(destination)
    if destination.is_file() and file_sha256(destination) == sha256:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    run('curl', '--fail', '--location', '--connect-timeout', '20',
        '--retry', '5', '--retry-all-errors', *STALL_ARGS, '--output', destination, url)
    actual = file_sha256(destination)
    if actual != sha256:
        raise SystemExit(f'Checksum mismatch for {url}: {actual} != {sha256}')


def is_complete_tarball(path):
    try:
        with tarfile.open(path, 'r:*') as archive:
            for _ in archive:
                pass
            # Read past the tar end marker to validate the compressed stream's trailer.
            while archive.fileobj.read(1024 * 1024):
                pass
        return True
    except (OSError, EOFError, tarfile.TarError, zlib.error):
        return False


def download_restarting(url, destination):
    """Download an archive without a pinned checksum, restarting across stalls and rebuilds.

    A stalled chunked response never terminates, so abort on low throughput and let the next
    attempt restart the transfer instead of hanging forever.

    Restarting rather than resuming because the only caller is GitHub's codeload archive endpoint,
    which ignores Range requests -- it answers 200 rather than 206 and sends no accept-ranges
    header -- so `curl -C -` against a cached partial fails outright with exit 56 ("server doesn't
    seem to support byte ranges"), exhausting every retry without transferring a byte.

    Each attempt writes to a sibling temporary file that is renamed onto destination only once
    it validates as a complete tarball. A half-finished transfer therefore can never be mistaken
    for a finished archive by the next build, which is what `curl --output destination` allowed.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if is_complete_tarball(destination):
        return
    partial = destination.with_name(destination.name + '.partial')
    for attempt in range(1, 11):
        try:
            run('curl', '--fail', '--location', '--connect-timeout', '20',
                '--retry', '5', '--retry-all-errors', *STALL_ARGS,
                '--output', partial, url)
        except subprocess.CalledProcessError:
            # A transfer aborted mid-body leaves the tail missing, but one that completed just
            # before curl gave up (e.g. a stall on the trailing read) is still usable.
            if not is_complete_tarball(partial):
                if attempt == 10:
                    raise
                continue
        else:
            if not is_complete_tarball(partial):
                continue
        partial.replace(destination)
        return
    raise SystemExit(f'Incomplete download for {url}')


def git_network_config():
    # GitHub transfers stall and drop on this link; fail fast so retries can resume.
    # Global (not local) so submodule clones, which are separate repositories, inherit it.
    for key, value in (('http.version', 'HTTP/1.1'), ('http.postBuffer', '524288000'),
                       ('http.lowSpeedLimit', '1024'), ('http.lowSpeedTime', '60')):
        run('git', 'config', '--global', key, value)


def checkout(tool, info, commit):
    source = Path('/src') / tool
    source.mkdir(parents=True, exist_ok=True)
    run('git', 'init', source)
    run('git', 'remote', 'add', 'origin', info['repository'], cwd=source)
    git_network_config()
    retry('git', 'fetch', '--depth=1', '--no-tags', 'origin', commit, cwd=source)
    run('git', 'checkout', '--detach', 'FETCH_HEAD', cwd=source)
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    if actual != commit:
        raise SystemExit(f'Unexpected {tool} source commit: {actual}')
    retry('git', 'submodule', 'update', '--init', '--recursive', '--depth=1', '--jobs=4',
          cwd=source)
    return source


def openroad_source(info, commit):
    """Bootstrap an OpenROAD source tree from GitHub's archive, then point HEAD at the pinned commit.

    A shallow clone of OpenROAD produces a ~1.4 GB pack in a single non-resumable transfer. The
    archive is roughly a quarter of that (~330 MB at this pin) and the blobless fetch below pulls
    only the commit and its trees -- a few MB, and enough for `submodule update` to read the
    gitlinks out of the index. The blobs are never fetched: the archive already supplied them.
    """
    source = Path('/src/openroad')
    archive = DOWNLOAD_CACHE / f'openroad-{commit}.tar.gz'
    download_restarting(f'{info["repository"]}/archive/{commit}.tar.gz', archive)
    source.mkdir(parents=True, exist_ok=True)
    run('tar', '-xzf', archive, '-C', source, '--strip-components=1',
        '--no-same-owner', '--no-same-permissions')
    run('git', 'init', source)
    run('git', 'remote', 'add', 'origin', info['repository'], cwd=source)
    git_network_config()
    # tree:0 would leave the trees on the promisor remote, and the reset below would then have to
    # re-fetch the whole tree graph in one non-resumable request that this link cannot sustain.
    retry('git', 'fetch', '--depth=1', '--no-tags', '--filter=blob:none', 'origin', commit,
          cwd=source)
    # Mixed (not soft) so the index records the commit tree, including the submodule gitlinks
    # that `submodule update` needs; the archive already populated the working tree.
    run('git', 'reset', '--mixed', 'FETCH_HEAD', cwd=source)
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    if actual != commit:
        raise SystemExit(f'Unexpected openroad source commit: {actual}')
    retry('git', 'submodule', 'update', '--init', '--recursive', '--depth=1', '--jobs=4',
          cwd=source)
    return source


def record(tool, info, build):
    # Source archive checksums describe inputs; hash installed outputs below.
    for key in ('archive', 'archive_size_bytes', 'download_url', 'sha256'):
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


def distdir_args(fetch):
    entries = json.loads((MANIFESTS / 'source-build.json').read_text()).get('bazel_distdir', [])
    for entry in entries:
        destination = DISTDIR / entry['filename']
        if fetch:
            download(entry['url'], destination, entry['sha256'])
        elif not destination.is_file() or file_sha256(destination) != entry['sha256']:
            raise SystemExit(f'Missing or corrupt prefetched dependency: {destination}; rerun fetch')
    return [f'--distdir={DISTDIR}'] if entries else []


def bazel(source, jobs, *args, fetch=False):
    registries = os.environ.get('BAZEL_REGISTRY', '').replace(',', ' ').split()
    registry_args = [f'--registry={registry}' for registry in registries]
    command = ['fetch', '--nobuild_tests_only', '--announce_rc', '--verbose_failures',
               '--show_progress_rate_limit=10', '--color=no', '--curses=no'] if fetch else [
        'build', '--nofetch', f'--jobs={jobs}',
        f'--local_resources=memory={1536 * jobs}', f'--local_resources=cpu={jobs}',
        '--disk_cache=/home/builder/.cache/bazel-disk-cache',
    ]
    repository_cache = Path('/home/builder/.cache/bazel-repository-cache')
    persistent_cache = Path('/var/cache/eda/bazel-repository-cache')
    if fetch and persistent_cache.is_dir():
        print(f'FETCH CACHE [{source.name}]: restoring downloaded repositories', flush=True)
        run('mkdir', '-p', repository_cache)
        run('cp', '-a', '--update=none', str(persistent_cache) + '/.', repository_cache)
    try:
        runner = fetch_with_progress if fetch else run
        runner('bazelisk', '--batch', '--output_user_root=/home/builder/.cache/bazel',
               '--host_jvm_args=-Xmx2048m', *command, '-c', 'opt', '--remote_cache=',
               f'--repository_cache={repository_cache}',
               *registry_args, *distdir_args(fetch), *args, cwd=source)
    except (SystemExit, subprocess.CalledProcessError):
        if fetch:
            workspace_hash = hashlib.md5(str(source).encode(), usedforsecurity=False).hexdigest()
            java_log = Path('/home/builder/.cache/bazel') / workspace_hash / 'java.log'
            if java_log.is_file():
                print(f'FETCH DIAGNOSTICS [{source.name}]: last 16 KiB of {java_log}', flush=True)
                with java_log.open('rb') as log:
                    log.seek(max(0, java_log.stat().st_size - 16384))
                    print(log.read().decode(errors='replace'), flush=True)
        raise
    finally:
        # Retain partial downloads for retries without making the image depend on the cache mount.
        if fetch and repository_cache.is_dir():
            print(f'FETCH CACHE [{source.name}]: saving downloads for retry', flush=True)
            run('mkdir', '-p', persistent_cache)
            run('cp', '-a', '--update=none', str(repository_cache) + '/.', persistent_cache)
            print(f'FETCH CACHE [{source.name}]: saved', flush=True)


def main():
    tool = sys.argv[1]
    source_config = json.loads((MANIFESTS / 'source-build.json').read_text())
    if tool == 'bootstrap':
        arch = sys.argv[2]
        version = source_config['bazelisk_version']
        destination = Path('/var/cache/eda') / f'bazelisk-{version}-linux-{arch}'
        download(f'https://github.com/bazelbuild/bazelisk/releases/download/v{version}/bazelisk-linux-{arch}',
                 destination, source_config['bazelisk_sha256'][arch])
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

        asap7 = manifest('asap7')
        asap7_base = ('https://raw.githubusercontent.com/The-OpenROAD-Project/OpenROAD-flow-scripts/'
                      + asap7['commit'] + '/' + asap7['source_path'])
        for filename, checksum in asap7['files'].items():
            download(asap7_base + '/' + filename, ROOT / 'asap7' / filename, checksum)
        asap7_license = asap7['license']
        download(asap7_license['url'], ROOT / 'asap7' / asap7_license['filename'],
                 asap7_license['sha256'])
        return

    if tool not in ('fetch', 'build'):
        raise SystemExit('Expected bootstrap, libraries, fetch TOOL, or build TOOL')
    fetch = tool == 'fetch'
    tool = sys.argv[2]
    jobs = int(sys.argv[3])
    if jobs < 1:
        raise SystemExit('BUILD_JOBS must be positive')
    info = manifest(tool)
    source = Path('/src') / tool
    if not fetch and not (source / '.git').is_dir():
        raise SystemExit(f'Missing prefetched source: {source}; rerun fetch')
    print(f'{"FETCH" if fetch else "OFFLINE BUILD"}: {tool}', flush=True)
    if tool == 'openroad':
        patch = Path('/opt/build/openroad-parser-eof.patch')
        if fetch:
            source = openroad_source(info, info['commit'])
            run('git', 'apply', '--check', patch, cwd=source)
            run('git', 'apply', patch, cwd=source)
            run('git', 'tag', info['version'], cwd=source)
        info['patches'] = [{'file': patch.name, 'sha256': file_sha256(patch)}]
        sta_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                                            cwd=source / 'src/sta', text=True).strip()
        if sta_commit != info['embedded_opensta']['source_submodule_commit']:
            raise SystemExit(f'Unexpected embedded OpenSTA revision: {sta_commit}')
        gui = sys.argv[4] == 'ON'
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
        bazel(source, jobs, *options, '//:tarfile', fetch=fetch)
        if fetch:
            return
        install = ROOT / tool
        (install / 'bin').mkdir(parents=True)
        with tarfile.open(source / 'bazel-bin/packaging/openroad.tar') as archive:
            archive.extractall(install / 'bin', filter='data')
        run(install / 'bin/openroad', '-version')
        record(tool, info, {'system': 'bazel', 'configuration': 'release', 'gui': gui,
                            'jobs': jobs, 'bazel_options': options,
                            'bazel_version': (source / '.bazelversion').read_text().strip()})
    elif tool == 'yosys':
        if fetch:
            source = checkout(tool, info, info['commit'])
        pinned = info['build_dependencies']['submodules']
        for path, expected in pinned.items():
            actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                                             cwd=source / path, text=True).strip()
            if actual != expected:
                raise SystemExit(f'Unexpected yosys submodule revision for {path}: {actual}')
        if fetch:
            run('git', 'tag', 'v' + info['version'], cwd=source)
            return
        options = info['cmake_options'] | {'CMAKE_INSTALL_PREFIX': str(ROOT / tool)}
        flags = [f'-D{key}={"ON" if value is True else "OFF" if value is False else value}'
                 for key, value in options.items()]
        run('cmake', '-S', source, '-B', source / 'build', '-G', 'Ninja', *flags)
        run('cmake', '--build', source / 'build', '--parallel', jobs)
        run('cmake', '--install', source / 'build', '--strip')
        record(tool, info, {'system': 'cmake', 'jobs': jobs,
                            'source_submodule_commits': pinned, 'cmake_options': options})
    elif tool == 'xls':
        info['commit'] = source_config['xls_commit']
        if fetch:
            source = checkout(tool, info, info['commit'])
        arch = 'arm64' if platform.machine() == 'aarch64' else 'x64'
        bazel(source, jobs, '//dist:xls_dist_tar', '--//dist:os=linux', f'--//dist:arch={arch}',
              '--//dist:version=' + info['release'], fetch=fetch)
        if fetch:
            return
        archive_path = source / 'bazel-bin/dist' / f'xls-{info["release"]}-linux-{arch}.tar.gz'
        run('tar', '-xzf', archive_path, '-C', ROOT)
        (ROOT / f'xls-{info["release"]}-linux-{arch}').rename(ROOT / tool)
        record(tool, info, {'system': 'bazel', 'configuration': 'opt', 'jobs': jobs,
                            'bazel_version': (source / '.bazelversion').read_text().strip()})
    else:
        raise SystemExit(f'Unknown tool: {tool}')


if __name__ == '__main__':
    main()
