"""Check upgrades of launchers and viewers created before the repository rename."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ('eda', 'xls', 'yosys', 'sta', 'openroad', 'iverilog', 'vvp', 'verilator')
LEGACY_MARKER = '# Managed by xls-tools Docker tools; installed by scripts/install-docker-tools.sh.'


class LauncherUpgradeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='eda launcher upgrade ')
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

    def test_upgrades_legacy_launchers(self):
        for tool in TOOLS:
            (self.folder / tool).write_text(f'#!/usr/bin/env bash\n{LEGACY_MARKER}\nexit 0\n')
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.check_installed()

    def test_reinstalls_renamed_launchers(self):
        for _ in range(2):
            result = self.install()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.check_installed()

    def test_refuses_unrelated_command_before_changing_any_launchers(self):
        legacy = f'#!/usr/bin/env bash\n{LEGACY_MARKER}\nexit 0\n'
        (self.folder / 'eda').write_text(legacy)
        (self.folder / 'yosys').write_text('unrelated command\n')
        result = self.install()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('Refusing to overwrite an unrelated command', result.stderr)
        self.assertEqual((self.folder / 'eda').read_text(), legacy)
        self.assertEqual((self.folder / 'yosys').read_text(), 'unrelated command\n')
        self.assertEqual({path.name for path in self.folder.iterdir()}, {'eda', 'yosys'})


class ViewerUpgradeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='eda viewer upgrade ')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        self.log = self.folder / 'docker.jsonl'
        docker = self.folder / 'docker'
        docker.write_text('''#!/usr/bin/env python3
import json, os, re, sys
args = sys.argv[3:]
with open(os.environ['DOCKER_CALL_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
labels = json.loads(os.environ['VIEWER_LABELS'])
if args[:2] == ['container', 'inspect']:
    sys.exit(0 if args[-1] in labels else 1)
if args[0] == 'inspect':
    key = re.search(r'"([^"]+)"', args[2]).group(1)
    print(labels[args[-1]].get(key, ''))
elif args[0] != 'stop':
    sys.exit('Unexpected Docker command')
''')
        docker.chmod(0o755)
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith('EDA_')}
        self.environment.update(PATH=str(self.folder) + os.pathsep + os.environ['PATH'],
                                DOCKER_CALL_LOG=str(self.log))

    def stop(self, labels):
        result = subprocess.run([str(ROOT / 'bin/view-smoke-test.sh'), '--stop'],
                                env=self.environment | {'VIEWER_LABELS': json.dumps(labels)},
                                capture_output=True, text=True)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        stopped = [args[-1] for args in calls if args[0] == 'stop']
        return result, stopped

    def test_stops_both_renamed_and_legacy_viewers(self):
        viewers = {
            'eda-tools-docker-openroad-viewer': {
                'local.eda-tools-docker.viewer': 'eda-tools-docker-openroad-viewer'},
            'xls-tools-openroad-viewer': {'local.xls-tools.viewer': 'xls-tools-openroad-viewer'},
        }
        result, stopped = self.stop(viewers)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stopped, list(viewers))

    def test_refuses_unrelated_container(self):
        for viewer in ('eda-tools-docker-openroad-viewer', 'xls-tools-openroad-viewer'):
            with self.subTest(viewer=viewer):
                result, stopped = self.stop({viewer: {}})
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn('Refusing to stop an unrelated container', result.stderr)
                self.assertEqual(stopped, [])


if __name__ == '__main__':
    unittest.main()
