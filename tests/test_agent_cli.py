import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from analog_factory.agent_cli import run_cli_turn


@unittest.skipUnless(os.name == 'posix', 'CLI process groups require POSIX')
class AgentCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.claude_config = self.root / 'claude-config'
        self.claude_config.mkdir()
        config_patch = patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.claude_config)})
        config_patch.start()
        self.addCleanup(config_patch.stop)
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

    def test_codex_effort_is_forwarded_on_first_turn_and_resume_without_changing_model(self):
        self.successful_codex()
        for effort in ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'):
            for sid in (None, 'codex-thread-123'):
                with self.subTest(effort=effort, sid=sid):
                    self.run_turn(model='selected-model', effort=effort, backend_session_id=sid)
                    argv = json.loads((self.project / 'invocation.json').read_text())['argv']
                    self.assertIn(f'model_reasoning_effort="{effort}"', argv)
                    self.assertEqual(argv[argv.index('--model') + 1], 'selected-model')
        self.run_turn(effort=None)
        argv = json.loads((self.project / 'invocation.json').read_text())['argv']
        self.assertFalse(any('model_reasoning_effort' in arg for arg in argv))

    def test_claude_effort_forwarding_and_unsupported_values(self):
        self.fixture('''
Path('invocation.json').write_text(json.dumps({'argv':sys.argv[1:]}))
event({'type':'result','subtype':'success','result':'Done','session_id':'claude-session'})
''')
        for sid in (None, 'claude-session'):
            self.run_turn('claude', model='claude-opus-4-8', effort='xhigh', backend_session_id=sid)
            argv = json.loads((self.project / 'invocation.json').read_text())['argv']
            self.assertEqual(argv[argv.index('--effort') + 1], 'xhigh')
        self.run_turn('claude', model='opus', effort=None)
        self.assertNotIn('--effort', json.loads((self.project / 'invocation.json').read_text())['argv'])
        for model, effort in [('haiku', 'high'), ('sonnet', 'high'), ('opus', 'xhigh'),
                              ('claude-sonnet-4-6', 'xhigh'), ('custom', 'high')]:
            with self.subTest(model=model, effort=effort), self.assertRaisesRegex(ValueError, '[Ee]ffort'):
                self.run_turn('claude', model=model, effort=effort)
        for effort in ('', 'default', True, 7, [], {}, 'high\\n--dangerous'):
            with self.subTest(effort=effort), self.assertRaisesRegex(ValueError, '[Ee]ffort'):
                self.run_turn(effort=effort)

    def test_claude_effort_uses_user_alias_configuration_without_changing_model(self):
        self.fixture('''
Path('invocation.json').write_text(json.dumps({'argv':sys.argv[1:]}))
event({'type':'result','subtype':'success','result':'Done','session_id':'claude-session'})
''')
        (self.claude_config / 'settings.json').write_text(json.dumps({'env': {
            'ANTHROPIC_DEFAULT_SONNET_MODEL': 'claude-sonnet-5-5',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'claude-haiku-4-5'}}))
        self.run_turn('claude', model='sonnet', effort='xhigh')
        argv = json.loads((self.project / 'invocation.json').read_text())['argv']
        self.assertEqual(argv[argv.index('--effort') + 1], 'xhigh')
        self.assertEqual(argv[argv.index('--model') + 1], 'sonnet')
        with self.assertRaisesRegex(ValueError, '[Ee]ffort'):
            self.run_turn('claude', model='opus', effort='high')

    def test_explicit_claude_effort_overrides_child_environment_and_default_preserves_it(self):
        self.fixture('''
Path('invocation.json').write_text(json.dumps({'argv':sys.argv[1:],
    'effort':os.environ.get('CLAUDE_CODE_EFFORT_LEVEL')}))
event({'type':'result','subtype':'success','result':'Done','session_id':'claude-session'})
''')
        with patch.dict(os.environ, {'CLAUDE_CODE_EFFORT_LEVEL': 'high'}):
            self.run_turn('claude', model='claude-opus-4-8', effort='low')
            invocation = json.loads((self.project / 'invocation.json').read_text())
            self.assertEqual(invocation['effort'], 'low')
            self.assertEqual(invocation['argv'][invocation['argv'].index('--effort') + 1], 'low')
            self.assertEqual(os.environ['CLAUDE_CODE_EFFORT_LEVEL'], 'high')
            self.run_turn('claude', model='claude-opus-4-8', effort=None)
            invocation = json.loads((self.project / 'invocation.json').read_text())
            self.assertEqual(invocation['effort'], 'high')
            self.assertNotIn('--effort', invocation['argv'])

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
        self.assertFalse(any(arg.startswith('service_tier=') for arg in argv))
        self.assertEqual(argv[-1], '-')
        self.assertNotIn('Private prompt', ' '.join(argv))
        self.assertEqual(result['reply'], 'A 1k resistor.')
        self.assertEqual(result['backend_session_id'], 'codex-thread-123')
        self.assertEqual(result['usage']['output_tokens'], 4)
        self.assertEqual(self.events[0], {'type': 'session', 'backend_session_id': 'codex-thread-123'})
        self.assertTrue(any(e['type'] == 'tool_call' and e['tool'] == 'command_execution' for e in self.events))
        self.assertTrue(any(e['type'] == 'tool_result' and e['text'] == 'R1 in out 1k' for e in self.events))
        self.assertEqual([event.get('tool_call_id') for event in self.events
                          if event['type'] in ('tool_call', 'tool_result')], ['i1', 'i1'])

    def test_operation_bridge_disables_native_writes_and_keeps_token_out_of_arguments(self):
        for backend in ('codex', 'claude'):
            with self.subTest(backend=backend):
                self.fixture("""
Path('invocation.json').write_text(json.dumps({'argv': sys.argv[1:], 'token': os.environ.get('AF_OPERATION_TOKEN')}))
sys.stdin.read()
if sys.argv[1] == 'exec':
    event({'type': 'thread.started', 'thread_id': 'bridge-session'})
    event({'type': 'item.completed', 'item': {'id': 'reply', 'type': 'agent_message', 'text': 'Done'}})
    event({'type': 'turn.completed', 'usage': {}})
else:
    event({'type': 'result', 'subtype': 'success', 'result': 'Done', 'usage': {}, 'session_id': 'bridge-session'})
""")
                try:
                    self.run_turn(backend, mode='workspace-write', operation_bridge={
                        'url': 'http://127.0.0.1:8765', 'token': 'private-turn-token'})
                except TypeError as error:
                    self.fail(f'Native driver cannot connect shared operations: {error}')
                invocation = json.loads((self.project / 'invocation.json').read_text())
                argv = invocation['argv']
                self.assertNotIn('private-turn-token', ' '.join(argv))
                self.assertEqual(invocation['token'], 'private-turn-token')
                if backend == 'codex':
                    self.assertIn('sandbox_mode="read-only"', argv)
                    self.assertIn('features.shell_tool=false', argv)
                    self.assertTrue(any(arg.startswith('mcp_servers=') and 'operation_mcp.py' in arg for arg in argv))
                else:
                    self.assertEqual(argv[argv.index('--tools') + 1], '')
                    self.assertEqual(argv[argv.index('--allowedTools') + 1], 'mcp__analog_factory')
                    self.assertIn('--mcp-config', argv)

    def test_codex_bridge_trusts_only_registered_tools_on_initial_and_resumed_turns(self):
        import tomllib
        from analog_factory.agent_cli import _command
        from analog_factory.operation_mcp import NAMES
        for session_id in (None, 'existing-native-session'):
            argv = _command('codex', 'codex', 'model', 'workspace-write', session_id,
                            operation_bridge={'url': 'http://localhost:8765', 'token': 'not-in-argv'})
            config = tomllib.loads(next(value for value in argv if value.startswith('mcp_servers=')))
            self.assertEqual(set(config['mcp_servers']), {'analog_factory'})
            bridge = config['mcp_servers']['analog_factory']
            self.assertEqual(set(bridge['enabled_tools']), NAMES)
            self.assertEqual(bridge['default_tools_approval_mode'], 'prompt')
            self.assertEqual(set(bridge['tools']), NAMES)
            self.assertTrue(all(value['approval_mode'] == 'approve' for value in bridge['tools'].values()))
            self.assertIn('sandbox_mode="read-only"', argv)
            self.assertIn('approval_policy="never"', argv)
            self.assertNotIn('not-in-argv', str(argv))

    def test_native_tool_events_redact_proposed_secret_values(self):
        self.fixture("""
sys.stdin.read()
event({'type': 'thread.started', 'thread_id': 'secret-session'})
event({'type': 'item.started', 'item': {'id': 'mcp-1', 'type': 'mcp_tool_call',
    'server': 'analog_factory', 'tool': 'invoke_operation',
    'arguments': {'operation_id': 'settings.update', 'parameters': {'settings': {'provider': {'api_key': 'private-input-secret'}}}}}})
event({'type': 'item.completed', 'item': {'id': 'reply', 'type': 'agent_message', 'text': 'Denied'}})
event({'type': 'turn.completed', 'usage': {}})
""")
        self.run_turn()
        self.assertNotIn('private-input-secret', json.dumps(self.events))

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
                self.assertEqual([event.get('tool_call_id') for event in self.events
                                  if event['type'] in ('tool_call', 'tool_result')], ['t1', 't1'])
                self.assertEqual(sum(e['type'] == 'message' for e in self.events), 1)

    def claude_environment_fixture(self):
        self.fixture("""
sys.stdin.read()
observed = {key: os.environ.get(key) for key in (
    'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL',
    'ANTHROPIC_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL', 'CLAUDE_CODE_OAUTH_TOKEN',
    'AF_UNTRUSTED_SETTING', 'CLAUDE_CODE_RESTRICTED')}
Path('invocation.json').write_text(json.dumps({'argv': sys.argv[1:], 'env': observed}))
event({'type': 'result', 'subtype': 'success', 'result': 'Done', 'usage': {}, 'session_id': 'claude-config-session'})
""")

    def test_claude_restores_user_connection_settings_without_loading_customizations(self):
        self.claude_environment_fixture()
        settings = {'env': {
            'ANTHROPIC_AUTH_TOKEN': 'fixture-user-token',
            'ANTHROPIC_BASE_URL': 'https://gateway.example.invalid',
            'ANTHROPIC_MODEL': 'gateway-default',
            'ANTHROPIC_DEFAULT_OPUS_MODEL': 'gateway-opus',
            'AF_UNTRUSTED_SETTING': 'must-not-load',
            'CLAUDE_CODE_RESTRICTED': '0',
        }, 'model': 'lower-priority-model',
            'hooks': {'SessionStart': [{'command': 'must-not-run'}]},
            'permissions': {'defaultMode': 'bypassPermissions'}}
        path = self.claude_config / 'settings.json'
        path.write_text(json.dumps(settings))
        (self.project / '.claude').mkdir()
        (self.project / '.claude' / 'settings.json').write_text(json.dumps({
            'env': {'ANTHROPIC_AUTH_TOKEN': 'untrusted-project-token',
                    'ANTHROPIC_BASE_URL': 'https://untrusted.example.invalid'}}))
        for session in (None, 'claude-config-session'):
            with self.subTest(session=session), patch.dict(os.environ, {
                'ANTHROPIC_AUTH_TOKEN': 'shell-token', 'CLAUDE_CODE_RESTRICTED': '1',
                'CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST': ''}):
                self.run_turn('claude', model='opus', fast_mode=True,
                              backend_session_id=session)
                invocation = json.loads((self.project / 'invocation.json').read_text())
                env, argv = invocation['env'], invocation['argv']
                self.assertEqual(env['ANTHROPIC_AUTH_TOKEN'], 'fixture-user-token')
                self.assertEqual(env['ANTHROPIC_BASE_URL'], 'https://gateway.example.invalid')
                self.assertEqual(env['ANTHROPIC_MODEL'], 'gateway-default')
                self.assertEqual(env['ANTHROPIC_DEFAULT_OPUS_MODEL'], 'gateway-opus')
                self.assertIsNone(env['AF_UNTRUSTED_SETTING'])
                self.assertEqual(env['CLAUDE_CODE_RESTRICTED'], '1')
                self.assertEqual(argv[argv.index('--model') + 1], 'opus')
                self.assertEqual(json.loads(argv[argv.index('--settings') + 1]), {'fastMode': True})
                self.assertIn('--restricted', argv)
                self.assertNotIn('fixture-user-token', ' '.join(argv))
                self.assertNotIn('must-not-run', ' '.join(argv))
                self.assertEqual(os.environ['ANTHROPIC_AUTH_TOKEN'], 'shell-token')
        self.assertEqual(json.loads(path.read_text()), settings)

    def test_claude_user_model_falls_back_behind_environment_model(self):
        self.claude_environment_fixture()
        (self.claude_config / 'settings.json').write_text(json.dumps({'model': 'user-default'}))
        for inherited in ('', 'shell-default'):
            with self.subTest(inherited=inherited), patch.dict(os.environ, {
                'ANTHROPIC_MODEL': inherited, 'CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST': ''}):
                self.run_turn('claude', model='', fast_mode=False)
                invocation = json.loads((self.project / 'invocation.json').read_text())
                self.assertEqual(invocation['env']['ANTHROPIC_MODEL'], inherited)
                argv = invocation['argv']
                expected_settings = {'fastMode': False}
                if not inherited:
                    expected_settings['model'] = 'user-default'
                self.assertEqual(json.loads(argv[argv.index('--settings') + 1]), expected_settings)
                self.assertNotIn('--model', invocation['argv'])

    def test_claude_host_managed_provider_keeps_its_auth_and_model(self):
        self.claude_environment_fixture()
        (self.claude_config / 'settings.json').write_text(json.dumps({
            'env': {'ANTHROPIC_AUTH_TOKEN': 'user-token'}, 'model': 'user-model'}))
        with patch.dict(os.environ, {'CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST': '1',
                                    'ANTHROPIC_AUTH_TOKEN': 'host-token',
                                    'ANTHROPIC_MODEL': 'host-model'}):
            self.run_turn('claude')
        observed = json.loads((self.project / 'invocation.json').read_text())['env']
        self.assertEqual(observed['ANTHROPIC_AUTH_TOKEN'], 'host-token')
        self.assertEqual(observed['ANTHROPIC_MODEL'], 'host-model')

    def test_claude_invalid_user_config_fails_without_echoing_secrets(self):
        self.claude_environment_fixture()
        for contents in ('{"env": "private-secret"}', '{"private-secret":',
                         json.dumps({'env': {'ANTHROPIC_AUTH_TOKEN': 'private-secret\0'}})):
            with self.subTest(contents=contents):
                (self.claude_config / 'settings.json').write_text(contents)
                with self.assertRaisesRegex(RuntimeError, 'Claude user settings') as caught:
                    self.run_turn('claude')
                self.assertNotIn('private-secret', str(caught.exception))
        self.assertFalse((self.project / 'invocation.json').exists())

    def test_claude_fast_preference_is_explicit_and_preserves_resumed_permissions(self):
        self.fixture("""
Path('invocation.json').write_text(json.dumps({'argv': sys.argv[1:], 'stdin': sys.stdin.read()}))
event({'type': 'result', 'subtype': 'success', 'result': 'Done', 'usage': {}, 'session_id': 'claude-fast-session'})
""")
        for fast in (True, False):
            for session_id in (None, 'saved-claude'):
                with self.subTest(fast=fast, session_id=session_id):
                    self.run_turn('claude', model='opus', fast_mode=fast,
                                  mode='workspace-write', backend_session_id=session_id)
                    argv = json.loads((self.project / 'invocation.json').read_text())['argv']
                    self.assertEqual(json.loads(argv[argv.index('--settings') + 1]), {'fastMode': fast})
                    self.assertEqual(argv[argv.index('--model') + 1], 'opus')
                    self.assertEqual(argv[argv.index('--tools') + 1], 'Read,Glob,Grep,Edit,Write')
                    self.assertEqual(argv[argv.index('--permission-mode') + 1], 'dontAsk')
                    self.assertIn('--restricted', argv)
                    self.assertIn('--strict-mcp-config', argv)
                    if session_id:
                        self.assertEqual(argv[argv.index('--resume') + 1], session_id)

    def test_codex_fast_and_standard_reapply_the_explicit_service_tier_on_resume(self):
        self.successful_codex()
        for fast, expected in ((True, 'service_tier="fast"'), (False, 'service_tier="default"')):
            for session_id in (None, 'saved-codex'):
                with self.subTest(fast=fast, session_id=session_id):
                    self.run_turn(model='chosen-model', fast_mode=fast,
                                  backend_session_id=session_id)
                    argv = json.loads((self.project / 'invocation.json').read_text())['argv']
                    self.assertIn(expected, argv)
                    self.assertEqual(argv[argv.index(expected) - 1], '-c')
                    self.assertEqual(argv[argv.index('--model') + 1], 'chosen-model')
                    self.assertIn('sandbox_mode="read-only"', argv)
                    self.assertIn('approval_policy="never"', argv)
                    if session_id:
                        self.assertEqual(argv[:2], ['exec', 'resume'])
                        self.assertEqual(argv[-2:], [session_id, '-'])

    def test_cli_fast_mode_rejects_invalid_values_before_launch(self):
        self.fixture("Path('started').touch()")
        for fast in ('false', 0, 1, [], {}):
            with self.subTest(fast=fast), self.assertRaises(ValueError):
                self.run_turn(fast_mode=fast)
        for model in ('', 'default', 'sonnet', 'haiku', 'fable', 'claude-opus-4-7'):
            with self.subTest(model=model), self.assertRaises(ValueError):
                self.run_turn('claude', model=model, fast_mode=True)
        self.assertFalse((self.project / 'started').exists())

    def test_claude_reports_actual_fast_state_without_inventing_success(self):
        self.fixture("""
sys.stdin.read()
event({'type': 'system', 'subtype': 'init', 'fast_mode_state': 'on', 'session_id': 'claude-fast-session'})
event({'type': 'system', 'subtype': 'notification', 'fast_mode_state': 'cooldown'})
event({'type': 'result', 'subtype': 'success', 'result': 'Done', 'usage': {},
       'fast_mode_state': 'off', 'fast_mode_disabled_reason': 'extra_usage_disabled'})
""")
        self.run_turn('claude', model='opus', fast_mode=True)
        states = [event for event in self.events if event.get('fast_mode_state')]
        self.assertEqual([event['fast_mode_state'] for event in states], ['on', 'cooldown', 'off'])
        self.assertTrue(all(event['type'] == 'status' for event in states))
        self.assertEqual(states[-1]['fast_mode_disabled_reason'], 'extra_usage_disabled')
        self.events.clear()
        self.fixture("event({'type': 'result', 'subtype': 'success', 'result': 'Done', 'usage': {}, 'session_id': 'claude-fast-session'})")
        self.run_turn('claude', model='opus', fast_mode=True)
        self.assertFalse(any('fast_mode_state' in event for event in self.events))

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
                       'tool_call_id': 'command', 'text': 'No such file', 'status': 'failed'}, self.events)

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
