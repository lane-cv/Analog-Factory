import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

from analog_factory.agent_cli import run_cli_turn


@unittest.skipUnless(os.name == 'posix', 'CLI process groups require POSIX')
class AgentCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.executable = self.root / 'fake-agent'
        self.events = []
        self.cancel = threading.Event()

    def fixture(self, code):
        self.executable.write_text(
            f'#!{sys.executable}\n'
            'import json, os, sys, time\nfrom pathlib import Path\n'
            "def event(data): print(json.dumps(data), flush=True)\n" + code)
        self.executable.chmod(0o700)

    def run_turn(self, backend='codex', **kwargs):
        options = dict(backend=backend, executable=str(self.executable),
                       project_root=self.project, model='fixture-model',
                       mode='read-only', backend_session_id=None,
                       prompt='Private prompt\nwith unicode Ω',
                       emit=self.events.append, cancel=self.cancel, timeout=3)
        options.update(kwargs)
        return run_cli_turn(**options)

    def successful_codex(self):
        self.fixture("""
Path('invocation.json').write_text(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'stdin': sys.stdin.read()}))
event({'type': 'thread.started', 'thread_id': 'codex-thread-123'})
event({'type': 'turn.started'})
event({'type': 'item.started', 'item': {'id': 'i1', 'type': 'command_execution', 'command': 'cat design.cir', 'status': 'in_progress'}})
event({'type': 'item.completed', 'item': {'id': 'i1', 'type': 'command_execution', 'command': 'cat design.cir', 'aggregated_output': 'R1 in out 1k', 'exit_code': 0, 'status': 'completed'}})
event({'type': 'item.completed', 'item': {'id': 'i2', 'type': 'agent_message', 'text': 'A 1k resistor.'}})
event({'type': 'turn.completed', 'usage': {'input_tokens': 12, 'cached_input_tokens': 2, 'output_tokens': 4}})
""")

    def test_codex_stream_session_tools_and_prompt_transport(self):
        self.successful_codex()
        result = self.run_turn()
        invocation = json.loads((self.project / 'invocation.json').read_text())
        self.assertEqual(invocation['cwd'], str(self.project))
        self.assertEqual(invocation['stdin'], 'Private prompt\nwith unicode Ω')
        argv = invocation['argv']
        self.assertEqual(argv[0], 'exec')
        self.assertIn('--json', argv)
        self.assertIn('--skip-git-repo-check', argv)
        self.assertIn('sandbox_mode="read-only"', argv)
        self.assertIn('approval_policy="never"', argv)
        self.assertEqual(argv[-1], '-')
        self.assertNotIn('Private prompt', ' '.join(argv))
        self.assertEqual(result['reply'], 'A 1k resistor.')
        self.assertEqual(result['backend_session_id'], 'codex-thread-123')
        self.assertEqual(result['usage']['output_tokens'], 4)
        self.assertEqual(self.events[0], {'type': 'session', 'backend_session_id': 'codex-thread-123'})
        self.assertTrue(any(e['type'] == 'tool_call' and e['tool'] == 'command_execution' for e in self.events))
        self.assertTrue(any(e['type'] == 'tool_result' and e['text'] == 'R1 in out 1k' for e in self.events))

    def test_absent_cli_usage_remains_unknown(self):
        self.fixture("""
sys.stdin.read()
event({'type': 'thread.started', 'thread_id': 'native-1'})
event({'type': 'item.completed', 'item': {'id': 'm1', 'type': 'agent_message', 'text': 'Done'}})
event({'type': 'turn.completed'})
""")
        self.assertIsNone(self.run_turn()['usage'])

    def test_codex_reconnection_notices_do_not_abort_a_recoverable_turn(self):
        self.fixture("""
sys.stdin.read()
event({'type': 'thread.started', 'thread_id': 'native-reconnect'})
event({'type': 'turn.started'})
event({'type': 'error', 'message': 'Reconnecting... 2/5 (workspace routing discovery failed)'})
time.sleep(0.05)
Path('continued-after-reconnect').touch()
event({'type': 'item.completed', 'item': {'id': 'reply', 'type': 'agent_message', 'text': 'Recovered reply'}})
event({'type': 'turn.completed', 'usage': {'input_tokens': 4, 'output_tokens': 2}})
""")
        result = self.run_turn()
        self.assertEqual(result['reply'], 'Recovered reply')
        self.assertTrue((self.project / 'continued-after-reconnect').exists())
        self.assertIn({'type': 'status', 'status': 'warning',
                       'text': 'Reconnecting... 2/5 (workspace routing discovery failed)'}, self.events)

    def test_codex_reconnection_then_terminal_failure_uses_final_error(self):
        self.fixture("""
sys.stdin.read()
event({'type': 'thread.started', 'thread_id': 'native-reconnect'})
event({'type': 'error', 'message': 'Reconnecting... 2/5 (workspace routing discovery failed)'})
event({'type': 'turn.failed', 'error': {'message': 'retries exhausted: final connection failure'}})
""")
        with self.assertRaisesRegex(RuntimeError, 'retries exhausted: final connection failure'):
            self.run_turn()

    def test_codex_reconnection_without_completion_remains_a_failure(self):
        self.fixture("""
sys.stdin.read()
event({'type': 'thread.started', 'thread_id': 'native-reconnect'})
event({'type': 'error', 'message': 'workspace routing discovery failed'})
""")
        with self.assertRaisesRegex(RuntimeError, 'without a successful completion event.*workspace routing discovery failed'):
            self.run_turn()

    def test_codex_resume_reapplies_permissions_and_actual_session(self):
        self.successful_codex()
        self.run_turn(mode='workspace-write', backend_session_id='saved-session-456')
        argv = json.loads((self.project / 'invocation.json').read_text())['argv']
        self.assertEqual(argv[:2], ['exec', 'resume'])
        self.assertEqual(argv[-2:], ['saved-session-456', '-'])
        self.assertIn('sandbox_mode="workspace-write"', argv)
        self.assertIn('approval_policy="never"', argv)
        self.assertFalse(any('bypass' in arg or arg == '--last' for arg in argv))

    def test_claude_tools_resume_and_final_usage(self):
        self.fixture("""
Path('invocation.json').write_text(json.dumps({'argv': sys.argv[1:], 'stdin': sys.stdin.read()}))
event({'type': 'system', 'subtype': 'init', 'session_id': 'claude-session-123', 'tools': ['Read', 'Glob', 'Grep', 'Edit', 'Write']})
event({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 't1', 'name': 'Read', 'input': {'file_path': 'design.cir'}}]}, 'session_id': 'claude-session-123'})
event({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'R1 in out 1k', 'is_error': False}]}})
event({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'A resistor.'}]}})
event({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'A resistor.', 'session_id': 'claude-session-123', 'usage': {'input_tokens': 10, 'output_tokens': 3}})
""")
        for mode, wanted_tools in [('read-only', 'Read,Glob,Grep'),
                                   ('workspace-write', 'Read,Glob,Grep,Edit,Write')]:
            with self.subTest(mode=mode):
                self.events.clear()
                result = self.run_turn('claude', mode=mode, backend_session_id='claude-saved-456')
                invocation = json.loads((self.project / 'invocation.json').read_text())
                argv = invocation['argv']
                self.assertEqual(argv[argv.index('--tools') + 1], wanted_tools)
                self.assertEqual(argv[argv.index('--allowedTools') + 1], wanted_tools)
                self.assertEqual(argv[argv.index('--permission-mode') + 1], 'dontAsk')
                self.assertEqual(argv[argv.index('--resume') + 1], 'claude-saved-456')
                self.assertEqual(argv[argv.index('--output-format') + 1], 'stream-json')
                self.assertIn('--restricted', argv)
                self.assertIn('--strict-mcp-config', argv)
                self.assertIn('--verbose', argv)
                self.assertIn('-p', argv)
                self.assertFalse(any('bypass' in arg or 'dangerously' in arg for arg in argv))
                self.assertEqual(invocation['stdin'], 'Private prompt\nwith unicode Ω')
                self.assertEqual(result['reply'], 'A resistor.')
                self.assertEqual(result['usage'], {'input_tokens': 10, 'output_tokens': 3})
                self.assertEqual(self.events[0]['backend_session_id'], 'claude-session-123')
                self.assertTrue(any(e['type'] == 'tool_call' and e['tool'] == 'Read' for e in self.events))
                self.assertTrue(any(e['type'] == 'tool_result' and e['text'] == 'R1 in out 1k' for e in self.events))
                self.assertEqual(sum(e['type'] == 'message' for e in self.events), 1)

    def test_failed_or_missing_final_events_never_succeed(self):
        cases = [
            ('codex', "event({'type': 'turn.failed', 'error': {'message': 'quota exhausted'}})", 'quota exhausted'),
            ('codex', "event({'type': 'error', 'message': 'authentication needed'})", 'authentication needed'),
            ('claude', "event({'type': 'result', 'subtype': 'error_during_execution', 'is_error': True, 'errors': ['access denied']})", 'access denied'),
            ('codex', "event({'type': 'thread.started', 'thread_id': 'keep-for-resume'})", 'completion'),
            ('claude', "event({'type': 'system', 'subtype': 'init', 'session_id': 'keep-for-resume'})", 'completion'),
            ('codex', "print('not json', flush=True)", 'JSON'),
            ('claude', "event(['invalid'])", 'event'),
            ('codex', "event({'type': 'item.completed', 'item': []})", 'event'),
            ('claude', "event({'type': 'result', 'subtype': 'success', 'result': 42})", 'event'),
        ]
        for backend, code, error in cases:
            with self.subTest(backend=backend, code=code):
                self.fixture(code)
                with self.assertRaisesRegex(RuntimeError, error):
                    self.run_turn(backend)

    def test_nonzero_exit_after_success_includes_bounded_stderr(self):
        self.fixture("""
sys.stderr.write('x' * 200000 + '\\ncredentials expired\\n')
sys.stderr.flush()
event({'type': 'turn.completed', 'usage': {}})
raise SystemExit(7)
""")
        with self.assertRaisesRegex(RuntimeError, 'credentials expired') as error:
            self.run_turn()
        self.assertIn('7', str(error.exception))
        self.assertLess(len(str(error.exception)), 20000)

    def test_claude_malformed_error_flag_cannot_report_success(self):
        self.fixture("""
event({'type': 'result', 'subtype': 'success', 'is_error': 'false', 'result': 'Invalid reply', 'session_id': 'claude-session', 'usage': {}})
""")
        with self.assertRaisesRegex(RuntimeError, 'event'):
            self.run_turn('claude')

    def test_failed_command_keeps_its_output_and_failure_status(self):
        self.fixture("""
event({'type': 'thread.started', 'thread_id': 'codex-thread'})
event({'type': 'item.completed', 'item': {'id': 'command', 'type': 'command_execution', 'command': 'cat missing.cir', 'aggregated_output': 'No such file', 'exit_code': 1, 'status': 'failed'}})
event({'type': 'item.completed', 'item': {'id': 'reply', 'type': 'agent_message', 'text': 'The file is missing.'}})
event({'type': 'turn.completed', 'usage': {'input_tokens': 5, 'output_tokens': 2}})
""")
        result = self.run_turn()
        self.assertEqual(result['reply'], 'The file is missing.')
        self.assertIn({'type': 'tool_result', 'tool': 'command_execution',
                       'text': 'No such file', 'status': 'failed'}, self.events)

    def test_rejects_invalid_modes_backends_and_session_option_injection(self):
        self.fixture("Path('started').touch()")
        for options in [dict(mode='confirm'), dict(mode='danger-full-access'),
                        dict(backend='unknown'), dict(backend_session_id='--last')]:
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    self.run_turn(**options)
        self.assertFalse((self.project / 'started').exists())

    def test_pre_cancelled_turn_never_launches(self):
        self.fixture("Path('started').touch()")
        self.cancel.set()
        with self.assertRaises(InterruptedError):
            self.run_turn()
        self.assertFalse((self.project / 'started').exists())

    def test_cancel_and_timeout_kill_children_even_if_parent_exits(self):
        for reason in ['cancel', 'timeout']:
            with self.subTest(reason=reason):
                self.cancel.clear()
                self.fixture("""
import subprocess
subprocess.Popen([sys.executable, '-c', "import signal, time; from pathlib import Path; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(1); Path('escaped').touch()"])
event({'type': 'thread.started', 'thread_id': 'recover-before-cancel'})
time.sleep(10)
""")
                def on_event(event):
                    self.events.append(event)
                    if reason == 'cancel' and event['type'] == 'session':
                        self.cancel.set()
                with self.assertRaises(InterruptedError if reason == 'cancel' else TimeoutError):
                    self.run_turn(emit=on_event, timeout=0.2)
                self.assertIn({'type': 'session', 'backend_session_id': 'recover-before-cancel'}, self.events)
                time.sleep(1.05)
                self.assertFalse((self.project / 'escaped').exists())

    def test_timeout_while_child_never_reads_large_stdin(self):
        self.fixture("sys.stderr.write('x' * 200000); sys.stderr.flush(); time.sleep(10)")
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            self.run_turn(prompt='p' * 500000, timeout=0.15)
        self.assertLess(time.monotonic() - started, 2)

    def test_unbounded_stdout_is_rejected_before_timeout(self):
        self.fixture("sys.stdout.write('x' * 2000000); sys.stdout.flush(); time.sleep(10)")
        with self.assertRaisesRegex(RuntimeError, 'limit'):
            self.run_turn()

    def test_emit_failure_cleans_up_process(self):
        self.fixture("""
event({'type': 'thread.started', 'thread_id': 'session'})
time.sleep(1)
Path('escaped').touch()
""")
        def broken_sink(event):
            raise RuntimeError('event storage full')
        with self.assertRaisesRegex(RuntimeError, 'event storage full'):
            self.run_turn(emit=broken_sink)
        time.sleep(1.05)
        self.assertFalse((self.project / 'escaped').exists())


if __name__ == '__main__':
    unittest.main()
