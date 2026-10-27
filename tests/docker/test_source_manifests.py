"""Check runtime provenance is generated from source pins on both architectures."""

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('build_source_tools', ROOT / 'docker/build-source-tools.py')
BUILDER = importlib.util.module_from_spec(SPEC)
with mock.patch.object(sys, 'path', [str(ROOT / 'docker'), *sys.path]):
    SPEC.loader.exec_module(BUILDER)


class SourceManifestTest(unittest.TestCase):
    def test_records_runtime_checksums_for_each_tool_and_architecture(self):
        for machine, architecture in (('aarch64', 'arm64'), ('x86_64', 'x64')):
            for tool in ('xls', 'yosys', 'openroad'):
                with self.subTest(machine=machine, tool=tool), tempfile.TemporaryDirectory() as temporary:
                    image_root = Path(temporary)
                    manifests = image_root / 'manifests'
                    manifests.mkdir()
                    installation = image_root / tool
                    binary_dir = installation if tool == 'xls' else installation / 'bin'
                    binary_dir.mkdir(parents=True)
                    names = {'xls': ('interpreter_main', 'codegen_main'),
                             'yosys': ('yosys', 'yosys-abc'), 'openroad': ('openroad',)}[tool]
                    expected = {}
                    for name in names:
                        contents = f'{machine} {name}'.encode()
                        binary = binary_dir / name
                        binary.write_bytes(contents)
                        binary.chmod(0o755)
                        expected[name] = hashlib.sha256(contents).hexdigest()
                    (installation / 'runtime-data').write_text('bundled runtime data')
                    info = json.loads((ROOT / 'config/tools' / f'{tool}-install.json').read_text())
                    build = {'system': 'cmake' if tool == 'yosys' else 'bazel'}
                    if tool == 'yosys':
                        build['cmake_options'] = info['cmake_options'] | {
                            'CMAKE_INSTALL_PREFIX': str(installation)}
                    with mock.patch.object(BUILDER, 'ROOT', image_root), \
                         mock.patch.object(BUILDER, 'MANIFESTS', manifests), \
                         mock.patch.object(BUILDER.platform, 'machine', return_value=machine):
                        BUILDER.record(tool, info, build)
                    output = json.loads((manifests / f'{tool}-install.json').read_text())
                    self.assertEqual(output['platform'], f'linux-{architecture}')
                    self.assertEqual(output['variant'], 'source-build')
                    self.assertEqual(output['installed_directory'], str(installation))
                    self.assertEqual(output['build'], build)
                    self.assertTrue(output['repository'].startswith('https://github.com/'))
                    self.assertNotIn('sha256', output)  # Archive hashes are not executable hashes.
                    if tool == 'xls':
                        self.assertEqual(output['binary_sha256'], expected)
                    else:
                        self.assertEqual(output['binary'], str(binary_dir / tool))
                        self.assertEqual(output['binary_sha256'], expected[tool])
                    if tool == 'yosys':
                        self.assertEqual(output['abc_binary_sha256'], expected['yosys-abc'])
                        self.assertEqual(output['cmake_options'], build['cmake_options'])
                    if tool == 'openroad':
                        self.assertEqual(output['runtime_tree_sha256'], BUILDER.tree_sha256(installation))
                        self.assertEqual(output['embedded_opensta'], info['embedded_opensta'])


if __name__ == '__main__':
    unittest.main()
