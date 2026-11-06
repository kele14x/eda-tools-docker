"""Check runtime provenance is generated from source pins on both architectures."""

import hashlib
import importlib.util
import io
import json
import sys
import tarfile
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


class SourceDownloadTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.archive = Path(temporary.name) / 'source.tar.gz'
        with tarfile.open(self.archive, 'w:gz') as archive:
            member = tarfile.TarInfo('source.txt')
            member.size = 1024
            archive.addfile(member, io.BytesIO(b'x' * member.size))
        self.complete = self.archive.read_bytes()

    def test_validates_archive_contents_and_compressed_trailer(self):
        self.assertTrue(BUILDER.is_complete_tarball(self.archive))
        corrupt = bytearray(self.complete)
        corrupt[-8] ^= 0xff
        for contents in (self.complete[:len(self.complete) // 2], self.complete[:-8], bytes(corrupt)):
            with self.subTest(size=len(contents)):
                self.archive.write_bytes(contents)
                self.assertFalse(BUILDER.is_complete_tarball(self.archive))

    def test_reuses_only_a_complete_archive(self):
        with mock.patch.object(BUILDER, 'run') as run:
            BUILDER.download_restarting('https://example.invalid/source.tar.gz', self.archive)
        run.assert_not_called()

    def test_restarts_truncated_archives_after_a_failed_transfer(self):
        self.archive.write_bytes(self.complete[:-8])
        partial = self.archive.with_name(self.archive.name + '.partial')
        attempts = []

        def transfer(*args):
            attempts.append(args)
            if len(attempts) == 1:
                raise BUILDER.subprocess.CalledProcessError(18, args)
            partial.write_bytes(self.complete)

        with mock.patch.object(BUILDER, 'run', side_effect=transfer):
            BUILDER.download_restarting('https://example.invalid/source.tar.gz', self.archive)
        self.assertEqual(len(attempts), 2)
        self.assertTrue(BUILDER.is_complete_tarball(self.archive))
        self.assertFalse(partial.exists())

    def test_never_asks_the_server_for_a_byte_range(self):
        """codeload answers Range with 200, so -C - would fail every retry with exit 56."""
        self.archive.write_bytes(self.complete[:-8])
        partial = self.archive.with_name(self.archive.name + '.partial')
        attempts = []

        def transfer(*args):
            attempts.append(args)
            partial.write_bytes(self.complete)

        with mock.patch.object(BUILDER, 'run', side_effect=transfer):
            BUILDER.download_restarting('https://example.invalid/source.tar.gz', self.archive)
        for args in attempts:
            self.assertNotIn('-C', args)
            self.assertNotIn('--continue-at', args)

    def test_does_not_publish_an_incomplete_transfer_as_the_archive(self):
        """A stalled body must leave no destination behind, only an ignored .partial file."""
        self.archive.unlink()
        partial = self.archive.with_name(self.archive.name + '.partial')
        self.assertFalse(self.archive.exists())

        def transfer(*args):
            partial.write_bytes(self.complete[:-8])
            raise BUILDER.subprocess.CalledProcessError(28, args)

        with mock.patch.object(BUILDER, 'run', side_effect=transfer), \
             self.assertRaises(BUILDER.subprocess.CalledProcessError):
            BUILDER.download_restarting('https://example.invalid/source.tar.gz', self.archive)
        self.assertFalse(self.archive.exists())
        self.assertTrue(partial.exists())


class OfflineSourceBuildTest(unittest.TestCase):
    def test_bazel_fetch_and_build_use_the_same_targets_and_configuration(self):
        options = ('--config=release', '--//:platform=gui', '//:tarfile')
        calls = []
        for fetch in (True, False):
            with mock.patch.object(BUILDER, 'run') as run, \
                 mock.patch.object(BUILDER, 'fetch_with_progress') as fetch_runner, \
                 mock.patch.object(BUILDER.Path, 'is_dir', return_value=False), \
                 mock.patch.object(BUILDER, 'distdir_args', return_value=['--distdir=/prepared']) as distdir:
                BUILDER.bazel(Path('/src/openroad'), 2, *options, fetch=fetch)
            distdir.assert_called_once_with(fetch)
            runner = fetch_runner if fetch else run
            (run if fetch else fetch_runner).assert_not_called()
            arguments = runner.call_args.args
            calls.append(arguments)
            self.assertIn('fetch' if fetch else 'build', arguments)
            self.assertEqual('--nofetch' in arguments, not fetch)
            self.assertEqual(arguments[-len(options):], options)
            self.assertEqual(runner.call_args.kwargs['cwd'], Path('/src/openroad'))
            self.assertIn('--output_user_root=/home/builder/.cache/bazel', arguments)
            self.assertIn('--repository_cache=/home/builder/.cache/bazel-repository-cache', arguments)
            self.assertIn('--remote_cache=', arguments)
            self.assertEqual('--nobuild_tests_only' in arguments, fetch)
            self.assertEqual('--announce_rc' in arguments, fetch)
            self.assertEqual('--verbose_failures' in arguments, fetch)
        self.assertNotIn('--disk_cache=/home/builder/.cache/bazel-disk-cache', calls[0])
        self.assertIn('--disk_cache=/home/builder/.cache/bazel-disk-cache', calls[1])

    def test_failed_or_timed_out_fetch_preserves_partial_downloads_for_retry(self):
        failures = (BUILDER.subprocess.CalledProcessError(1, ['bazelisk']),
                    SystemExit('Bazel fetch exceeded timeout'))
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), \
                 mock.patch.object(BUILDER.Path, 'is_dir', return_value=True), \
                 mock.patch.object(BUILDER, 'distdir_args', return_value=[]), \
                 mock.patch.object(BUILDER, 'fetch_with_progress', side_effect=failure), \
                 mock.patch.object(BUILDER, 'run') as run:
                with self.assertRaises(type(failure)):
                    BUILDER.bazel(Path('/src/openroad'), 1, '//:tarfile', fetch=True)
            copies = [call.args for call in run.call_args_list if call.args[0] == 'cp']
            self.assertEqual(copies, [
                ('cp', '-a', '--update=none', '/var/cache/eda/bazel-repository-cache/.',
                 Path('/home/builder/.cache/bazel-repository-cache')),
                ('cp', '-a', '--update=none', '/home/builder/.cache/bazel-repository-cache/.',
                 Path('/var/cache/eda/bazel-repository-cache')),
            ])

    def test_failed_fetch_prints_bounded_java_diagnostics(self):
        failure = BUILDER.subprocess.CalledProcessError(1, ['bazelisk'])
        with mock.patch.object(BUILDER.Path, 'is_dir', return_value=False), \
             mock.patch.object(BUILDER.Path, 'is_file', return_value=True), \
             mock.patch.object(BUILDER.Path, 'stat', return_value=mock.Mock(st_size=20000)), \
             mock.patch.object(BUILDER.Path, 'open', return_value=io.BytesIO(b'x' * 20000)), \
             mock.patch.object(BUILDER, 'distdir_args', return_value=[]), \
             mock.patch.object(BUILDER, 'fetch_with_progress', side_effect=failure), \
             mock.patch.object(BUILDER, 'print', create=True) as output:
            with self.assertRaises(BUILDER.subprocess.CalledProcessError):
                BUILDER.bazel(Path('/src/xls'), 1, '//dist:xls_dist_tar', fetch=True)
        self.assertIn('FETCH DIAGNOSTICS [xls]', output.call_args_list[0].args[0])
        self.assertEqual(output.call_args_list[1].args[0], 'x' * 16384)

    def test_offline_distdir_checks_inputs_without_downloading(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            contents = b'dependency archive'
            entry = {'filename': 'dependency.tar.gz', 'url': 'https://example.invalid/dependency',
                     'sha256': hashlib.sha256(contents).hexdigest()}
            (folder / 'source-build.json').write_text(json.dumps({'bazel_distdir': [entry]}))
            archive = folder / entry['filename']
            with mock.patch.object(BUILDER, 'MANIFESTS', folder), \
                 mock.patch.object(BUILDER, 'DISTDIR', folder), \
                 mock.patch.object(BUILDER, 'download') as download:
                for data in (None, b'corrupt', contents):
                    with self.subTest(data=data):
                        if data is not None:
                            archive.write_bytes(data)
                        if data == contents:
                            self.assertEqual(BUILDER.distdir_args(False), [f'--distdir={folder}'])
                        else:
                            with self.assertRaisesRegex(SystemExit, 'rerun fetch'):
                                BUILDER.distdir_args(False)
                download.assert_not_called()
                BUILDER.distdir_args(True)
                download.assert_called_once_with(entry['url'], archive, entry['sha256'])

    def test_fetch_prepares_sources_without_compiling_or_recording_outputs(self):
        for tool in ('openroad', 'yosys', 'xls'):
            with self.subTest(tool=tool):
                info = json.loads((ROOT / 'config/tools' / f'{tool}-install.json').read_text())
                expected = info.get('build_dependencies', {}).get('submodules', {})
                sta = info.get('embedded_opensta', {}).get('source_submodule_commit')

                def revision(arguments, *, cwd, text):
                    return sta if tool == 'openroad' else expected[str(cwd.relative_to('/src/yosys'))]

                with mock.patch.object(sys, 'argv', ['builder', 'fetch', tool, '1', 'ON']), \
                     mock.patch.object(BUILDER, 'MANIFESTS', ROOT / 'config/tools'), \
                     mock.patch.object(BUILDER, 'checkout', return_value=Path('/src') / tool) as checkout, \
                     mock.patch.object(BUILDER, 'openroad_source', return_value=Path('/src/openroad')) as openroad, \
                     mock.patch.object(BUILDER.subprocess, 'check_output', side_effect=revision), \
                     mock.patch.object(BUILDER, 'file_sha256', return_value='patch-checksum'), \
                     mock.patch.object(BUILDER, 'run') as run, \
                     mock.patch.object(BUILDER, 'bazel') as bazel, \
                     mock.patch.object(BUILDER, 'record') as record:
                    BUILDER.main()
                record.assert_not_called()
                self.assertFalse(any(call.args[0] in ('cmake', 'ninja', 'tar') for call in run.call_args_list))
                if tool == 'yosys':
                    bazel.assert_not_called()
                else:
                    self.assertTrue(bazel.call_args.kwargs['fetch'])
                if tool == 'openroad':
                    openroad.assert_called_once()
                    checkout.assert_not_called()
                else:
                    checkout.assert_called_once()
                    openroad.assert_not_called()

    def test_offline_yosys_build_never_checks_out_or_downloads_sources(self):
        info = json.loads((ROOT / 'config/tools/yosys-install.json').read_text())
        expected = info['build_dependencies']['submodules']

        def revision(arguments, *, cwd, text):
            return expected[str(cwd.relative_to('/src/yosys'))]

        with mock.patch.object(sys, 'argv', ['builder', 'build', 'yosys', '2']), \
             mock.patch.object(BUILDER, 'MANIFESTS', ROOT / 'config/tools'), \
             mock.patch.object(BUILDER.Path, 'is_dir', return_value=True), \
             mock.patch.object(BUILDER.subprocess, 'check_output', side_effect=revision), \
             mock.patch.object(BUILDER, 'checkout') as checkout, \
             mock.patch.object(BUILDER, 'download') as download, \
             mock.patch.object(BUILDER, 'run') as run, \
             mock.patch.object(BUILDER, 'record') as record:
            BUILDER.main()
        checkout.assert_not_called()
        download.assert_not_called()
        self.assertEqual([call.args[0] for call in run.call_args_list], ['cmake'] * 3)
        record.assert_called_once()

    def test_missing_source_fails_before_build_or_fetch(self):
        with mock.patch.object(sys, 'argv', ['builder', 'build', 'yosys', '2']), \
             mock.patch.object(BUILDER, 'MANIFESTS', ROOT / 'config/tools'), \
             mock.patch.object(BUILDER.Path, 'is_dir', return_value=False), \
             mock.patch.object(BUILDER, 'run') as run:
            with self.assertRaisesRegex(SystemExit, 'Missing prefetched source.*rerun fetch'):
                BUILDER.main()
        run.assert_not_called()

    def test_xls_checkpoint_refreshes_helper_and_fetch_timeout(self):
        dockerfile = (ROOT / 'docker/Dockerfile.source').read_text()
        self.assertIn('ARG XLS_FETCH_BASE_IMAGE=yosys-fetch', dockerfile)
        stage = dockerfile.split('FROM ${XLS_FETCH_BASE_IMAGE} AS xls-fetch\n', 1)[1].split('\nFROM ', 1)[0]
        self.assertIn('COPY docker/build-source-tools.py docker/runtime_checksums.py /opt/build/', stage)
        self.assertIn('ARG BAZEL_FETCH_TIMEOUT=1800', stage)
        self.assertIn('ENV BAZEL_FETCH_TIMEOUT=$BAZEL_FETCH_TIMEOUT', stage)

    def test_every_offline_docker_step_disables_network(self):
        dockerfile = (ROOT / 'docker/Dockerfile.source').read_text()
        prepared = dockerfile.split('FROM xls-fetch AS dependencies\n', 1)[1]
        self.assertTrue(prepared.startswith('COPY --from=runtime-packages / /opt/runtime-root/'))
        offline = prepared.split('FROM ${DEPENDENCIES_IMAGE} AS prepared\n', 1)[1]
        runs = [line for line in offline.splitlines() if line.startswith('RUN ')]
        self.assertEqual(len(runs), 4)
        self.assertTrue(all(line.startswith('RUN --network=none ') for line in runs))
        self.assertNotIn('target=/home/builder/.cache,', offline)
        self.assertNotIn('apt-get', offline)
        self.assertNotIn('# syntax=', dockerfile)
        self.assertIn('COPY --from=prepared /opt/runtime-root/ /', offline)


if __name__ == '__main__':
    unittest.main()
