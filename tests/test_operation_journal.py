"""Crash and restart behavior at the actual operation handler boundary."""
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from analog_factory.operations import InvocationContext, Operation, OperationRegistry
from analog_factory.service import Workspace


class OperationJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = Workspace(self.root)

    def registry(self, handler=None, **options):
        registry = OperationRegistry(Workspace(self.root))
        self.addCleanup(registry.close)
        registry.register(Operation('example.effect', 'Effect', 'Record an actual effect.', 'example',
            {'type': 'object'}, {'type': 'object'}, handler or self.effect,
            permissions=('write',), **options))
        return registry

    def effect(self, context, params):
        with (self.root / 'effects.txt').open('a') as stream:
            stream.write('effect\n')
        return {'recorded': True}

    def test_completed_response_and_event_cursor_survive_registry_restart(self):
        first_registry = self.registry()
        first = first_registry.invoke('example.effect', {'value': 3}, idempotency_key='once')
        packet = first_registry.subscribe_events()
        first_registry.close()
        second_registry = self.registry()
        second = second_registry.invoke('example.effect', {'value': 3}, idempotency_key='once')
        self.assertTrue(first['ok'], first)
        self.assertEqual(first, second)
        self.assertEqual((self.root / 'effects.txt').read_text(), 'effect\n')
        self.assertEqual(packet, second_registry.subscribe_events())
        second_registry.invoke('example.effect', {'value': 4})
        self.assertGreater(second_registry.subscribe_events()['cursor'], packet['cursor'])

    def test_real_process_crash_after_effect_never_reexecutes_reserved_key(self):
        source = '''
import os, sys
from pathlib import Path
from analog_factory.operations import Operation, OperationRegistry
from analog_factory.service import Workspace
root = Path(sys.argv[1])
registry = OperationRegistry(Workspace(root))
def crash(context, params):
    with (root / 'effects.txt').open('a') as stream:
        stream.write('effect\\n')
        stream.flush()
        os.fsync(stream.fileno())
    os._exit(73)
registry.register(Operation('example.effect', 'Effect', 'Record an actual effect.', 'example',
    {'type': 'object'}, {'type': 'object'}, crash, permissions=('write',)))
registry.invoke('example.effect', {'value': 3}, idempotency_key='crashed')
'''
        crashed = subprocess.run([sys.executable, '-c', source, str(self.root)],
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(crashed.returncode, 73, crashed.stderr)
        for _ in range(2):
            registry = self.registry()
            result = registry.invoke('example.effect', {'value': 3}, idempotency_key='crashed')
            self.assertFalse(result['ok'], result)
            self.assertEqual(result['error']['code'], 'OUTCOME_UNKNOWN')
            registry.close()
        self.assertEqual((self.root / 'effects.txt').read_text(), 'effect\n')

    def test_live_concurrent_retry_waits_for_exact_original_response(self):
        entered, release = threading.Event(), threading.Event()
        def handler(context, params):
            entered.set()
            release.wait(3)
            return self.effect(context, params)
        registry = self.registry(handler)
        results = []
        first = threading.Thread(target=lambda: results.append(registry.invoke('example.effect', {}, idempotency_key='live')))
        second = threading.Thread(target=lambda: results.append(registry.invoke('example.effect', {}, idempotency_key='live')))
        first.start()
        self.assertTrue(entered.wait(2))
        second.start()
        release.set()
        first.join(3)
        second.join(3)
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0]['ok'], results)
        self.assertEqual(results[0], results[1])
        self.assertEqual((self.root / 'effects.txt').read_text(), 'effect\n')

    def test_live_waiter_receives_sensitive_result_without_persisting_it(self):
        entered, waiting, release = threading.Event(), threading.Event(), threading.Event()
        def handler(context, params):
            entered.set()
            release.wait(3)
            self.effect(context, params)
            return {'token': 'live-result-credential'}
        registry = self.registry(handler)
        original_wait = registry.request_condition.wait
        def wait(*args, **kwargs):
            waiting.set()
            return original_wait(*args, **kwargs)
        results = []
        def invoke():
            results.append(registry.invoke('example.effect', {}, idempotency_key='live-sensitive'))
        with patch.object(registry.request_condition, 'wait', side_effect=wait):
            first, second = threading.Thread(target=invoke), threading.Thread(target=invoke)
            first.start()
            self.assertTrue(entered.wait(2))
            second.start()
            self.assertTrue(waiting.wait(2))
            release.set()
            first.join(3)
            second.join(3)
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0]['ok'], results)
        self.assertEqual(results[0], results[1])
        self.assertEqual((self.root / 'effects.txt').read_text(), 'effect\n')
        self.assertEqual(registry.invoke('example.effect', {}, idempotency_key='live-sensitive')['error']['code'], 'OUTCOME_UNKNOWN')

    def test_close_returns_without_waiting_for_running_handler(self):
        import time
        entered, release = threading.Event(), threading.Event()
        def handler(context, params):
            entered.set()
            release.wait(3)
            return self.effect(context, params)
        registry = self.registry(handler)
        result = []
        thread = threading.Thread(target=lambda: result.append(registry.invoke('example.effect', {}, idempotency_key='closing')))
        thread.start()
        self.assertTrue(entered.wait(2))
        started = time.monotonic()
        registry.close()
        duration = time.monotonic() - started
        release.set()
        thread.join(3)
        self.assertLess(duration, .2)
        self.assertTrue(result[0]['ok'], result)

    def test_event_storage_failure_does_not_claim_rollback_or_repeat_effect(self):
        registry = self.registry()
        with patch.object(registry.journal, 'append_event', side_effect=OSError('disk full')):
            result = registry.invoke('example.effect', {}, idempotency_key='event-full')
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error']['code'], 'OUTCOME_UNKNOWN', result)
        self.assertIsNone(result['data'])
        self.assertEqual(registry.invoke('example.effect', {}, idempotency_key='event-full'), result)
        self.assertEqual((self.root / 'effects.txt').read_text(), 'effect\n')

    def test_journal_refuses_directory_and_database_symlinks(self):
        from analog_factory.operation_journal import OperationJournal
        target = self.root / 'other-directory'
        target.mkdir()
        directory = self.root / '.operations'
        directory.symlink_to(target, target_is_directory=True)
        with self.assertRaises(OSError):
            OperationJournal(self.root)
        self.assertEqual(list(target.iterdir()), [])
        directory.unlink()
        directory.mkdir()
        victim = self.root / 'other-file'
        victim.write_text('untouched')
        (directory / 'journal.sqlite3').symlink_to(victim)
        with self.assertRaises(OSError):
            OperationJournal(self.root)
        self.assertEqual(victim.read_text(), 'untouched')

    def test_permissions_project_and_source_are_checked_before_replay(self):
        pid = self.workspace.projects.create('Journal ownership')['id']
        other = self.workspace.projects.create('Other project')['id']
        registry = self.registry()
        original = InvocationContext(principal='same-owner', project_id=pid,
                                     permissions=frozenset({'read', 'write'}))
        first = registry.invoke('example.effect', {}, original, idempotency_key='bound')
        self.assertTrue(first['ok'], first)
        restarted = self.registry()
        denied = InvocationContext(principal='same-owner', project_id=pid, permissions=frozenset({'read'}))
        self.assertEqual(restarted.invoke('example.effect', {}, denied, idempotency_key='bound')['error']['code'], 'PERMISSION_DENIED')
        for changed in (InvocationContext(principal='same-owner', project_id=other, permissions=original.permissions),
                        InvocationContext(principal='same-owner', project_id=pid),
                        InvocationContext(principal='same-owner', project_id=pid, permissions=original.permissions, source='agent')):
            result = restarted.invoke('example.effect', {}, changed, idempotency_key='bound')
            self.assertEqual(result['error']['code'], 'IDEMPOTENCY_CONFLICT', result)
        self.assertEqual((self.root / 'effects.txt').read_text(), 'effect\n')

    def test_cached_frontend_response_requires_a_current_browser(self):
        registry = self.registry()
        connected = registry.sessions.connect({'tab': 'overview'})
        registry.register(Operation('ui.journal_test', 'Browser test', 'Acknowledge a browser action.', 'frontend',
            {'type': 'object', 'properties': {'client_id': {'type': 'string'}}, 'required': ['client_id']},
            {'type': 'object'}, lambda c, p: {'acknowledged': True}, kind='frontend', permissions=('ui',)))
        params = {'client_id': connected['client_id']}
        first = registry.invoke('ui.journal_test', params, idempotency_key='browser')
        self.assertTrue(first['ok'], first)
        restarted = self.registry()
        restarted.register(registry.operations['ui.journal_test'])
        result = restarted.invoke('ui.journal_test', params, idempotency_key='browser')
        self.assertEqual(result['error']['code'], 'FRONTEND_DISCONNECTED', result)

    def test_private_storage_contains_no_raw_request_key_or_secret_inputs(self):
        registry = self.registry()
        result = registry.invoke('example.effect', {'api_key': 'private-value-8675309', 'input': 'unpersisted-input'},
                                 idempotency_key='private-key-123456')
        self.assertTrue(result['ok'], result)
        directory = self.root / '.operations'
        self.assertTrue(directory.is_dir())
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        for path in directory.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            stored = path.read_bytes()
            for secret in (b'private-value-8675309', b'private-key-123456', b'unpersisted-input'):
                self.assertNotIn(secret, stored)

    def test_volatile_response_rejects_request_keys_before_reading_private_output(self):
        registry = self.registry(volatile_response=True)
        result = registry.invoke('example.effect', {}, idempotency_key='volatile')
        self.assertEqual(result['error']['code'], 'INVALID_ARGUMENT', result)
        self.assertFalse((self.root / 'effects.txt').exists())
        self.assertTrue(registry.invoke('example.effect', {})['ok'])

    def test_sensitive_response_is_not_persisted_and_retry_cannot_repeat_effect(self):
        def handler(context, params):
            self.effect(context, params)
            return {'reply': 'received ' + params['api_key']}
        registry = self.registry(handler)
        first = registry.invoke('example.effect', {'api_key': 'sensitive-output-987'}, idempotency_key='secret')
        self.assertTrue(first['ok'], first)
        restarted = self.registry(handler)
        result = restarted.invoke('example.effect', {'api_key': 'sensitive-output-987'}, idempotency_key='secret')
        self.assertEqual(result['error']['code'], 'OUTCOME_UNKNOWN', result)
        self.assertEqual((self.root / 'effects.txt').read_text(), 'effect\n')
        for path in (self.root / '.operations').iterdir():
            self.assertNotIn(b'sensitive-output-987', path.read_bytes())

    def test_storage_failure_after_effect_reports_unknown_and_keeps_reservation(self):
        registry = self.registry()
        with patch.object(registry.journal, 'finalize', side_effect=OSError('disk full')):
            result = registry.invoke('example.effect', {}, idempotency_key='disk-full')
        self.assertEqual(result['error']['code'], 'OUTCOME_UNKNOWN', result)
        restarted = self.registry()
        replay = restarted.invoke('example.effect', {}, idempotency_key='disk-full')
        self.assertEqual(replay['error']['code'], 'OUTCOME_UNKNOWN', replay)
        self.assertEqual((self.root / 'effects.txt').read_text(), 'effect\n')

    def test_reservation_failure_does_not_execute_handler(self):
        registry = self.registry()
        with patch.object(registry.journal, 'reserve', side_effect=OSError('disk full')):
            result = registry.invoke('example.effect', {}, idempotency_key='no-room')
        self.assertEqual(result['error']['code'], 'UNAVAILABLE', result)
        self.assertFalse((self.root / 'effects.txt').exists())

    def test_process_interrupt_during_handler_retains_unknown_outcome(self):
        def interrupted(context, params):
            self.effect(context, params)
            raise KeyboardInterrupt()
        registry = self.registry(interrupted)
        with self.assertRaises(KeyboardInterrupt):
            registry.invoke('example.effect', {}, idempotency_key='interrupt')
        result = self.registry().invoke('example.effect', {}, idempotency_key='interrupt')
        self.assertEqual(result['error']['code'], 'OUTCOME_UNKNOWN', result)
        self.assertEqual((self.root / 'effects.txt').read_text(), 'effect\n')

    def test_task_watcher_retries_transient_journal_failure_without_losing_final_event(self):
        registry = self.registry()
        original = registry.journal.append_event
        attempts = []
        def append(event):
            attempts.append(event)
            if len(attempts) == 1:
                raise OSError('temporarily unavailable')
            return original(event)
        status = {'ok': True, 'status': 'succeeded', 'data': {'updated_at': 'done'}}
        with patch.object(registry.journal, 'append_event', side_effect=append), \
                patch.object(registry, 'get_operation_status', return_value=status):
            registry._watch_task('job:project:record')
        self.assertEqual(len(attempts), 2)
        self.assertEqual(registry.subscribe_events()['events'][-1]['status'], 'succeeded')

    def test_trace_mapping_survives_restart_and_uses_authoritative_task_record(self):
        pid = self.workspace.projects.create('Durable task trace')['id']
        registry = self.registry()
        registry.register(Operation('example.start', 'Start', 'Start a real stored job.', 'example',
            {'type': 'object'}, {'type': 'object'},
            lambda c, p: registry.workspace.jobs.start(registry.workspace.projects.path(pid), 'journal-test',
                lambda job, cancel, update: {'status': 'succeeded'}),
            permissions=('execute',), asynchronous=True, task_kind='job'))
        result = registry.invoke('example.start', {'project_id': pid}, idempotency_key='job')
        self.assertTrue(result['ok'], result)
        restarted = self.registry()
        status = restarted.get_operation_status(result['task_id'])
        self.assertTrue(status['ok'], status)
        self.assertEqual(status['trace_id'], result['trace_id'])
        self.assertEqual(status['operation_id'], 'example.start')

    def test_retention_bounds_completed_entries_events_and_protects_unknown_claims(self):
        from analog_factory.operation_journal import OperationJournal
        journal = OperationJournal(self.root, replay_limit=2, event_limit=2, task_limit=2, claim_limit=3)
        envelope = {'ok': True, 'trace_id': 't', 'operation_id': 'example.effect', 'data': {}}
        for number in range(4):
            key = str(number)
            self.assertTrue(journal.reserve(key, 'fingerprint', envelope)['acquired'])
            journal.finalize(key, 'fingerprint', envelope)
            journal.append_event({'operation_id': 'example.effect', 'status': 'succeeded'})
            journal.save_task(key, {'operation_id': 'example.effect', 'trace_id': key})
        self.assertEqual(len(journal.snapshot()['events']), 2)
        self.assertEqual(journal.snapshot()['cursor'], 4)
        self.assertEqual(len(journal.snapshot()['tasks']), 2)
        self.assertTrue(journal.reserve('unknown-1', 'fingerprint', envelope)['acquired'])
        self.assertTrue(journal.reserve('unknown-2', 'fingerprint', envelope)['acquired'])
        self.assertTrue(journal.reserve('unknown-3', 'fingerprint', envelope)['acquired'])
        with self.assertRaises(OSError):
            journal.reserve('would-repeat', 'fingerprint', envelope)
        self.assertFalse(journal.reserve('unknown-1', 'fingerprint', envelope)['acquired'])
        self.assertEqual(journal.reserve('unknown-1', 'fingerprint', envelope)['response']['error']['code'], 'OUTCOME_UNKNOWN')


if __name__ == '__main__':
    unittest.main()
