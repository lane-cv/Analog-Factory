import tempfile
import threading
import unittest
from pathlib import Path

from analog_factory.operations import Operation, OperationRegistry, InvocationContext
from analog_factory.service import Workspace


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Workspace(Path(self.tmp.name))
        self.registry = OperationRegistry(self.workspace)
        self.calls = []
        self.registry.register(Operation('example.echo', 'Echo', 'Return supplied text.', 'example',
            {'type': 'object', 'properties': {'text': {'type': 'string', 'minLength': 1}},
             'required': ['text'], 'additionalProperties': False},
            {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']},
            lambda c, p: self.calls.append(p) or p, permissions=('write',), confirmation='agent'))

    def test_registration_validation(self):
        with self.assertRaises(ValueError):
            self.registry.register(self.registry.operations['example.echo'])

    def test_validation_permissions_and_approval_precede_handler(self):
        self.assertEqual(self.registry.invoke('example.echo', {'text': 1})['error']['code'], 'INVALID_ARGUMENT')
        readonly = InvocationContext(permissions=frozenset({'read'}))
        self.assertEqual(self.registry.invoke('example.echo', {'text': 'x'}, context=readonly)['error']['code'], 'PERMISSION_DENIED')
        agent = InvocationContext(source='agent')
        self.assertEqual(self.registry.invoke('example.echo', {'text': 'x'}, context=agent)['error']['code'], 'CONFIRMATION_REQUIRED')
        self.assertEqual(self.calls, [])

    def test_deduplication_and_changed_request_rejection(self):
        first = self.registry.invoke('example.echo', {'text': 'x'}, idempotency_key='once')
        second = self.registry.invoke('example.echo', {'text': 'x'}, idempotency_key='once')
        self.assertTrue(first['ok'])
        self.assertEqual(first, second)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.registry.invoke('example.echo', {'text': 'y'}, idempotency_key='once')['error']['code'], 'IDEMPOTENCY_CONFLICT')

    def test_session_requires_real_acknowledgement(self):
        sessions = self.registry.sessions
        session = sessions.connect({'tab': 'overview'})
        result = []
        thread = threading.Thread(target=lambda: result.append(sessions.command(session['client_id'], 'ui.navigate', {'tab': 'editor'})))
        thread.start()
        packet = sessions.poll(session['client_id'], session['token'], {'tab': 'overview'}, 0)
        command = packet['commands'][0]
        sessions.ack(session['client_id'], session['token'], command['command_id'], True, {'tab': 'editor'}, None, {'tab': 'editor'})
        thread.join(2)
        self.assertEqual(result[0]['state']['tab'], 'editor')
        self.assertRaises(FileNotFoundError, sessions.command, 'missing', 'ui.navigate', {})

    def test_events_do_not_include_parameters(self):
        self.registry.invoke('example.echo', {'text': 'private-text'})
        packet = self.registry.subscribe_events()
        self.assertTrue(packet['events'])
        self.assertNotIn('private-text', str(packet))

    def test_stale_session_command_is_rejected_before_delivery(self):
        sessions = self.registry.sessions
        connected = sessions.connect({'tab': 'overview'})
        errors = []
        started = threading.Event()
        def invoke():
            started.set()
            try:
                sessions.command(connected['client_id'], 'ui.navigate', {'tab': 'editor'}, expected_version=0)
            except RuntimeError as error:
                errors.append(str(error))
        thread = threading.Thread(target=invoke)
        thread.start()
        started.wait(1)
        packet = sessions.poll(connected['client_id'], connected['token'], {'tab': 'settings'}, 0)
        thread.join(2)
        self.assertEqual(packet['commands'], [])
        self.assertIn('version conflict', errors[0])

    def test_capability_token_is_revoked_and_never_published(self):
        token = self.registry.issue_context_token(InvocationContext(permissions=frozenset({'read'})),
                                                 lambda name, args: {'name': name})
        self.assertEqual(self.registry.invoke_agent_tool(token, 'list_operations', {}), {'name': 'list_operations'})
        self.assertNotIn(token, str(self.registry.subscribe_events()))
        self.registry.revoke_context_token(token)
        self.assertIsNone(self.registry.resolve_context_token(token))
        self.assertRaises(PermissionError, self.registry.invoke_agent_tool, token, 'list_operations', {})

    def test_example_operation_has_one_handler_for_all_callers(self):
        from dataclasses import replace
        operation = self.registry.operations['example.echo']
        self.registry.operations['example.echo'] = replace(operation, permissions=('read',), kind='query', confirmation='none')
        ui = self.registry.invoke('example.echo', {'text': 'shared'})
        agent = self.registry.invoke('example.echo', {'text': 'shared'}, context=InvocationContext(source='agent', permissions=frozenset({'read'})))
        self.assertEqual(ui['data'], agent['data'])
        self.assertEqual(len(self.calls), 2)
        descriptor = self.registry.describe_operation('example.echo')
        self.assertEqual(descriptor['input_schema']['required'], ['text'])

    def test_invalid_result_is_a_handler_error(self):
        self.registry.register(Operation('example.invalid', 'Bad result test', 'Exercise a handler contract violation.', 'example',
            {'type': 'object'}, {'type': 'integer'}, lambda c, p: 'wrong', kind='query'))
        result = self.registry.invoke('example.invalid')
        self.assertEqual(result['error']['code'], 'INVALID_RESULT')

    def test_registration_rejects_bad_schema_handler_and_permissions(self):
        from dataclasses import replace
        op = self.registry.operations['example.echo']
        for invalid in (replace(op, operation_id='Not Stable'), replace(op, operation_id='example.bad', handler=None),
                        replace(op, operation_id='example.bad', permissions=('administrator',)),
                        replace(op, operation_id='example.bad', input_schema={'type': 'unrecognized'})):
            with self.assertRaises(ValueError):
                self.registry.register(invalid)

    def test_agent_task_status_is_bound_to_one_turn(self):
        pid = self.workspace.projects.create('Turn identity')['id']
        session = self.workspace.agents.create(pid, {'backend': 'codex'})
        sid, first, second = session['id'], 'a' * 32, 'b' * 32
        record = self.workspace.agents._load(pid, sid)
        record['status'] = 'succeeded'
        record['turns'] = [{'id': first, 'status': 'failed', 'error': 'first failed'},
                           {'id': second, 'status': 'succeeded', 'reply': 'second done'}]
        self.workspace.agents._save(pid, record)
        result = self.registry.get_operation_status(f'agent:{pid}:{sid}:{first}')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['data']['turns'][0]['id'], first)
        cancelled = self.registry.cancel_operation(f'agent:{pid}:{sid}:{first}')
        self.assertTrue(cancelled['ok'], cancelled)
        self.assertEqual(self.workspace.agents.get(pid, sid)['status'], 'succeeded')

    def test_native_source_change_emits_state_event(self):
        from analog_factory.operation_catalog import register_operations
        register_operations(self.registry)
        pid = self.workspace.projects.create('Native source changes')['id']
        self.registry.invoke('project.detail', {'project_id': pid})
        source = self.workspace.projects.path(pid) / 'testbenches/pre.cir'
        source.write_text(source.read_text() + '\n* native editor change\n')
        self.registry.poll_external_changes()
        events = self.registry.subscribe_events()['events']
        self.assertEqual(events[-1]['status'], 'changed')
        self.assertEqual(events[-1]['project_id'], pid)

    def test_concurrent_frontend_retries_execute_once(self):
        import time
        entered, release = threading.Event(), threading.Event()
        calls, results = [], []
        def handler(context, params):
            calls.append('executed')
            entered.set()
            release.wait(2)
            return {'value': 1}
        self.registry.register(Operation('example.frontend', 'Frontend retry', 'Wait for browser-like completion.', 'example',
            {'type': 'object'}, {'type': 'object'}, handler, kind='frontend', permissions=('ui',)))
        def invoke():
            results.append(self.registry.invoke('example.frontend', {}, idempotency_key='same-request'))
        first, second = threading.Thread(target=invoke), threading.Thread(target=invoke)
        first.start(); entered.wait(1); second.start()
        time.sleep(.02)
        release.set(); first.join(2); second.join(2)
        self.assertEqual(calls, ['executed'])
        self.assertEqual(results[0], results[1])

    def test_scoped_frontend_ack_cannot_reveal_another_project(self):
        from analog_factory.operation_ui import register_operations
        register_operations(self.registry)
        pid = self.workspace.projects.create('Scoped browser')['id']
        session = self.registry.sessions.connect({'project_id': pid})
        result = []
        context = InvocationContext(source='agent', project_id=pid, permissions=frozenset({'read', 'ui'}))
        thread = threading.Thread(target=lambda: result.append(self.registry.invoke('ui.workspace.refresh',
            {'client_id': session['client_id']}, context)))
        thread.start()
        import time
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            packet = self.registry.sessions.poll(session['client_id'], session['token'], {'project_id': pid})
            if packet['commands']:
                break
            time.sleep(.01)
        command = packet['commands'][0]
        self.registry.sessions.ack(session['client_id'], session['token'], command['command_id'], True,
            {'files': ['foreign-private-file']}, None, {'project_id': 'b' * 32})
        thread.join(2)
        self.assertFalse(result[0]['ok'])
        self.assertEqual(result[0]['error']['code'], 'PERMISSION_DENIED')
        self.assertNotIn('foreign-private-file', str(result[0]))
