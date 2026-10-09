"""Compact task reads retain authoritative state and full evidence access."""
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from urllib.error import HTTPError

from analog_factory.agent_api import _arguments, _execute
from analog_factory.operations import InvocationContext
from analog_factory.service import Workspace
from analog_factory.server import create_server


class OperationStatusSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Workspace(Path(self.temp.name))
        self.addCleanup(self.workspace.operations.close)
        self.addCleanup(self.workspace.agents.close)
        self.pid = self.workspace.projects.create('Summary fixture')['id']
        self.other = self.workspace.projects.create('Other fixture')['id']
        self.registry = self.workspace.operations
        self.context = InvocationContext(source='cli')
        path = self.workspace.projects.path(self.pid)
        run = self.workspace.runner.start(path, 'simulate', [sys.executable, '-c', 'print("fixture_metric=2")'],
            timeout=5, metadata={'pdk': {'configuration_hashes': {
                f'/public/pdk/model-{index}.spice': 'a' * 64 for index in range(278)}}})
        self.run = self.workspace.runner.wait(path, run['id'], timeout=6)
        self.task_id = f'run:{self.pid}:{run["id"]}'

    def invoke(self, view=None, context=None):
        params = {'project_id': self.pid, 'run_id': self.run['id']}
        if view is not None:
            params['view'] = view
        return self.registry.invoke('run.get', params, context or self.context)

    def test_summary_omits_large_metadata_but_retains_state_and_full_read_reference(self):
        full = self.invoke()['data']
        result = self.invoke('summary')
        self.assertTrue(result['ok'], result)
        summary = result['data']
        self.assertLess(len(json.dumps(summary).encode()), 16384)
        self.assertLess(len(json.dumps(summary).encode()), len(json.dumps(full).encode()) / 4)
        for key in ('id', 'kind', 'status', 'exit_code', 'metrics', 'stale'):
            self.assertEqual(summary[key], full[key])
        self.assertEqual(summary['verification']['status'], full['verification']['status'])
        self.assertEqual(len(summary['record_sha256']), 64)
        self.assertNotIn('metadata', summary)
        self.assertNotIn('source_hashes', summary)
        reference = summary['full_record']
        full_again = self.registry.invoke(reference['operation_id'], reference['parameters'], self.context)
        self.assertEqual(full_again['data'], full)
        self.assertTrue(summary['artifact_operations'])

    def test_task_summary_keeps_status_and_same_project_scope(self):
        full = self.registry.get_operation_status(self.task_id, self.context)
        summary = self.registry.get_operation_status(self.task_id, self.context, view='summary')
        self.assertEqual(summary['status'], full['status'])
        self.assertEqual(summary['data']['metrics'], full['data']['metrics'])
        self.assertLess(len(json.dumps(summary).encode()), 16384)
        wrong = InvocationContext(principal='outside', source='agent', project_id=self.other)
        for read in (self.invoke('summary', wrong), self.registry.get_operation_status(self.task_id, wrong, view='summary')):
            self.assertFalse(read['ok'])
            self.assertEqual(read['error']['code'], 'PERMISSION_DENIED')

    def test_summary_preserves_staleness_after_actual_source_edit(self):
        self.workspace.projects.write(self.pid, 'netlists/new.spice', '* changed\n')
        self.assertTrue(self.invoke()['data']['stale'])
        self.assertTrue(self.invoke('summary')['data']['stale'])

    def test_summary_preserves_electrical_failure_of_successfully_executed_job(self):
        root = self.workspace.projects.path(self.pid)
        job = self.workspace.jobs.start(root, 'ota-characterization', lambda *args: {
            'status': 'succeeded', 'electrical_status': 'fail', 'termination': 'completed',
            'completed_runs': 10, 'scenario_count': 10,
            'qualification': {'qualified': False, 'reason': 'x' * 20000},
            'groups': [{'assessment': {'status': 'fail', 'checks': {'gain_db_min': {'value': 42, 'limit': 45}}}}]})
        job = self.workspace.jobs.wait(root, job['id'], timeout=5)
        self.assertEqual(job['status'], 'succeeded')
        result = self.registry.get_operation_status(f'job:{self.pid}:{job["id"]}', self.context, view='summary')
        self.assertTrue(result['ok'], result)
        summary = result['data']
        self.assertEqual(summary['status'], 'succeeded')
        self.assertEqual(summary['electrical_status'], 'fail')
        self.assertEqual(summary['termination'], 'completed')
        self.assertEqual(summary['completed_runs'], 10)
        self.assertEqual(summary['scenario_count'], 10)
        self.assertIs(summary['qualification']['qualified'], False)
        self.assertLess(len(json.dumps(result).encode()), 16384)

    def test_invalid_view_is_rejected_in_registry_and_shared_tool_schema(self):
        self.assertEqual(self.invoke('invented')['error']['code'], 'INVALID_ARGUMENT')
        result = self.registry.get_operation_status(self.task_id, self.context, view='invented')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'INVALID_ARGUMENT')
        with self.assertRaises(ValueError):
            _arguments('get_operation_status', json.dumps({'task_id': self.task_id, 'view': 'invented'}))

    def test_shared_agent_tool_defaults_to_compact_status_and_allows_explicit_full(self):
        for arguments in ({'task_id': self.task_id}, {'task_id': self.task_id, 'view': 'full'}):
            checked = _arguments('get_operation_status', json.dumps(arguments))
            result = _execute('get_operation_status', checked, self.workspace, self.pid, 'read-only',
                              lambda event: None, threading.Event(), lambda *args: False)
            self.assertTrue(result['ok'])
            self.assertEqual('metadata' in result['data'], arguments.get('view') == 'full')

    def test_projection_is_bounded_and_never_renames_long_metrics_or_artifact_paths(self):
        path = self.workspace.projects.path(self.pid) / '.af/runs' / self.run['id'] / 'manifest.json'
        record = json.loads(path.read_text())
        record.update(error='错误' * 10000, metrics={f'metric_{index}': index for index in range(500)},
                      artifact_hashes={f'artifact_{index}': 'a' * 64 for index in range(500)})
        record['metrics']['long_' + '电' * 100] = 7
        record['artifact_hashes']['long_' + '电' * 100] = 'b' * 64
        path.write_text(json.dumps(record))
        summary = self.invoke('summary')['data']
        self.assertLess(len(json.dumps(summary).encode()), 16384)
        self.assertEqual(len(summary['metrics']), 32)
        self.assertEqual(len(summary['artifact_operations']), 8)
        self.assertNotIn('\ufffd', summary['error'])
        for metric in summary['metrics']:
            self.assertIn(metric, record['metrics'])
        for reference in summary['artifact_operations']:
            self.assertIn(reference['parameters']['path'], record['artifact_hashes'])

    def test_http_mcp_and_cli_share_compact_status_and_keep_full_default(self):
        server = create_server(self.workspace, 0, desktop_url='')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = self.workspace.operation_url
        for route in (f'/api/operations/status?task_id={self.task_id}',
                      f'/api/projects/{self.pid}/runs/{self.run["id"]}?'):
            with urllib.request.urlopen(url + route + '&view=summary') as response:
                result = json.load(response)
            data = result['data'] if 'data' in result else result
            self.assertTrue(data['summary'])
            with urllib.request.urlopen(url + route) as response:
                result = json.load(response)
            data = result['data'] if 'data' in result else result
            self.assertIn('metadata', data)
        with self.assertRaises(HTTPError) as error:
            urllib.request.urlopen(url + f'/api/operations/status?task_id={self.task_id}&view=invalid')
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
        cli = subprocess.run([sys.executable, '-m', 'analog_factory', 'operations', 'status', self.task_id,
                              '--url', url, '--view', 'summary', '--project', self.pid],
                              capture_output=True, text=True, timeout=10)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertTrue(json.loads(cli.stdout)['data']['summary'])
        def bridge(name, arguments):
            return _execute(name, _arguments(name, json.dumps(arguments)), self.workspace, self.pid,
                            'read-only', lambda event: None, threading.Event(), lambda *args: False)
        token = self.registry.issue_context_token(InvocationContext(project_id=self.pid, permissions=frozenset({'read'})), bridge)
        self.addCleanup(self.registry.revoke_context_token, token)
        environment = {**os.environ, 'AF_OPERATION_URL': url, 'AF_OPERATION_TOKEN': token}
        messages = [{'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
                        'name': 'get_operation_status', 'arguments': {'task_id': self.task_id}}}]
        mcp = subprocess.run([sys.executable, '-m', 'analog_factory.operation_mcp'],
            input=''.join(json.dumps(item) + '\n' for item in messages), env=environment,
            capture_output=True, text=True, timeout=10)
        self.assertEqual(mcp.returncode, 0, mcp.stderr)
        self.assertNotIn(token, mcp.stdout + mcp.stderr)
        replies = [json.loads(line) for line in mcp.stdout.splitlines()]
        tool = next(tool for tool in replies[0]['result']['tools'] if tool['name'] == 'get_operation_status')
        self.assertEqual(tool['inputSchema']['properties']['view']['default'], 'summary')
        result = json.loads(replies[1]['result']['content'][0]['text'])
        self.assertTrue(result['data']['summary'])
        self.assertEqual(result['data']['status'], self.run['status'])


if __name__ == '__main__':
    unittest.main()
