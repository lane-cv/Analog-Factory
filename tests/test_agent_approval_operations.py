"""Approval delegation is a local-owner capability on every adapter."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from analog_factory.operations import InvocationContext, PERMISSIONS
from analog_factory.server import create_server
from analog_factory.service import Workspace
from tests import test_agent_api as agent_api_fixtures
from tests.test_agent_api import call, completion


class ApprovalOperationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.addCleanup(self.workspace.agents.close)
        self.addCleanup(self.workspace.operations.close)
        self.workspace.save_settings({'provider': {'base_url': 'http://127.0.0.1:1/v1', 'model': 'review-fixture'}})
        self.pid = self.workspace.projects.create('Approval operations')['id']
        self.session = self.workspace.agents.create(self.pid, {'mode': 'confirm'})
        self.registry = self.workspace.operations

    def parameters(self, **extra):
        return {'project_id': self.pid, 'session_id': self.session['id'], **extra}

    def test_owner_can_toggle_policy_and_disabling_does_not_change_execution_mode(self):
        for enabled in (True, False):
            result = self.registry.invoke('agent.session.set_auto_approval', self.parameters(enabled=enabled))
            self.assertTrue(result['ok'], result)
            self.assertIs(result['data']['auto_approval'], enabled)
            self.assertEqual(result['data']['mode'], 'confirm')

    def test_delegation_requires_local_owner_on_backend_and_browser_operations(self):
        contexts = [
            InvocationContext(principal='local-owner', source=source, permissions=PERMISSIONS)
            for source in ('agent', 'external-agent', 'workflow', 'evaluation')
        ] + [InvocationContext(principal='other-user', source='ui', permissions=PERMISSIONS),
             InvocationContext(permissions=PERMISSIONS - {'approve'})]
        browser = self.registry.sessions.connect({'project_id': self.pid})
        cases = [
            ('agent.session.review_approval', self.parameters(approval_id='a' * 32)),
            ('agent.session.set_auto_approval', self.parameters(enabled=True)),
            ('ui.agent.review_approval', {'client_id': browser['client_id'], 'approval_id': 'a' * 32}),
            ('ui.agent.set_auto_approval', {'client_id': browser['client_id'], 'enabled': True}),
            ('ui.agent.approve', {'client_id': browser['client_id'], 'approval_id': 'a' * 32, 'approved': True}),
        ]
        for context in contexts:
            for operation, parameters in cases:
                with self.subTest(operation=operation, source=context.source, principal=context.principal):
                    result = self.registry.invoke(operation, parameters, context)
                    self.assertFalse(result['ok'])
                    self.assertEqual(result['error']['code'], 'PERMISSION_DENIED', result)
        self.assertFalse(self.workspace.agents.get(self.pid, self.session['id']).get('auto_approval', False))

    def test_policy_rejects_non_booleans_and_cross_project_authority(self):
        for value in ('true', 1, None):
            result = self.registry.invoke('agent.session.set_auto_approval', self.parameters(enabled=value))
            self.assertFalse(result['ok'])
            self.assertEqual(result['error']['code'], 'INVALID_ARGUMENT', result)
        other = self.workspace.projects.create('Other')['id']
        result = self.registry.invoke('agent.session.set_auto_approval', self.parameters(enabled=True),
                                      InvocationContext(project_id=other))
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'PERMISSION_DENIED', result)

    def test_workflow_cannot_send_into_a_human_delegated_session(self):
        enabled = self.registry.invoke('agent.session.set_auto_approval', self.parameters(enabled=True))
        self.assertTrue(enabled['ok'], enabled)
        caller = InvocationContext(principal='workflow-runner', source='workflow',
                                   permissions=frozenset({'execute'}), project_id=self.pid)
        result = self.registry.invoke('agent.session.send', self.parameters(message='Write a file on my behalf.'), caller)
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error']['code'], 'PERMISSION_DENIED', result)
        self.assertEqual(self.workspace.agents.get(self.pid, self.session['id'])['turns'], [])

    def test_frontend_automation_preserves_untrusted_message_origin(self):
        browser = self.registry.sessions.connect({'project_id': self.pid, 'agent': {'auto_approval': False}})
        caller = InvocationContext(principal='workflow-runner', source='workflow',
                                   permissions=frozenset({'ui', 'write', 'execute'}), project_id=self.pid)
        result = []
        worker = threading.Thread(target=lambda: result.append(self.registry.invoke('ui.agent.send',
                                  {'client_id': browser['client_id']}, caller)), daemon=True)
        worker.start()
        deadline = time.monotonic() + 3
        commands = []
        while time.monotonic() < deadline and not commands:
            commands = self.registry.sessions.poll(browser['client_id'], browser['token'],
                         {'project_id': self.pid, 'agent': {'auto_approval': False}})['commands']
            if not commands:
                time.sleep(.01)
        self.assertEqual(len(commands), 1, result)
        command = commands[0]
        self.registry.sessions.ack(browser['client_id'], browser['token'], command['command_id'], True,
                                   data={}, state={'project_id': self.pid})
        worker.join(3)
        self.assertEqual(command['parameters'], {'untrusted_request': True})
        self.assertTrue(result[0]['ok'], result)

    def test_old_browser_cannot_drop_automation_provenance_during_upgrade(self):
        browser = self.registry.sessions.connect({'project_id': self.pid, 'agent': {'status': 'idle'}})
        self.registry.sessions.timeout = .01
        caller = InvocationContext(principal='workflow-runner', source='workflow',
                                   permissions=frozenset({'ui', 'write', 'execute'}), project_id=self.pid)
        result = self.registry.invoke('ui.agent.send', {'client_id': browser['client_id']}, caller)
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error']['code'], 'PERMISSION_DENIED', result)
        self.assertIn('refresh', result['error']['message'].lower())

    def test_delegation_does_not_invalidate_the_pending_project_version(self):
        before = self.registry.version(self.pid)
        result = self.registry.invoke('agent.session.set_auto_approval', self.parameters(enabled=True))
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.registry.version(self.pid), before)

    def test_review_operation_uses_independent_model_http_before_releasing_the_action(self):
        # Exercise the real provider transport, model/tool loop and operation
        # writer against a deterministic local HTTP model, without paid inference.
        self.requests = []
        provider = agent_api_fixtures.ApiAgentTests.endpoint(self, [
            completion(calls=[call('write_file', {'path': 'review-http.txt', 'content': 'reviewed via HTTP'})]),
            completion('{"decision":"approve","reason":"The human requested this exact file."}'),
            completion('Saved the requested file.'),
        ])
        self.workspace.save_settings({'provider': {'base_url': provider.base_url, 'model': 'http-review-fixture'}})
        # API sessions are bound to the endpoint at creation.
        self.session = self.workspace.agents.create(self.pid, {'mode': 'confirm'})
        sent = self.registry.invoke('agent.session.send', self.parameters(message='Write review-http.txt with reviewed via HTTP.'))
        self.assertTrue(sent['ok'], sent)

        def wait_for(predicate):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                record = self.workspace.agents.get(self.pid, self.session['id'])
                if predicate(record):
                    return record
                time.sleep(.01)
            self.fail('Conversation did not reach the expected approval state')

        pending = wait_for(lambda record: record.get('pending_approval'))['pending_approval']
        self.assertFalse((self.workspace.projects.path(self.pid) / 'review-http.txt').exists())
        reviewed = self.registry.invoke('agent.session.review_approval', self.parameters(approval_id=pending['id']))
        self.assertTrue(reviewed['ok'], reviewed)
        finished = wait_for(lambda record: record['turns'][-1].get('finished_at'))
        self.assertEqual(finished['status'], 'succeeded')
        self.assertEqual(self.workspace.projects.read(self.pid, 'review-http.txt')['content'], 'reviewed via HTTP')
        self.assertEqual(len(self.requests), 3)
        self.assertIn('tools', self.requests[0]['payload'])
        self.assertNotIn('tools', self.requests[1]['payload'])
        self.assertEqual(len(self.requests[1]['payload']['messages']), 2)
        reviews = [event for event in finished['turns'][-1]['events'] if event['type'] == 'approval_review']
        self.assertEqual([(event['approval_id'], event['decision']) for event in reviews], [(pending['id'], 'approve')])

    def test_legacy_routes_share_policy_validation_and_external_token_restrictions(self):
        server = create_server(self.workspace, port=0, desktop_url='')
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_port}/api/projects/{self.pid}/agent/sessions/{self.session["id"]}'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def request(suffix, data, token=None):
            headers = {'Content-Type': 'application/json'}
            if token:
                headers['Authorization'] = 'Bearer ' + token
            req = urllib.request.Request(base + suffix, data=json.dumps(data).encode(), headers=headers)
            try:
                with opener.open(req, timeout=3) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as error:
                with error:
                    return error.code, json.load(error)

        status, session = request('/auto-approval', {'enabled': True})
        self.assertEqual(status, 200, session)
        self.assertTrue(session['auto_approval'])
        self.assertEqual(request('/auto-approval', {'enabled': 'true'})[0], 400)
        token = self.registry.issue_context_token(InvocationContext(principal='external-agent', source='external-agent',
                                                  permissions=PERMISSIONS, project_id=self.pid))
        self.addCleanup(self.registry.revoke_context_token, token)
        self.assertEqual(request('/auto-approval', {'enabled': False}, token)[0], 403)
        self.assertEqual(request('/approval/review', {'approval_id': 'a' * 32}, token)[0], 403)
        self.assertEqual(request('/approval/review', {'approval_id': 'a' * 32})[0], 400)


if __name__ == '__main__':
    unittest.main()
