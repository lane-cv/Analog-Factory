"""Native subprocess evidence must authorize the real HTTP bridge and file writer."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

from analog_factory.server import create_server
from analog_factory.service import Workspace


@unittest.skipUnless(os.name == 'posix', 'Native CLI process groups require POSIX')
class OfficialApprovalIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = Workspace(self.root / 'workspace')
        self.addCleanup(self.workspace.operations.close)
        self.addCleanup(self.workspace.agents.close)
        self.executable = self.root / 'codex-protocol-fixture'
        self.workspace.save_settings({'tools': {'codex': str(self.executable)}})
        self.pid = self.workspace.projects.create('Official review integration')['id']
        self.project = self.workspace.projects.path(self.pid)
        self.source = self.project / 'official-note.txt'
        self.source.write_text('original')
        self.session = self.workspace.agents.create(self.pid,
            {'backend': 'codex', 'mode': 'workspace-write'})
        self.server = create_server(self.workspace, port=0, desktop_url='')
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def fixture(self, decision, stale=False):
        # Only the slow native inference boundary is controlled. All parent
        # transport, approval bookkeeping, HTTP, registry and file writes are real.
        source = f'#!{sys.executable}\n' + '''
import json, os, sys, time, urllib.error, urllib.request
from pathlib import Path
def send(value): print(json.dumps(value), flush=True)
def read(): return json.loads(sys.stdin.readline())
def notify(method, **params):
    send({'method':method,'params':{'threadId':'official-thread','turnId':'official-turn',**params}})
request=read()
send({'id':request['id'],'result':{'userAgent':'integration-fixture'}})
read()
request=read()
send({'id':request['id'],'result':{'approvalPolicy':{'granular':{'mcp_elicitations':True,'rules':False,'sandbox_approval':False,'request_permissions':False,'skill_approval':False}},
    'approvalsReviewer':'auto_review','sandbox':{'type':'readOnly','networkAccess':False},
    'thread':{'id':'official-thread','turns':[]}}})
request=read()
send({'id':request['id'],'result':{'turn':{'id':'official-turn','status':'inProgress','items':[],'error':None}}})
notify('turn/started',turn={'id':'official-turn','status':'inProgress'})
arguments={'operation_id':'file.write','parameters':{'path':'official-note.txt','content':'officially reviewed'}}
item={'id':'native-call','type':'mcpToolCall','server':'analog_factory','tool':'invoke_operation','arguments':arguments,'status':'inProgress'}
action={'type':'mcpToolCall','server':'analog_factory','toolName':'invoke_operation'}
notify('item/started',item=item,startedAtMs=1)
notify('item/autoApprovalReview/started',reviewId='native-review',targetItemId='native-call',action=action,review={'status':'inProgress'},startedAtMs=2)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
''' + f'''
base=os.environ['AF_OPERATION_URL']
session_path='/api/projects/{self.pid}/agent/sessions/{self.session['id']}'
deadline=time.monotonic()+5
while time.monotonic()<deadline:
    with opener.open(base+session_path,timeout=2) as response:
        record=json.load(response)
    if any(e.get('decision')=='reviewing' for e in record['turns'][-1]['events']):
        break
    time.sleep(.01)
else:
    raise RuntimeError('Official review was not published')
assert Path('official-note.txt').read_text()=='original', 'Action ran before approval'
if {stale!r}:
    Path('official-note.txt').write_text('concurrent human edit')
notify('item/autoApprovalReview/completed',reviewId='native-review',targetItemId='native-call',action=action,
       decisionSource='agent',review={{'status':{decision!r},'rationale':'Official fixture decision'}},startedAtMs=2,completedAtMs=3)
''' + '''
request=urllib.request.Request(base+'/api/agent-tools',data=json.dumps({'name':'invoke_operation','arguments':arguments}).encode(),
    headers={'Content-Type':'application/json','Authorization':'Bearer '+os.environ['AF_OPERATION_TOKEN']})
try:
    response=opener.open(request,timeout=8)
except urllib.error.HTTPError as error:
    response=error
with response:
    result=json.load(response)
notify('item/completed',item={**item,'status':'completed','result':result})
notify('item/completed',item={'id':'reply','type':'agentMessage','text':json.dumps(result)})
notify('turn/completed',turn={'id':'official-turn','status':'completed','items':[],'error':None})
time.sleep(3)
'''
        self.executable.write_text(source)
        self.executable.chmod(0o700)

    def run_turn(self):
        self.workspace.agents.set_auto_approval(self.pid, self.session['id'], True, authorized=True)
        self.workspace.agents.send(self.pid, self.session['id'],
            {'message': 'Change official-note.txt to officially reviewed.'}, human_authorized=True)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            record = self.workspace.agents.get(self.pid, self.session['id'])
            if record['turns'][-1].get('finished_at'):
                self.assertEqual(record['status'], 'succeeded', record['turns'][-1].get('error'))
                return record
            time.sleep(.01)
        self.fail('Official integration turn did not finish')

    def test_official_approval_releases_real_file_write_without_project_model_api(self):
        self.fixture('approved')
        record = self.run_turn()
        self.assertEqual(self.source.read_text(), 'officially reviewed')
        reviews = [event for event in record['turns'][-1]['events'] if event['type'] == 'approval_review']
        self.assertEqual([event['decision'] for event in reviews], ['reviewing', 'approve'])
        self.assertTrue(all(event['source'] == 'codex_official' for event in reviews))
        self.assertTrue(json.loads(record['turns'][-1]['reply'])['ok'])

    def test_native_denial_cannot_be_bypassed_by_calling_the_http_bridge(self):
        self.fixture('denied')
        record = self.run_turn()
        self.assertEqual(self.source.read_text(), 'original')
        result = json.loads(record['turns'][-1]['reply'])
        self.assertTrue(result.get('error') or result.get('ok') is False, result)

    def test_file_changed_during_official_review_is_not_overwritten(self):
        self.fixture('approved', stale=True)
        record = self.run_turn()
        self.assertEqual(self.source.read_text(), 'concurrent human edit')
        result = json.loads(record['turns'][-1]['reply'])
        self.assertTrue(result.get('error') or result.get('ok') is False, result)


if __name__ == '__main__':
    unittest.main()
