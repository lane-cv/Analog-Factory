import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from analog_factory.service import Workspace


class AgentSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Workspace(Path(self.temp.name))
        self.pid = self.workspace.projects.create('Agent project')['id']
        self.other = self.workspace.projects.create('Other project')['id']
        self.workspace.save_settings({'provider': {'model': 'test', 'base_url': 'http://localhost:1/v1'}})
        self.assertTrue(hasattr(self.workspace, 'agents'), 'Workspace needs a persistent agent coordinator')
        self.agents = self.workspace.agents
        self.addCleanup(self.agents.close)

    def session(self, **options):
        return self.agents.create(self.pid, {'backend': 'api', **options})

    def wait(self, sid, wanted=('succeeded', 'failed', 'cancelled', 'interrupted', 'timed_out')):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            session = self.agents.get(self.pid, sid)
            if session['status'] in wanted:
                return session
            time.sleep(.01)
        self.fail('Agent did not reach expected status')

    def test_session_history_survives_restart_and_stays_in_its_project(self):
        def driver(provider, history, prompt, workspace, pid, mode, emit, cancel, approve):
            emit({'type': 'message', 'text': 'Inspect the simulation.'})
            return {'reply': 'Inspect the simulation.', 'usage': {'total_tokens': 7},
                    'history': history + [{'role': 'user', 'content': prompt}, {'role': 'assistant', 'content': 'Inspect the simulation.'}]}
        session = self.session()
        with patch('analog_factory.agents.run_api_turn', driver):
            self.agents.send(self.pid, session['id'], {'message': 'Explain', 'files': ['testbenches/pre.cir']})
            result = self.wait(session['id'])
        self.assertEqual(result['turns'][0]['reply'], 'Inspect the simulation.')
        self.assertEqual(result['usage']['total_tokens'], 7)
        again = Workspace(Path(self.temp.name))
        self.addCleanup(again.agents.close)
        self.assertEqual(again.agents.get(self.pid, session['id'])['turns'], result['turns'])
        self.assertEqual(len(again.agents.list(self.pid)), 1)
        self.assertEqual(again.agents.list(self.other), [])
        with self.assertRaises(FileNotFoundError):
            self.agents.get(self.other, session['id'])
        self.assertNotIn('history', result)

    def test_approval_is_explicit_scoped_and_cancel_unblocks_it(self):
        mutation = []
        def driver(provider, history, prompt, workspace, pid, mode, emit, cancel, approve):
            if approve('write_file', {'path': 'netlists/a.cir', 'content': 'R1 a 0 1k'}):
                mutation.append(True)
            return {'reply': 'Done', 'history': []}
        session = self.session(mode='confirm')
        with patch('analog_factory.agents.run_api_turn', driver):
            self.agents.send(self.pid, session['id'], {'message': 'Write a file'})
            pending = self.wait(session['id'], ('waiting_approval',))
            with self.assertRaises(ValueError):
                self.agents.approve(self.pid, session['id'], 'wrong', True)
            with self.assertRaises(ValueError):
                self.agents.approve(self.pid, session['id'], pending['pending_approval']['id'], 'yes')
            self.assertEqual(mutation, [])
            self.agents.cancel(self.pid, session['id'])
            self.wait(session['id'])
        self.assertEqual(mutation, [])
        self.assertIsNone(self.agents.get(self.pid, session['id'])['pending_approval'])

    def test_approved_turn_completes_and_second_turn_replays_history(self):
        seen = []
        def driver(provider, history, prompt, workspace, pid, mode, emit, cancel, approve):
            seen.append(history)
            if not history:
                self.assertTrue(approve('run_simulation', {'netlist': 'testbenches/pre.cir'}))
            return {'reply': 'OK', 'history': [{'role': 'user', 'content': 'previous'}]}
        session = self.session(mode='confirm')
        with patch('analog_factory.agents.run_api_turn', driver):
            self.agents.send(self.pid, session['id'], {'message': 'Simulate'})
            pending = self.wait(session['id'], ('waiting_approval',))
            self.agents.approve(self.pid, session['id'], pending['pending_approval']['id'], True)
            self.wait(session['id'])
            self.agents.send(self.pid, session['id'], {'message': 'Explain'})
            self.wait(session['id'])
        self.assertEqual(seen, [[], [{'role': 'user', 'content': 'previous'}]])

    def test_cancelled_http_turn_cannot_append_late_result_or_start_overlap(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def driver(*args):
            entered.set()
            release.wait(3)
            args[6]({'type': 'message', 'text': 'too late'})
            return {'reply': 'too late', 'history': []}
        session = self.session()
        with patch('analog_factory.agents.run_api_turn', driver):
            self.agents.send(self.pid, session['id'], {'message': 'Wait'})
            self.assertTrue(entered.wait(2))
            self.agents.cancel(self.pid, session['id'])
            self.assertEqual(self.agents.get(self.pid, session['id'])['status'], 'cancelled')
            with self.assertRaises(RuntimeError):
                self.agents.send(self.pid, session['id'], {'message': 'Overlap'})
            release.set()
        self.agents.close()
        result = self.agents.get(self.pid, session['id'])
        self.assertNotIn('too late', json.dumps(result))

    def test_validation_and_provider_binding_prevent_unintended_context_transfers(self):
        with self.assertRaises(ValueError):
            self.session(backend='shell')
        with self.assertRaises(ValueError):
            self.session(mode='workspace-write')
        session = self.session()
        for body in ({'message': ''}, {'message': 'x', 'files': ['../settings.json']},
                     {'message': 'x', 'files': 'all'}, {'message': 'x', 'run_id': '../bad'}):
            with self.subTest(body=body), self.assertRaises((ValueError, FileNotFoundError)):
                self.agents.send(self.pid, session['id'], body)
        self.workspace.save_settings({'provider': {'base_url': 'http://localhost:2/v1'}})
        with self.assertRaises(ValueError):
            self.agents.send(self.pid, session['id'], {'message': 'new endpoint'})
        self.assertEqual(self.agents.get(self.pid, session['id'])['turns'], [])

    def test_combined_message_and_context_limit_rejected_before_start(self):
        session = self.session()
        self.workspace.projects.write(self.pid, 'notes.txt', 'x' * 240000)
        with self.assertRaises(ValueError):
            self.agents.send(self.pid, session['id'], {'message': 'm' * 20000, 'files': ['notes.txt']})
        self.assertEqual(self.agents.get(self.pid, session['id'])['turns'], [])

    def test_restart_marks_unfinished_turn_interrupted(self):
        session = self.session()
        record = self.workspace.projects.path(self.pid) / '.af/agent-sessions' / (session['id'] + '.json')
        value = json.loads(record.read_text())
        value['status'] = 'running'
        value['turns'] = [{'id': 'old', 'message': 'hello', 'status': 'running', 'events': []}]
        record.write_text(json.dumps(value))
        result = self.agents.get(self.pid, session['id'])
        self.assertEqual(result['status'], 'interrupted')
        self.assertEqual(result['turns'][0]['status'], 'interrupted')

    def test_backend_discovery_uses_effective_project_configuration(self):
        self.workspace.save_settings({'provider': {'base_url': '', 'model': ''}, 'tools': {'codex': '/missing/codex'}})
        self.workspace.configure_project(self.pid, {'provider': {'base_url': 'http://localhost:1/v1', 'model': 'project-model'},
                                                    'tools': {'codex': '/bin/true'}})
        global_backends = {b['id']: b for b in self.agents.backends()['backends']}
        project_backends = {b['id']: b for b in self.agents.backends(self.pid)['backends']}
        self.assertFalse(global_backends['api']['available'])
        self.assertFalse(global_backends['codex']['available'])
        self.assertTrue(project_backends['api']['available'])
        self.assertTrue(project_backends['codex']['available'])

    def test_failed_persistence_or_thread_start_does_not_leak_an_active_slot(self):
        session = self.session()
        with patch.object(self.agents, '_save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.agents.send(self.pid, session['id'], {'message': 'hello'})
        self.assertFalse(self.agents.active)
        with patch('threading.Thread.start', side_effect=RuntimeError('no threads')):
            with self.assertRaises(RuntimeError):
                self.agents.send(self.pid, session['id'], {'message': 'hello'})
        self.assertFalse(self.agents.active)

    def test_shutdown_signals_live_worker_even_if_session_file_was_removed(self):
        entered, stopped = threading.Event(), threading.Event()
        def driver(*args):
            entered.set()
            if args[7].wait(2):
                stopped.set()
            raise InterruptedError('shutdown')
        session = self.session()
        with patch('analog_factory.agents.run_api_turn', driver):
            self.agents.send(self.pid, session['id'], {'message': 'wait'})
            self.assertTrue(entered.wait(2))
            record = self.workspace.projects.path(self.pid) / '.af/agent-sessions' / (session['id'] + '.json')
            record.unlink()
            self.agents.close()
        self.assertTrue(stopped.is_set())

    def test_cli_native_id_is_retained_after_failed_turn_and_used_on_resume(self):
        sessions = []
        def driver(backend, executable, root, model, mode, session_id, prompt, emit, cancel):
            sessions.append(session_id)
            if session_id is None:
                emit({'type': 'session', 'backend_session_id': 'native-session-1'})
                raise RuntimeError('failed after native session started')
            return {'reply': 'resumed', 'backend_session_id': session_id}
        session = self.agents.create(self.pid, {'backend': 'codex'})
        with patch('analog_factory.agents.shutil.which', return_value='/fake/codex'), patch('analog_factory.agents.run_cli_turn', driver):
            self.agents.send(self.pid, session['id'], {'message': 'first'})
            self.assertEqual(self.wait(session['id'])['backend_session_id'], 'native-session-1')
            self.agents.send(self.pid, session['id'], {'message': 'continue'})
            self.assertEqual(self.wait(session['id'])['turns'][-1]['reply'], 'resumed')
        self.assertEqual(sessions, [None, 'native-session-1'])

    def test_failure_persists_and_run_context_has_real_record(self):
        def driver(provider, history, prompt, *args):
            self.assertIn('RUN CONTEXT', prompt)
            self.assertIn('unavailable', prompt)
            raise RuntimeError('fixture backend failed')
        run = self.workspace.runner.start(self.workspace.projects.path(self.pid), 'simulate', [], metadata={'unavailable': 'fixture'})
        session = self.session()
        with patch('analog_factory.agents.run_api_turn', driver):
            self.agents.send(self.pid, session['id'], {'message': 'What happened?', 'run_id': run['id']})
            result = self.wait(session['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertIn('fixture backend failed', result['turns'][0]['error'])
