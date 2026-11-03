"""Check launcher installation and architecture selection without a Docker engine."""

import json
import os
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
    if '--target' in arguments and arguments[arguments.index('--target') + 1] == 'dependencies':
        sys.exit(int(os.environ.get('FAKE_FETCH_EXIT_CODE', '0')))
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

    def assert_build(self, arguments, target, tags, platform='linux/arm64', context='orbstack',
                     dependencies_image='dependencies', jobs=None):
        self.assertEqual(arguments[:3], ['--context', context, 'build'])
        self.assertEqual(arguments[arguments.index('--file') + 1], str(ROOT / 'docker/Dockerfile.source'))
        self.assertEqual(arguments[arguments.index('--platform') + 1], platform)
        self.assertEqual(arguments[arguments.index('--target') + 1], target)
        self.assertEqual([arguments[i + 1] for i, value in enumerate(arguments) if value == '--tag'],
                         tags)
        build_args = [arguments[i + 1] for i, value in enumerate(arguments) if value == '--build-arg']
        self.assertIn(f'DEPENDENCIES_IMAGE={dependencies_image}', build_args)
        if jobs is not None:
            self.assertIn(f'BUILD_JOBS={jobs}', build_args)
        self.assertEqual(arguments[-1], str(ROOT))
        if target == 'runtime':
            self.assertIn('--network=none', arguments)
            self.assertIn('--pull=false', arguments)
        else:
            self.assertNotIn('--network=none', arguments)
            self.assertNotIn('--pull=false', arguments)

    def assert_inspect(self, arguments, image, context='orbstack'):
        self.assertEqual(arguments, ['--context', context, 'image', 'inspect', '--format',
                                     '{{.Os}}/{{.Architecture}}', image])

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

    def test_default_and_explicit_all_fetch_then_build_offline_with_compatibility_tag(self):
        for mode in ((), ('all',)):
            with self.subTest(mode=mode):
                result = self.invoke('scripts/build-docker-tools.sh', *mode, '--progress=plain',
                                     EDA_BUILD_JOBS='2')
                self.assertEqual(result.returncode, 0, result.stderr)
                fetch, build = [json.loads(line) for line in result.stdout.splitlines()]
                self.assert_build(fetch, 'dependencies', ['local/eda-tools:arm64-deps'])
                self.assert_build(build, 'runtime', ['local/eda-tools:arm64', 'local/eda-tools:v1'],
                                  dependencies_image='local/eda-tools:arm64-deps', jobs='2')
                calls = self.docker_calls()
                self.assertEqual(len(calls), 3)
                self.assertEqual(calls[0], fetch)
                self.assert_inspect(calls[1], 'local/eda-tools:arm64-deps')
                self.assertEqual(calls[2], build)
                for arguments in (fetch, build):
                    self.assertIn('--progress=plain', arguments)
                    self.assertNotIn('all', arguments)

    def test_amd64_source_build_and_custom_image(self):
        result = self.invoke('scripts/build-docker-tools.sh', '--build-arg', 'XLS_INSTALL_MODE=source',
                             EDA_PLATFORM='linux/amd64', FAKE_IMAGE_PLATFORM='linux/amd64',
                             EDA_IMAGE='custom:tag')
        self.assertEqual(result.returncode, 0, result.stderr)
        fetch, build = [json.loads(line) for line in result.stdout.splitlines()]
        self.assert_build(fetch, 'dependencies', ['custom:tag-deps'], platform='linux/amd64')
        self.assert_build(build, 'runtime', ['custom:tag'], platform='linux/amd64',
                          dependencies_image='custom:tag-deps', jobs='4')
        self.assert_inspect(self.docker_calls()[1], 'custom:tag-deps')
        for arguments in (fetch, build):
            self.assertIn('XLS_INSTALL_MODE=source', arguments)

    def test_amd64_source_build_uses_amd64_tag(self):
        result = self.invoke('scripts/build-docker-tools.sh', EDA_PLATFORM='linux/amd64',
                             FAKE_IMAGE_PLATFORM='linux/amd64')
        self.assertEqual(result.returncode, 0, result.stderr)
        fetch, build = [json.loads(line) for line in result.stdout.splitlines()]
        self.assert_build(fetch, 'dependencies', ['local/eda-tools:amd64-deps'], platform='linux/amd64')
        self.assert_build(build, 'runtime', ['local/eda-tools:amd64'], platform='linux/amd64',
                          dependencies_image='local/eda-tools:amd64-deps')

    def test_custom_native_build_does_not_add_default_tags(self):
        result = self.invoke('scripts/build-docker-tools.sh', EDA_IMAGE='custom:tag')
        self.assertEqual(result.returncode, 0, result.stderr)
        fetch, build = [json.loads(line) for line in result.stdout.splitlines()]
        self.assert_build(fetch, 'dependencies', ['custom:tag-deps'])
        self.assert_build(build, 'runtime', ['custom:tag'], dependencies_image='custom:tag-deps')

    def test_fetch_only_prepares_dependencies_without_inspecting_or_compiling(self):
        result = self.invoke('scripts/build-docker-tools.sh', 'fetch', FAKE_IMAGE_MISSING='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = json.loads(result.stdout)
        self.assert_build(arguments, 'dependencies', ['local/eda-tools:arm64-deps'])
        self.assertEqual(self.docker_calls(), [arguments])
        self.assertNotIn('fetch', arguments)
        self.assertNotIn('BUILD_JOBS=4', arguments)

    def test_build_only_inspects_prepared_image_without_fetching(self):
        result = self.invoke('scripts/build-docker-tools.sh', 'build', FAKE_FETCH_EXIT_CODE='9')
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = json.loads(result.stdout)
        self.assert_build(arguments, 'runtime', ['local/eda-tools:arm64', 'local/eda-tools:v1'],
                          dependencies_image='local/eda-tools:arm64-deps', jobs='4')
        calls = self.docker_calls()
        self.assertEqual(len(calls), 2)
        self.assert_inspect(calls[0], 'local/eda-tools:arm64-deps')
        self.assertEqual(calls[1], arguments)

    def test_modes_forward_options_and_dependency_image_override(self):
        options = ['--progress=plain', '--no-cache', '--build-arg', 'XLS_INSTALL_MODE=source',
                   '--build-arg', 'OPENROAD_GUI=OFF', '--label', 'test.note=two words']
        for mode in ('all', 'fetch', 'build'):
            with self.subTest(mode=mode):
                result = self.invoke('scripts/build-docker-tools.sh', mode, *options,
                                     EDA_PLATFORM='linux/amd64', FAKE_IMAGE_PLATFORM='linux/amd64',
                                     EDA_DOCKER_CONTEXT='test-context', EDA_BUILD_JOBS='7',
                                     EDA_IMAGE='custom:runtime', EDA_DEPENDENCIES_IMAGE='custom:prepared')
                self.assertEqual(result.returncode, 0, result.stderr)
                commands = [json.loads(line) for line in result.stdout.splitlines()]
                self.assertEqual(len(commands), 2 if mode == 'all' else 1)
                for arguments in commands:
                    target = arguments[arguments.index('--target') + 1]
                    self.assertIn(target, ['dependencies', 'runtime'] if mode == 'all' else
                                  ['dependencies' if mode == 'fetch' else 'runtime'])
                    offline = target == 'runtime'
                    self.assert_build(arguments, target,
                                      ['custom:runtime' if offline else 'custom:prepared'],
                                      platform='linux/amd64', context='test-context',
                                      dependencies_image='custom:prepared' if offline else 'dependencies',
                                      jobs='7' if offline else None)
                    start = arguments.index(options[0])
                    self.assertEqual(arguments[start:start + len(options)], options)
                    self.assertEqual(arguments.count('build'), 1)
                    self.assertNotIn('fetch', arguments)
                    self.assertNotIn('all', arguments)
                calls = self.docker_calls()
                if mode == 'fetch':
                    self.assertEqual(calls, commands)
                else:
                    self.assertEqual(len(calls), len(commands) + 1)
                    self.assert_inspect(calls[-2], 'custom:prepared', context='test-context')
                    self.assertEqual(calls[-1], commands[-1])

    def test_missing_dependency_image_prevents_offline_build(self):
        for mode in ('build', 'all'):
            with self.subTest(mode=mode):
                result = self.invoke('scripts/build-docker-tools.sh', mode,
                                     EDA_DEPENDENCIES_IMAGE='custom:prepared', FAKE_IMAGE_MISSING='1')
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn('Missing dependency image custom:prepared', result.stderr)
                self.assertIn('fetch first', result.stderr)
                calls = self.docker_calls()
                self.assertEqual(len(calls), 1 if mode == 'build' else 2)
                self.assert_inspect(calls[-1], 'custom:prepared')
                if mode == 'build':
                    self.assertEqual(result.stdout, '')
                else:
                    self.assert_build(calls[0], 'dependencies', ['custom:prepared'])

    def test_wrong_arch_dependency_image_prevents_offline_build(self):
        for mode in ('build', 'all'):
            for platform, prepared in (('linux/arm64', 'linux/amd64'),
                                       ('linux/amd64', 'linux/arm64')):
                with self.subTest(mode=mode, platform=platform):
                    result = self.invoke('scripts/build-docker-tools.sh', mode, EDA_PLATFORM=platform,
                                         EDA_DEPENDENCIES_IMAGE='custom:prepared',
                                         FAKE_IMAGE_PLATFORM=prepared)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn(f'platform {prepared} does not match {platform}', result.stderr)
                    self.assertIn('rerun fetch', result.stderr)
                    calls = self.docker_calls()
                    self.assertEqual(len(calls), 1 if mode == 'build' else 2)
                    self.assert_inspect(calls[-1], 'custom:prepared')
                    if mode == 'build':
                        self.assertEqual(result.stdout, '')
                    else:
                        self.assert_build(calls[0], 'dependencies', ['custom:prepared'], platform=platform)

    def test_failed_fetch_prevents_inspection_and_offline_build(self):
        for mode in ((), ('all',), ('fetch',)):
            with self.subTest(mode=mode):
                result = self.invoke('scripts/build-docker-tools.sh', *mode, FAKE_FETCH_EXIT_CODE='9')
                self.assertEqual(result.returncode, 9, result.stderr)
                arguments = json.loads(result.stdout)
                self.assert_build(arguments, 'dependencies', ['local/eda-tools:arm64-deps'])
                self.assertEqual(self.docker_calls(), [arguments])

    def test_unsupported_build_platform_fails_before_docker(self):
        for mode in ('all', 'fetch', 'build'):
            with self.subTest(mode=mode):
                result = self.invoke('scripts/build-docker-tools.sh', mode, EDA_PLATFORM='linux/riscv64')
                self.assertEqual(result.returncode, 2)
                self.assertIn('Unsupported EDA_PLATFORM', result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertEqual(self.docker_calls(), [])


if __name__ == '__main__':
    unittest.main()
