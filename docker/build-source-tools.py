"""Fetch (online) or build (offline) pinned tools inside the target-architecture Linux container.

fetch stores everything a tool build needs under /downloads (the project's downloads/ folder);
build reads only those files and never touches the network.
"""

import hashlib
import json
import os
import platform
import shlex
import shutil
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
PLATFORM_DOWNLOADS = Path('/downloads/platform')
COMMON_DOWNLOADS = Path('/downloads/common')
# Partial transfers kept only so a failed fetch can resume; the offline build never reads them.
FETCH_CACHE = Path('/downloads/fetch-cache')
# Bazel records absolute paths, so fetch and build must use the same locations.
CACHE = Path('/home/builder/.cache')
PATCH = Path('/opt/build/openroad-parser-eof.patch')
XLS_RELEASE = Path('/opt/build/install-xls-release.py')
ORFS = 'https://raw.githubusercontent.com/The-OpenROAD-Project/OpenROAD-flow-scripts/'
# Abort a transfer that stays below 1 KiB/s for a minute so retries can restart it.
STALL_ARGS = ('--speed-limit', '1024', '--speed-time', '60')
TOOLS = ('libraries', 'yosys', 'openroad', 'xls')


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
    partial = destination.with_name(destination.name + '.partial')
    run('curl', '--fail', '--location', '--connect-timeout', '20',
        '--retry', '5', '--retry-all-errors', *STALL_ARGS, '--output', partial, url)
    actual = file_sha256(partial)
    if actual != sha256:
        partial.unlink()
        raise SystemExit(f'Checksum mismatch for {url}: {actual} != {sha256}')
    partial.replace(destination)


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
    archive = FETCH_CACHE / f'openroad-{commit}.tar.gz'
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


def distdir_args(cache, fetch):
    entries = json.loads((MANIFESTS / 'source-build.json').read_text()).get('bazel_distdir', [])
    distdir = cache / 'distdir'
    for entry in entries:
        destination = distdir / entry['filename']
        if fetch:
            download(entry['url'], destination, entry['sha256'])
        elif not destination.is_file() or file_sha256(destination) != entry['sha256']:
            raise SystemExit(f'Missing or corrupt prefetched dependency: {destination}; rerun fetch')
    return [f'--distdir={distdir}'] if entries else []


def retry_store(source):
    return FETCH_CACHE / 'bazel' / f'{source.name}-{platform.machine()}'


def bazel(source, jobs, *args, fetch=False):
    cache = CACHE / source.name
    os.environ['BAZELISK_HOME'] = str(cache / 'bazelisk')
    output_root = cache / 'bazel'
    repository_cache = cache / 'repository-cache'
    downloaded = repository_cache / 'content_addressable'
    store = retry_store(source)
    registries = os.environ.get('BAZEL_REGISTRY', '').replace(',', ' ').split()
    registry_args = [f'--registry={registry}' for registry in registries]
    command = ['fetch', '--nobuild_tests_only', '--announce_rc', '--verbose_failures',
               '--show_progress_rate_limit=10', '--color=no', '--curses=no'] if fetch else [
        'build', '--nofetch', f'--jobs={jobs}',
        f'--local_resources=memory={1536 * jobs}', f'--local_resources=cpu={jobs}',
    ]
    if fetch and store.is_dir():
        print(f'FETCH RESUME [{source.name}]: reusing downloads from an earlier attempt', flush=True)
        run('mkdir', '-p', downloaded)
        run('cp', '-a', '--update=none', str(store) + '/.', downloaded)
    try:
        runner = fetch_with_progress if fetch else run
        runner('bazelisk', '--batch', f'--output_user_root={output_root}',
               '--host_jvm_args=-Xmx2048m', *command, '-c', 'opt', '--remote_cache=',
               f'--repository_cache={repository_cache}',
               *registry_args, *distdir_args(cache, fetch), *args, cwd=source)
    except (SystemExit, subprocess.CalledProcessError):
        if fetch:
            workspace_hash = hashlib.md5(str(source).encode(), usedforsecurity=False).hexdigest()
            java_log = output_root / workspace_hash / 'java.log'
            if java_log.is_file():
                print(f'FETCH DIAGNOSTICS [{source.name}]: last 16 KiB of {java_log}', flush=True)
                with java_log.open('rb') as log:
                    log.seek(max(0, java_log.stat().st_size - 16384))
                    print(log.read().decode(errors='replace'), flush=True)
        raise
    finally:
        if fetch and downloaded.is_dir():
            print(f'FETCH RESUME [{source.name}]: saving downloads for a retry', flush=True)
            run('mkdir', '-p', store)
            run('cp', '-a', '--update=none', str(downloaded) + '/.', store)


def openroad_gui():
    value = os.environ.get('OPENROAD_GUI', 'OFF')
    if value not in ('ON', 'OFF'):
        raise SystemExit(f'Unsupported OPENROAD_GUI: {value} (use ON or OFF)')
    return value == 'ON'


def xls_mode():
    mode = os.environ.get('XLS_INSTALL_MODE', 'auto')
    if mode not in ('auto', 'source', 'release'):
        raise SystemExit(f'Unsupported XLS_INSTALL_MODE: {mode} (use auto, source or release)')
    if mode == 'auto':
        return 'release' if platform.machine() == 'x86_64' else 'source'
    if mode == 'release' and platform.machine() != 'x86_64':
        raise SystemExit('XLS_INSTALL_MODE=release requires AMD64; use source on ARM64')
    return mode


def library_files():
    physical = manifest('nangate45-physical')
    base = ORFS + physical['commit'] + '/' + physical['source_path']
    for filename, checksum in physical['files'].items():
        yield base + '/' + filename, Path('nangate45-physical') / filename, checksum
    liberty = manifest('nangate45')
    yield (base + '/lib/' + liberty['filename'], Path('nangate45') / liberty['filename'],
           liberty['sha256'])
    asap7 = manifest('asap7')
    asap7_base = ORFS + asap7['commit'] + '/' + asap7['source_path']
    for filename, checksum in asap7['files'].items():
        yield asap7_base + '/' + filename, Path('asap7') / filename, checksum
    license_file = asap7['license']
    yield license_file['url'], Path('asap7') / license_file['filename'], license_file['sha256']


def libraries(action):
    count = 0
    for url, relative, checksum in library_files():
        stored = COMMON_DOWNLOADS / relative
        if action == 'fetch':
            download(url, stored, checksum)
        elif not stored.is_file() or file_sha256(stored) != checksum:
            raise SystemExit(f'Missing or corrupt prefetched library file: {stored}; rerun fetch')
        if action == 'build':
            target = ROOT / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(stored, target)
        count += 1
    if action == 'fetch':
        print(f'FETCH COMPLETE [libraries]: {count} files in {COMMON_DOWNLOADS}', flush=True)


def prepared_files(tool):
    return PLATFORM_DOWNLOADS / f'{tool}.tar', PLATFORM_DOWNLOADS / f'{tool}.json'


def inputs(tool):
    """Everything that decides the contents of a prepared tool archive."""
    files = [MANIFESTS / f'{tool}-install.json']
    result = {'machine': platform.machine()}
    if tool in ('openroad', 'xls'):
        files.append(MANIFESTS / 'source-build.json')
        result['bazel_registry'] = os.environ.get('BAZEL_REGISTRY', '')
        result['bazelisk_format_url'] = os.environ.get('BAZELISK_FORMAT_URL', '')
    if tool == 'openroad':
        files.append(PATCH)
        result['gui'] = openroad_gui()
    result['files'] = {path.name: file_sha256(path) for path in files}
    return result


def prepared(tool):
    archive, metadata = prepared_files(tool)
    try:
        recorded = json.loads(metadata.read_text())
    except (OSError, ValueError):
        return None
    if recorded.get('inputs') != inputs(tool):
        return None
    if not archive.is_file() or archive.stat().st_size != recorded.get('size'):
        return None
    return recorded


def pack(tool, source):
    archive, metadata = prepared_files(tool)
    partial = archive.with_name(archive.name + '.partial')
    members = [source.relative_to('/')]
    if (CACHE / tool).is_dir():
        members.append((CACHE / tool).relative_to('/'))
    print(f'FETCH PACK [{tool}]: writing {archive}', flush=True)
    run('tar', '-cf', partial, '-C', '/', *members)
    partial.replace(archive)
    size = archive.stat().st_size
    metadata.write_text(json.dumps({'inputs': inputs(tool), 'size': size,
                                    'sha256': file_sha256(archive)}, indent=2) + '\n')
    print(f'FETCH READY [{tool}]: {archive} ({size} bytes)', flush=True)


def unpack(tool):
    archive, _ = prepared_files(tool)
    recorded = prepared(tool)
    if recorded is None:
        raise SystemExit(f'Missing or outdated prefetched {tool}: {archive}; rerun fetch')
    if file_sha256(archive) != recorded['sha256']:
        raise SystemExit(f'Corrupt prefetched {tool}: {archive}; rerun fetch')
    run('tar', '-xpf', archive, '-C', '/')


def remove_tree(path):
    if path.exists() or path.is_symlink():
        # Bazel leaves read-only directories behind.
        run('chmod', '-R', 'u+w', path)
        run('rm', '-rf', path)


def check():
    """Fail in seconds, before any compilation, if an offline input is missing."""
    mode = xls_mode()
    enabled = os.environ.get('OPENROAD_ENABLED', 'OFF')
    if enabled not in ('ON', 'OFF'):
        raise SystemExit(f'Unsupported OPENROAD_ENABLED: {enabled} (use ON or OFF)')
    libraries('check')
    tools = ['yosys']
    if enabled == 'ON':
        tools.append('openroad')
    if mode == 'source':
        tools.append('xls')
    else:
        run(sys.executable, XLS_RELEASE, 'check')
    for tool in tools:
        if prepared(tool) is None:
            archive, _ = prepared_files(tool)
            raise SystemExit(f'Missing or outdated prefetched {tool}: {archive}; rerun fetch')
    print(f'OFFLINE INPUTS: verified libraries and {", ".join(tools)}'
          + ('' if mode == 'source' else ', xls release'), flush=True)


def main():
    arguments = sys.argv[1:]
    if arguments == ['check']:
        check()
        return
    if (len(arguments) < 2 or arguments[0] not in ('fetch', 'build')
            or len(arguments) != (3 if arguments[0] == 'build' and arguments[1] != 'libraries'
                                  else 2)):
        raise SystemExit('Expected check, fetch TOOL, build libraries, or build TOOL JOBS')
    action, tool = arguments[:2]
    if tool not in TOOLS:
        raise SystemExit(f'Unknown tool: {tool}')
    if tool == 'libraries':
        libraries(action)
        return
    fetch = action == 'fetch'
    jobs = 1 if fetch else int(arguments[2])
    if jobs < 1:
        raise SystemExit('BUILD_JOBS must be positive')
    if tool == 'xls' and xls_mode() == 'release':
        run(sys.executable, XLS_RELEASE, 'fetch' if fetch else 'install')
        return
    archive, metadata = prepared_files(tool)
    if fetch and prepared(tool) is not None:
        print(f'FETCH SKIP [{tool}]: {archive} already matches the pinned inputs', flush=True)
        return
    if fetch:
        metadata.unlink(missing_ok=True)
    else:
        os.environ['GIT_NO_LAZY_FETCH'] = '1'
        unpack(tool)
    print(f'{"FETCH" if fetch else "OFFLINE BUILD"}: {tool}', flush=True)
    info = manifest(tool)
    source = Path('/src') / tool
    if tool == 'openroad':
        if fetch:
            source = openroad_source(info, info['commit'])
            run('git', 'apply', '--check', PATCH, cwd=source)
            run('git', 'apply', PATCH, cwd=source)
            run('git', 'tag', info['version'], cwd=source)
        info['patches'] = [{'file': PATCH.name, 'sha256': file_sha256(PATCH)}]
        sta_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                                             cwd=source / 'src/sta', text=True).strip()
        if sta_commit != info['embedded_opensta']['source_submodule_commit']:
            raise SystemExit(f'Unexpected embedded OpenSTA revision: {sta_commit}')
        gui = openroad_gui()
        options = ['--config=release', f'--//:platform={"gui" if gui else "cli"}']
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
        if not fetch:
            install = ROOT / tool
            (install / 'bin').mkdir(parents=True)
            with tarfile.open(source / 'bazel-bin/packaging/openroad.tar') as archive_file:
                archive_file.extractall(install / 'bin', filter='data')
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
        else:
            options = info['cmake_options'] | {'CMAKE_INSTALL_PREFIX': str(ROOT / tool)}
            flags = [f'-D{key}={"ON" if value is True else "OFF" if value is False else value}'
                     for key, value in options.items()]
            run('cmake', '-S', source, '-B', source / 'build', '-G', 'Ninja', *flags)
            run('cmake', '--build', source / 'build', '--parallel', jobs)
            run('cmake', '--install', source / 'build', '--strip')
            record(tool, info, {'system': 'cmake', 'jobs': jobs,
                                'source_submodule_commits': pinned, 'cmake_options': options})
    else:
        info['commit'] = json.loads((MANIFESTS / 'source-build.json').read_text())['xls_commit']
        if fetch:
            source = checkout(tool, info, info['commit'])
        arch = 'arm64' if platform.machine() == 'aarch64' else 'x64'
        bazel(source, jobs, '//dist:xls_dist_tar', '--//dist:os=linux', f'--//dist:arch={arch}',
              '--//dist:version=' + info['release'], fetch=fetch)
        if not fetch:
            archive_path = source / 'bazel-bin/dist' / f'xls-{info["release"]}-linux-{arch}.tar.gz'
            run('tar', '-xzf', archive_path, '-C', ROOT)
            (ROOT / f'xls-{info["release"]}-linux-{arch}').rename(ROOT / tool)
            record(tool, info, {'system': 'bazel', 'configuration': 'opt', 'jobs': jobs,
                                'bazel_version': (source / '.bazelversion').read_text().strip()})
    if fetch:
        pack(tool, source)
        remove_tree(retry_store(source))
    else:
        # Keep sources and Bazel's output base out of the image layer.
        remove_tree(source)
        remove_tree(CACHE / tool)


if __name__ == '__main__':
    main()
