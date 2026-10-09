import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

import analog_factory
from analog_factory.agent_api import _context, _execute
from analog_factory.server import create_server
from analog_factory.service import Workspace


class OperationMcpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Workspace(Path(self.tmp.name))
        self.pid = self.workspace.projects.create('MCP fixture')['id']
        self.server = create_server(self.workspace, 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def exchange(self, messages, mode='confirm', approved=True):
        approvals = []
        def callback(name, arguments):
            return _execute(name, arguments, self.workspace, self.pid, mode, lambda event: None,
                            threading.Event(), lambda name, args: approvals.append((name, args)) or approved)
        token = self.workspace.operations.issue_context_token(_context(self.pid, mode=mode), callback)
        self.addCleanup(self.workspace.operations.revoke_context_token, token)
        env = dict(os.environ, AF_OPERATION_TOKEN=token, AF_OPERATION_URL=self.workspace.operation_url)
        script = Path(analog_factory.__file__).with_name('operation_mcp.py')
        result = subprocess.run([sys.executable, str(script)],
            input=''.join(json.dumps(item) + '\n' for item in messages),
            text=True, capture_output=True, env=env, timeout=10, cwd=self.tmp.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(token, result.stdout + result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines()], approvals

    def test_stdio_discovery_and_mutation_reach_real_workspace_approval(self):
        replies, approvals = self.exchange([
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2024-11-05'}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {
                'name': 'invoke_operation', 'arguments': {'operation_id': 'file.write',
                    'parameters': {'path': 'mcp.txt', 'content': 'Approved through shared operations'}}}}])
        self.assertEqual([reply['id'] for reply in replies], [1, 2, 3])
        self.assertIn('tools', replies[0]['result']['capabilities'])
        self.assertIn('invoke_operation', [tool['name'] for tool in replies[1]['result']['tools']])
        result = json.loads(replies[2]['result']['content'][0]['text'])
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.workspace.projects.read(self.pid, 'mcp.txt')['content'],
                         'Approved through shared operations')
        self.assertEqual(len(approvals), 1)

    def test_read_only_native_bridge_cannot_mutate_even_when_requested(self):
        replies, approvals = self.exchange([{'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
            'name': 'invoke_operation', 'arguments': {'operation_id': 'file.write',
                'parameters': {'path': 'blocked.txt', 'content': 'Denied'}}}}], mode='read-only')
        self.assertTrue(replies[0]['result']['isError'])
        self.assertFalse((self.workspace.projects.path(self.pid) / 'blocked.txt').exists())
        self.assertEqual(approvals, [])

    def test_tool_annotations_distinguish_queries_from_mutating_dispatch(self):
        replies, _ = self.exchange([{'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}])
        tools = {tool['name']: tool for tool in replies[0]['result']['tools']}
        for name in ('list_operations', 'describe_operation', 'get_operation_status', 'subscribe_events'):
            self.assertTrue(tools[name]['annotations']['readOnlyHint'])
            self.assertFalse(tools[name]['annotations']['destructiveHint'])
        for name in ('invoke_operation', 'cancel_operation'):
            self.assertFalse(tools[name]['annotations']['readOnlyHint'])
            self.assertTrue(tools[name]['annotations']['destructiveHint'])

    def test_stdio_discovery_continues_structured_registry_pages(self):
        def page(arguments):
            replies,_=self.exchange([{'jsonrpc':'2.0','id':1,'method':'tools/call',
                'params':{'name':'list_operations','arguments':arguments}}])
            self.assertFalse(replies[0]['result']['isError'])
            return json.loads(replies[0]['result']['content'][0]['text'])
        first=page({'limit':1})
        second=page({'cursor':first['next_cursor'],'limit':2})
        self.assertFalse(first['complete']);self.assertEqual(second['offset'],1)
        names=[row['operation_id'] for result in (first,second) for row in result['operations']]
        self.assertEqual(names,sorted(self.workspace.operations.operations)[:3])

    def test_bridge_transport_does_not_override_denied_workspace_approval(self):
        replies, approvals = self.exchange([{'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
            'name': 'invoke_operation', 'arguments': {'operation_id': 'file.write',
                'parameters': {'path': 'denied.txt', 'content': 'Never written'}}}}], approved=False)
        self.assertTrue(replies[0]['result']['isError'])
        self.assertEqual(len(approvals), 1)
        self.assertFalse((self.workspace.projects.path(self.pid) / 'denied.txt').exists())


if __name__ == '__main__':
    unittest.main()
