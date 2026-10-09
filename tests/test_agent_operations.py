import contextlib
import io
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from analog_factory.__main__ import main
from analog_factory.service import Workspace
from analog_factory.templates import tuning_workflow
from tests import test_agent_api as fixtures

call, completion = fixtures.call, fixtures.completion


class AgentOperationTests(unittest.TestCase):
    setUp = fixtures.ApiAgentTests.setUp
    turn = fixtures.ApiAgentTests.turn

    endpoint = fixtures.ApiAgentTests.endpoint

    def test_agent_discovers_and_invokes_project_operations(self):
        provider = self.endpoint([
            completion(calls=[call('list_operations', {'module': 'file'})]),
            completion(calls=[call('describe_operation', {'operation_id': 'file.read'})]),
            completion(calls=[call('invoke_operation', {'operation_id': 'file.read',
                                                      'parameters': {'path': 'testbenches/pre.cir'}})]),
            completion('Read through shared operations.')])
        self.turn(provider)
        results = [json.loads(request['payload']['messages'][-1]['content'])
                   for request in self.requests[1:]]
        self.assertTrue(any(item['operation_id'] == 'file.read' for item in results[0]['operations']))
        self.assertEqual(results[1]['operation_id'], 'file.read')
        self.assertTrue(results[2]['ok'], results[2])
        self.assertEqual(results[2]['operation_id'], 'file.read')
        self.assertIn('content', results[2]['data'])

    def test_complete_operation_discovery_fits_model_budget_and_events_are_available(self):
        pages=[];cursor=None;responses=[];history=[]
        provider=self.endpoint(responses)
        # Continue across completed turns rather than requiring an arbitrarily
        # growing catalog to fit the runtime's fixed tool-round limit.
        while True:
            responses.extend([completion(calls=[call('list_operations',{'cursor':cursor} if cursor else {})]),
                              completion('Discovery page read.')])
            result=self.turn(provider,history=history);history=result['history']
            page=json.loads(self.requests[-1]['payload']['messages'][-1]['content'])
            self.assertTrue('operations' in page,'Discovery lost its structured page')
            pages.append(page);cursor=page['next_cursor']
            if cursor is None:break
            self.assertLess(len(pages),len(self.workspace.operations.operations))
        names=[item['operation_id'] for page in pages for item in page['operations']]
        self.assertEqual(names,sorted(self.workspace.operations.operations))
        self.assertGreater(len(pages),1)
        self.assertTrue(pages[-1]['complete'])
        for page in pages:
            self.assertLess(len(json.dumps(page,ensure_ascii=False,separators=(',', ':')).encode()),64000)
            self.assertEqual(page['total'],len(names))
            self.assertEqual(page['returned'],len(page['operations']))
        responses.extend([completion(calls=[call('subscribe_events',{'after':0})]),completion('Changes read.')])
        self.turn(provider,history=history)
        events = json.loads(self.requests[-1]['payload']['messages'][-1]['content'])
        self.assertIn('events', events)
        self.assertIsInstance(events['cursor'], int)

    def test_generic_write_needs_approval_and_rejects_stale_project(self):
        self.workspace.projects.write(self.pid, 'notes.txt', 'original')
        approvals = []
        def approve(name, arguments):
            approvals.append((name, arguments))
            self.workspace.projects.write(self.pid, 'notes.txt', 'manual edit')
            return True
        provider = self.endpoint([
            completion(calls=[call('invoke_operation', {'operation_id': 'file.write',
                'parameters': {'path': 'notes.txt', 'content': 'agent edit'}})]),
            completion('Changes need new approval.')])
        self.turn(provider, mode='confirm', approve=approve)
        self.assertEqual(len(approvals), 1)
        result = json.loads(self.requests[-1]['payload']['messages'][-1]['content'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'VERSION_CONFLICT')
        self.assertEqual(self.workspace.projects.read(self.pid, 'notes.txt')['content'], 'manual edit')

    def test_generic_mutations_are_denied_in_read_only_mode(self):
        provider = self.endpoint([
            completion(calls=[call('invoke_operation', {'operation_id': 'file.write',
                'parameters': {'path': 'denied.txt', 'content': 'agent edit'}})]), completion('Denied.')])
        self.turn(provider, approve=lambda *args: self.fail('Read-only action reached approval'))
        result = json.loads(self.requests[-1]['payload']['messages'][-1]['content'])
        self.assertIn('read-only', json.dumps(result))
        self.assertFalse((self.workspace.projects.path(self.pid) / 'denied.txt').exists())

    def test_generic_operations_cannot_self_approve_or_expose_credentials(self):
        secret = 'private-provider-secret-value'
        provider = self.endpoint([
            completion(calls=[
                call('invoke_operation', {'operation_id': 'settings.update',
                    'parameters': {'settings': {'provider': {'api_key': secret}}}}),
                call('invoke_operation', {'operation_id': 'agent.session.approve',
                    'parameters': {'session_id': 'a' * 32, 'approval_id': 'b' * 32,
                                   'approved': True}}, 'self-approve')]), completion('Denied.')])
        result = self.turn(provider, mode='confirm', approve=lambda *args: self.fail('Forbidden action reached approval'))
        self.assertNotIn(secret, json.dumps(self.events))
        self.assertNotIn(secret, json.dumps(result['history']))
        self.assertIsNone(self.workspace._secret)
        results = [json.loads(item['content']) for item in self.requests[-1]['payload']['messages']
                   if item['role'] == 'tool']
        self.assertTrue(all('error' in item for item in results))

    def test_generic_operations_cannot_target_another_project(self):
        other = self.workspace.projects.create('Other')['id']
        provider = self.endpoint([completion(calls=[call('invoke_operation', {
            'operation_id': 'file.write', 'parameters': {'project_id': other,
                'path': 'foreign.txt', 'content': 'unauthorized'}})]), completion('Denied.')])
        self.turn(provider, mode='confirm', approve=lambda *args: self.fail('Foreign project reached approval'))
        self.assertFalse((self.workspace.projects.path(other) / 'foreign.txt').exists())
        result = json.loads(self.requests[-1]['payload']['messages'][-1]['content'])
        self.assertIn('project', json.dumps(result).lower())

    def test_simulation_approval_does_not_execute_changed_project_sources(self):
        provider = self.endpoint([completion(calls=[call('run_simulation',
            {'netlist': 'testbenches/pre.cir'})]), completion('New approval needed.')])
        def approve(name, arguments):
            self.workspace.projects.write(self.pid, 'notes.txt', 'manual change during approval')
            return True
        self.turn(provider, mode='confirm', approve=approve)
        self.assertEqual(self.workspace.runs(self.pid), [])
        result = json.loads(self.requests[-1]['payload']['messages'][-1]['content'])
        self.assertIn('changed', json.dumps(result).lower())

    def test_frontend_operation_uses_project_version_and_awaits_actual_acknowledgement(self):
        registry = self.workspace.operations
        state = {'project_id': self.pid, 'tab': 'overview'}
        session = registry.sessions.connect(state)
        acknowledged = threading.Event()
        def browser():
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                packet = registry.sessions.poll(session['client_id'], session['token'], state)
                if packet['commands']:
                    command = packet['commands'][0]
                    registry.sessions.ack(session['client_id'], session['token'], command['command_id'],
                        True, {'tab': 'simulation'}, state={**state, 'tab': 'simulation'})
                    acknowledged.set()
                    return
                time.sleep(.01)
        worker = threading.Thread(target=browser)
        worker.start()
        self.addCleanup(worker.join, 4)
        provider = self.endpoint([completion(calls=[call('invoke_operation', {
            'operation_id': 'ui.workspace.navigate',
            'parameters': {'client_id': session['client_id'], 'tab': 'simulation'}})]), completion('Navigated.')])
        proposals = []
        self.turn(provider, mode='confirm', approve=lambda name, args: proposals.append(args) or True)
        result = json.loads(self.requests[-1]['payload']['messages'][-1]['content'])
        self.assertTrue(result['ok'], result)
        self.assertTrue(acknowledged.is_set())
        self.assertEqual(result['data']['state']['tab'], 'simulation')
        self.assertEqual(proposals[0]['parameters']['ui_version'], 0)

    def test_frontend_state_changed_during_approval_rejects_pending_action(self):
        registry = self.workspace.operations
        session = registry.sessions.connect({'project_id': self.pid, 'tab': 'overview'})
        def approve(name, arguments):
            registry.sessions.poll(session['client_id'], session['token'],
                                   {'project_id': self.pid, 'tab': 'editor'})
            return True
        provider = self.endpoint([completion(calls=[call('invoke_operation', {
            'operation_id': 'ui.workspace.navigate',
            'parameters': {'client_id': session['client_id'], 'tab': 'simulation'}})]), completion('State changed.')])
        self.turn(provider, mode='confirm', approve=approve)
        result = json.loads(self.requests[-1]['payload']['messages'][-1]['content'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'VERSION_CONFLICT')
        self.assertEqual(registry.sessions.list()[0]['state']['tab'], 'editor')


class OperationCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def cli(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            try:
                code = main(['--workspace', str(self.root), 'operations', *arguments])
            except SystemExit as error:
                self.fail(f'Operation CLI rejected its command: {error}')
        return code, json.loads(output.getvalue())

    def test_cli_discovery_and_confirmed_mutation_use_operation_envelopes(self):
        code, listed = self.cli('list', '--module', 'project')
        self.assertEqual(code, 0)
        self.assertIn('project.create', [item['operation_id'] for item in listed['operations']])
        code, descriptor = self.cli('describe', 'project.create')
        self.assertEqual(code, 0)
        self.assertEqual(descriptor['operation_id'], 'project.create')
        code, denied = self.cli('invoke', 'project.create', '--parameters', '{"name":"Example"}')
        self.assertEqual(code, 1)
        self.assertFalse(denied['ok'])
        code, created = self.cli('invoke', 'project.create', '--parameters', '{"name":"Example"}', '--confirm')
        self.assertEqual(code, 0)
        self.assertTrue(created['ok'], created)
        self.assertEqual(created['data']['name'], 'Example')
        self.assertEqual(len(Workspace(self.root).projects.list()), 1)


class WorkflowOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Workspace(Path(self.tmp.name))
        self.pid = self.workspace.projects.create('Workflow operations')['id']

    @unittest.skipUnless(shutil.which('ngspice'), 'ngspice required for real workflow execution')
    def test_tuning_runs_emit_shared_operation_events(self):
        job = self.workspace.start_workflow(self.pid, tuning_workflow())
        result = self.workspace.jobs.wait(self.workspace.projects.path(self.pid), job['id'], timeout=60)
        self.assertEqual(result['status'], 'succeeded', result)
        events = self.workspace.operations.subscribe_events()['events']
        started = {event['task_id'] for event in events if event['operation_id'] == 'run.start'}
        self.assertTrue(started, 'Tuning bypassed the shared operation registry')
        self.assertEqual(started, {f'run:{self.pid}:{step["run_id"]}' for step in result['iterations']})

    @unittest.skipUnless(shutil.which('ngspice'), 'ngspice required for isolated evaluation execution')
    def test_evaluation_uses_shared_operations_and_preserves_episode_isolation(self):
        source = self.workspace.projects.read(self.pid, 'testbenches/pre.cir')['content']
        job = self.workspace.evaluate(self.pid, {'episodes': 1, 'seed': 42})
        result = self.workspace.jobs.wait(self.workspace.projects.path(self.pid), job['id'], timeout=60)
        self.assertEqual(result['status'], 'succeeded', result)
        events = self.workspace.operations.subscribe_events()['events']
        rid = result['iterations'][0]['run_id']
        self.assertTrue(any(event['operation_id'] == 'run.start' and
                            event['task_id'] == f'run:{self.pid}:{rid}' for event in events),
                        'Evaluation bypassed the shared operation registry')
        episode_root = self.workspace.run_root(self.pid, rid)
        self.assertFalse((episode_root / 'specifications.json').exists())
        self.assertEqual(self.workspace.projects.read(self.pid, 'testbenches/pre.cir')['content'], source)


if __name__ == '__main__':
    unittest.main()
