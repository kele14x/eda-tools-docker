"""Check runtime provenance is generated from source pins on both architectures."""

import hashlib
import importlib.util
import io
import json
import os
import re
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
    def setUp(self):
        environment = mock.patch.dict(os.environ, {}, clear=False)
        environment.start()
        self.addCleanup(environment.stop)
        for name in ('OPENROAD_GUI', 'OPENROAD_ENABLED', 'XLS_INSTALL_MODE', 'BAZELISK_HOME'):
            os.environ.pop(name, None)

    def test_bazel_fetch_and_build_use_the_same_targets_and_configuration(self):
        options = ('--config=release', '--//:platform=gui', '//:tarfile')
        cache = Path('/home/builder/.cache/openroad')
        for fetch in (True, False):
            with mock.patch.object(BUILDER, 'run') as run, \
                 mock.patch.object(BUILDER, 'fetch_with_progress') as fetch_runner, \
                 mock.patch.object(BUILDER.Path, 'is_dir', return_value=False), \
                 mock.patch.object(BUILDER, 'distdir_args', return_value=['--distdir=/prepared']) as distdir:
                BUILDER.bazel(Path('/src/openroad'), 2, *options, fetch=fetch)
            distdir.assert_called_once_with(cache, fetch)
            runner = fetch_runner if fetch else run
            (run if fetch else fetch_runner).assert_not_called()
            arguments = runner.call_args.args
            self.assertIn('fetch' if fetch else 'build', arguments)
            self.assertEqual('--nofetch' in arguments, not fetch)
            self.assertEqual(arguments[-len(options):], options)
            self.assertEqual(runner.call_args.kwargs['cwd'], Path('/src/openroad'))
            self.assertIn(f'--output_user_root={cache}/bazel', arguments)
            self.assertIn(f'--repository_cache={cache}/repository-cache', arguments)
            self.assertIn('--remote_cache=', arguments)
            self.assertFalse(any(argument.startswith('--disk_cache') for argument in arguments))
            self.assertEqual('--nobuild_tests_only' in arguments, fetch)
            self.assertEqual('--announce_rc' in arguments, fetch)
            self.assertEqual('--verbose_failures' in arguments, fetch)
            self.assertEqual(os.environ['BAZELISK_HOME'], f'{cache}/bazelisk')

    def test_failed_or_timed_out_fetch_preserves_partial_downloads_for_retry(self):
        failures = (BUILDER.subprocess.CalledProcessError(1, ['bazelisk']),
                    SystemExit('Bazel fetch exceeded timeout'))
        store = '/downloads/fetch-cache/bazel/openroad-aarch64'
        downloaded = Path('/home/builder/.cache/openroad/repository-cache/content_addressable')
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), \
                 mock.patch.object(BUILDER.platform, 'machine', return_value='aarch64'), \
                 mock.patch.object(BUILDER.Path, 'is_dir', return_value=True), \
                 mock.patch.object(BUILDER, 'distdir_args', return_value=[]), \
                 mock.patch.object(BUILDER, 'fetch_with_progress', side_effect=failure), \
                 mock.patch.object(BUILDER, 'run') as run:
                with self.assertRaises(type(failure)):
                    BUILDER.bazel(Path('/src/openroad'), 1, '//:tarfile', fetch=True)
            copies = [call.args for call in run.call_args_list if call.args[0] == 'cp']
            self.assertEqual(copies, [
                ('cp', '-a', '--update=none', store + '/.', downloaded),
                ('cp', '-a', '--update=none', str(downloaded) + '/.', Path(store)),
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
        self.assertIn('/home/builder/.cache/xls/bazel/', output.call_args_list[0].args[0])
        self.assertEqual(output.call_args_list[1].args[0], 'x' * 16384)

    def test_offline_distdir_checks_inputs_without_downloading(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            contents = b'dependency archive'
            entry = {'filename': 'dependency.tar.gz', 'url': 'https://example.invalid/dependency',
                     'sha256': hashlib.sha256(contents).hexdigest()}
            (folder / 'source-build.json').write_text(json.dumps({'bazel_distdir': [entry]}))
            distdir = folder / 'distdir'
            distdir.mkdir()
            archive = distdir / entry['filename']
            with mock.patch.object(BUILDER, 'MANIFESTS', folder), \
                 mock.patch.object(BUILDER, 'download') as download:
                for data in (None, b'corrupt', contents):
                    with self.subTest(data=data):
                        if data is not None:
                            archive.write_bytes(data)
                        if data == contents:
                            self.assertEqual(BUILDER.distdir_args(folder, False), [f'--distdir={distdir}'])
                        else:
                            with self.assertRaisesRegex(SystemExit, 'rerun fetch'):
                                BUILDER.distdir_args(folder, False)
                download.assert_not_called()
                BUILDER.distdir_args(folder, True)
                download.assert_called_once_with(entry['url'], archive, entry['sha256'])

    def test_fetch_prepares_and_packs_sources_without_compiling_or_recording_outputs(self):
        os.environ['XLS_INSTALL_MODE'] = 'source'
        for tool in ('openroad', 'yosys', 'xls'):
            with self.subTest(tool=tool):
                info = json.loads((ROOT / 'config/tools' / f'{tool}-install.json').read_text())
                expected = info.get('build_dependencies', {}).get('submodules', {})
                sta = info.get('embedded_opensta', {}).get('source_submodule_commit')

                def revision(arguments, *, cwd, text):
                    return sta if tool == 'openroad' else expected[str(cwd.relative_to('/src/yosys'))]

                with mock.patch.object(sys, 'argv', ['builder', 'fetch', tool]), \
                     mock.patch.object(BUILDER, 'MANIFESTS', ROOT / 'config/tools'), \
                     mock.patch.object(BUILDER, 'PATCH', ROOT / 'docker/openroad-parser-eof.patch'), \
                     mock.patch.object(BUILDER, 'prepared', return_value=None), \
                     mock.patch.object(BUILDER, 'checkout', return_value=Path('/src') / tool) as checkout, \
                     mock.patch.object(BUILDER, 'openroad_source', return_value=Path('/src/openroad')) as openroad, \
                     mock.patch.object(BUILDER.subprocess, 'check_output', side_effect=revision), \
                     mock.patch.object(BUILDER, 'run') as run, \
                     mock.patch.object(BUILDER, 'bazel') as bazel, \
                     mock.patch.object(BUILDER, 'unpack') as unpack, \
                     mock.patch.object(BUILDER, 'pack') as pack, \
                     mock.patch.object(BUILDER, 'remove_tree') as remove, \
                     mock.patch.object(BUILDER, 'record') as record:
                    BUILDER.main()
                record.assert_not_called()
                unpack.assert_not_called()
                pack.assert_called_once_with(tool, Path('/src') / tool)
                remove.assert_called_once_with(BUILDER.retry_store(Path('/src') / tool))
                self.assertFalse(any(call.args[0] in ('cmake', 'ninja', 'tar') for call in run.call_args_list))
                if tool == 'yosys':
                    bazel.assert_not_called()
                else:
                    self.assertTrue(bazel.call_args.kwargs['fetch'])
                if tool == 'openroad':
                    openroad.assert_called_once()
                    checkout.assert_not_called()
                    self.assertIn('--//:platform=cli', bazel.call_args.args)
                else:
                    checkout.assert_called_once()
                    openroad.assert_not_called()

    def test_fetch_skips_a_tool_whose_prepared_archive_matches(self):
        with mock.patch.object(sys, 'argv', ['builder', 'fetch', 'yosys']), \
             mock.patch.object(BUILDER, 'prepared', return_value={'size': 1}), \
             mock.patch.object(BUILDER, 'checkout') as checkout, \
             mock.patch.object(BUILDER, 'run') as run, \
             mock.patch.object(BUILDER, 'pack') as pack:
            BUILDER.main()
        checkout.assert_not_called()
        run.assert_not_called()
        pack.assert_not_called()

    def test_offline_yosys_build_unpacks_then_removes_sources(self):
        info = json.loads((ROOT / 'config/tools/yosys-install.json').read_text())
        expected = info['build_dependencies']['submodules']

        def revision(arguments, *, cwd, text):
            return expected[str(cwd.relative_to('/src/yosys'))]

        with mock.patch.object(sys, 'argv', ['builder', 'build', 'yosys', '2']), \
             mock.patch.object(BUILDER, 'MANIFESTS', ROOT / 'config/tools'), \
             mock.patch.object(BUILDER.subprocess, 'check_output', side_effect=revision), \
             mock.patch.object(BUILDER, 'unpack') as unpack, \
             mock.patch.object(BUILDER, 'checkout') as checkout, \
             mock.patch.object(BUILDER, 'download') as download, \
             mock.patch.object(BUILDER, 'run') as run, \
             mock.patch.object(BUILDER, 'remove_tree') as remove, \
             mock.patch.object(BUILDER, 'record') as record:
            BUILDER.main()
        unpack.assert_called_once_with('yosys')
        self.assertEqual(os.environ['GIT_NO_LAZY_FETCH'], '1')
        checkout.assert_not_called()
        download.assert_not_called()
        self.assertEqual([call.args[0] for call in run.call_args_list], ['cmake'] * 3)
        record.assert_called_once()
        self.assertEqual([call.args[0] for call in remove.call_args_list],
                         [Path('/src/yosys'), Path('/home/builder/.cache/yosys')])

    def test_missing_prepared_archive_fails_before_build(self):
        with mock.patch.object(sys, 'argv', ['builder', 'build', 'yosys', '2']), \
             mock.patch.object(BUILDER, 'prepared', return_value=None), \
             mock.patch.object(BUILDER, 'run') as run:
            with self.assertRaisesRegex(SystemExit, 'Missing or outdated prefetched yosys.*rerun fetch'):
                BUILDER.main()
        run.assert_not_called()

    def test_xls_release_mode_delegates_to_the_release_installer(self):
        os.environ['XLS_INSTALL_MODE'] = 'release'
        for argv, action in ((['fetch', 'xls'], 'fetch'), (['build', 'xls', '2'], 'install')):
            with self.subTest(action=action), \
                 mock.patch.object(sys, 'argv', ['builder', *argv]), \
                 mock.patch.object(BUILDER.platform, 'machine', return_value='x86_64'), \
                 mock.patch.object(BUILDER, 'unpack') as unpack, \
                 mock.patch.object(BUILDER, 'run') as run:
                BUILDER.main()
            run.assert_called_once_with(sys.executable, BUILDER.XLS_RELEASE, action)
            unpack.assert_not_called()

    def test_rejects_invalid_command_lines(self):
        for argv in ([], ['fetch'], ['fetch', 'yosys', '1'], ['build', 'yosys'], ['compile', 'yosys'],
                     ['fetch', 'unknown'], ['build', 'libraries', '4']):
            with self.subTest(argv=argv), mock.patch.object(sys, 'argv', ['builder', *argv]), \
                 mock.patch.object(BUILDER, 'run') as run:
                with self.assertRaises(SystemExit):
                    BUILDER.main()
                run.assert_not_called()


class SelectionTest(unittest.TestCase):
    def test_xls_mode_resolves_auto_by_architecture(self):
        for machine, mode, expected in (('x86_64', 'auto', 'release'), ('aarch64', 'auto', 'source'),
                                        ('x86_64', 'source', 'source'), ('aarch64', 'source', 'source'),
                                        ('x86_64', 'release', 'release')):
            with self.subTest(machine=machine, mode=mode), \
                 mock.patch.dict(os.environ, {'XLS_INSTALL_MODE': mode}), \
                 mock.patch.object(BUILDER.platform, 'machine', return_value=machine):
                self.assertEqual(BUILDER.xls_mode(), expected)

    def test_xls_mode_rejects_release_on_arm64_and_unknown_modes(self):
        for machine, mode, message in (('aarch64', 'release', 'requires AMD64'),
                                       ('x86_64', 'invalid', 'Unsupported XLS_INSTALL_MODE')):
            with self.subTest(mode=mode), \
                 mock.patch.dict(os.environ, {'XLS_INSTALL_MODE': mode}), \
                 mock.patch.object(BUILDER.platform, 'machine', return_value=machine):
                with self.assertRaisesRegex(SystemExit, message):
                    BUILDER.xls_mode()

    def test_openroad_gui_defaults_off_and_rejects_unknown_values(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(BUILDER.openroad_gui())
        with mock.patch.dict(os.environ, {'OPENROAD_GUI': 'ON'}):
            self.assertTrue(BUILDER.openroad_gui())
        with mock.patch.dict(os.environ, {'OPENROAD_GUI': 'yes'}):
            with self.assertRaisesRegex(SystemExit, 'Unsupported OPENROAD_GUI'):
                BUILDER.openroad_gui()


class PreparedDownloadsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        self.downloads = self.folder / 'downloads'
        self.downloads.mkdir()
        self.cache = self.folder / 'cache'
        for patch in (mock.patch.object(BUILDER, 'PLATFORM_DOWNLOADS', self.downloads),
                      mock.patch.object(BUILDER, 'COMMON_DOWNLOADS', self.folder / 'common'),
                      mock.patch.object(BUILDER, 'ROOT', self.folder / 'eda'),
                      mock.patch.object(BUILDER, 'MANIFESTS', ROOT / 'config/tools'),
                      mock.patch.object(BUILDER, 'CACHE', self.cache),
                      mock.patch.object(BUILDER, 'PATCH', ROOT / 'docker/openroad-parser-eof.patch'),
                      mock.patch.dict(os.environ, {'OPENROAD_GUI': 'OFF'})):
            patch.start()
            self.addCleanup(patch.stop)

    def make_tree(self, tool):
        source = self.folder / 'src' / tool
        (source / 'sub').mkdir(parents=True)
        (source / 'sub/file.txt').write_text('source')
        (self.cache / tool / 'bazel').mkdir(parents=True)
        (self.cache / tool / 'bazel/output').write_text('bazel output')
        return source

    def test_pack_then_unpack_restores_source_and_bazel_cache(self):
        source = self.make_tree('openroad')
        BUILDER.pack('openroad', source)
        metadata = json.loads((self.downloads / 'openroad.json').read_text())
        self.assertEqual(metadata['inputs'], BUILDER.inputs('openroad'))
        self.assertFalse(metadata['inputs']['gui'])
        self.assertIn('openroad-parser-eof.patch', metadata['inputs']['files'])
        self.assertEqual(metadata['size'], (self.downloads / 'openroad.tar').stat().st_size)
        self.assertFalse((self.downloads / 'openroad.tar.partial').exists())
        BUILDER.remove_tree(source)
        BUILDER.remove_tree(self.cache / 'openroad')
        BUILDER.unpack('openroad')
        self.assertEqual((source / 'sub/file.txt').read_text(), 'source')
        self.assertEqual((self.cache / 'openroad/bazel/output').read_text(), 'bazel output')

    def test_changed_inputs_or_size_make_the_archive_outdated(self):
        BUILDER.pack('openroad', self.make_tree('openroad'))
        self.assertIsNotNone(BUILDER.prepared('openroad'))
        with mock.patch.dict(os.environ, {'OPENROAD_GUI': 'ON'}):
            self.assertIsNone(BUILDER.prepared('openroad'))
            with self.assertRaisesRegex(SystemExit, 'Missing or outdated prefetched openroad.*rerun fetch'):
                BUILDER.unpack('openroad')
        with (self.downloads / 'openroad.tar').open('ab') as archive:
            archive.write(b'x')
        self.assertIsNone(BUILDER.prepared('openroad'))

    def test_unpack_rejects_a_corrupt_archive(self):
        BUILDER.pack('yosys', self.make_tree('yosys'))
        archive = self.downloads / 'yosys.tar'
        data = bytearray(archive.read_bytes())
        data[0] ^= 0xff
        archive.write_bytes(bytes(data))
        with mock.patch.object(BUILDER, 'run') as run:
            with self.assertRaisesRegex(SystemExit, 'Corrupt prefetched yosys.*rerun fetch'):
                BUILDER.unpack('yosys')
        run.assert_not_called()

    def test_libraries_fetch_store_in_common_downloads_and_build_copies_them(self):
        files = list(BUILDER.library_files())
        self.assertEqual(len(files), 18)
        with self.assertRaisesRegex(SystemExit, 'Missing or corrupt prefetched library file.*rerun fetch'):
            BUILDER.libraries('check')

        def fake_download(url, destination, checksum):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(url)

        with mock.patch.object(BUILDER, 'download', side_effect=fake_download) as download, \
             mock.patch.object(BUILDER, 'file_sha256', side_effect=lambda path: next(
                 sha for url, relative, sha in files if url == Path(path).read_text())):
            BUILDER.libraries('fetch')
            self.assertEqual(download.call_count, len(files))
            BUILDER.libraries('check')
            self.assertFalse((self.folder / 'eda').exists())
            BUILDER.libraries('build')
        for url, relative, _ in files:
            self.assertEqual((self.folder / 'common' / relative).read_text(), url)
            self.assertEqual((self.folder / 'eda' / relative).read_text(), url)

    def test_check_requires_every_selected_input(self):
        cases = (('OFF', 'source', ['yosys', 'xls']), ('ON', 'source', ['yosys', 'openroad', 'xls']),
                 ('OFF', 'release', ['yosys']))
        for enabled, mode, tools in cases:
            with self.subTest(enabled=enabled, mode=mode), \
                 mock.patch.dict(os.environ, {'OPENROAD_ENABLED': enabled, 'XLS_INSTALL_MODE': mode}), \
                 mock.patch.object(BUILDER.platform, 'machine', return_value='x86_64'), \
                 mock.patch.object(BUILDER, 'libraries') as libraries, \
                 mock.patch.object(BUILDER, 'run') as run, \
                 mock.patch.object(BUILDER, 'prepared', return_value={}) as prepared:
                BUILDER.check()
                libraries.assert_called_once_with('check')
                self.assertEqual([call.args[0] for call in prepared.call_args_list], tools)
                if mode == 'release':
                    run.assert_called_once_with(sys.executable, BUILDER.XLS_RELEASE, 'check')
                else:
                    run.assert_not_called()
        with mock.patch.dict(os.environ, {'OPENROAD_ENABLED': 'ON', 'XLS_INSTALL_MODE': 'source'}), \
             mock.patch.object(BUILDER, 'libraries'), \
             mock.patch.object(BUILDER, 'prepared', side_effect=lambda tool: None if tool == 'openroad' else {}):
            with self.assertRaisesRegex(SystemExit, 'Missing or outdated prefetched openroad.*rerun fetch'):
                BUILDER.check()


class DockerfileTest(unittest.TestCase):
    def setUp(self):
        self.dockerfile = (ROOT / 'docker/Dockerfile.source').read_text()

    def test_every_step_is_offline_and_uncached(self):
        runs = [line for line in self.dockerfile.splitlines() if line.startswith('RUN ')]
        self.assertGreater(len(runs), 5)
        self.assertTrue(all(line.startswith('RUN --network=none') for line in runs), runs)
        self.assertNotIn('type=cache', self.dockerfile)
        self.assertNotIn('apt-get', self.dockerfile)
        self.assertNotIn('# syntax=', self.dockerfile)

    def test_dockerfile_invocations_are_accepted_by_the_builder(self):
        commands = re.findall(r'/opt/build/build-source-tools\.py ([^;\\\n&]+)', self.dockerfile)
        self.assertGreaterEqual(len(commands), 5)

        class Accepted(Exception):
            pass

        for command in commands:
            argv = command.replace('"$BUILD_JOBS"', '4').split()
            with self.subTest(argv=argv), mock.patch.object(sys, 'argv', ['builder', *argv]), \
                 mock.patch.dict(os.environ, {'XLS_INSTALL_MODE': 'source'}), \
                 mock.patch.object(BUILDER, 'check', side_effect=Accepted), \
                 mock.patch.object(BUILDER, 'libraries', side_effect=Accepted), \
                 mock.patch.object(BUILDER, 'unpack', side_effect=Accepted):
                with self.assertRaises(Accepted):
                    BUILDER.main()

    def test_base_image_and_packages_come_from_downloads(self):
        bases = [line.split()[1] for line in self.dockerfile.splitlines() if line.startswith('FROM ')]
        self.assertEqual(bases[0], 'scratch')
        self.assertTrue(set(bases) <= {'scratch', 'rootfs', 'runtime-root', 'builder', 'libraries',
                                       'openroad-build', 'yosys-build'}, bases)
        self.assertIn('ADD downloads/linux-${TARGETARCH}/ubuntu-base.tar.gz /', self.dockerfile)
        self.assertIn('COPY --from=platform --chmod=755 bazelisk /usr/local/bin/bazelisk', self.dockerfile)
        self.assertEqual(self.dockerfile.count('install-apt-packages.sh runtime'), 2)

    def test_runtime_copies_only_installed_outputs(self):
        runtime = self.dockerfile.split('FROM runtime-root AS runtime\n', 1)[1]
        self.assertIn('COPY --from=xls-build --chown=0:0 /opt/eda/ /opt/eda/', runtime)
        self.assertNotIn('/src', runtime)
        self.assertNotIn('.cache', runtime)


if __name__ == '__main__':
    unittest.main()
