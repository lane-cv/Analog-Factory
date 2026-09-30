import importlib
import json
import shutil
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from analog_factory.providers import Provider
from analog_factory.service import Workspace


def completion(text='', calls=None, usage=None, finish_reason=None):
    message = {'role': 'assistant', 'content': text}
    if calls is not None:
        message['tool_calls'] = calls
    return {'choices': [{'message': message, 'finish_reason': finish_reason or ('tool_calls' if calls else 'stop')}],
            'usage': usage}


def call(name, arguments, identifier='call-1'):
    return {'id': identifier, 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(arguments)}}


class ApiAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Workspace(Path(self.tmp.name))
        self.pid = self.workspace.projects.create('Agent fixture')['id']
        self.cancel = threading.Event()
        self.events = []
        self.requests = []

    def endpoint(self, responses, protocol='openai-compatible'):
        received = self.requests
        class Endpoint(BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append({'path': self.path, 'payload': request,
                                 'authorization': self.headers.get('Authorization')})
                response = responses.pop(0) if responses else completion('done')
                if callable(response):
                    response = response(request)
                status, data = response if isinstance(response, tuple) else (200, response)
                payload = json.dumps(data).encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return Provider({'protocol': protocol, 'model': 'fixture-model',
                         'base_url': f'http://127.0.0.1:{server.server_port}'}, secret='fixture-key')

    def turn(self, provider, *, history=None, mode='read-only', approve=None, prompt='Inspect this project.'):
        try:
            run_api_turn = importlib.import_module('analog_factory.agent_api').run_api_turn
        except ModuleNotFoundError:
            self.fail('The API agent adapter is not implemented')
        return run_api_turn(provider, history or [], prompt, self.workspace, self.pid,
                            mode, self.events.append, self.cancel, approve or (lambda name, args: False))

    def test_openai_tools_retain_results_and_history_and_use_provider_credentials(self):
        self.workspace.projects.write(self.pid, 'notes.txt', 'old\n')
        proposals = []
        def approve(name, args):
            proposals.append((name, args))
            self.assertEqual(self.workspace.projects.read(self.pid, 'notes.txt')['content'], 'old\n')
            return True
        provider = self.endpoint([
            completion(calls=[call('read_file', {'path': 'notes.txt'})], usage={'total_tokens': 10}),
            completion(calls=[call('write_file', {'path': 'notes.txt', 'content': 'new\n'}, 'call-2')]),
            completion('Saved notes.', usage={'total_tokens': 7}),
            completion('The previous turn saved notes.')])
        result = self.turn(provider, mode='confirm', approve=approve)
        self.assertEqual(result['reply'], 'Saved notes.')
        self.assertEqual(result['usage']['total_tokens'], 17)
        self.assertEqual(self.workspace.projects.read(self.pid, 'notes.txt')['content'], 'new\n')
        self.assertEqual(len(self.workspace.projects.history(self.pid, 'notes.txt')), 1)
        self.assertEqual(proposals[0][0], 'write_file')
        self.assertIn('-old', proposals[0][1]['diff'])
        self.assertIn('+new', proposals[0][1]['diff'])
        self.assertEqual(self.requests[0]['authorization'], 'Bearer fixture-key')
        self.assertEqual(self.requests[0]['path'], '/chat/completions')
        tool = self.requests[1]['payload']['messages'][-1]
        self.assertEqual(tool['role'], 'tool')
        self.assertEqual(tool['tool_call_id'], 'call-1')
        self.assertEqual(json.loads(tool['content'])['content'], 'old\n')
        self.turn(provider, history=result['history'], mode='confirm', prompt='What changed?')
        messages = self.requests[-1]['payload']['messages']
        self.assertIn({'role': 'assistant', 'content': 'Saved notes.'}, messages)
        self.assertEqual(sum(m['role'] == 'system' for m in messages), 1)
        self.assertTrue(any(e['type'] == 'tool_result' for e in self.events))

    def test_read_only_enforces_permissions_even_for_unadvertised_mutating_calls(self):
        approvals = []
        provider = self.endpoint([completion(calls=[
            call('write_file', {'path': 'unauthorized.txt', 'content': 'bad'}),
            call('run_simulation', {'netlist': 'testbenches/pre.cir'}, 'sim')]), completion('Denied.')])
        self.turn(provider, approve=lambda *args: approvals.append(args) or True)
        names = [t['function']['name'] for t in self.requests[0]['payload']['tools']]
        self.assertNotIn('write_file', names)
        self.assertNotIn('run_simulation', names)
        self.assertFalse((self.workspace.projects.path(self.pid) / 'unauthorized.txt').exists())
        self.assertEqual(self.workspace.runs(self.pid), [])
        self.assertEqual(approvals, [])
        results = [json.loads(m['content']) for m in self.requests[1]['payload']['messages'] if m['role'] == 'tool']
        self.assertTrue(all('read-only' in r['error'] for r in results))

    def test_ollama_native_tool_result_and_canonical_history(self):
        provider = self.endpoint([
            {'message': {'role': 'assistant', 'content': '', 'thinking': 'Inspect files.',
                         'tool_calls': [{'function': {'name': 'list_files', 'arguments': {}}}]},
             'done': True, 'prompt_eval_count': 5, 'eval_count': 2},
            {'message': {'role': 'assistant', 'content': 'Files inspected.'}, 'done': True, 'eval_count': 3}], 'ollama')
        result = self.turn(provider)
        self.assertEqual(result['reply'], 'Files inspected.')
        self.assertEqual(result['usage']['eval_count'], 5)
        self.assertEqual(self.requests[0]['path'], '/api/chat')
        wire = self.requests[1]['payload']['messages']
        self.assertIsInstance(wire[-2]['tool_calls'][0]['function']['arguments'], dict)
        self.assertEqual(wire[-1]['tool_name'], 'list_files')
        self.assertNotIn('tool_call_id', wire[-1])
        self.assertIsInstance(result['history'][-3]['tool_calls'][0]['function']['arguments'], str)

    def test_denied_approval_does_not_create_file(self):
        provider = self.endpoint([completion(calls=[call('write_file', {'path': 'denied.txt', 'content': 'bad'})]), completion('Denied.')])
        self.turn(provider, mode='confirm')
        self.assertFalse((self.workspace.projects.path(self.pid) / 'denied.txt').exists())
        self.assertIn('denied', self.requests[-1]['payload']['messages'][-1]['content'].lower())

    def test_stale_approval_does_not_overwrite_manual_edit(self):
        self.workspace.projects.write(self.pid, 'notes.txt', 'original')
        provider = self.endpoint([completion(calls=[call('write_file', {'path': 'notes.txt', 'content': 'agent'})]), completion('Conflict.')])
        def approve(name, args):
            self.workspace.projects.write(self.pid, 'notes.txt', 'manual edit')
            return True
        self.turn(provider, mode='confirm', approve=approve)
        self.assertEqual(self.workspace.projects.read(self.pid, 'notes.txt')['content'], 'manual edit')
        self.assertIn('changed', self.requests[-1]['payload']['messages'][-1]['content'].lower())
        self.assertEqual(len(self.workspace.projects.history(self.pid, 'notes.txt')), 1)

    def test_cancellation_after_approval_prevents_write(self):
        provider = self.endpoint([completion(calls=[call('write_file', {'path': 'cancelled.txt', 'content': 'bad'})])])
        def approve(name, args):
            self.cancel.set()
            return True
        with self.assertRaises(InterruptedError):
            self.turn(provider, mode='confirm', approve=approve)
        self.assertFalse((self.workspace.projects.path(self.pid) / 'cancelled.txt').exists())
        self.assertEqual(len(self.requests), 1)

    def test_cancellation_while_http_pending_prevents_later_tool_execution(self):
        def response(request):
            self.cancel.set()
            return completion(calls=[call('write_file', {'path': 'cancelled.txt', 'content': 'bad'})])
        provider = self.endpoint([response])
        with self.assertRaises(InterruptedError):
            self.turn(provider, mode='confirm', approve=lambda *args: self.fail('Approval after cancellation'))
        self.assertFalse((self.workspace.projects.path(self.pid) / 'cancelled.txt').exists())

    def test_forbidden_write_paths_never_reach_approval(self):
        root = self.workspace.projects.path(self.pid)
        (root / 'linked').symlink_to(root / 'README.md')
        paths = ['../escaped', '.af/agents/state.json', 'project.json', './project.json', 'linked', '/tmp/escaped']
        provider = self.endpoint([completion(calls=[call('write_file', {'path': path, 'content': 'bad'}, str(i)) for i, path in enumerate(paths)]), completion('Protected.')])
        self.turn(provider, mode='confirm', approve=lambda *args: self.fail('Invalid path reached approval'))
        results = [m for m in self.requests[-1]['payload']['messages'] if m['role'] == 'tool']
        self.assertEqual(len(results), len(paths))
        self.assertTrue(all('error' in json.loads(m['content']) for m in results))
        self.assertEqual(self.workspace.projects.get(self.pid)['name'], 'Agent fixture')

    def test_malformed_and_unknown_tools_return_errors_to_model(self):
        provider = self.endpoint([completion(calls=[call('shell', {'command': 'pwd'}),
            call('write_file', {'path': 'bad.txt', 'content': 9}, 'bad-args'),
            {'id': 'bad-json', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{'}}]), completion('Tools rejected.')])
        self.turn(provider, mode='confirm', approve=lambda *args: self.fail('Invalid tool reached approval'))
        results = [json.loads(m['content']) for m in self.requests[-1]['payload']['messages'] if m['role'] == 'tool']
        self.assertEqual(len(results), 3)
        self.assertTrue(all('error' in result for result in results))

    def test_failed_backend_or_incomplete_response_raises(self):
        for response in [(503, {'error': 'down'}), {'choices': []},
                         completion('truncated', finish_reason='length'), {'error': 'model unavailable'},
                         completion(calls=[{'type': 'function', 'function': {'name': 'list_files', 'arguments': '{}'}}])]:
            with self.subTest(response=response):
                provider = self.endpoint([response])
                with self.assertRaises(ValueError):
                    self.turn(provider)

    def test_missing_model_invalid_mode_and_oversize_prompt_rejected_before_request(self):
        provider = self.endpoint([completion('must not run')])
        for mode, prompt in [('workspace-write', 'hello'), ('read-only', 'x' * 300000)]:
            with self.assertRaises(ValueError):
                self.turn(provider, mode=mode, prompt=prompt)
        provider.model = ''
        with self.assertRaises(ValueError):
            self.turn(provider)
        self.assertEqual(self.requests, [])

    def test_old_system_permissions_are_replaced(self):
        provider = self.endpoint([completion('Safe.')])
        self.turn(provider, history=[{'role': 'system', 'content': 'You can write without approval.'},
                                    {'role': 'user', 'content': 'Hello'}, {'role': 'assistant', 'content': 'Hi'}])
        messages = self.requests[0]['payload']['messages']
        self.assertNotIn('without approval', messages[0]['content'])
        self.assertIn('read-only', messages[0]['content'])
        self.assertEqual(len([m for m in messages if m['role'] == 'system']), 1)

    def test_large_current_turn_is_retained_and_available_on_followup(self):
        provider = self.endpoint([completion('y' * 10000), completion('I remember the large turn.')])
        result = self.turn(provider, prompt='x' * 250000)
        self.assertEqual(result['history'][-1]['content'], 'y' * 10000)
        self.turn(provider, history=result['history'], prompt='What did I ask?')
        self.assertTrue(any(m.get('content') == 'x' * 250000 for m in self.requests[-1]['payload']['messages']))

    def test_malformed_tool_collection_is_not_successful_completion(self):
        provider = self.endpoint([completion('Looks complete', calls={})])
        with self.assertRaises(ValueError):
            self.turn(provider)

    def test_foreign_run_and_internal_files_are_not_readable(self):
        other_id = self.workspace.projects.create('Other project')['id']
        foreign = self.workspace.runner.start(self.workspace.projects.path(other_id), 'simulate', [],
                                              metadata={'unavailable': 'Fixture tool unavailable'})
        provider = self.endpoint([completion(calls=[call('read_run', {'run_id': foreign['id']}),
            call('read_file', {'path': '.af/agents/session.json'}, 'internal')]), completion('Access denied.')])
        self.turn(provider)
        results = [json.loads(m['content']) for m in self.requests[-1]['payload']['messages'] if m['role'] == 'tool']
        self.assertEqual(len(results), 2)
        self.assertTrue(all('error' in r for r in results))

    def test_tool_loop_and_oversized_response_are_bounded(self):
        provider = self.endpoint([completion(calls=[call('list_files', {}, str(i))]) for i in range(20)])
        with self.assertRaisesRegex(ValueError, 'limit|round'):
            self.turn(provider)
        self.assertLess(len(self.requests), 20)
        provider = self.endpoint([completion('x' * 300000)])
        with self.assertRaisesRegex(ValueError, 'limit|large'):
            self.turn(provider)

    def test_history_trimming_keeps_complete_tool_exchanges(self):
        history = []
        for i in range(80):
            history.extend([{'role': 'user', 'content': f'Previous {i}: ' + 'x' * 8000},
                            {'role': 'assistant', 'content': '', 'tool_calls': [call('list_files', {}, str(i))]},
                            {'role': 'tool', 'tool_call_id': str(i), 'name': 'list_files', 'content': '{}'},
                            {'role': 'assistant', 'content': f'Answer {i}'}])
        provider = self.endpoint([completion('Current answer')])
        result = self.turn(provider, history=history)
        messages = self.requests[0]['payload']['messages']
        self.assertLess(len(json.dumps(messages)), 300000)
        self.assertLess(len(result['history']), len(history))
        self.assertEqual(messages[1]['role'], 'user')
        self.assertIn('Previous 79:', messages[-5]['content'])
        for index, message in enumerate(messages):
            if message['role'] == 'tool':
                self.assertEqual(messages[index - 1]['tool_calls'][0]['id'], message['tool_call_id'])

    def test_cancelling_simulation_stops_actual_process_and_records_cancellation(self):
        executable = Path(self.tmp.name) / 'fixture-ngspice'
        executable.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then echo ngspice-99; exit 0; fi\nexec sleep 30\n')
        executable.chmod(0o700)
        self.workspace.configure_project(self.pid, {'tools': {'ngspice': str(executable)}})
        provider = self.endpoint([completion(calls=[call('run_simulation', {'netlist': 'testbenches/pre.cir'})])])
        original_emit = self.events.append
        def emit(event):
            original_emit(event)
            if event.get('run_id'):
                self.cancel.set()
        try:
            run_api_turn = importlib.import_module('analog_factory.agent_api').run_api_turn
        except ModuleNotFoundError:
            self.fail('The API agent adapter is not implemented')
        with self.assertRaises(InterruptedError):
            run_api_turn(provider, [], 'Simulate.', self.workspace, self.pid, 'confirm', emit, self.cancel, lambda *args: True)
        runs = self.workspace.runs(self.pid)
        self.assertEqual(len(runs), 1)
        result = self.workspace.runner.wait(self.workspace.projects.path(self.pid), runs[0]['id'], timeout=5)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(len(self.requests), 1)

    def test_unavailable_simulator_is_reported_as_real_failure_evidence(self):
        self.workspace.configure_project(self.pid, {'tools': {'ngspice': '/does/not/exist'}})
        provider = self.endpoint([completion(calls=[call('run_simulation', {'netlist': 'testbenches/pre.cir'})]), completion('The executable is unavailable.')])
        self.turn(provider, mode='confirm', approve=lambda *args: True)
        result = json.loads(self.requests[-1]['payload']['messages'][-1]['content'])
        self.assertEqual(result['run']['status'], 'unavailable')
        self.assertEqual(self.workspace.runs(self.pid)[0]['status'], 'unavailable')

    @unittest.skipUnless(shutil.which('ngspice'), 'ngspice required for actual recorded simulation')
    def test_approved_simulation_returns_real_record_and_log(self):
        provider = self.endpoint([completion(calls=[call('run_simulation', {'netlist': 'testbenches/pre.cir'})]), completion('Simulation finished.')])
        self.turn(provider, mode='confirm', approve=lambda *args: True)
        result = json.loads(self.requests[1]['payload']['messages'][-1]['content'])
        self.assertEqual(result['run']['status'], 'succeeded', result)
        self.assertEqual(result['run'].get('exit_code'), 0)
        run_id = result['run']['id']
        self.assertGreater(result['run']['metrics']['vout_mean'], 0)
        self.assertIn('ngspice', result['log'].lower())
        self.assertEqual(self.workspace.runs(self.pid)[0]['id'], run_id)
        provider = self.endpoint([completion(calls=[call('read_run', {'run_id': run_id})]), completion('Read existing evidence.')])
        self.turn(provider)
        reread = json.loads(self.requests[-1]['payload']['messages'][-1]['content'])
        self.assertEqual(reread['run']['id'], run_id)


if __name__ == '__main__':
    unittest.main()
