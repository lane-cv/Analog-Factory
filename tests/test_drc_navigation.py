"""DRC focus authorization/acknowledgment plus opt-in actual Magic GUI acceptance."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from analog_factory.service import Workspace
from analog_factory.operations import InvocationContext


class DrcNavigationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.workspace=Workspace(Path(self.tmp.name));self.addCleanup(self.workspace.agents.close)
        self.registry=self.workspace.operations;self.addCleanup(self.registry.close)
        self.pid=self.workspace.projects.create('DRC navigation')['id'];self.root=self.workspace.projects.path(self.pid)
        self.workspace.projects.write(self.pid,'layout/amplifier.mag','magic\ntech sky130A\ntimestamp 0\n<< metal1 >>\nrect 0 0 1 1\n<< end >>\n')
        self.metadata={'sources':{'layout':'layout/amplifier.mag'},'pdk':{},'tool_versions':{'magic':'fixture'}}
        script="from pathlib import Path; Path('../output/drc.txt').write_text('AF_DRC_COUNT=1\\nAF_DRC_COMPLETE=1\\n')"
        row=self.workspace.runner.start(self.root,'drc',[sys.executable,'-c',script],metadata=self.metadata)
        self.report=self.workspace.runner.wait(self.root,row['id'],timeout=5)
        self.fixture=Path(self.tmp.name)/'focus-fixture.py'
        self.fixture.write_text('''import pathlib,re,sys,time
text=pathlib.Path(sys.argv[1]).read_text()
nonce=re.search(r'set af_nonce "([a-f0-9]+)"',text)[1]
index=re.search(r'set af_index ([0-9]+)',text)[1]
pathlib.Path('../output/drc-focus.txt').write_text('AF_DRC_FOCUS_COMPLETE=1\\nAF_DRC_FOCUS_NONCE='+nonce+'\\nAF_DRC_FOCUS_INDEX='+index+'\\nAF_DRC_FOCUS_COUNT=1\\nAF_DRC_FOCUS_BOX=0 0 1 1\\n')
time.sleep(30)
''')

    def prepared(self,*args,**kwargs):
        return {'command':[sys.executable,str(self.fixture),'original.mag'],'env':{},'timeout':15,
                'metadata':{**self.metadata,'generated_files':{},'gui_edits_original':True}}

    def invoke(self,name,parameters=None,context=None):
        return self.registry.invoke(name,{'project_id':self.pid,**(parameters or {})},context,expected_version=self.registry.version(self.pid))

    def test_focus_requires_actual_ack_and_has_managed_cancellation(self):
        with patch('analog_factory.eda.prepare',side_effect=self.prepared):
            response=self.invoke('verification.drc.focus',{'run_id':self.report['id'],'error_index':1})
        self.assertTrue(response['ok'],response);run=response['data'];self.addCleanup(self.workspace.runner.cancel,self.root,run['id'])
        self.assertEqual(response['task_id'],f'run:{self.pid}:{run["id"]}')
        for _ in range(100):
            result=self.invoke('verification.drc.focus_status',{'run_id':run['id']})
            if result['data'].get('focused'):break
            time.sleep(.02)
        self.assertTrue(result['data']['focused'],result)
        self.assertEqual(result['data']['box'],[0,0,1,1])
        self.assertEqual(result['data']['source_run_id'],self.report['id'])
        script=(self.root/'.af/runs'/run['id']/'input/af_scripts/drc-focus.tcl').read_text()
        self.assertIn('drc find $af_index',script);self.assertIn('findbox zoom',script)
        self.assertTrue(self.registry.cancel_operation(response['task_id'])['ok'])
        self.assertEqual(self.workspace.runner.wait(self.root,run['id'],timeout=5)['status'],'cancelled')
        stopped=self.invoke('verification.drc.focus_status',{'run_id':run['id']})['data']
        self.assertIn('no longer running',stopped['reason'])

    def test_stale_imported_clean_out_of_range_and_cross_project_reports_are_rejected(self):
        params={'run_id':self.report['id'],'error_index':1}
        other=self.workspace.projects.create('Other')['id']
        self.assertEqual(self.invoke('verification.drc.focus',params,InvocationContext(project_id=other))['error']['code'],'PERMISSION_DENIED')
        self.assertFalse(self.invoke('verification.drc.focus',{**params,'error_index':2})['ok'])
        manifest=self.root/'.af/runs'/self.report['id']/'manifest.json'
        original=json.loads(manifest.read_text())
        for change in ({'imported_evidence':True},{'verification':{'status':'pass','error_count':0}},{'owner_identity':None}):
            manifest.write_text(json.dumps({**original,**change}))
            self.assertFalse(self.invoke('verification.drc.focus',params)['ok'])
        manifest.write_text(json.dumps(original))
        self.workspace.projects.write(self.pid,'layout/amplifier.mag','changed')
        self.assertFalse(self.invoke('verification.drc.focus',params)['ok'])

    def test_unavailable_gui_and_missing_or_forged_ack_do_not_claim_focus(self):
        params={'run_id':self.report['id'],'error_index':1}
        with patch('analog_factory.eda.prepare',side_effect=RuntimeError('No GUI DISPLAY configured')):
            result=self.invoke('verification.drc.focus',params)
        self.assertFalse(result['ok']);self.assertEqual(result['error']['code'],'UNAVAILABLE')
        unknown=self.invoke('verification.drc.focus_status',{'run_id':self.report['id']})
        self.assertFalse(unknown['data']['focused'])
        with patch('analog_factory.eda.prepare',side_effect=self.prepared):
            response=self.invoke('verification.drc.focus',params)
        self.assertTrue(response['ok'],response);rid=response['data']['id'];self.addCleanup(self.workspace.runner.cancel,self.root,rid)
        self.workspace.runner.cancel(self.root,rid);self.workspace.runner.wait(self.root,rid,timeout=5)
        target=self.root/'.af/runs'/rid/'output/drc-focus.txt'
        target.write_text('AF_DRC_FOCUS_COMPLETE=1\nAF_DRC_FOCUS_NONCE=forged\nAF_DRC_FOCUS_INDEX=1\nAF_DRC_FOCUS_COUNT=1\nAF_DRC_FOCUS_BOX=0 0 1 1\n')
        result=self.invoke('verification.drc.focus_status',{'run_id':rid})
        self.assertFalse(result['data']['focused'])

    @unittest.skipUnless(os.environ.get('AF_DRC_FOCUS_REAL')=='1','Explicit actual Magic/Xvfb acceptance is opt-in')
    def test_actual_magic_gui_finds_real_sky130_error_and_acknowledges_box(self):
        config=json.loads(os.environ.get('AF_DRC_CONFIG','{"tools":{"magic":"/opt/eda/bin/magic"},"pdk_root":"/pdk","pdk_variant":"sky130A"}'))
        self.workspace.configure_project(self.pid,config)
        self.workspace.projects.write(self.pid,'layout/amplifier.mag','magic\ntech sky130A\ntimestamp 0\n<< metal1 >>\nrect 0 0 1 1\nrect 1000 1000 1001 1001\n<< end >>\n')
        response=self.invoke('run.start',{'kind':'drc','options':{'layout':'layout/amplifier.mag'},'timeout':60})
        self.assertTrue(response['ok'],response)
        report=self.workspace.runner.wait(self.root,response['data']['id'],timeout=65)
        self.assertEqual(report['status'],'succeeded',report);self.assertGreater(report['verification']['error_count'],1)
        boxes=[]
        for index in (1,2):
            response=self.invoke('verification.drc.focus',{'run_id':report['id'],'error_index':index,'timeout':30})
            self.assertTrue(response['ok'],response);rid=response['data']['id'];self.addCleanup(self.workspace.runner.cancel,self.root,rid)
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                ack=self.invoke('verification.drc.focus_status',{'run_id':rid})
                if ack['data'].get('focused'):break
                if ack['data']['status'] in ('failed','unavailable','cancelled','timed_out'):break
                time.sleep(.1)
            self.assertTrue(ack['data']['focused'],{'ack':ack,'log':self.workspace.log(self.pid,rid)})
            self.assertEqual(ack['data']['error_index'],index);self.assertEqual(len(ack['data']['box']),4)
            boxes.append(ack['data']['box'])
            self.assertEqual(self.invoke('run.get',{'run_id':rid})['data']['status'],'running')
            self.registry.cancel_operation(response['task_id'])
            self.assertEqual(self.workspace.runner.wait(self.root,rid,timeout=5)['status'],'cancelled')
        self.assertNotEqual(*boxes,'Different real error indices must find different Magic tiles')
        print(json.dumps({'magic':report['tool_versions'],'error_count':report['verification']['error_count'],'acknowledged_boxes':boxes,'cancelled':True}),flush=True)


if __name__=='__main__':unittest.main()
