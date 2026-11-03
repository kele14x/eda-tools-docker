"""Exercise bounded Bazel fetches without downloading or running Bazel."""

import contextlib
import ctypes
import importlib.util
import io
import os
import select
import shlex
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'fetch_progress_builder', ROOT / 'docker/build-source-tools.py')
BUILDER = importlib.util.module_from_spec(SPEC)
with mock.patch.object(sys, 'path', [str(ROOT / 'docker'), *sys.path]):
    SPEC.loader.exec_module(BUILDER)


class FetchProgressTest(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.dict(os.environ, {}, clear=True))
        self.clock = self.stack.enter_context(
            mock.patch.object(BUILDER.time, 'monotonic', return_value=100))
        self.popen = self.stack.enter_context(mock.patch.object(BUILDER.subprocess, 'Popen'))
        self.killpg = self.stack.enter_context(mock.patch.object(BUILDER.os, 'killpg', create=True))
        self.print = self.stack.enter_context(mock.patch.object(BUILDER, 'print', create=True))
        self.process = self.popen.return_value.__enter__.return_value
        self.process.pid = 43210
        self.process.wait.return_value = 0
        self.events = mock.Mock()
        self.events.attach_mock(self.process.wait, 'wait')
        self.events.attach_mock(self.killpg, 'killpg')
        self.cwd = Path('/src/open road')
        self.args = (Path('/opt/bazel tools/bazelisk'), 'fetch', '//:tarfile')
        self.command = list(map(str, self.args))

    def output(self):
        for call in self.print.call_args_list:
            self.assertTrue(call.kwargs.get('flush'), 'fetch diagnostics must be flushed')
        return '\n'.join(call.args[0] for call in self.print.call_args_list)

    def expired(self, timeout):
        return subprocess.TimeoutExpired(self.command, timeout)

    def prepare_timeout(self, cooperative=True):
        os.environ['BAZEL_FETCH_TIMEOUT'] = '1'
        self.clock.side_effect = [100, 100, 101]
        self.process.wait.side_effect = [
            self.expired(1),
            -signal.SIGTERM if cooperative else self.expired(10),
            -signal.SIGTERM if cooperative else -signal.SIGKILL,
        ]

    def assert_fetch_timeout(self):
        with self.assertRaises(SystemExit) as caught:
            BUILDER.fetch_with_progress(*self.args, cwd=self.cwd)
        message = str(caught.exception)
        self.assertIn('exceeded 1s', message)
        self.assertIn(self.cwd.name, message)
        self.assertIn('fetch timeout, not a compilation failure', message)
        self.assertIn('FETCH TIMEOUT', self.output())
        self.assertNotIn('FETCH COMPLETE', self.output())

    def test_success_uses_default_limit_new_session_and_quoted_command(self):
        self.clock.side_effect = [100, 100, 102]
        self.assertIsNone(BUILDER.fetch_with_progress(*self.args, cwd=self.cwd))
        self.popen.assert_called_once_with(self.command, cwd=self.cwd, start_new_session=True)
        self.process.wait.assert_called_once_with(timeout=30)
        self.killpg.assert_not_called()
        self.popen.return_value.__exit__.assert_called_once_with(None, None, None)
        output = self.output()
        self.assertIn(f'FETCH COMMAND [open road, limit 1800s]: {shlex.join(self.command)}', output)
        self.assertIn('FETCH COMPLETE [open road]: 2s', output)
        self.assertNotIn('FETCH WAIT', output)

    def test_positive_override_caps_wait_to_remaining_deadline(self):
        os.environ['BAZEL_FETCH_TIMEOUT'] = '7'
        self.clock.side_effect = [100, 102, 103]
        BUILDER.fetch_with_progress(*self.args, cwd=self.cwd)
        self.process.wait.assert_called_once_with(timeout=5)
        self.assertIn('limit 7s', self.output())
        self.assertIn('FETCH COMPLETE [open road]: 3s', self.output())
        self.killpg.assert_not_called()

    def test_nonzero_exit_preserves_return_code_and_command(self):
        for code in (1, 37, -signal.SIGTERM):
            with self.subTest(code=code):
                self.print.reset_mock()
                self.process.wait.return_value = code
                with self.assertRaises(subprocess.CalledProcessError) as caught:
                    BUILDER.fetch_with_progress(*self.args, cwd=self.cwd)
                self.assertEqual(caught.exception.returncode, code)
                self.assertEqual(caught.exception.cmd, self.command)
                self.assertNotIn('FETCH COMPLETE', self.output())
                self.assertNotIn('FETCH TIMEOUT', self.output())
        self.killpg.assert_not_called()

    def test_heartbeats_are_bounded_and_do_not_claim_download_progress(self):
        os.environ['BAZEL_FETCH_TIMEOUT'] = '65'
        self.clock.side_effect = [100, 100, 130, 130, 160, 160, 163]
        self.process.wait.side_effect = [self.expired(30), self.expired(30), 0]
        BUILDER.fetch_with_progress(*self.args, cwd=self.cwd)
        self.assertEqual(self.process.wait.call_args_list, [
            mock.call(timeout=30), mock.call(timeout=30), mock.call(timeout=5)])
        output = self.output()
        for elapsed in (30, 60):
            self.assertIn(
                f'FETCH WAIT [open road]: {elapsed}s elapsed, limit 65s; '
                'process still running (not proof of download progress)', output)
        self.assertIn('FETCH COMPLETE [open road]: 63s', output)
        self.killpg.assert_not_called()

    def test_timeout_kills_child_group_even_when_parent_accepts_sigterm(self):
        self.prepare_timeout()
        self.assert_fetch_timeout()
        self.assertEqual(self.events.mock_calls, [
            mock.call.wait(timeout=1),
            mock.call.killpg(self.process.pid, signal.SIGTERM),
            mock.call.wait(timeout=10),
            mock.call.killpg(self.process.pid, signal.SIGKILL),
            mock.call.wait(),
        ])
        self.popen.return_value.__exit__.assert_called_once()

    def test_timeout_escalates_after_ten_second_grace_for_uncooperative_parent(self):
        self.prepare_timeout(cooperative=False)
        self.assert_fetch_timeout()
        self.assertEqual(self.events.mock_calls, [
            mock.call.wait(timeout=1),
            mock.call.killpg(self.process.pid, signal.SIGTERM),
            mock.call.wait(timeout=10),
            mock.call.killpg(self.process.pid, signal.SIGKILL),
            mock.call.wait(),
        ])

    def test_group_exiting_before_sigkill_still_reports_fetch_timeout(self):
        self.prepare_timeout()
        self.killpg.side_effect = [None, ProcessLookupError()]
        self.assert_fetch_timeout()
        self.assertEqual(self.process.wait.call_args_list[-1], mock.call())
        self.assertEqual(self.killpg.call_args_list, [
            mock.call(self.process.pid, signal.SIGTERM),
            mock.call(self.process.pid, signal.SIGKILL),
        ])

    def test_group_exiting_before_sigterm_still_reports_fetch_timeout(self):
        self.prepare_timeout()
        self.killpg.side_effect = ProcessLookupError()
        # A launcher can exit between wait() timing out and the first group signal.
        self.assert_fetch_timeout()
        self.assertEqual(self.killpg.call_args_list[0],
                         mock.call(self.process.pid, signal.SIGTERM))

    def test_deadline_already_elapsed_never_passes_negative_wait_timeout(self):
        self.prepare_timeout()
        self.clock.side_effect = [100, 103, 103]
        self.assert_fetch_timeout()
        self.assertEqual(self.process.wait.call_args_list[0], mock.call(timeout=0))
        self.assertIn('3s elapsed, limit 1s', self.output())

    def test_nonpositive_limits_are_rejected_before_starting_a_process(self):
        for value in ('0', '-1', '-1800'):
            with self.subTest(value=value):
                os.environ['BAZEL_FETCH_TIMEOUT'] = value
                with self.assertRaisesRegex(SystemExit, 'BAZEL_FETCH_TIMEOUT must be positive'):
                    BUILDER.fetch_with_progress(*self.args, cwd=self.cwd)
        self.popen.assert_not_called()
        self.clock.assert_not_called()
        self.killpg.assert_not_called()

    def test_noninteger_limits_are_rejected_before_starting_a_process(self):
        for value in ('', 'garbage', '1.5'):
            with self.subTest(value=value):
                os.environ['BAZEL_FETCH_TIMEOUT'] = value
                with self.assertRaises((ValueError, SystemExit)):
                    BUILDER.fetch_with_progress(*self.args, cwd=self.cwd)
        self.popen.assert_not_called()
        self.clock.assert_not_called()
        self.killpg.assert_not_called()


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'killpg'), 'requires POSIX process groups')
class FetchProgressIntegrationTest(unittest.TestCase):
    def test_real_timeout_cleans_up_descendant_ignoring_sigterm(self):
        # Reap orphaned descendants even when a container's PID 1 does not.
        restore_subreaper = None
        if sys.platform.startswith('linux'):
            libc = ctypes.CDLL(None, use_errno=True)
            previous = ctypes.c_int()
            if libc.prctl(37, ctypes.byref(previous), 0, 0, 0) != 0:
                self.skipTest('cannot query Linux child-subreaper setting')
            if libc.prctl(36, 1, 0, 0, 0) != 0:
                self.skipTest('cannot temporarily adopt test descendants')
            restore_subreaper = lambda: libc.prctl(36, previous.value, 0, 0, 0)

        child_code = '''
import os, signal, sys
fd = int(sys.argv[1])
signal.signal(signal.SIGTERM, signal.SIG_IGN)
signal.alarm(15)
os.write(fd, ('%s %s\\n' % (os.getpid(), os.getpgrp())).encode())
while True:
    signal.pause()
'''
        parent_code = '''
import os, signal, subprocess, sys
signal.alarm(15)
fd = int(sys.argv[1])
subprocess.Popen([sys.executable, '-c', sys.argv[2], str(fd)], pass_fds=(fd,))
os.close(fd)
while True:
    signal.pause()
'''
        read_fd = write_fd = None
        processes = []
        descendant_exited = False
        real_popen = subprocess.Popen
        try:
            read_fd, write_fd = os.pipe()

            def start_process(*args, **kwargs):
                nonlocal write_fd
                # Keep the real Popen/waits/signals; only add an observation pipe.
                process = real_popen(*args, pass_fds=(write_fd,), **kwargs)
                processes.append(process)
                os.close(write_fd)
                write_fd = None
                self.assertTrue(select.select([read_fd], [], [], 5)[0],
                                'descendant did not report readiness within 5s')
                ready = os.read(read_fd, 256).decode().strip().split()
                self.assertEqual(len(ready), 2, 'descendant exited before readiness')
                child_pid, group = map(int, ready)
                self.assertNotEqual(child_pid, process.pid)
                self.assertEqual(group, process.pid)
                self.assertNotEqual(group, os.getpgrp())
                return process

            output = io.StringIO()
            started = time.monotonic()
            with mock.patch.dict(os.environ, {'BAZEL_FETCH_TIMEOUT': '1'}), \
                 mock.patch.object(BUILDER.subprocess, 'Popen', side_effect=start_process), \
                 contextlib.redirect_stdout(output):
                with self.assertRaisesRegex(SystemExit, 'fetch timeout, not a compilation failure'):
                    BUILDER.fetch_with_progress(
                        sys.executable, '-c', parent_code, str(write_fd), child_code,
                        cwd=ROOT / 'tests/docker')
            self.assertEqual(len(processes), 1)
            self.assertEqual(processes[0].returncode, -signal.SIGTERM)
            # EOF proves the descendant died without signalling a possibly recycled PID.
            self.assertTrue(select.select([read_fd], [], [], 3)[0],
                            'SIGTERM-ignoring descendant survived fetch timeout')
            self.assertEqual(os.read(read_fd, 1), b'')
            descendant_exited = True
            self.assertLess(time.monotonic() - started, 8)
            self.assertIn('FETCH TIMEOUT [docker]', output.getvalue())
            self.assertNotIn('FETCH COMPLETE', output.getvalue())
        finally:
            try:
                for process in processes:
                    if not descendant_exited:
                        # This is exclusively the new session started by this test.
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    process.wait(timeout=5)
                    if restore_subreaper is not None:
                        # Negative PIDs select only this test's process group.
                        while True:
                            try:
                                os.waitpid(-process.pid, 0)
                            except ChildProcessError:
                                break
            finally:
                for fd in (read_fd, write_fd):
                    if fd is not None:
                        os.close(fd)
                if restore_subreaper is not None:
                    restore_subreaper()


if __name__ == '__main__':
    unittest.main()
