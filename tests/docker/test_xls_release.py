"""Check XLS release installation fails before using untrusted or wrong-arch data."""

import hashlib
import importlib.util
import json
import os
import runpy
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('install_xls_release', ROOT / 'docker/install-xls-release.py')
INSTALLER = importlib.util.module_from_spec(SPEC)
with mock.patch.object(sys, 'path', [str(ROOT / 'docker'), *sys.path]):
    SPEC.loader.exec_module(INSTALLER)


class XLSReleaseTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.archive = self.folder / '.cache/xls-releases/xls-test-linux-x64.tar.gz'
        self.archive.parent.mkdir(parents=True)
        self.contents = b'expected archive'
        self.info = json.loads((ROOT / 'config/tools/xls-install.json').read_text())
        self.info.update(archive=self.archive.name,
                         download_url='https://example.invalid/xls.tar.gz',
                         sha256=hashlib.sha256(self.contents).hexdigest())
        self.manifest = self.folder / 'xls-install.json'
        self.manifest.write_text(json.dumps(self.info))
        self.original_manifest = self.manifest.read_bytes()
        for patch in (mock.patch.object(INSTALLER, 'ROOT', self.folder),
                      mock.patch.object(INSTALLER, 'MANIFEST', self.manifest),
                      mock.patch.object(INSTALLER.platform, 'machine', return_value='x86_64'),
                      mock.patch.object(INSTALLER.Path, 'home', return_value=self.folder)):
            patch.start()
            self.addCleanup(patch.stop)
        download = mock.patch.object(INSTALLER.subprocess, 'run')
        self.download = download.start()
        self.addCleanup(download.stop)

    def assert_not_installed(self):
        self.assertEqual(self.manifest.read_bytes(), self.original_manifest)
        self.assertFalse((self.folder / 'xls').exists())
        self.assertFalse((self.folder / self.archive.name.removesuffix('.tar.gz')).exists())

    def assert_downloaded(self):
        self.download.assert_called_once()
        arguments = self.download.call_args.args[0]
        self.assertEqual(arguments[0], 'curl')
        self.assertEqual(arguments[arguments.index('--output') + 1], str(self.archive))
        self.assertEqual(arguments[-1], self.info['download_url'])
        self.assertEqual(self.download.call_args.kwargs, {'check': True})

    @unittest.skipUnless(hasattr(tarfile, 'data_filter'),
                         'Release extraction needs tar data filters, available in the Docker image')
    def test_installs_verified_release_and_records_runtime_checksums(self):
        source = self.folder / 'release'
        source.mkdir()
        executable = source / 'interpreter_main'
        executable.write_bytes(b'XLS executable fixture')
        executable.chmod(0o755)
        (source / 'stdlib.x').write_text('stdlib fixture')
        with tarfile.open(self.archive, 'w:gz') as output:
            output.add(source, arcname=self.archive.name.removesuffix('.tar.gz'))
        self.info['sha256'] = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.manifest.write_text(json.dumps(self.info))
        INSTALLER.main()
        self.download.assert_not_called()
        installed = self.folder / 'xls'
        self.assertEqual((installed / 'stdlib.x').read_text(), 'stdlib fixture')
        self.assertTrue(os.access(installed / 'interpreter_main', os.X_OK))
        output = json.loads(self.manifest.read_text())
        self.assertEqual(output['variant'], 'binary-release')
        self.assertEqual(output['installed_directory'], str(installed))
        self.assertEqual(output['sha256'], self.info['sha256'])
        self.assertEqual(output['binary_sha256'], {
            'interpreter_main': hashlib.sha256(executable.read_bytes()).hexdigest()})

    def test_rejects_arm64_before_reading_manifest(self):
        for action in ('fetch', 'install'):
            with self.subTest(action=action), \
                 mock.patch.object(INSTALLER.platform, 'machine', return_value='aarch64'), \
                 mock.patch.object(Path, 'read_text') as read, \
                 mock.patch.object(INSTALLER.tarfile, 'open') as extraction:
                with self.assertRaisesRegex(SystemExit, 'requires Linux AMD64'):
                    INSTALLER.main(action)
                read.assert_not_called()
                self.download.assert_not_called()
                extraction.assert_not_called()

    def assert_offline_rejects_archive(self):
        for arguments in ((), ('install',)):
            with self.subTest(arguments=arguments), \
                 mock.patch.object(INSTALLER.tarfile, 'open') as extraction:
                with self.assertRaisesRegex(SystemExit, 'Missing or corrupt prefetched XLS release') as error:
                    INSTALLER.main(*arguments)
                self.assertIn(str(self.archive), str(error.exception))
                self.assertIn('rerun fetch', str(error.exception))
                self.download.assert_not_called()
                extraction.assert_not_called()
                self.assert_not_installed()

    def test_offline_install_rejects_missing_archive_without_downloading(self):
        self.assert_offline_rejects_archive()
        self.assertFalse(self.archive.exists())

    def test_offline_install_rejects_corrupt_archive_without_downloading(self):
        self.archive.write_bytes(b'corrupt archive')
        self.assert_offline_rejects_archive()
        self.assertEqual(self.archive.read_bytes(), b'corrupt archive')

    def test_rejects_corrupt_download_before_extraction(self):
        self.download.side_effect = lambda *args, **kwargs: self.archive.write_bytes(b'corrupt archive')
        with mock.patch.object(INSTALLER.tarfile, 'open') as extraction:
            with self.assertRaisesRegex(SystemExit, 'XLS release checksum mismatch'):
                INSTALLER.main('fetch')
            extraction.assert_not_called()
        self.assert_downloaded()
        self.assert_not_installed()

    def test_fetch_downloads_and_verifies_without_extracting_or_changing_manifest(self):
        for cached in (None, b'corrupt archive'):
            with self.subTest(cached=cached):
                if cached is not None:
                    self.archive.write_bytes(cached)
                self.download.reset_mock()
                self.download.side_effect = lambda *args, **kwargs: self.archive.write_bytes(self.contents)
                with mock.patch.object(INSTALLER.tarfile, 'open') as extraction:
                    INSTALLER.main('fetch')
                    extraction.assert_not_called()
                self.assert_downloaded()
                self.assertEqual(self.archive.read_bytes(), self.contents)
                self.assert_not_installed()

    def test_fetch_reuses_verified_archive_without_downloading_or_extracting(self):
        self.archive.write_bytes(self.contents)
        with mock.patch.object(INSTALLER.tarfile, 'open') as extraction:
            INSTALLER.main('fetch')
            extraction.assert_not_called()
        self.download.assert_not_called()
        self.assert_not_installed()

    def test_cli_dispatches_explicit_actions_and_defaults_to_offline_install(self):
        script = str(ROOT / 'docker/install-xls-release.py')
        for arguments in ([], ['fetch'], ['install']):
            with self.subTest(arguments=arguments):
                self.archive.unlink(missing_ok=True)
                self.download.reset_mock()
                self.download.side_effect = lambda *args, **kwargs: self.archive.write_bytes(self.contents)
                with mock.patch.object(sys, 'argv', [script, *arguments]), \
                     mock.patch.object(sys, 'path', [str(ROOT / 'docker'), *sys.path]), \
                     mock.patch.object(Path, 'read_text', return_value=json.dumps(self.info)), \
                     mock.patch.object(INSTALLER.tarfile, 'open',
                                       side_effect=AssertionError('CLI must not extract here')) as extraction:
                    if arguments == ['fetch']:
                        runpy.run_path(script, run_name='__main__')
                        self.assert_downloaded()
                        self.assertEqual(self.archive.read_bytes(), self.contents)
                    else:
                        with self.assertRaisesRegex(SystemExit, 'Missing or corrupt prefetched XLS release'):
                            runpy.run_path(script, run_name='__main__')
                        self.download.assert_not_called()
                    extraction.assert_not_called()
                self.assert_not_installed()


class XLSInstallSelectionTest(unittest.TestCase):
    STAGES = ('xls-fetch', 'xls-build')

    def stage(self, name):
        dockerfile = (ROOT / 'docker/Dockerfile.source').read_text()
        return dockerfile.split(f' AS {name}\n', 1)[1].split('\nFROM ', 1)[0]

    def invoke(self, stage_name, architecture, mode=None):
        default_mode = next(line.split('=', 1)[1] for line in self.stage('xls-fetch').splitlines()
                            if line.startswith('ARG XLS_INSTALL_MODE='))
        # Execute the real case command with the inherited mode and a recording Python stub.
        lines = self.stage(stage_name).splitlines()
        start = next(i for i, line in enumerate(lines)
                     if line.startswith('RUN ') and lines[i + 1].lstrip().startswith('case '))
        self.assertIn('--mount=', lines[start])
        if stage_name == 'xls-build':
            self.assertIn('--network=none', lines[start])
        else:
            self.assertNotIn('--network=none', lines[start])
        command_lines = []
        for line in lines[start + 1:]:
            command_lines.append(line)
            if not line.endswith('\\'):
                break
        self.assertTrue(command_lines)
        with tempfile.TemporaryDirectory() as temporary:
            python = Path(temporary) / 'python3'
            python.write_text(f'#!{sys.executable}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n')
            python.chmod(0o755)
            environment = os.environ | {
                'PATH': temporary + os.pathsep + os.environ['PATH'],
                'TARGETARCH': architecture, 'BUILD_JOBS': '2',
                'XLS_INSTALL_MODE': default_mode if mode is None else mode,
            }
            return subprocess.run(['sh', '-ec', '\n'.join(command_lines)], env=environment,
                                  capture_output=True, text=True)

    def source_command(self, stage):
        return ['/opt/build/build-source-tools.py',
                'fetch' if stage == 'xls-fetch' else 'build', 'xls',
                '1' if stage == 'xls-fetch' else '2']

    def release_command(self, stage):
        return ['/opt/build/install-xls-release.py', 'fetch' if stage == 'xls-fetch' else 'install']

    def test_install_mode_is_defaulted_and_persisted_by_fetch_not_reset_by_build(self):
        fetch = self.stage('xls-fetch').splitlines()
        self.assertIn('ARG XLS_INSTALL_MODE=auto', fetch)
        self.assertIn('ENV XLS_INSTALL_MODE=$XLS_INSTALL_MODE', fetch)
        self.assertNotIn('ARG XLS_INSTALL_MODE', self.stage('xls-build'))
        self.assertNotIn('ENV XLS_INSTALL_MODE', self.stage('xls-build'))

    def test_amd64_defaults_to_pinned_release(self):
        for stage in self.STAGES:
            with self.subTest(stage=stage):
                result = self.invoke(stage, 'amd64')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), self.release_command(stage))

    def test_arm64_defaults_to_source(self):
        for stage in self.STAGES:
            with self.subTest(stage=stage):
                result = self.invoke(stage, 'arm64')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), self.source_command(stage))

    def test_explicit_modes_override_automatic_selection(self):
        for stage in self.STAGES:
            for architecture, mode in (('amd64', 'source'), ('amd64', 'release'), ('arm64', 'source')):
                with self.subTest(stage=stage, architecture=architecture, mode=mode):
                    result = self.invoke(stage, architecture, mode)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    command = self.source_command(stage) if mode == 'source' else self.release_command(stage)
                    self.assertEqual(json.loads(result.stdout), command)

    def test_release_on_arm64_fails_before_fetching_or_installing(self):
        for stage in self.STAGES:
            with self.subTest(stage=stage):
                result = self.invoke(stage, 'arm64', 'release')
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertIn('release requires AMD64', result.stderr)

    def test_invalid_selection_fails_before_fetching_or_installing(self):
        for stage in self.STAGES:
            for architecture, mode in (('amd64', 'invalid'), ('riscv64', 'auto')):
                with self.subTest(stage=stage, architecture=architecture, mode=mode):
                    result = self.invoke(stage, architecture, mode)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, '')
                    self.assertIn('Unsupported XLS_INSTALL_MODE/TARGETARCH', result.stderr)


if __name__ == '__main__':
    unittest.main()
