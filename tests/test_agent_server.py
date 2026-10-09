import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from analog_factory.server import create_server
from analog_factory.service import Workspace


class AgentHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Workspace(Path(self.temp.name))
        self.workspace.save_settings({'provider': {'model': 'fixture', 'base_url': 'http://127.0.0.1:1/v1'}})
        self.pid = self.workspace.projects.create('HTTP agent')['id']
        self.server = create_server(self.workspace, port=0, desktop_url='')
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.path = f'/api/projects/{self.pid}/agent/sessions'

    def request(self, path, value=None, headers=None, method=None):
        request = urllib.request.Request(self.url + path,
            data=None if value is None else json.dumps(value).encode(),
            headers={'Content-Type': 'application/json', **(headers or {})}, method=method)
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.load(response)

    def test_session_routes_create_run_read_list_and_reject_foreign_project(self):
        def driver(*args):
            args[6]({'type': 'message', 'text': '<script>untrusted output</script>'})
            return {'reply': 'fixture response', 'history': []}
        status, choices = self.request('/api/agents')
        self.assertEqual([b['id'] for b in choices['backends']], ['api', 'codex', 'claude'])
        status, session = self.request(self.path, {'backend': 'api'})
        self.assertEqual(status, 201)
        endpoint = self.path + '/' + session['id']
        with patch('analog_factory.agents.run_api_turn', driver):
            self.assertEqual(self.request(endpoint + '/messages', {'message': 'Inspect circuit'})[0], 202)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                result = self.request(endpoint)[1]
                if result['status'] == 'succeeded':
                    break
                time.sleep(.01)
        self.assertEqual(result['turns'][0]['reply'], 'fixture response')
        self.assertEqual(len(self.request(self.path)[1]), 1)
        self.assertNotIn('history', result)
        other = self.workspace.projects.create('other')['id']
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(endpoint.replace(self.pid, other))
        self.assertEqual(error.exception.code, 404)
        error.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(endpoint + '/messages', {'message': 'foreign'}, {'Origin': 'https://other.invalid'})
        self.assertEqual(error.exception.code, 403)
        error.exception.close()

    def test_cli_model_catalog_uses_project_executable_and_refresh(self):
        self.workspace.save_settings({'tools': {'codex': '/configured/codex'}})
        expected = {'backend': 'codex', 'models': [{'id': 'catalog-model', 'label': 'Catalog model', 'fast_mode': True}], 'source': 'cli', 'fast_mode': {'supported': True}}
        with patch('analog_factory.operation_catalog.model_catalog', return_value=expected) as discover, patch('shutil.which', return_value='/configured/codex'):
            status, result = self.request(f'/api/agents/models?backend=codex&project_id={self.pid}&refresh=1')
        self.assertEqual(status, 200)
        self.assertEqual(result, expected)
        discover.assert_called_once_with('codex', '/configured/codex', self.workspace.projects.path(self.pid), refresh=True)

    def test_cli_model_catalog_rejects_invalid_backend_project_and_origin(self):
        for suffix, headers, status in [
            ('?backend=api', {}, 400),
            ('?backend=codex&project_id=../escape', {}, 400),
            (f'?backend=codex&project_id={self.pid}', {'Origin': 'https://foreign.invalid'}, 403),
        ]:
            with self.subTest(suffix=suffix), self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/agents/models' + suffix, headers=headers)
            self.assertEqual(error.exception.code, status)
            error.exception.close()
    def test_invalid_mode_and_stale_approval_return_client_errors(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(self.path, {'backend': 'api', 'mode': 'workspace-write'})
        self.assertEqual(error.exception.code, 400)
        error.exception.close()

    def test_session_rename_search_and_delete_http_contract(self):
        session = self.request(self.path, {'backend': 'api'})[1]
        endpoint = self.path + '/' + session['id']
        self.assertEqual(self.request(endpoint, {'title': 'Reviewed amplifier'}, method='PUT')[1]['title'],
                         'Reviewed amplifier')
        self.assertEqual([item['id'] for item in self.request(self.path + '?q=AMPLIFIER')[1]], [session['id']])
        self.assertEqual(self.request(self.path + '?q=no-match')[1], [])
        for payload in ({'title': ''}, {'title': 'ok', 'backend': 'codex'}):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request(endpoint, payload, method='PUT')
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
        for method, body in [('PUT', {'title': 'Foreign origin'}), ('DELETE', None)]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request(endpoint, body, {'Origin': 'https://other.invalid'}, method=method)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
        deleted = self.request(endpoint, method='DELETE')[1]
        self.assertEqual(deleted, {'deleted': True, 'id': session['id']})
        self.assertEqual(self.request(self.path)[1], [])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(endpoint)
        self.assertEqual(error.exception.code, 404)
        error.exception.close()

    def test_session_metadata_routes_reject_a_cancelled_worker_still_draining(self):
        entered, release = threading.Event(), threading.Event()
        def driver(*args):
            entered.set()
            release.wait(3)
            return {'reply': 'late', 'history': []}
        session = self.request(self.path, {'backend': 'api'})[1]
        endpoint = self.path + '/' + session['id']
        with patch('analog_factory.agents.run_api_turn', driver):
            self.request(endpoint + '/messages', {'message': 'Wait'})
            try:
                self.assertTrue(entered.wait(2))
                self.request(endpoint + '/cancel', {})
                for method, body in [('PUT', {'title': 'Too soon'}), ('DELETE', None)]:
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        self.request(endpoint, body, method=method)
                    self.assertEqual(error.exception.code, 409)
                    error.exception.close()
            finally:
                release.set()
                self.workspace.agents.close()

    def test_real_provider_tool_call_reaches_http_approval_and_revision(self):
        calls = []
        class Endpoint(BaseHTTPRequestHandler):
            def do_POST(inner):
                calls.append(json.loads(inner.rfile.read(int(inner.headers['Content-Length']))))
                message = {'role': 'assistant', 'content': 'Saved after approval.'} if len(calls) > 1 else {
                    'role': 'assistant', 'content': None, 'tool_calls': [
                        {'id': 'write-1', 'type': 'function', 'function': {'name': 'write_file',
                         'arguments': json.dumps({'path': 'netlists/agent.txt', 'content': 'new text'})}}]}
                data = json.dumps({'choices': [{'message': message}]}).encode()
                inner.send_response(200)
                inner.send_header('Content-Length', str(len(data)))
                inner.end_headers()
                inner.wfile.write(data)
            def log_message(self, *args):
                pass
        provider = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
        threading.Thread(target=provider.serve_forever, daemon=True).start()
        self.addCleanup(provider.server_close)
        self.addCleanup(provider.shutdown)
        self.workspace.save_settings({'provider': {'base_url': f'http://127.0.0.1:{provider.server_port}'}})
        self.workspace.projects.write(self.pid, 'netlists/agent.txt', 'old text')
        session = self.request(self.path, {'backend': 'api', 'mode': 'confirm'})[1]
        endpoint = self.path + '/' + session['id']
        self.request(endpoint + '/messages', {'message': 'Edit my file'})
        def wait(status):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                value = self.request(endpoint)[1]
                if value['status'] == status:
                    return value
                time.sleep(.01)
            self.fail(f'Did not reach {status}: {value}')
        pending = wait('waiting_approval')['pending_approval']
        self.assertIn('-old text', pending['arguments']['diff'])
        self.assertEqual(self.workspace.projects.read(self.pid, 'netlists/agent.txt')['content'], 'old text')
        self.request(endpoint + '/approval', {'approval_id': pending['id'], 'approved': True})
        complete = wait('succeeded')
        self.assertEqual(complete['turns'][0]['reply'], 'Saved after approval.')
        self.assertEqual(self.workspace.projects.read(self.pid, 'netlists/agent.txt')['content'], 'new text')
        self.assertEqual(len(self.workspace.projects.history(self.pid, 'netlists/agent.txt')), 1)
        self.assertEqual(calls[1]['messages'][-1]['role'], 'tool')
        sid = self.request(self.path, {'backend': 'api'})[1]['id']
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(self.path + '/' + sid + '/approval', {'approval_id': 'missing', 'approved': True})
        self.assertEqual(error.exception.code, 400)
        error.exception.close()

    def test_server_close_cancels_pending_agent(self):
        entered = threading.Event()
        def driver(*args):
            entered.set()
            args[7].wait(4)
            raise InterruptedError('shutdown')
        sid = self.request(self.path, {'backend': 'api'})[1]['id']
        with patch('analog_factory.agents.run_api_turn', driver):
            self.request(self.path + '/' + sid + '/messages', {'message': 'wait'})
            self.assertTrue(entered.wait(2))
            self.server.shutdown()
            self.server.server_close()
        self.assertEqual(self.workspace.agents.get(self.pid, sid)['status'], 'cancelled')
