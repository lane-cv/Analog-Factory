import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from analog_factory.agent_api import _execute
from analog_factory.agent_codex_approval import OfficialApprovalLedger
from analog_factory.providers import Provider
from analog_factory.service import Workspace


class OfficialApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Workspace(Path(self.tmp.name))
        self.workspace.operation_url = 'http://127.0.0.1:8765'
        self.pid = self.workspace.projects.create('Official review')['id']
        self.registry = self.workspace.operations
        self.addCleanup(self.registry.close)
        self.cancel = threading.Event()
        self.events = []
        self.ledger = OfficialApprovalLedger(self.workspace, self.pid, self.events.append, self.cancel,
                                             receipt_timeout=.15)
        self.addCleanup(self.ledger.close)
        self.ledger.bind('native-thread', 'native-turn')
        self.args = {'operation_id': 'file.write', 'parameters': {'path': 'official.txt', 'content': 'approved text'}}

    def notification(self, method, **params):
        return {'method': method, 'params': {'threadId': 'native-thread', 'turnId': 'native-turn', **params}}

    def item(self, args=None, item_id='item-1', method='item/started'):
        return self.notification(method, item={'id': item_id, 'type': 'mcpToolCall',
            'server': 'analog_factory', 'tool': 'invoke_operation', 'arguments': copy.deepcopy(args or self.args),
            'status': 'inProgress'})

    def review(self, status='inProgress', item_id='item-1', review_id='review-1'):
        return self.notification('item/autoApprovalReview/' + ('started' if status == 'inProgress' else 'completed'),
            targetItemId=item_id, reviewId=review_id, action={'type': 'mcpToolCall',
                'server': 'analog_factory', 'toolName': 'invoke_operation'},
            review={'status': status, 'rationale': 'Authorized project edit.', 'riskLevel': 'low'},
            decisionSource='agent')

    def approve_native(self, args=None, item_id='item-1', review_id='review-1'):
        self.ledger.accept(self.item(args, item_id))
        self.ledger.accept(self.review(item_id=item_id, review_id=review_id))
        self.ledger.accept(self.review('approved', item_id=item_id, review_id=review_id))

    def execute(self, args=None):
        args = args or self.args
        return _execute('invoke_operation', args, self.workspace, self.pid, 'confirm', self.events.append,
                        self.cancel, lambda name, proposal: self.ledger.consume('invoke_operation', args, name, proposal))

    def test_approved_native_receipt_allows_real_write_once_without_model_api(self):
        self.approve_native()
        self.assertFalse((self.workspace.projects.path(self.pid) / 'official.txt').exists())
        with patch.object(Provider, 'complete', side_effect=AssertionError('Must not call project model')):
            self.assertTrue(self.execute()['ok'])
            self.assertTrue(self.execute().get('error'))
        self.assertEqual(self.workspace.projects.read(self.pid, 'official.txt')['content'], 'approved text')
        self.assertEqual([event['decision'] for event in self.events], ['reviewing', 'approve'])
        self.assertTrue(all(event['source'] == 'codex_official' and event['model'] == '' for event in self.events))

    def test_denial_timeout_abort_and_missing_started_never_allow_write(self):
        for status in ('denied', 'timedOut', 'aborted', 'approved'):
            with self.subTest(status=status):
                self.ledger.accept(self.item(item_id=status))
                if status != 'approved':
                    self.ledger.accept(self.review(item_id=status, review_id=status))
                self.ledger.accept(self.review(status, item_id=status, review_id=status))
                self.assertTrue(self.execute().get('error'))
                self.assertFalse((self.workspace.projects.path(self.pid) / 'official.txt').exists())

    def test_wrong_thread_turn_target_server_tool_or_arguments_cannot_authorize(self):
        for field, value in (('threadId', 'other'), ('turnId', 'other'), ('targetItemId', 'other')):
            with self.subTest(field=field):
                self.ledger.accept(self.item(item_id=field))
                self.ledger.accept(self.review(item_id=field, review_id=field))
                event = self.review('approved', item_id=field, review_id=field)
                event['params'][field] = value
                self.ledger.accept(event)
        for field, value in (('server', 'other'), ('toolName', 'cancel_operation')):
            event = self.review('approved', item_id=field, review_id=field)
            self.ledger.accept(self.item(item_id=field))
            self.ledger.accept(self.review(item_id=field, review_id=field))
            event['params']['action'][field] = value
            self.ledger.accept(event)
        self.assertTrue(self.execute().get('error'))
        self.approve_native()
        altered = copy.deepcopy(self.args)
        altered['parameters']['content'] = 'unreviewed text'
        self.assertTrue(self.execute(altered).get('error'))
        self.assertFalse((self.workspace.projects.path(self.pid) / 'official.txt').exists())

    def test_project_change_during_review_is_not_silently_rebased(self):
        self.ledger.accept(self.item())
        self.ledger.accept(self.review())
        self.workspace.projects.write(self.pid, 'official.txt', 'human edit')
        self.ledger.accept(self.review('approved'))
        with self.assertRaisesRegex(ValueError, '[Vv]ersion|changed'):
            self.execute()
        self.assertEqual(self.workspace.projects.read(self.pid, 'official.txt')['content'], 'human edit')

    def test_frontend_version_is_bound_even_when_model_omits_ui_version(self):
        session = self.registry.sessions.connect({'project_id': self.pid})
        args = {'operation_id': 'ui.editor.save', 'parameters': {'client_id': session['client_id']}}
        self.approve_native(args)
        self.registry.sessions.poll(session['client_id'], session['token'], {'project_id': self.pid, 'changed': True})
        proposal = {'operation_id': args['operation_id'], 'parameters': {**args['parameters'], 'ui_version': 1},
                    'expected_version': self.registry.version(self.pid)}
        with self.assertRaisesRegex(ValueError, '[Vv]ersion|changed'):
            self.ledger.consume('invoke_operation', args, 'invoke_operation', proposal)

    def test_native_frontend_uses_exact_native_revision_without_browser_binding(self):
        operation = self.registry.operations['native.canvas.select']
        self.assertEqual(operation.kind, 'frontend')
        self.assertEqual(operation.frontend_adapter, 'native')
        self.assertIn('expected_native_revision', operation.input_schema['required'])
        self.assertNotIn('client_id', operation.input_schema['properties'])
        args = {'operation_id': operation.operation_id, 'parameters': {
            'session_id': 'a' * 32, 'run_id': 'b' * 32,
            'expected_native_revision': 9, 'instance_id': 'M1'}}
        self.approve_native(args)
        proposal = {**copy.deepcopy(args), 'expected_version': self.registry.version(self.pid)}
        changed = copy.deepcopy(args)
        changed['parameters']['expected_native_revision'] = 10
        self.assertFalse(self.ledger.consume('invoke_operation', changed, 'invoke_operation', proposal))
        self.assertTrue(self.ledger.consume('invoke_operation', args, 'invoke_operation', proposal))

    def test_duplicate_completed_and_completed_item_do_not_leave_reusable_permits(self):
        self.approve_native()
        self.assertTrue(self.execute()['ok'])
        self.ledger.accept(self.review('approved'))
        self.assertTrue(self.execute().get('error'))
        self.approve_native(item_id='query', review_id='query')
        self.ledger.accept(self.item(item_id='query', method='item/completed'))
        self.assertTrue(self.execute().get('error'))

    def test_completed_before_started_does_not_create_a_permit_later(self):
        self.ledger.accept(self.item())
        self.ledger.accept(self.review('approved'))
        self.ledger.accept(self.review())
        self.ledger.accept(self.review('approved'))
        self.assertTrue(self.execute().get('error'))

    def test_completed_item_before_started_cannot_be_resurrected(self):
        self.ledger.accept(self.item(method='item/completed'))
        self.approve_native()
        self.assertTrue(self.execute().get('error'))

    def test_review_started_requires_an_existing_live_matching_item(self):
        # Native stdout is ordered: review before item-start is not a receipt.
        self.ledger.accept(self.review())
        self.ledger.accept(self.item())
        self.ledger.accept(self.review('approved'))
        proposal = {'operation_id': self.args['operation_id'], 'parameters': self.args['parameters'],
                    'expected_version': self.registry.version(self.pid)}
        self.assertFalse(self.ledger.consume('invoke_operation', self.args, 'invoke_operation', proposal))
        # A review for another tool cannot later be repaired into authorization.
        self.ledger.accept(self.item(item_id='mismatch'))
        review = self.review(item_id='mismatch', review_id='mismatch')
        review['params']['action']['toolName'] = 'cancel_operation'
        self.ledger.accept(review)
        self.ledger.accept(self.review('approved', item_id='mismatch', review_id='mismatch'))
        self.assertTrue(self.execute().get('error'))

    def test_two_review_ids_for_one_item_can_only_release_one_callback(self):
        self.approve_native()
        self.approve_native(review_id='second-review')
        proposal = {'operation_id': self.args['operation_id'], 'parameters': self.args['parameters'],
                    'expected_version': self.registry.version(self.pid)}
        self.assertTrue(self.ledger.consume('invoke_operation', self.args, 'invoke_operation', proposal))
        self.assertFalse(self.ledger.consume('invoke_operation', self.args, 'invoke_operation', proposal))

    def test_official_approval_must_be_published_before_business_effect(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def emit(event):
            if event['decision'] == 'approve':
                entered.set()
                release.wait(2)
            self.events.append(event)
        self.ledger.emit = emit
        self.ledger.receipt_timeout = 1
        self.ledger.accept(self.item())
        self.ledger.accept(self.review())
        completed = threading.Thread(target=lambda: self.ledger.accept(self.review('approved')))
        completed.start()
        self.assertTrue(entered.wait(1))
        results = []
        callback = threading.Thread(target=lambda: results.append(self.execute()))
        callback.start()
        time.sleep(.03)
        self.assertFalse((self.workspace.projects.path(self.pid) / 'official.txt').exists())
        release.set()
        completed.join(1)
        callback.join(1)
        self.assertFalse(callback.is_alive())
        self.assertTrue(results[0]['ok'])

    def test_failed_approval_publication_does_not_release_business_effect(self):
        self.ledger.accept(self.item())
        self.ledger.accept(self.review())
        self.ledger.emit = lambda event: (_ for _ in ()).throw(RuntimeError('Transcript full'))
        with self.assertRaisesRegex(RuntimeError, 'Transcript full'):
            self.ledger.accept(self.review('approved'))
        self.assertTrue(self.execute().get('error'))
        self.assertFalse((self.workspace.projects.path(self.pid) / 'official.txt').exists())

    def test_http_bridge_waits_for_live_receipt_and_close_wakes_it(self):
        self.ledger.accept(self.item())
        self.ledger.accept(self.review())
        results = []
        thread = threading.Thread(target=lambda: results.append(self.execute()))
        thread.start()
        time.sleep(.03)
        self.assertFalse((self.workspace.projects.path(self.pid) / 'official.txt').exists())
        self.ledger.accept(self.review('approved'))
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertTrue(results[0]['ok'])
        self.ledger.close()
        self.ledger.accept(self.review('approved', item_id='late', review_id='late'))
        with self.assertRaises(InterruptedError):
            self.execute()

    def test_cancellation_rejects_late_approved_and_cross_project_arguments(self):
        self.approve_native()
        altered = copy.deepcopy(self.args)
        altered['parameters']['project_id'] = '0' * 32
        with self.assertRaisesRegex(ValueError, 'current project'):
            self.execute(altered)
        self.cancel.set()
        with self.assertRaises(InterruptedError):
            self.execute()


class OfficialApprovalManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Workspace(Path(self.tmp.name))
        self.workspace.operation_url = 'http://127.0.0.1:8765'
        self.pid = self.workspace.projects.create('Manager official review')['id']
        self.agents = self.workspace.agents
        self.registry = self.workspace.operations
        self.addCleanup(self.registry.close)
        patcher = patch('analog_factory.agents.shutil.which', return_value='/fake/codex')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.session = self.agents.create(self.pid, {'backend': 'codex', 'mode': 'workspace-write'})
        self.sid = self.session['id']

    def wait(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            record = self.agents.get(self.pid, self.sid)
            if (self.pid, self.sid) not in self.agents.active:
                return record
            time.sleep(.01)
        self.fail('Official manager did not finish')

    def test_codex_metadata_is_official_without_project_model_api(self):
        backends = {item['id']: item for item in self.agents.backends(self.pid)['backends']}
        review = backends['codex']['approval_review']
        self.assertEqual(review['provider'], 'codex_official')
        self.assertTrue(review['available'])
        self.assertFalse(review['single_action'])
        self.assertFalse(review['runtime_toggle'])
        self.assertEqual(review['model'], '')
        self.assertEqual(backends['claude']['approval_review']['provider'], 'project_model_api')

    def test_codex_one_shot_never_calls_custom_reviewer(self):
        with patch.object(Provider, 'complete', side_effect=AssertionError('custom reviewer called')):
            with self.assertRaisesRegex(ValueError, 'official|Official'):
                self.agents.review_approval(self.pid, self.sid, 'anything', authorized=True)

    def test_auto_routes_through_ledger_and_off_returns_to_manual_driver(self):
        calls = []
        def driver(*args, official_approval=None, operation_bridge=None, **kwargs):
            calls.append((args[5], official_approval))
            if official_approval:
                official_approval.bind('native-thread', 'native-turn')
                raw = {'operation_id': 'file.write', 'parameters': {'path': 'manager.txt', 'content': 'official'}}
                common = {'threadId': 'native-thread', 'turnId': 'native-turn'}
                official_approval.accept({'method': 'item/started', 'params': {**common,
                    'item': {'id': 'item', 'type': 'mcpToolCall', 'server': 'analog_factory',
                        'tool': 'invoke_operation', 'arguments': raw}}})
                review = {**common, 'reviewId': 'review', 'targetItemId': 'item', 'action': {
                    'type': 'mcpToolCall', 'server': 'analog_factory', 'toolName': 'invoke_operation'}}
                official_approval.accept({'method': 'item/autoApprovalReview/started', 'params': {
                    **review, 'review': {'status': 'inProgress'}}})
                official_approval.accept({'method': 'item/autoApprovalReview/completed', 'params': {
                    **review, 'review': {'status': 'approved', 'rationale': 'Authorized write.'}, 'decisionSource': 'agent'}})
                result = self.registry.invoke_agent_tool(operation_bridge['token'], 'invoke_operation', raw)
                self.assertTrue(result['ok'], result)
            return {'reply': 'Done', 'backend_session_id': 'native-thread'}
        self.agents.set_auto_approval(self.pid, self.sid, True, authorized=True)
        with patch('analog_factory.agents.run_cli_turn', driver), \
                patch.object(Provider, 'complete', side_effect=AssertionError('custom reviewer called')):
            self.agents.send(self.pid, self.sid, {'message': 'Write manager.txt'}, human_authorized=True)
            self.assertEqual(self.wait()['status'], 'succeeded')
            self.agents.set_auto_approval(self.pid, self.sid, False, authorized=True)
            self.agents.send(self.pid, self.sid, {'message': 'Continue'}, human_authorized=True)
            self.assertEqual(self.wait()['status'], 'succeeded')
        self.assertIsInstance(calls[0][1], OfficialApprovalLedger)
        self.assertEqual(calls[1], ('native-thread', None))

    def test_auto_cannot_change_during_active_turn(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def driver(*args, **kwargs):
            started.set()
            release.wait(2)
            return {'reply': 'Done'}
        with patch('analog_factory.agents.run_cli_turn', driver):
            self.agents.send(self.pid, self.sid, {'message': 'Wait'}, human_authorized=True)
            self.assertTrue(started.wait(1))
            for enabled in (True, False):
                with self.assertRaisesRegex(RuntimeError, '[Ss]top|active'):
                    self.agents.set_auto_approval(self.pid, self.sid, enabled, authorized=True)
            release.set()
            self.wait()

    def test_old_and_nonhuman_native_history_cannot_inherit_official_authority(self):
        for trusted in (False, None):
            record = self.agents._load(self.pid, self.sid)
            turn = {'message': 'Old untrusted request'}
            if trusted is not None:
                turn['human_authorized'] = trusted
            record['turns'] = [turn]
            self.agents._save(self.pid, record)
            with self.assertRaisesRegex(PermissionError, 'new|human'):
                self.agents.set_auto_approval(self.pid, self.sid, True, authorized=True)

    def test_untrusted_send_after_auto_off_prevents_later_reenable(self):
        self.agents.set_auto_approval(self.pid, self.sid, True, authorized=True)
        with self.assertRaises(PermissionError):
            self.agents.send(self.pid, self.sid, {'message': 'External automation'})
        self.agents.set_auto_approval(self.pid, self.sid, False, authorized=True)
        with patch('analog_factory.agents.run_cli_turn', return_value={'reply': 'Done'}):
            self.agents.send(self.pid, self.sid, {'message': 'External automation'})
            self.wait()
        with self.assertRaises(PermissionError):
            self.agents.set_auto_approval(self.pid, self.sid, True, authorized=True)
