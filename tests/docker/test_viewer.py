"""Check viewer cleanup without requiring a Docker engine."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIEWER = 'eda-tools-docker-openroad-viewer'
LABEL_KEY = 'local.eda-tools-docker.viewer'


class ViewerCleanupTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='eda viewer cleanup ')
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
        result = subprocess.run([str(ROOT / 'scripts/view-smoke-test.sh'), '--stop'],
                                env=self.environment | {'VIEWER_LABELS': json.dumps(labels)},
                                capture_output=True, text=True)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        stopped = [args[-1] for args in calls if args[0] == 'stop']
        return result, stopped

    def test_stops_viewer(self):
        result, stopped = self.stop({VIEWER: {LABEL_KEY: VIEWER}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stopped, [VIEWER])

    def test_refuses_unrelated_container(self):
        result, stopped = self.stop({VIEWER: {}})
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('Refusing to stop an unrelated container', result.stderr)
        self.assertEqual(stopped, [])


if __name__ == '__main__':
    unittest.main()
