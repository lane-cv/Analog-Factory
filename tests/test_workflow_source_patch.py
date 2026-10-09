"""Reviewed changes must move one accepted snapshot without granting model writes."""
import hashlib
import importlib
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path

from analog_factory.operations import InvocationContext
from analog_factory.service import Workspace
from tests.test_workflow_graph import graph, node, literal, ref


class SourcePatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Workspace(Path(self.tmp.name))
        self.addCleanup(self.workspace.agents.close)
        self.addCleanup(self.workspace.operations.close)
        self.pid = self.workspace.projects.create('Reviewed patch')['id']
        self.root = self.workspace.projects.path(self.pid)
        self.engine = importlib.import_module('analog_factory.workflow_graph')
        self.path = 'testbenches/pre.cir'
        self.old = self.workspace.projects.read(self.pid, self.path)['content']
        self.new = self.old.replace('RD=3300', 'RD=2200')
        self.assertNotEqual(self.old, self.new)
        self.control = self.engine.GraphControl()
        self.cancel = threading.Event()
        self.events = []

    def patch(self, name='patch', old=None, content=None, **extra):
        return node(name, 'source_patch', {'path': self.path},
                    {'content': literal(self.new if content is None else content),
                     'expected_sha256': literal(hashlib.sha256((self.old if old is None else old).encode()).hexdigest())}, **extra)

    def execute(self, flow=None, decide=None):
        def update(**values):
            self.events.append(values)
            review = values.get('pending_review')
            if review and decide:
                decide(review)
        return self.engine.execute_graph(self.workspace, self.pid, flow or graph([self.patch()]),
            {'id': 'a' * 32}, self.cancel, update, control=self.control)

    def accept(self, review, approved=True):
        self.control.command('review', node_id=review['node_id'], approved=approved,
                             review_token=review['review_token'])

    def test_approved_patch_preserves_revision_and_moves_only_accepted_source(self):
        try:
            result = self.execute(decide=self.accept)
        except ValueError as error:
            self.fail(f'Reviewed source patches are unavailable: {error}')
        self.assertEqual(result['status'], 'succeeded', result)
        outputs = result['nodes']['patch']['outputs']
        self.assertTrue(outputs['applied'])
        self.assertEqual(self.workspace.projects.read(self.pid, self.path)['content'], self.new)
        self.assertEqual(self.workspace.projects.history(self.pid, self.path)[0]['sha256'], hashlib.sha256(self.old.encode()).hexdigest())
        self.assertEqual(result['source_generations'][1]['source_hashes'][self.path], outputs['after_sha256'])
        self.assertEqual(outputs['receipt']['operation_id'], 'file.write')
        self.assertEqual(len(outputs['receipt']['trace_id']), 32)
        self.assertEqual(self.workspace.projects.history(self.pid, self.path)[0]['id'], outputs['receipt']['data']['revision_id'])

    def test_rejection_cancel_and_timeout_leave_sources_and_history_unchanged(self):
        for mode in ('reject', 'cancel', 'timeout'):
            with self.subTest(mode=mode):
                self.control = self.engine.GraphControl()
                self.cancel = threading.Event()
                def decide(review):
                    if mode == 'reject': self.accept(review, False)
                    if mode == 'cancel': self.cancel.set()
                result = self.execute(graph([self.patch(timeout_seconds=.1)]), decide)
                self.assertIn(result['status'], ('failed', 'cancelled', 'timed_out'), result)
                self.assertEqual(self.workspace.projects.read(self.pid, self.path)['content'], self.old)
                self.assertEqual(self.workspace.projects.history(self.pid, self.path), [])
                self.assertIn('proposal', result['nodes']['patch']['outputs'])

    def test_source_change_while_reviewing_never_gets_overwritten(self):
        def decide(review):
            self.workspace.projects.write(self.pid, self.path, 'human change\n')
            self.accept(review)
        result = self.execute(decide=decide)
        self.assertEqual(result['termination'], 'source_conflict', result)
        self.assertEqual(self.workspace.projects.read(self.pid, self.path)['content'], 'human change\n')
        self.assertEqual(len(self.workspace.projects.history(self.pid, self.path)), 1)

    def test_tokens_are_mandatory_consumed_once_and_cannot_be_replayed(self):
        def decide(review):
            for token in (None, 'wrong'):
                with self.assertRaisesRegex(ValueError, 'token'):
                    self.control.command('review', node_id=review['node_id'], approved=True, review_token=token)
            self.accept(review)
            with self.assertRaises(ValueError):
                self.accept(review)
        result = self.execute(decide=decide)
        self.assertEqual(result['status'], 'succeeded', result)

    def test_consumed_review_decision_cannot_reopen_before_commit(self):
        control = self.engine.GraphControl()
        control.pending_review = 'patch'
        control.review_token = 'a' * 64
        control.command('review', node_id='patch', approved=True, review_token=control.review_token)
        control.decisions.pop('patch')  # The executor consumed it and is entering commit.
        with self.assertRaisesRegex(ValueError, 'already'):
            control.command('review', node_id='patch', approved=True, review_token=control.review_token)

    def test_patch_retries_and_missing_required_inputs_reject_validation(self):
        for patch in (self.patch(retries=1), node('patch', 'source_patch', {'path': self.path})):
            with self.assertRaises(ValueError):
                self.engine.validate_graph(graph([patch]))

    def test_post_write_failure_records_receipt_stops_and_never_retries(self):
        operation = self.workspace.operations.operations['file.write']
        calls = []
        def write(context, params):
            calls.append(params)
            operation.handler(context, params)
            raise OSError('write committed but receipt failed')
        self.workspace.operations.operations['file.write'] = replace(operation, handler=write)
        result = self.execute(graph([self.patch(), node('later', 'report')],
                                    [{'source': 'patch', 'target': 'later'}]), self.accept)
        self.assertEqual(result['termination'], 'patch_outcome_unknown', result)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.workspace.projects.read(self.pid, self.path)['content'], self.new)
        self.assertTrue(result['nodes']['patch']['outputs']['outcome_unknown'])
        self.assertEqual(result['nodes']['later']['status'], 'skipped')

    def test_registered_review_requires_human_write_permission_and_exact_token(self):
        start = importlib.import_module('analog_factory.operation_graph').start_graph
        job = start(self.workspace, self.pid, graph([self.patch()]))
        def stop():
            self.workspace.jobs.cancel(self.root, job['id'])
            self.workspace.jobs.wait(self.root, job['id'], 5)
        self.addCleanup(stop)
        review = None
        for _ in range(200):
            review = self.workspace.jobs.get(self.root, job['id']).get('pending_review')
            if review: break
            time.sleep(.01)
        self.assertIsNotNone(review)
        params = {'project_id': self.pid, 'job_id': job['id'], 'action': 'review',
                  'node_id': 'patch', 'approved': True, 'review_token': review['review_token']}
        for context in (InvocationContext(source='agent', project_id=self.pid, confirmed=True),
                        InvocationContext(source='workflow', project_id=self.pid, confirmed=True),
                        InvocationContext(permissions=frozenset({'read', 'execute', 'approve'}))):
            denied = self.workspace.operations.invoke('job.control', params, context=context)
            self.assertEqual(denied['error']['code'], 'PERMISSION_DENIED', denied)
        accepted = self.workspace.operations.invoke('job.control', params)
        self.assertTrue(accepted['ok'], accepted)
        finished = self.workspace.jobs.wait(self.root, job['id'], 5)
        self.assertEqual(finished['status'], 'succeeded', finished)

    def test_actual_approved_resistor_patch_changes_simulation_and_snapshot(self):
        executable = Path(__file__).resolve().parents[1] / '.tools/eda/bin/ngspice-sky130'
        if not executable.is_file(): self.skipTest('Prepared ngspice executable required')
        self.workspace.save_settings({'tools': {'ngspice': str(executable)}})
        flow = graph([node('before', 'simulate'), self.patch(), node('after', 'simulate')],
                     [{'source': 'before', 'target': 'patch'}, {'source': 'patch', 'target': 'after'}],
                     limits={'runtime_seconds': 30, 'max_node_executions': 20})
        result = self.execute(flow, self.accept)
        self.assertEqual(result['status'], 'succeeded', result)
        before = result['nodes']['before']['outputs']['result']
        after = result['nodes']['after']['outputs']['result']
        self.assertNotAlmostEqual(before['metrics']['vout_mean'], after['metrics']['vout_mean'], places=3)
        self.assertEqual(before['source_hashes'][self.path], hashlib.sha256(self.old.encode()).hexdigest())
        self.assertEqual(after['source_hashes'][self.path], hashlib.sha256(self.new.encode()).hexdigest())
        self.assertEqual(after['metadata']['source_generation'], 1)
        self.assertTrue(self.workspace.runner.get(self.root, before['id'])['stale'])

    def test_loop_patch_reviews_have_distinct_tokens_and_successive_generations(self):
        body = graph([node('read', 'project', {'path': self.path}),
                      node('rd', 'input', {'name': 'item', 'type': 'number'}),
                      node('deck', 'netlist', {'parameter': 'RD'}, {'content': ref('read', 'content'), 'value': ref('rd')}),
                      node('patch', 'source_patch', {'path': self.path},
                           {'content': ref('deck', 'content'), 'expected_sha256': ref('read', 'sha256')})])
        tokens = []
        def decide(review):
            tokens.append(review['review_token'])
            self.accept(review)
        try:
            result = self.execute(graph([node('loop', 'loop', {'body': body, 'max_iterations': 3,
                                                              'values': [2200, 3300, 4400]})]), decide)
        except ValueError as error:
            self.fail(f'Read source hash is unavailable for iteration review: {error}')
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(len(set(tokens)), 3)
        self.assertEqual(len(result['source_generations']), 4)
        self.assertEqual(len(self.workspace.projects.history(self.pid, self.path)), 3)

    def test_shared_twenty_proposal_limit_cannot_reset_inside_loop(self):
        self.workspace.projects.write(self.pid, 'notes.txt', 'a\n')
        body = graph([node('read', 'project', {'path': 'notes.txt'}),
                      node('item', 'input', {'name': 'item', 'type': 'string'}),
                      node('patch', 'source_patch', {'path': 'notes.txt'},
                           {'content': ref('item'), 'expected_sha256': ref('read', 'sha256')})])
        result = self.execute(graph([node('loop', 'loop', {'body': body, 'max_iterations': 21,
                                                          'values': ['b\n', 'a\n'] * 10 + ['b\n']})]), self.accept)
        self.assertEqual(result['termination'], 'proposal_limit', result)
        self.assertEqual(result['proposal_count'], 20)
        self.assertEqual(len(self.workspace.projects.history(self.pid, 'notes.txt')), 20)

    def test_same_bytes_replacement_or_other_file_edit_invalidates_review(self):
        for change in ('same_bytes', 'other_file'):
            self.control = self.engine.GraphControl()
            def decide(review):
                path = self.path if change == 'same_bytes' else 'specifications.json'
                content = self.workspace.projects.read(self.pid, path)['content']
                self.workspace.projects.write(self.pid, path, content + (' ' if change == 'other_file' else ''))
                self.accept(review)
            result = self.execute(decide=decide)
            self.assertEqual(result['status'], 'failed', result)
            self.assertEqual(self.workspace.projects.read(self.pid, self.path)['content'], self.old)

    def test_shared_proposal_byte_limit_stops_before_next_review(self):
        self.workspace.projects.write(self.pid, 'notes.txt', 'a' * 60000 + '\n')
        body = graph([node('read', 'project', {'path': 'notes.txt'}),
                      node('item', 'input', {'name': 'item', 'type': 'string'}),
                      node('patch', 'source_patch', {'path': 'notes.txt'},
                           {'content': ref('item'), 'expected_sha256': ref('read', 'sha256')})])
        result = self.execute(graph([node('loop', 'loop', {'body': body, 'max_iterations': 12,
                               'values': ['b' * 60000 + '\n', 'a' * 60000 + '\n'] * 6})]), self.accept)
        self.assertEqual(result['termination'], 'proposal_limit', result)
        self.assertLess(result['proposal_count'], 12)
        self.assertLessEqual(result['proposal_bytes'], 2 * 1024 * 1024)
        self.assertEqual(len(self.workspace.projects.history(self.pid, 'notes.txt')), result['proposal_count'])

    def test_uncertain_write_cannot_continue_even_when_evidence_update_fails(self):
        operation = self.workspace.operations.operations['file.write']
        def write(context, params):
            operation.handler(context, params)
            raise OSError('committed without receipt')
        self.workspace.operations.operations['file.write'] = replace(operation, handler=write)
        def update(**values):
            review = values.get('pending_review')
            if review: self.accept(review)
            if 'pending_review' in values and review is None:
                raise OSError('evidence update failed')
        result = self.engine.execute_graph(self.workspace, self.pid,
            graph([self.patch(on_error='continue'), node('later', 'report')]), {'id': 'a' * 32},
            self.cancel, update, control=self.control)
        self.assertEqual(result['termination'], 'patch_outcome_unknown', result)
        self.assertEqual(result['nodes']['later']['status'], 'skipped')

    def test_deadline_expiring_while_waiting_for_commit_lock_prevents_write(self):
        ready = threading.Event()
        threads = []
        def decide(review):
            def hold():
                with self.workspace.projects.lock:
                    ready.set()
                    time.sleep(.15)
            thread = threading.Thread(target=hold)
            threads.append(thread)
            thread.start()
            self.assertTrue(ready.wait(1))
            self.accept(review)
        result = self.execute(graph([self.patch(timeout_seconds=.1)]), decide)
        for thread in threads: thread.join(1)
        self.assertEqual(result['status'], 'timed_out', result)
        self.assertFalse(result['nodes']['patch']['outputs']['applied'])
        self.assertEqual(self.workspace.projects.read(self.pid, self.path)['content'], self.old)
        self.assertEqual(self.workspace.projects.history(self.pid, self.path), [])
