"""Optional native Codex gate regression; all model responses are local fixtures.

This exercises ``codex exec`` tool routing and approval checks, not app-server
``tool/call`` (which bypasses the native approval gate). No account is required.
The Responses SSE event shapes follow Codex's 0.159.2 integration fixtures.
"""

import json
import os
from contextlib import ExitStack
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from analog_factory.agent_api import _context, _execute
from analog_factory.agent_cli import run_cli_turn
from analog_factory.server import create_server
from analog_factory.service import Workspace


@unittest.skipUnless(os.name == 'posix', 'Native drivers require POSIX process groups')
class CodexOperationBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.executable = shutil.which('codex')
        if not cls.executable:
            raise unittest.SkipTest('Installed Codex 0.159.2 is required for the native gate fixture')
        try:
            version = subprocess.run([cls.executable, '--version'], capture_output=True,
                                     text=True, timeout=5, check=True).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            raise unittest.SkipTest('Installed Codex version could not be verified')
        if version != 'codex-cli 0.159.2':
            raise unittest.SkipTest('Native SSE regression is pinned to Codex 0.159.2')

    def test_native_gate_allows_bridge_tools_but_workspace_still_approves_writes(self):
        with tempfile.TemporaryDirectory(prefix='af-native-mcp-') as directory, ExitStack() as cleanup:
            root = Path(directory)
            workspace = Workspace(root / 'workspace')
            cleanup.callback(workspace.agents.close)
            pid = workspace.projects.create('Native bridge fixture')['id']
            project = workspace.projects.path(pid)
            server = create_server(workspace, 0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            cleanup.callback(server.server_close)
            cleanup.callback(server.shutdown)
            approvals, reached, requests, events, incoming = [], [], [], [], []
            cancel = threading.Event()

            def approve(name, arguments):
                self.assertFalse((project / 'native-bridge.txt').exists(),
                                 'The mutation ran before workspace approval')
                approvals.append((name, arguments))
                return True

            def callback(name, arguments):
                reached.append(name)
                return _execute(name, arguments, workspace, pid, 'confirm', events.append,
                                cancel, approve)

            token = workspace.operations.issue_context_token(_context(pid, mode='confirm'), callback)
            cleanup.callback(workspace.operations.revoke_context_token, token)

            class Responses(BaseHTTPRequestHandler):
                def do_POST(self):
                    incoming.append((self.path, self.headers.get('Authorization') is not None))
                    body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                    request = json.loads(body)
                    requests.append(request)
                    step = len(requests)
                    if step <= 2:
                        name = 'list_operations' if step == 1 else 'invoke_operation'
                        arguments = {'module': 'file'} if step == 1 else {
                            'operation_id': 'file.write',
                            'parameters': {'path': 'native-bridge.txt', 'content': 'Approved native execution'}}
                        item = {'type': 'function_call', 'call_id': f'fixture-call-{step}',
                                'namespace': 'mcp__analog_factory', 'name': name,
                                'arguments': json.dumps(arguments)}
                    else:
                        item = {'type': 'message', 'id': 'fixture-message', 'role': 'assistant',
                                'content': [{'type': 'output_text', 'text': 'Fixture complete.'}]}
                    response = [
                        {'type': 'response.created', 'response': {'id': f'fixture-response-{step}'}},
                        {'type': 'response.output_item.done', 'item': item},
                        {'type': 'response.completed', 'response': {'id': f'fixture-response-{step}',
                         'usage': {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}}},
                    ]
                    data = ''.join('event: ' + item['type'] + '\ndata: ' + json.dumps(item) + '\n\n'
                                   for item in response).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

                def log_message(self, *args):
                    pass

            provider = ThreadingHTTPServer(('127.0.0.1', 0), Responses)
            threading.Thread(target=provider.serve_forever, daemon=True).start()
            cleanup.callback(provider.server_close)
            cleanup.callback(provider.shutdown)
            native_home = root / 'codex-home'
            native_home.mkdir()
            provider_url = f'http://127.0.0.1:{provider.server_port}/v1'
            (native_home / 'config.toml').write_text(
                'model_provider = "fixture"\n'
                'check_for_update_on_startup = false\n'
                '[model_providers.fixture]\n'
                'name = "Local test responses"\n'
                f'base_url = {json.dumps(provider_url)}\n'
                'wire_api = "responses"\nrequires_openai_auth = false\n'
                'supports_websockets = false\n'
                '[analytics]\nenabled = false\n')
            # Isolate native configuration and credentials; the fixture endpoint
            # is the only configured model provider and uses no authentication.
            environment = {key: value for key, value in os.environ.items()
                           if key in ('PATH', 'HOME', 'USER', 'LANG', 'LC_ALL', 'TMPDIR')}
            environment['CODEX_HOME'] = str(native_home)
            with patch.dict(os.environ, environment, clear=True):
                result = run_cli_turn('codex', self.executable, project, 'gpt-5.1-codex',
                    'workspace-write', None, 'Use the shared operations to inspect and write the fixture.',
                    events.append, cancel, timeout=35, operation_bridge={
                        'url': workspace.operation_url, 'token': token})

            outputs = [item for request in requests[1:] for item in request.get('input', [])
                       if item.get('type') == 'function_call_output']
            self.assertEqual(result['reply'], 'Fixture complete.')
            self.assertEqual(reached, ['list_operations', 'invoke_operation'],
                             'Native gate blocked the bridge: ' + json.dumps(outputs)[-6000:])
            self.assertEqual(len(approvals), 1)
            self.assertEqual(approvals[0][0], 'invoke_operation')
            self.assertEqual(approvals[0][1]['operation_id'], 'file.write')
            self.assertEqual((project / 'native-bridge.txt').read_text(), 'Approved native execution')
            self.assertEqual(len(requests), 3)
            self.assertEqual(incoming, [('/v1/responses', False)] * 3)
            self.assertIn('list_operations', json.dumps(requests[0].get('tools', [])))
            self.assertTrue(any(item.get('call_id') == 'fixture-call-1' for item in outputs))
            self.assertTrue(any(item.get('call_id') == 'fixture-call-2' for item in outputs))
            self.assertNotIn('MCP tool call requires approval', json.dumps(outputs))
            self.assertNotIn(token, json.dumps([events, requests, result]))


if __name__ == '__main__':
    unittest.main()
