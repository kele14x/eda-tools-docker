"""Check XLS release installation fails before using untrusted or wrong-arch data."""

import hashlib
import importlib.util
import json
import os
import runpy
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
        self.archive = self.folder / 'downloads/xls-release/xls-test-linux-x64.tar.gz'
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
                      mock.patch.object(INSTALLER, 'DOWNLOADS', self.archive.parent),
                      mock.patch.object(INSTALLER.platform, 'machine', return_value='x86_64')):
            patch.start()
            self.addCleanup(patch.stop)
        download = mock.patch.object(INSTALLER.subprocess, 'run')
        self.download = download.start()
        self.addCleanup(download.stop)

    def assert_not_installed(self):
        self.assertEqual(self.manifest.read_bytes(), self.original_manifest)
        self.assertFalse((self.folder / 'xls').exists())
        self.assertFalse((self.folder / self.archive.name.removesuffix('.tar.gz')).exists())

    def assert_downloaded(self, destination=None):
        self.download.assert_called_once()
        arguments = self.download.call_args.args[0]
        self.assertEqual(arguments[0], 'curl')
        self.assertEqual(arguments[arguments.index('--output') + 1], str(destination or self.archive))
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
        for action in ('fetch', 'check', 'install'):
            with self.subTest(action=action), \
                 mock.patch.object(INSTALLER.platform, 'machine', return_value='aarch64'), \
                 mock.patch.object(Path, 'read_text') as read, \
                 mock.patch.object(INSTALLER.tarfile, 'open') as extraction:
                with self.assertRaisesRegex(SystemExit, 'requires Linux AMD64'):
                    INSTALLER.main(action)
                read.assert_not_called()
                self.download.assert_not_called()
                extraction.assert_not_called()

    def test_rejects_unknown_action(self):
        with self.assertRaisesRegex(SystemExit, 'Expected fetch, check or install'):
            INSTALLER.main('build')
        self.download.assert_not_called()

    def assert_offline_rejects_archive(self):
        for arguments in ((), ('install',), ('check',)):
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

    def test_check_accepts_verified_archive_without_downloading_or_extracting(self):
        self.archive.write_bytes(self.contents)
        with mock.patch.object(INSTALLER.tarfile, 'open') as extraction:
            INSTALLER.main('check')
            extraction.assert_not_called()
        self.download.assert_not_called()
        self.assert_not_installed()

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

    def test_fetch_creates_the_downloads_folder(self):
        self.archive.parent.rmdir()
        self.download.side_effect = lambda *args, **kwargs: self.archive.write_bytes(self.contents)
        INSTALLER.main('fetch')
        self.assert_downloaded()

    def test_fetch_reuses_verified_archive_without_downloading_or_extracting(self):
        self.archive.write_bytes(self.contents)
        with mock.patch.object(INSTALLER.tarfile, 'open') as extraction:
            INSTALLER.main('fetch')
            extraction.assert_not_called()
        self.download.assert_not_called()
        self.assert_not_installed()

    def test_cli_dispatches_explicit_actions_and_defaults_to_offline_install(self):
        script = str(ROOT / 'docker/install-xls-release.py')
        destination = Path('/downloads/platform/xls-release') / self.archive.name
        for arguments in ([], ['fetch'], ['check'], ['install']):
            with self.subTest(arguments=arguments):
                self.download.reset_mock()
                self.download.side_effect = RuntimeError('download attempted')
                with mock.patch.object(sys, 'argv', [script, *arguments]), \
                     mock.patch.object(sys, 'path', [str(ROOT / 'docker'), *sys.path]), \
                     mock.patch.object(Path, 'read_text', return_value=json.dumps(self.info)), \
                     mock.patch.object(Path, 'is_file', return_value=False), \
                     mock.patch.object(Path, 'mkdir'), \
                     mock.patch.object(INSTALLER.tarfile, 'open',
                                       side_effect=AssertionError('CLI must not extract here')) as extraction:
                    if arguments == ['fetch']:
                        with self.assertRaisesRegex(RuntimeError, 'download attempted'):
                            runpy.run_path(script, run_name='__main__')
                        self.assert_downloaded(destination)
                    else:
                        with self.assertRaisesRegex(SystemExit, 'Missing or corrupt prefetched XLS release'):
                            runpy.run_path(script, run_name='__main__')
                        self.download.assert_not_called()
                    extraction.assert_not_called()
                self.assert_not_installed()


if __name__ == '__main__':
    unittest.main()
