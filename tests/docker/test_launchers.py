"""Check architecture selection without requiring a Docker engine."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class DockerPlatformTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='eda platform ')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name).resolve()
        docker = self.folder / 'docker'
        docker.write_text('''#!/usr/bin/env python3
import json, os, sys
if sys.argv[3:5] == ['image', 'inspect']:
    print(os.environ.get('FAKE_IMAGE_PLATFORM', 'linux/arm64'))
else:
    print(json.dumps(sys.argv[1:]))
''')
        docker.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('EDA_')}
        self.env['PATH'] = str(self.folder) + os.pathsep + self.env['PATH']

    def invoke(self, script, *arguments, **environment):
        return subprocess.run([str(ROOT / script), *arguments], env=self.env | environment,
                              cwd=self.folder, text=True, capture_output=True)

    def test_selected_native_image_does_not_force_amd64(self):
        result = self.invoke('docker/bin/eda', 'openroad', '-version',
                             EDA_IMAGE='local/eda-tools:arm64')
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = json.loads(result.stdout)
        self.assertEqual(arguments[arguments.index('--platform') + 1], 'linux/arm64')
        self.assertEqual(arguments[-3:], ['local/eda-tools:arm64', 'openroad', '-version'])
        self.assertIn(f'type=bind,source={self.folder},target={self.folder}', arguments)

    def test_explicit_platform_and_legacy_default(self):
        result = self.invoke('docker/bin/eda', 'true', EDA_PLATFORM='linux/amd64')
        arguments = json.loads(result.stdout)
        self.assertEqual(arguments[arguments.index('--platform') + 1], 'linux/amd64')
        self.assertEqual(arguments[-2:], ['local/eda-tools:v1', 'true'])

    def test_amd64_image_uses_its_own_architecture(self):
        result = self.invoke('docker/bin/eda', 'true', FAKE_IMAGE_PLATFORM='linux/amd64')
        arguments = json.loads(result.stdout)
        self.assertEqual(arguments[arguments.index('--platform') + 1], 'linux/amd64')

    def test_image_not_cached_leaves_platform_selection_to_docker(self):
        result = self.invoke('docker/bin/eda', 'true', FAKE_IMAGE_PLATFORM='')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('--platform', json.loads(result.stdout))

    def test_native_build_uses_source_dockerfile_and_separate_tag(self):
        result = self.invoke('scripts/build-docker-tools.sh', '--progress=plain',
                             EDA_PLATFORM='linux/arm64', EDA_BUILD_JOBS='2')
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = json.loads(result.stdout)
        self.assertEqual(arguments[arguments.index('--file') + 1], str(ROOT / 'docker/Dockerfile.source'))
        self.assertEqual(arguments[arguments.index('--tag') + 1], 'local/eda-tools:arm64')
        self.assertIn('BUILD_JOBS=2', arguments)
        self.assertIn('--progress=plain', arguments)

    def test_legacy_build_and_custom_image(self):
        result = self.invoke('scripts/build-docker-tools.sh', EDA_IMAGE='custom:tag')
        arguments = json.loads(result.stdout)
        self.assertEqual(arguments[arguments.index('--file') + 1], str(ROOT / 'Dockerfile'))
        self.assertEqual(arguments[arguments.index('--platform') + 1], 'linux/amd64')
        self.assertEqual(arguments[arguments.index('--tag') + 1], 'custom:tag')

    def test_unsupported_build_platform_fails_before_docker(self):
        result = self.invoke('scripts/build-docker-tools.sh', EDA_PLATFORM='linux/riscv64')
        self.assertEqual(result.returncode, 2)
        self.assertIn('Unsupported EDA_PLATFORM', result.stderr)
        self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
