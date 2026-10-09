"""CLI lifetime and authenticated HTTP transport contracts."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from analog_factory.__main__ import main
from analog_factory.server import create_server
from analog_factory.service import Workspace


def slow_tool(root):
    executable = root / 'slow-simulator'
    executable.write_text('#!' + sys.executable + '\n'
        'import sys, time\n'
        'if "--version" in sys.argv or "-v" in sys.argv:\n'
        '    print("test process fixture 1.0")\n'
        'else:\n'
        '    time.sleep(1)\n'
        '    print("delay_probe = 1")\n')
    executable.chmod(0o700)
    return str(executable)


class LocalOperationCliTests(unittest.TestCase):
    def test_local_async_invocation_waits_before_its_process_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = Workspace(root / 'workspace')
            pid = workspace.projects.create('Local CLI task')['id']
            workspace.configure_project(pid, {'tools': {'ngspice': slow_tool(root)}})
            result = subprocess.run([sys.executable, '-m', 'analog_factory', '--workspace',
                str(workspace.root), 'operations', 'invoke', 'run.start', '--project', pid, '--confirm'],
                capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            response = json.loads(result.stdout)
            self.assertEqual(response['status'], 'succeeded', response)
            record = workspace.runner.get(workspace.projects.path(pid), response['data']['id'])
            self.assertEqual(record['status'], 'succeeded', record)
            self.assertEqual(record['metrics']['delay_probe'], 1)


class RemoteOperationCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = Workspace(self.root / 'server')
        self.server = create_server(self.workspace, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.environment = patch.dict(os.environ, {'AF_AGENT_TOKEN': 'private-cli-test-token',
            'AF_OPERATION_TOKEN': 'private-cli-test-token', 'AF_AGENT_PERMISSIONS': 'read,write,execute'})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def cli(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = main(['--workspace', str(self.root / 'unused-local'), 'operations', *arguments])
            except SystemExit as error:
                self.fail(f'Remote operation CLI rejected its arguments: {error}: {stderr.getvalue()}')
        self.assertNotIn('private-cli-test-token', stdout.getvalue() + stderr.getvalue())
        self.assertTrue(stdout.getvalue(), stderr.getvalue())
        return code, json.loads(stdout.getvalue())

    def test_remote_discovery_and_creation_use_server_and_environment_token(self):
        code, listed = self.cli('--url', self.url, 'list', '--module', 'project')
        self.assertEqual(code, 0)
        self.assertIn('project.create', [item['operation_id'] for item in listed['operations']])
        code, created = self.cli('invoke', 'project.create', '--url', self.url,
                                  '--parameters', '{"name":"Remote project"}', '--confirm')
        self.assertEqual(code, 0, created)
        self.assertEqual(self.workspace.projects.list()[0]['id'], created['data']['id'])
        self.assertFalse((self.root / 'unused-local').exists())

    def test_remote_read_only_and_unconfirmed_invocations_do_not_mutate(self):
        for options in (['--read-only', '--confirm'], []):
            code, result = self.cli('invoke', 'project.create', '--url', self.url,
                                    '--parameters', '{"name":"Denied"}', *options)
            self.assertEqual(code, 1)
            self.assertFalse(result['ok'])
        self.assertEqual(self.workspace.projects.list(), [])

    def test_remote_async_work_remains_owned_by_server_and_can_be_cancelled(self):
        pid = self.workspace.projects.create('Remote CLI task')['id']
        self.workspace.configure_project(pid, {'tools': {'ngspice': slow_tool(self.root)}})
        code, started = self.cli('invoke', 'run.start', '--url', self.url, '--project', pid, '--confirm')
        self.assertEqual(code, 0, started)
        self.assertIn(started['status'], ('queued', 'running'))
        self.addCleanup(self.workspace.runner.wait, self.workspace.projects.path(pid), started['data']['id'], 5)
        self.addCleanup(self.workspace.runner.cancel, self.workspace.projects.path(pid), started['data']['id'])
        other = self.workspace.projects.create('Unrelated project')['id']
        code, rejected = self.cli('status', started['task_id'], '--url', self.url, '--project', other)
        self.assertEqual(code, 1)
        self.assertEqual(rejected['error']['code'], 'PERMISSION_DENIED')
        code, active = self.cli('status', started['task_id'], '--url', self.url, '--project', pid)
        self.assertEqual(code, 0, active)
        self.assertEqual(active['data']['id'], started['data']['id'])
        code, cancelled = self.cli('cancel', started['task_id'], '--url', self.url, '--project', pid, '--confirm')
        self.assertEqual(code, 0, cancelled)
        record = self.workspace.runner.wait(self.workspace.projects.path(pid), started['data']['id'])
        self.assertEqual(record['status'], 'cancelled', record)


if __name__ == '__main__':
    unittest.main()
