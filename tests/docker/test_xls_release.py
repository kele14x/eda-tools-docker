"""Check XLS release installation fails before using untrusted or wrong-arch data."""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
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
    def test_rejects_arm64_before_reading_manifest(self):
        with mock.patch.object(INSTALLER.platform, 'machine', return_value='aarch64'):
            with self.assertRaisesRegex(SystemExit, 'requires Linux AMD64'):
                INSTALLER.main()

    def test_rejects_corrupt_download_before_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            archive = folder / '.cache/xls-releases/xls-test-linux-x64.tar.gz'
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b'corrupt archive')
            manifest = folder / 'xls-install.json'
            manifest.write_text(json.dumps({
                'platform': 'linux-x64', 'archive': archive.name,
                'download_url': 'https://example.invalid/xls.tar.gz',
                'sha256': hashlib.sha256(b'expected archive').hexdigest(),
            }))
            with mock.patch.object(INSTALLER, 'ROOT', folder), \
                 mock.patch.object(INSTALLER, 'MANIFEST', manifest), \
                 mock.patch.object(INSTALLER.platform, 'machine', return_value='x86_64'), \
                 mock.patch.object(INSTALLER.Path, 'home', return_value=folder), \
                 mock.patch.object(INSTALLER.subprocess, 'run'), \
                 mock.patch.object(INSTALLER.tarfile, 'open') as extraction:
                with self.assertRaisesRegex(SystemExit, 'XLS release checksum mismatch'):
                    INSTALLER.main()
                extraction.assert_not_called()
                self.assertFalse((folder / 'xls').exists())


class XLSInstallSelectionTest(unittest.TestCase):
    def invoke(self, architecture, mode=None):
        dockerfile = (ROOT / 'docker/Dockerfile.source').read_text()
        stage = dockerfile.split('FROM yosys-build AS xls-build\n', 1)[1].split('\nFROM ', 1)[0]
        default_mode = next(line.split('=', 1)[1] for line in stage.splitlines()
                            if line.startswith('ARG XLS_INSTALL_MODE='))
        # Exercise the actual Dockerfile RUN command, with only python3 replaced.
        command_lines = []
        recording = False
        for line in stage.splitlines():
            if line.startswith('RUN --mount='):
                recording = True
                continue
            if recording:
                command_lines.append(line)
                if not line.endswith('\\'):
                    break
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

    def test_amd64_defaults_to_pinned_release(self):
        result = self.invoke('amd64')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ['/opt/build/install-xls-release.py'])

    def test_arm64_defaults_to_source(self):
        result = self.invoke('arm64')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ['/opt/build/build-source-tools.py', 'xls', '2'])

    def test_explicit_modes_override_automatic_selection(self):
        for mode, command in (
            ('source', ['/opt/build/build-source-tools.py', 'xls', '2']),
            ('release', ['/opt/build/install-xls-release.py']),
        ):
            with self.subTest(mode=mode):
                result = self.invoke('amd64', mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), command)

    def test_invalid_selection_fails_before_installing(self):
        for architecture, mode in (('amd64', 'invalid'), ('riscv64', 'auto')):
            with self.subTest(architecture=architecture, mode=mode):
                result = self.invoke(architecture, mode)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertIn('Unsupported XLS_INSTALL_MODE/TARGETARCH', result.stderr)


if __name__ == '__main__':
    unittest.main()
