"""Public approval routes select the official Codex reviewer without a Model API."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from analog_factory.server import create_server
from analog_factory.service import Workspace


class CodexApprovalOperationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Workspace(Path(temporary.name))
        self.addCleanup(self.workspace.operations.close)
        self.addCleanup(self.workspace.agents.close)
        # Only discover a local executable here; no model inference is performed.
        self.workspace.save_settings({'tools': {'codex': '/bin/true'}})
        self.pid = self.workspace.projects.create('Official approval routes')['id']
        self.session = self.workspace.agents.create(self.pid,
            {'backend': 'codex', 'mode': 'workspace-write'})
        self.server = create_server(self.workspace, port=0, desktop_url='')
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, data=None):
        body = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(self.base + path, data=body,
            headers={'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=3) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error)

    def session_path(self, suffix):
        return f'/api/projects/{self.pid}/agent/sessions/{self.session["id"]}' + suffix

    def test_codex_can_enable_official_review_without_a_project_model_api(self):
        status, enabled = self.request(self.session_path('/auto-approval'), {'enabled': True})
        self.assertEqual(status, 200, enabled)
        self.assertTrue(enabled['auto_approval'])
        self.assertEqual(enabled['mode'], 'workspace-write')
        status, response = self.request('/api/agents?project_id=' + self.pid)
        self.assertEqual(status, 200)
        backends = {item['id']: item for item in response['backends']}
        capability = backends['codex']['approval_review']
        self.assertTrue(capability['available'])
        self.assertEqual(capability['provider'], 'codex_official')
        self.assertFalse(capability['single_action'])
        self.assertFalse(capability['runtime_toggle'])
        self.assertFalse(backends['api']['approval_review']['available'])
        status, disabled = self.request(self.session_path('/auto-approval'), {'enabled': False})
        self.assertEqual(status, 200, disabled)
        self.assertFalse(disabled['auto_approval'])

    def test_codex_single_action_route_explains_official_api_limitation(self):
        status, response = self.request(self.session_path('/approval/review'), {'approval_id': 'a' * 32})
        self.assertEqual(status, 400, response)
        self.assertIn('Codex', json.dumps(response))
        self.assertIn('official', json.dumps(response).lower())


if __name__ == '__main__':
    unittest.main()
