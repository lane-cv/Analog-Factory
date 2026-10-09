import json
import os
import shutil
import time
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from analog_factory.server import create_server
from analog_factory.service import Workspace


class OperationHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Workspace(Path(self.tmp.name))
        self.server = create_server(self.workspace, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def request(self, path, value=None, method=None, headers=None):
        request = urllib.request.Request(self.url + path, data=None if value is None else json.dumps(value).encode(),
                                         method=method, headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response), response.headers

    def invoke(self, operation, parameters, **extra):
        return self.request('/api/operations/invoke', {'operation_id': operation, 'parameters': parameters, **extra})

    def test_discovery_legacy_parity_and_revision_conflict(self):
        code, discovery, _ = self.request('/api/operations?module=file')
        self.assertEqual(code, 200)
        self.assertIn('file.write', [item['operation_id'] for item in discovery['operations']])
        _, created, _ = self.invoke('project.create', {'name': 'HTTP parity'})
        pid = created['data']['id']
        path = f'/api/projects/{pid}/files?path=testbenches/pre.cir'
        _, source, headers = self.request(path)
        original = headers['ETag'].strip('"')
        _, written, _ = self.invoke('file.write', {'project_id': pid, 'path': 'testbenches/pre.cir',
                                                  'content': source['content'] + '\n* agent edit\n'}, expected_version=original)
        self.assertTrue(written['ok'], written)
        code, stale, _ = self.request(path, {'content': 'stale UI draft'}, method='PUT',
                                      headers={'X-AF-Client': 'test-browser', 'If-Match': original})
        self.assertEqual(code, 409)
        self.assertEqual(stale['code'], 'VERSION_CONFLICT')
        _, latest, _ = self.request(path)
        self.assertIn('* agent edit', latest['content'])
        _, history, _ = self.invoke('file.history', {'project_id': pid, 'path': 'testbenches/pre.cir'})
        self.assertEqual(len(history['data']), 1)

    def test_token_permissions_apply_to_new_and_legacy_routes(self):
        with patch.dict(os.environ, {'AF_AGENT_TOKEN': 'test-agent-credential', 'AF_AGENT_PERMISSIONS': 'read'}):
            headers = {'Authorization': 'Bearer test-agent-credential'}
            code, result, _ = self.request('/api/operations/invoke', {'operation_id': 'project.create',
                'parameters': {'name': 'Forbidden'}, 'confirmed': True}, headers=headers)
            self.assertEqual(code, 403)
            self.assertEqual(result['error']['code'], 'PERMISSION_DENIED')
            code, _, _ = self.request('/api/projects', {'name': 'Forbidden legacy'}, headers=headers)
            self.assertEqual(code, 403)
            code, result, _ = self.request('/api/operations/invoke', {'operation_id': 'project.list', 'parameters': {}}, headers=headers)
            self.assertEqual(code, 200)
            self.assertEqual(result['data'], [])

    def test_invalid_params_and_disconnected_frontend(self):
        code, result, _ = self.invoke('project.create', {'name': 123})
        self.assertEqual(code, 400)
        self.assertEqual(result['error']['code'], 'INVALID_ARGUMENT')
        code, result, _ = self.invoke('ui.workspace.navigate', {'client_id': 'a' * 32, 'tab': 'editor'})
        self.assertEqual(code, 404, result)
        self.assertEqual(result['error']['code'], 'FRONTEND_DISCONNECTED')

    def test_settings_secret_never_appears_in_registry_events(self):
        _, current, _ = self.invoke('settings.get', {})
        code, result, _ = self.invoke('settings.update', {'settings': {'provider': {
            'base_url': 'http://localhost:9/v1', 'model': 'test', 'api_key': 'never-show-this-value'}}},
            expected_version=current['version'])
        self.assertEqual(code, 200, result)
        _, events, _ = self.request('/api/operations/events')
        self.assertNotIn('never-show-this-value', json.dumps([result, events]))

    @unittest.skipUnless(shutil.which('ngspice'), 'A real ngspice executable is required')
    def test_external_agent_completes_real_eda_workflow(self):
        _, created, _ = self.invoke('project.create', {'name': 'External EDA workflow'})
        pid = created['data']['id']
        with patch.dict(os.environ, {'AF_AGENT_TOKEN': 'eda-agent-token',
            'AF_AGENT_PERMISSIONS': 'read,write,execute', 'AF_AGENT_PROJECT': pid}):
            headers = {'Authorization': 'Bearer eda-agent-token'}
            def agent(operation, parameters, **extra):
                return self.request('/api/operations/invoke', {'operation_id': operation,
                    'parameters': parameters, 'confirmed': True, **extra}, headers=headers)[1]
            source = agent('file.read', {'path': 'testbenches/pre.cir'})
            written = agent('file.write', {'path': 'testbenches/pre.cir',
                'content': source['data']['content'] + '\n* reviewed by external agent\n'},
                expected_version=source['version'], idempotency_key='eda-review')
            self.assertTrue(written['ok'], written)
            started = agent('run.start', {'kind': 'simulate'}, idempotency_key='real-baseline')
            self.assertTrue(started['ok'], started)
            self.assertEqual(agent('run.start', {'kind': 'simulate'}, idempotency_key='real-baseline')['task_id'], started['task_id'])
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                _, status, _ = self.request('/api/operations/status?task_id=' + started['task_id'], headers=headers)
                if status['status'] not in ('queued', 'running'):
                    break
                time.sleep(.05)
            self.assertEqual(status['status'], 'succeeded', status)
            self.assertGreater(status['data']['metrics']['vout_mean'], 0)
            logs = agent('run.log', {'run_id': started['data']['id']})
            self.assertIn('ngspice', logs['data']['content'].lower())
            waves = agent('run.waveforms', {'run_id': started['data']['id']})
            self.assertTrue(waves['ok'], waves)
            self.assertEqual(len(self.workspace.runs(pid)), 1)

    def test_missing_tool_reports_unavailable_evidence_and_prerequisite(self):
        _, created, _ = self.invoke('project.create', {'name': 'Missing tool'})
        pid = created['data']['id']
        configured = self.workspace.operations.invoke('project.configure', {'project_id': pid,
            'config': {'tools': {'ngspice': '/no/such/ngspice'}}}, expected_version=self.workspace.operations.version(pid))
        self.assertTrue(configured['ok'], configured)
        _, described, _ = self.request('/api/operations/run.start?project_id=' + pid)
        self.assertFalse(described['availability']['available'])
        self.assertIn('ngspice', str(described['availability']['reasons']))
        code, unavailable, _ = self.invoke('run.start', {'project_id': pid, 'kind': 'simulate'})
        self.assertEqual(code, 409)
        self.assertEqual(unavailable['error']['code'], 'UNAVAILABLE')
        self.assertEqual(unavailable['data']['status'], 'unavailable')
        self.assertTrue(unavailable['task_id'])
