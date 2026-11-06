"""Check launcher installation and architecture selection without a Docker engine."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ('eda', 'xls', 'yosys', 'sta', 'openroad', 'iverilog', 'vvp', 'verilator')


class LauncherInstallTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='eda launcher install ')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()

    def install(self):
        return subprocess.run([str(ROOT / 'scripts/install-docker-tools.sh'), str(self.folder)],
                              capture_output=True, text=True)

    def check_installed(self):
        for tool in TOOLS:
            with self.subTest(tool=tool):
                self.assertEqual((self.folder / tool).read_bytes(),
                                 (ROOT / 'docker/bin' / tool).read_bytes())
                self.assertTrue(os.access(self.folder / tool, os.X_OK))

    def test_installs_and_reinstalls_launchers(self):
        for _ in range(2):
            result = self.install()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.check_installed()

    def test_refuses_unrelated_command_before_changing_any_launchers(self):
        managed = ('#!/usr/bin/env bash\n'
                   '# Managed by eda-tools-docker; installed by scripts/install-docker-tools.sh.\n'
                   'exit 0\n')
        (self.folder / 'eda').write_text(managed)
        (self.folder / 'yosys').write_text('unrelated command\n')
        result = self.install()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('Refusing to overwrite an unrelated command', result.stderr)
        self.assertEqual((self.folder / 'eda').read_text(), managed)
        self.assertEqual((self.folder / 'yosys').read_text(), 'unrelated command\n')
        self.assertEqual({path.name for path in self.folder.iterdir()}, {'eda', 'yosys'})


class DockerPlatformTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='eda platform ')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name).resolve()
        docker = self.folder / 'docker'
        docker.write_text('''#!/usr/bin/env python3
import json, os, sys
arguments = sys.argv[1:]
with open(os.environ['FAKE_DOCKER_LOG'], 'a') as log:
    print(json.dumps(arguments), file=log)
if arguments[2:4] == ['image', 'inspect']:
    if os.environ.get('FAKE_IMAGE_MISSING') == '1':
        sys.exit(1)
    print(os.environ.get('FAKE_IMAGE_PLATFORM', 'linux/arm64'))
else:
    print(json.dumps(arguments))
''')
        docker.chmod(0o755)
        self.log = self.folder / 'docker-calls.jsonl'
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(('EDA_', 'FAKE_'))}
        self.env['PATH'] = str(self.folder) + os.pathsep + self.env['PATH']
        self.env['FAKE_DOCKER_LOG'] = str(self.log)

    def invoke(self, script, *arguments, **environment):
        self.log.write_text('')
        return subprocess.run([str(ROOT / script), *arguments], env=self.env | environment,
                              cwd=self.folder, text=True, capture_output=True)

    def docker_calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_selected_native_image_does_not_force_amd64(self):
        result = self.invoke('docker/bin/eda', 'openroad', '-version',
                             EDA_IMAGE='local/eda-tools:arm64')
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = json.loads(result.stdout)
        self.assertEqual(arguments[arguments.index('--platform') + 1], 'linux/arm64')
        self.assertEqual(arguments[-3:], ['local/eda-tools:arm64', 'openroad', '-version'])
        self.assertIn(f'type=bind,source={self.folder},target={self.folder}', arguments)

    def test_explicit_platform_and_image(self):
        result = self.invoke('docker/bin/eda', 'true', EDA_PLATFORM='linux/amd64',
                             EDA_IMAGE='local/eda-tools:amd64')
        arguments = json.loads(result.stdout)
        self.assertEqual(arguments[arguments.index('--platform') + 1], 'linux/amd64')
        self.assertEqual(arguments[-2:], ['local/eda-tools:amd64', 'true'])

    def test_default_launcher_selects_native_image(self):
        result = self.invoke('docker/bin/eda', 'true')
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = json.loads(result.stdout)
        self.assertEqual(arguments[-2:], ['local/eda-tools:arm64', 'true'])
        self.assertEqual(arguments[arguments.index('--platform') + 1], 'linux/arm64')

    def test_amd64_image_uses_its_own_architecture(self):
        result = self.invoke('docker/bin/eda', 'true', FAKE_IMAGE_PLATFORM='linux/amd64')
        arguments = json.loads(result.stdout)
        self.assertEqual(arguments[arguments.index('--platform') + 1], 'linux/amd64')

    def test_image_not_cached_leaves_platform_selection_to_docker(self):
        result = self.invoke('docker/bin/eda', 'true', FAKE_IMAGE_PLATFORM='')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('--platform', json.loads(result.stdout))


FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
arguments = sys.argv[1:]
with open(os.environ['FAKE_DOCKER_LOG'], 'a') as log:
    print(json.dumps(arguments), file=log)
if arguments[2] == 'run':
    command = ' '.join(arguments)
    if 'fetch-apt-packages.sh' in command:
        platform = next(a.split(',')[1].split('=', 1)[1] for a in arguments
                        if a.endswith('target=/downloads/platform'))
        request = next(a.split('=', 1)[1] for a in arguments if a.startswith('APT_REQUEST='))
        (Path(platform) / 'apt').mkdir(parents=True, exist_ok=True)
        (Path(platform) / 'apt/complete').write_text(request + '\n')
    if arguments[-1] == os.environ.get('FAKE_FAIL_FETCH'):
        sys.exit(9)
'''

FAKE_CURL = r'''#!/usr/bin/env python3
import os, sys
arguments = sys.argv[1:]
with open(os.environ['FAKE_CURL_LOG'], 'a') as log:
    print(arguments[-1], file=log)
contents = 'corrupt' if os.environ.get('FAKE_CURL_CORRUPT') else 'fake ' + arguments[-1]
with open(arguments[arguments.index('--output') + 1], 'w') as output:
    output.write(contents)
'''


class BuildScriptTest(unittest.TestCase):
    """Drive scripts/build-docker-tools.sh in a copy of the project with fake docker and curl."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='eda build ')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        self.project = self.folder / 'project'
        for name in ('scripts', 'docker', 'config'):
            shutil.copytree(ROOT / name, self.project / name,
                            ignore=shutil.ignore_patterns('__pycache__'))
        rootfs = json.loads((ROOT / 'config/tools/ubuntu-base.json').read_text())
        source = json.loads((ROOT / 'config/tools/source-build.json').read_text())
        self.urls = {}
        for arch in ('arm64', 'amd64'):
            self.urls[arch] = (rootfs['url'].replace('{arch}', arch),
                               'https://github.com/bazelbuild/bazelisk/releases/download/'
                               f'v{source["bazelisk_version"]}/bazelisk-linux-{arch}')
            rootfs['sha256'][arch] = self.fake_sha(self.urls[arch][0])
            source['bazelisk_sha256'][arch] = self.fake_sha(self.urls[arch][1])
        (self.project / 'config/tools/ubuntu-base.json').write_text(json.dumps(rootfs))
        (self.project / 'config/tools/source-build.json').write_text(json.dumps(source))
        bin_dir = self.folder / 'bin'
        bin_dir.mkdir()
        for name, text in (('docker', FAKE_DOCKER), ('curl', FAKE_CURL)):
            (bin_dir / name).write_text(text)
            (bin_dir / name).chmod(0o755)
        self.docker_log = self.folder / 'docker-calls.jsonl'
        self.curl_log = self.folder / 'curl-calls.txt'
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(('EDA_', 'FAKE_'))}
        self.env.update(PATH=str(bin_dir) + os.pathsep + self.env['PATH'],
                        FAKE_DOCKER_LOG=str(self.docker_log), FAKE_CURL_LOG=str(self.curl_log))

    @staticmethod
    def fake_sha(url):
        return hashlib.sha256(('fake ' + url).encode()).hexdigest()

    def invoke(self, *arguments, **environment):
        self.docker_log.write_text('')
        self.curl_log.write_text('')
        return subprocess.run([str(self.project / 'scripts/build-docker-tools.sh'), *arguments],
                              env=self.env | environment, cwd=self.folder, text=True,
                              capture_output=True)

    def docker_calls(self):
        return [json.loads(line) for line in self.docker_log.read_text().splitlines()]

    def curl_calls(self):
        return self.curl_log.read_text().splitlines()

    def downloads(self, arch='arm64'):
        return self.project / 'downloads' / f'linux-{arch}'

    @staticmethod
    def option_values(arguments, option):
        return [arguments[i + 1] for i, value in enumerate(arguments) if value == option]

    def assert_build(self, arguments, target, tags, arch='arm64', context='orbstack'):
        self.assertEqual(arguments[:3], ['--context', context, 'build'])
        self.assertEqual(arguments[arguments.index('--file') + 1],
                         str(self.project / 'docker/Dockerfile.source'))
        self.assertEqual(arguments[arguments.index('--platform') + 1], f'linux/{arch}')
        self.assertEqual(arguments[arguments.index('--target') + 1], target)
        self.assertEqual(self.option_values(arguments, '--tag'), tags)
        self.assertIn('--network=none', arguments)
        self.assertIn('--pull=false', arguments)
        self.assertEqual(self.option_values(arguments, '--build-context'), [
            f'platform={self.downloads(arch)}', f'common={self.project}/downloads/common'])
        build_args = self.option_values(arguments, '--build-arg')
        self.assertIn(f'BUILDER_UID={os.getuid()}', build_args)
        self.assertIn(f'BUILDER_GID={os.getgid()}', build_args)
        self.assertEqual(arguments[-1], str(self.project))
        return build_args

    def assert_fetch_runs(self, calls, items, image='local/eda-tools:arm64', arch='arm64',
                          gui='OFF', xls='auto'):
        self.assertEqual(len(calls), len(items))
        for arguments, item in zip(calls, items):
            self.assertEqual(arguments[2], 'run')
            self.assertIn('--rm', arguments)
            self.assertNotIn('--network=none', arguments)
            self.assertEqual(arguments[-5:], [f'{image}-fetch', 'python3',
                                              '/opt/build/build-source-tools.py', 'fetch', item])
            mounts = self.option_values(arguments, '--mount')
            self.assertEqual(mounts, [
                f'type=bind,source={self.downloads(arch)},target=/downloads/platform',
                f'type=bind,source={self.project}/downloads/common,target=/downloads/common',
                f'type=bind,source={self.project}/downloads/fetch-cache,target=/downloads/fetch-cache'])
            self.assertEqual(self.option_values(arguments, '--env'), [
                'BAZEL_FETCH_TIMEOUT=1800', f'OPENROAD_GUI={gui}', f'XLS_INSTALL_MODE={xls}'])

    def test_all_fetches_every_input_then_builds_offline(self):
        for mode in ((), ('all',)):
            with self.subTest(mode=mode):
                result = self.invoke(*mode, '--progress=plain', EDA_BUILD_JOBS='2')
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = self.docker_calls()
                if mode == ():
                    self.assertEqual(self.curl_calls(), list(self.urls['arm64']))
                    self.assertEqual(self.downloads().joinpath('ubuntu-base.tar.gz').read_text(),
                                     'fake ' + self.urls['arm64'][0])
                    rootfs, apt = calls[:2]
                    calls = calls[2:]
                    self.assert_build(rootfs, 'rootfs', ['local/eda-tools:arm64-fetch-base'])
                    self.assertEqual(apt[2], 'run')
                    self.assertEqual(apt[-3:], ['local/eda-tools:arm64-fetch-base', 'sh',
                                                '/opt/build/fetch-apt-packages.sh'])
                    self.assertIn(f'HOST_UID={os.getuid()}', apt)
                else:
                    # A second run reuses verified downloads and the completed apt fetch.
                    self.assertEqual(self.curl_calls(), [])
                builder, *runs, cleanup, runtime = calls
                self.assert_build(builder, 'builder', ['local/eda-tools:arm64-fetch'])
                self.assert_fetch_runs(runs, ['libraries', 'yosys', 'xls'])
                self.assertEqual(cleanup, ['--context', 'orbstack', 'image', 'rm',
                                           'local/eda-tools:arm64-fetch-base',
                                           'local/eda-tools:arm64-fetch'])
                build_args = self.assert_build(runtime, 'runtime',
                                               ['local/eda-tools:arm64', 'local/eda-tools:v1'])
                for expected in ('OPENROAD_ENABLED=OFF', 'OPENROAD_GUI=OFF',
                                 'XLS_INSTALL_MODE=auto', 'BUILD_JOBS=2'):
                    self.assertIn(expected, build_args)
                for arguments in (builder, runtime):
                    self.assertIn('--progress=plain', arguments)
                    self.assertNotIn('all', arguments)

    def test_openroad_and_custom_settings_reach_fetch_and_build(self):
        result = self.invoke(EDA_PLATFORM='linux/amd64', EDA_IMAGE='custom:tag',
                             EDA_OPENROAD_ENABLED='ON', EDA_OPENROAD_GUI='ON',
                             EDA_XLS_INSTALL_MODE='source', EDA_DOCKER_CONTEXT='test-context')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.curl_calls(), list(self.urls['amd64']))
        calls = self.docker_calls()
        self.assert_build(calls[0], 'rootfs', ['custom:tag-fetch-base'], arch='amd64',
                          context='test-context')
        self.assert_build(calls[2], 'builder', ['custom:tag-fetch'], arch='amd64', context='test-context')
        self.assert_fetch_runs(calls[3:7], ['libraries', 'yosys', 'openroad', 'xls'], image='custom:tag',
                               arch='amd64', gui='ON', xls='source')
        build_args = self.assert_build(calls[-1], 'runtime', ['custom:tag'], arch='amd64',
                                       context='test-context')
        for expected in ('OPENROAD_ENABLED=ON', 'OPENROAD_GUI=ON', 'XLS_INSTALL_MODE=source',
                         'BUILD_JOBS=4'):
            self.assertIn(expected, build_args)

    def test_fetch_only_never_builds_the_runtime(self):
        result = self.invoke('fetch')
        self.assertEqual(result.returncode, 0, result.stderr)
        targets = [call[call.index('--target') + 1] for call in self.docker_calls() if call[2] == 'build']
        self.assertEqual(targets, ['rootfs', 'builder'])
        self.assertIn('FETCH COMPLETE', result.stderr)

    def test_build_only_uses_downloads_without_network_or_fetch_images(self):
        self.assertEqual(self.invoke('fetch').returncode, 0)
        result = self.invoke('build', '--no-cache')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.curl_calls(), [])
        (runtime,) = self.docker_calls()
        self.assert_build(runtime, 'runtime', ['local/eda-tools:arm64', 'local/eda-tools:v1'])
        self.assertIn('--no-cache', runtime)

    def test_build_without_downloads_fails_before_docker(self):
        result = self.invoke('build')
        self.assertEqual(result.returncode, 1)
        self.assertIn('ubuntu-base.tar.gz bazelisk apt ../common', result.stderr)
        self.assertIn('fetch first', result.stderr)
        self.assertEqual(self.docker_calls(), [])
        self.assertEqual(self.curl_calls(), [])

    def test_changed_package_list_requires_a_new_apt_fetch(self):
        self.assertEqual(self.invoke('fetch').returncode, 0)
        with (self.project / 'config/apt/runtime.txt').open('a') as packages:
            packages.write('extra-package\n')
        result = self.invoke('build')
        self.assertEqual(result.returncode, 1)
        self.assertIn(': apt;', result.stderr)
        self.assertEqual(self.docker_calls(), [])
        self.assertEqual(self.invoke('fetch').returncode, 0)
        self.assertEqual(self.docker_calls()[0][self.docker_calls()[0].index('--target') + 1], 'rootfs')

    def test_failed_fetch_stops_before_the_build_and_removes_fetch_images(self):
        for mode in ((), ('fetch',)):
            with self.subTest(mode=mode):
                result = self.invoke(*mode, FAKE_FAIL_FETCH='yosys')
                self.assertEqual(result.returncode, 9, result.stderr)
                calls = self.docker_calls()
                self.assertEqual(calls[-2][-1], 'yosys')
                self.assertEqual(calls[-1][2:4], ['image', 'rm'])
                self.assertFalse(any('runtime' in call for call in calls))

    def test_corrupt_download_is_discarded(self):
        result = self.invoke('fetch', FAKE_CURL_CORRUPT='1')
        self.assertEqual(result.returncode, 1)
        self.assertIn('Checksum mismatch', result.stderr)
        self.assertEqual(list(self.downloads().iterdir()), [])
        self.assertEqual(self.docker_calls(), [])

    def test_fetch_refuses_root(self):
        fake_id = self.folder / 'bin/id'
        fake_id.write_text('#!/bin/sh\necho 0\n')
        fake_id.chmod(0o755)
        result = self.invoke('fetch')
        self.assertEqual(result.returncode, 2)
        self.assertIn('regular user', result.stderr)
        self.assertEqual(self.docker_calls(), [])

    def test_invalid_settings_fail_before_docker(self):
        cases = ({'EDA_PLATFORM': 'linux/riscv64'}, {'EDA_OPENROAD_ENABLED': 'yes'},
                 {'EDA_OPENROAD_GUI': '1'}, {'EDA_XLS_INSTALL_MODE': 'binary'},
                 {'EDA_XLS_INSTALL_MODE': 'release'}, {'EDA_BUILD_JOBS': '0'},
                 {'EDA_BAZEL_FETCH_TIMEOUT': 'soon'})
        for environment in cases:
            for mode in ('all', 'fetch', 'build'):
                with self.subTest(environment=environment, mode=mode):
                    result = self.invoke(mode, **environment)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn('Unsupported' if 'release' not in environment.values()
                                  else 'requires linux/amd64', result.stderr)
                    self.assertEqual(self.docker_calls(), [])
                    self.assertEqual(self.curl_calls(), [])


if __name__ == '__main__':
    unittest.main()
