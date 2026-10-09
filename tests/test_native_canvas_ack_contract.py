"""Actual browser-session acknowledgments must survive public state redaction."""
import tempfile
import threading
import time
import unittest

from analog_factory.service import Workspace
from analog_factory.operations import InvocationContext


class NativeCanvasAcknowledgmentTests(unittest.TestCase):
    def test_native_ui_permissions_and_confirmation_reject_before_browser_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(directory)
            registry = workspace.operations
            self.addCleanup(workspace.agents.close)
            self.addCleanup(registry.close)
            pid = workspace.projects.create('Native nested permissions')['id']
            connected = registry.sessions.connect({'project_id': pid})
            cases = [
                ('move', {'instance_id': 'R1'}, ('read', 'ui'), True, 'PERMISSION_DENIED'),
                ('move', {'instance_id': 'R1'}, ('read', 'execute', 'ui'), True, 'PERMISSION_DENIED'),
                ('save', {}, ('read', 'execute', 'ui'), True, 'PERMISSION_DENIED'),
                ('start', {}, ('read', 'ui'), True, 'PERMISSION_DENIED'),
                ('select', {'instance_id': 'R1'}, ('read', 'execute', 'ui'), False, 'CONFIRMATION_REQUIRED'),
            ]
            for name, arguments, permissions, confirmed, code in cases:
                with self.subTest(name=name, permissions=permissions):
                    context = InvocationContext(project_id=pid, source='external-agent',
                        permissions=frozenset(permissions), confirmed=confirmed)
                    result = registry.invoke('ui.native_canvas.' + name,
                        {'client_id': connected['client_id'], **arguments}, context=context,
                        expected_version=registry.version(pid))
                    self.assertFalse(result['ok'])
                    self.assertEqual(result['error']['code'], code)
                    self.assertEqual(registry.sessions._get(connected['client_id'])['commands'], {})

    def test_native_refresh_ack_remains_valid_after_geometry_token_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(directory)
            registry = workspace.operations
            self.addCleanup(workspace.agents.close)
            self.addCleanup(registry.close)
            pid = workspace.projects.create('Native acknowledgment contract')['id']
            native = {'project_id': pid, 'run_id': 'a' * 32, 'session_id': 'b' * 32,
                'tool': 'magic', 'source': 'layout/top.mag', 'native_revision': 1,
                'modified': False, 'instances': [], 'selected': [], 'box': [0, 0, 10, 10],
                'box_token': '0,0,10,10', 'layers': ['metal1'], 'connected': True,
                'source_sha256': 'c' * 64}
            panel = {'open': True, 'project_id': pid, 'busy': False,
                'options': {'tool': 'magic', 'source': 'layout/top.mag'},
                'source_version': registry.version(pid), 'run_id': 'a' * 32,
                'session_id': 'b' * 32, 'native': native, 'unknown_outcome': False,
                'needs_inspection': False, 'error': ''}
            state = {'project_id': pid, 'native_canvas': panel}
            connected = registry.sessions.connect(state)
            results = []
            thread = threading.Thread(target=lambda: results.append(registry.invoke(
                'ui.native_canvas.refresh', {'client_id': connected['client_id']})))
            thread.start()
            try:
                deadline = time.monotonic() + 2
                while True:
                    packet = registry.sessions.poll(connected['client_id'], connected['token'], state)
                    if packet['commands'] or time.monotonic() >= deadline:
                        break
                    time.sleep(.005)
                self.assertEqual(len(packet['commands']), 1)
                registry.sessions.ack(connected['client_id'], connected['token'],
                    packet['commands'][0]['command_id'], True, panel, None, state)
            finally:
                thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0]['ok'], results[0])
            public = results[0]['data']['state']['native_canvas']['native']
            self.assertEqual(public['box'], [0, 0, 10, 10])
            self.assertNotIn('box_token', public, 'Geometry must not bypass the shared credential filter')


if __name__ == '__main__':
    unittest.main()
