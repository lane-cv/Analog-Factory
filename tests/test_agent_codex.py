"""The official adapter talks to a real subprocess with controlled RPC output."""
import json
from contextlib import nullcontext
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import tomllib
import unittest
from unittest.mock import patch

from analog_factory.agent_cli import run_cli_turn

POLICY = {'granular': {'mcp_elicitations': True, 'rules': False, 'sandbox_approval': False,
                       'request_permissions': False, 'skill_approval': False}}

class ApprovalLedger:
    def __init__(self):
        self.binding = None
        self.events = []
        self.closed = False

    def bind(self, thread_id, turn_id):
        self.binding = thread_id, turn_id

    def accept(self, event):
        if not self.binding:
            raise AssertionError('Approval evidence arrived before the live turn was bound')
        self.events.append(event)

    def close(self):
        self.closed = True


@unittest.skipUnless(os.name == 'posix', 'CLI process groups require POSIX')
class OfficialCodexTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.executable = self.root / 'codex-fixture'
        self.cancel = threading.Event()
        self.events = []
        self.ledger = ApprovalLedger()

    def fixture(self, *, settings=None, body='', before_turn='', before_response='', fail_rpc=False):
        settings = settings or {}
        self.executable.write_text(f'#!{sys.executable}\n' + '''
import json, os, sys, time
from pathlib import Path
def send(value): print(json.dumps(value), flush=True)
def read():
    value = json.loads(sys.stdin.readline())
    with Path('rpc.jsonl').open('a') as f: f.write(json.dumps(value) + '\\n')
    return value
def notify(method, **params):
    send({'method': method, 'params': {'threadId':'native-thread', 'turnId':'live-turn', **params}})
Path('argv.json').write_text(json.dumps(sys.argv[1:]))
request = read()
send({'id':request['id'], 'result': {'userAgent':'fixture'}})
read()
request = read()
settings = {'approvalPolicy':{'granular':{'mcp_elicitations':True,'rules':False,'sandbox_approval':False,'request_permissions':False,'skill_approval':False}}, 'approvalsReviewer':'auto_review',
            'sandbox':{'type':'readOnly', 'networkAccess':False},
            'thread':{'id':'native-thread', 'turns':[{'id':'old-turn','items':[]} ]}}
''' + 'settings.update(' + repr(settings) + ')\n' + (
            "send({'id':request['id'],'error':{'code':-32602,'message':'private backend detail'}})\ntime.sleep(5)\n" if fail_rpc else
            "send({'id':request['id'], 'result':settings})\n" + before_turn + '''
request = read()
''' + before_response + '''
send({'id':request['id'], 'result': {'turn': {'id':'live-turn','status':'inProgress','items':[],'error':None}}})
notify('turn/started', turn={'id':'live-turn','status':'inProgress','items':[],'error':None})
''' + body + '''
notify('item/completed', item={'id':'reply','type':'agentMessage','text':'Official answer'})
notify('thread/tokenUsage/updated', tokenUsage={'last':{'inputTokens':12,'cachedInputTokens':2,'outputTokens':4,'totalTokens':16},'total':{}})
notify('turn/completed', turn={'id':'live-turn','status':'completed','items':[],'error':None})
time.sleep(5)
'''))
        self.executable.chmod(0o700)

    def run_turn(self, **kwargs):
        options = dict(backend='codex', executable=self.executable, project_root=self.root,
                       model='selected-model', mode='workspace-write', backend_session_id=None,
                       prompt='Private user request Ω', emit=self.events.append,
                       cancel=self.cancel, timeout=2, official_approval=self.ledger,
                       operation_bridge={'url':'http://127.0.0.1:8123','token':'private-turn-token'})
        options.update(kwargs)
        try:
            return run_cli_turn(**options)
        except TypeError as error:
            if 'official_approval' in str(error):
                self.fail('CLI driver does not expose the official approval transport')
            raise

    def requests(self):
        return [json.loads(line) for line in (self.root / 'rpc.jsonl').read_text().splitlines()]

    def test_official_handshake_is_interactive_readonly_and_keeps_secrets_off_argv(self):
        self.fixture()
        result = self.run_turn(fast_mode=True, effort='high')
        self.assertEqual(result['reply'], 'Official answer')
        self.assertEqual(result['backend_session_id'], 'native-thread')
        self.assertEqual(result['usage']['output_tokens'], 4)
        requests = self.requests()
        self.assertEqual([r['method'] for r in requests],
                         ['initialize','initialized','thread/start','turn/start'])
        self.assertTrue(requests[0]['params']['capabilities']['experimentalApi'])
        self.assertEqual(requests[2]['params']['approvalsReviewer'], 'auto_review')
        self.assertEqual(requests[2]['params']['approvalPolicy'], POLICY)
        self.assertEqual(requests[3]['params']['approvalPolicy'], POLICY)
        self.assertEqual(requests[2]['params']['sandbox'], 'read-only')
        self.assertEqual(requests[3]['params']['input'][0]['text'], 'Private user request Ω')
        self.assertEqual(requests[3]['params']['effort'], 'high')
        self.assertEqual(requests[3]['params']['serviceTier'], 'fast')
        argv = json.loads((self.root / 'argv.json').read_text())
        self.assertEqual(argv[:3], ['app-server','--listen','stdio://'])
        self.assertNotIn('Private user request', str(argv))
        self.assertNotIn('private-turn-token', str(argv))
        self.assertIn('features.shell_tool=false', argv)
        self.assertIn('features.guardian_approval=true', argv)
        native_policy = tomllib.loads(next(arg for arg in argv if arg.startswith('approval_policy=')))
        self.assertEqual(native_policy['approval_policy'], POLICY)
        self.assertIn('service_tier="fast"', argv)
        config = tomllib.loads(next(arg for arg in argv if arg.startswith('mcp_servers=')))
        server = config['mcp_servers']['analog_factory']
        self.assertEqual(server['tools']['invoke_operation']['approval_mode'], 'prompt')
        self.assertEqual(server['tools']['cancel_operation']['approval_mode'], 'prompt')
        self.assertEqual(server['tools']['list_operations']['approval_mode'], 'approve')
        self.assertEqual(self.ledger.binding, ('native-thread','live-turn'))
        self.assertTrue(self.ledger.closed)

    def test_resume_filters_history_other_threads_and_stale_turns_from_approval_evidence(self):
        self.fixture(before_turn="notify('item/autoApprovalReview/completed', turnId='old-turn', reviewId='historical', review={'status':'approved'})\n",
            body="""
notify('item/started', item={'id':'call','type':'mcpToolCall','server':'analog_factory','tool':'invoke_operation','arguments':{'operation_id':'files.write'},'status':'inProgress'})
notify('item/autoApprovalReview/started', reviewId='r1', targetItemId='call', action={'type':'mcpToolCall','server':'analog_factory','toolName':'invoke_operation'}, review={'status':'inProgress'})
notify('item/autoApprovalReview/completed', threadId='other-thread', reviewId='foreign', review={'status':'approved'})
notify('item/autoApprovalReview/completed', turnId='old-turn', reviewId='old', review={'status':'approved'})
notify('item/autoApprovalReview/completed', reviewId='r1', targetItemId='call', action={'type':'mcpToolCall','server':'analog_factory','toolName':'invoke_operation'}, review={'status':'denied','rationale':'Outside scope'})
notify('item/completed', item={'id':'call','type':'mcpToolCall','server':'analog_factory','tool':'invoke_operation','arguments':{},'status':'failed','error':{'message':'Denied'}})
""")
        self.run_turn(backend_session_id='native-thread')
        self.assertEqual(self.requests()[2]['method'], 'thread/resume')
        self.assertEqual(self.requests()[2]['params']['threadId'], 'native-thread')
        reviews = [e['params']['reviewId'] for e in self.ledger.events if 'autoApprovalReview' in e['method']]
        self.assertEqual(reviews, ['r1','r1'])
        self.assertTrue(any(e['type'] == 'tool_result' and e['status'] == 'failed' for e in self.events))

    def test_managed_policy_mismatch_prevents_turn_start(self):
        for settings in ({'approvalsReviewer':'user'}, {'approvalPolicy':'never'},
                         {'approvalPolicy':'on-request'},
                         {'approvalPolicy':{'granular':{**POLICY['granular'],'sandbox_approval':True}}},
                         {'approvalPolicy':{'granular':{**POLICY['granular'],'rules':0}}},
                         {'sandbox':{'type':'workspaceWrite'}},
                         {'sandbox':{'type':'readOnly','networkAccess':True}}):
            with self.subTest(settings=settings):
                (self.root / 'rpc.jsonl').unlink(missing_ok=True)
                self.fixture(settings=settings)
                with self.assertRaisesRegex(RuntimeError, 'official|policy|sandbox'):
                    self.run_turn()
                self.assertNotIn('turn/start', [r['method'] for r in self.requests()])
                self.assertTrue(self.ledger.closed)

    def test_pending_native_user_request_is_never_automatically_answered(self):
        self.fixture(body="""
send({'id':99,'method':'item/tool/requestUserInput','params':{'threadId':'native-thread','turnId':'live-turn','itemId':'i','isBlocking':True,'questions':[{'id':'q','header':'Approval','question':'Accept?','options':[{'label':'Accept','description':'Allow'}]}]}})
read()
Path('unexpected-response').write_text('answered')
""")
        with self.assertRaisesRegex(RuntimeError, 'human|manual|Stop'):
            self.run_turn()
        self.assertFalse((self.root / 'unexpected-response').exists())
        self.assertTrue(self.ledger.closed)

    def test_notifications_before_turn_response_are_bound_before_delivery(self):
        self.fixture(before_response="""
notify('turn/started', turn={'id':'live-turn','status':'inProgress','items':[],'error':None})
notify('item/started', item={'id':'early','type':'mcpToolCall','server':'analog_factory','tool':'list_operations','arguments':{},'status':'inProgress'})
""")
        self.run_turn(backend_session_id='native-thread', fast_mode=False, effort='low')
        self.assertEqual(self.ledger.events[0]['params']['item']['id'], 'early')
        params = self.requests()[3]['params']
        self.assertEqual(params['effort'], 'low')
        self.assertEqual(params['serviceTier'], 'default')

    def test_malformed_oversize_and_excessive_events_fail_closed(self):
        cases = [('print("{invalid",flush=True); time.sleep(5)', {}, 'malformed'),
                 ('print("x"*1025,flush=True); time.sleep(5)', {'_MAX_LINE':1024}, 'size'),
                 ("notify('ignored', value='x'*600); time.sleep(5)", {'_MAX_OUTPUT':1024}, 'size'),
                 ("\nfor i in range(30): notify('ignored')\ntime.sleep(5)", {'_MAX_EVENTS':10}, 'count'),
                 ("print('private-stderr-secret',file=sys.stderr,flush=True); sys.exit(7)", {}, 'without')]
        for body, limits, expected in cases:
            with self.subTest(expected=expected), (patch.multiple('analog_factory.agent_codex', **limits) if limits else nullcontext()):
                self.fixture(body=body)
                with self.assertRaisesRegex(RuntimeError, expected) as failure:
                    self.run_turn()
                self.assertNotIn('private-stderr-secret', str(failure.exception))
                self.assertTrue(self.ledger.closed)

    def test_failed_turn_and_missing_reply_never_count_as_success(self):
        for body, expected in [(
                "notify('turn/completed',turn={'id':'live-turn','status':'failed','error':{'message':'private'},'items':[]}); time.sleep(5)", 'failed'),
                ("notify('turn/completed',turn={'id':'live-turn','status':'completed','error':None,'items':[]}); time.sleep(5)", 'reply')]:
            with self.subTest(expected=expected):
                self.fixture(body=body)
                with self.assertRaisesRegex(RuntimeError, expected):
                    self.run_turn()
                self.assertTrue(self.ledger.closed)

    def test_buffered_completed_turn_cannot_publish_late_approval_evidence(self):
        self.fixture(before_response="""
notify('item/completed',item={'id':'early-reply','type':'agentMessage','text':'Completed early'})
notify('turn/completed',turn={'id':'live-turn','status':'completed','items':[],'error':None})
notify('item/started',item={'id':'late-call','type':'mcpToolCall','server':'analog_factory','tool':'invoke_operation','arguments':{},'status':'inProgress'})
""")
        self.assertEqual(self.run_turn()['reply'], 'Completed early')
        self.assertFalse(any(e['params'].get('item', {}).get('id') == 'late-call' for e in self.ledger.events))

    def test_cancellation_kills_child_process_group(self):
        self.fixture(body="""
import subprocess
child = subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
Path('child.pid').write_text(str(child.pid))
time.sleep(30)
""")
        def cancel_when_started():
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not (self.root / 'child.pid').exists():
                time.sleep(.01)
            self.cancel.set()
        thread = threading.Thread(target=cancel_when_started)
        thread.start()
        with self.assertRaises(InterruptedError):
            self.run_turn(timeout=3)
        thread.join(2)
        pid = int((self.root / 'child.pid').read_text())
        status = Path(f'/proc/{pid}/status')
        if status.exists():
            self.assertIn('Z (zombie)', status.read_text())
        self.assertTrue(self.ledger.closed)

    def test_bridge_and_backend_are_required_before_starting_process(self):
        for options in ({'operation_bridge':None}, {'backend':'claude'}, {'backend_session_id':'../bad'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.run_turn(**options)
            self.assertFalse((self.root / 'argv.json').exists())
            self.assertTrue(self.ledger.closed)

    def test_backend_errors_do_not_expose_raw_details(self):
        self.fixture(fail_rpc=True)
        with self.assertRaises(RuntimeError) as result:
            self.run_turn()
        self.assertNotIn('private backend detail', str(result.exception))
        self.assertTrue(self.ledger.closed)

    def test_timeout_and_cancel_close_the_approval_authority(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                self.cancel.clear()
                self.fixture(body="time.sleep(5)\n")
                if cancelled:
                    timer = threading.Timer(.15, self.cancel.set)
                    timer.start()
                    self.addCleanup(timer.cancel)
                with self.assertRaises(InterruptedError if cancelled else TimeoutError):
                    self.run_turn(timeout=.4)
                self.assertTrue(self.ledger.closed)


if __name__ == '__main__':
    unittest.main()
