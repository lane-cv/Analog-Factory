"""Real PTY child processes, native approval flags, and registry permission boundaries."""
import base64
import importlib.util
import json
import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path

from analog_factory.service import Workspace
from analog_factory.operations import InvocationContext


HELP = '--sandbox --ask-for-approval --no-alt-screen --no-daemon --permission-mode manual plan --model'


@unittest.skipUnless(os.name == 'posix', 'Native PTY sessions require POSIX')
class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.workspace=Workspace(Path(self.tmp.name)/'workspace');self.addCleanup(self.workspace.agents.close)
        self.pid=self.workspace.projects.create('Terminal fixture')['id']
        self.executable=Path(self.tmp.name)/'native-fixture'
        self.fixture(r'''
import tty,termios
print('READY tty='+str(os.isatty(0))+' cwd='+os.getcwd(),flush=True)
print('ARGS '+json.dumps(sys.argv[1:]),flush=True)
print('SIZE '+str(os.get_terminal_size(0)),flush=True)
for line in sys.stdin:
    line=line.rstrip('\n')
    if line=='quit': break
    if line=='size': print('SIZE '+str(os.get_terminal_size(0)),flush=True)
    else: print('ECHO '+line,flush=True)
''')
        self.workspace.configure_project(self.pid,{'tools':{'codex':str(self.executable),'claude':str(self.executable)}})
        self.assertIsNotNone(importlib.util.find_spec('analog_factory.native_terminals'),
                             'Native terminal lifecycle service must exist')
        self.registry=self.workspace.operations;self.addCleanup(self.registry.close)
        from analog_factory.operation_terminals import register_terminals
        if 'native.terminal.start' not in self.registry.operations:register_terminals(self.registry)
        self.manager=self.workspace.terminals;self.addCleanup(self.manager.close)

    def fixture(self,body):
        self.executable.write_text(f'#!{sys.executable}\nimport sys,os,time,json,signal\n'
            f"if '--help' in sys.argv:\n print({HELP!r});sys.exit(0)\n"
            "if '--version' in sys.argv:\n print('fixture 1.0');sys.exit(0)\n"+body)
        self.executable.chmod(0o700)

    def invoke(self,name,parameters=None,context=None):
        p={'project_id':self.pid,**(parameters or {})}
        return self.registry.invoke(name,p,context=context,expected_version=self.registry.version(self.pid))

    def start(self,**parameters):
        result=self.invoke('native.terminal.start',{'backend':'codex',**parameters})
        self.assertTrue(result['ok'],result)
        return result['data']['id']

    def read(self,identifier,after=0):
        response=self.invoke('native.terminal.read',{'terminal_id':identifier,'after':after})
        self.assertTrue(response['ok'],response)
        return response['data']

    def wait_for(self,identifier,text,timeout=3):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            data=self.read(identifier)
            if text in data['text']:return data
            time.sleep(.02)
        self.fail('Native terminal did not produce '+repr(text)+': '+repr(data))

    def wait_terminal(self,identifier):
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            record=self.invoke('native.terminal.get',{'terminal_id':identifier})['data']
            if record['status'] not in ('queued','running'):return record
            time.sleep(.02)
        self.fail('Native terminal did not stop')

    def test_real_pty_input_resize_and_exit_keep_original_project_scope(self):
        sid=self.start(cols=90,rows=26)
        data=self.wait_for(sid,'READY tty=True')
        self.assertIn(str(self.workspace.projects.path(self.pid)),data['text'])
        self.assertIn('on-request',data['text'])
        argv=json.loads(data['text'].split('ARGS ')[1].split('\r\n')[0])
        self.assertIn('approvals_reviewer="user"',argv)
        self.assertIn('read-only',data['text'])
        response=self.invoke('native.terminal.input',{'terminal_id':sid,'text':'hello Ω\n'})
        self.assertTrue(response['ok'],response)
        self.wait_for(sid,'ECHO hello Ω')
        self.assertTrue(self.invoke('native.terminal.resize',{'terminal_id':sid,'cols':110,'rows':38})['ok'])
        self.invoke('native.terminal.input',{'terminal_id':sid,'text':'size\n'})
        self.wait_for(sid,'columns=110, lines=38')
        self.invoke('native.terminal.input',{'terminal_id':sid,'text':'quit\n'})
        self.assertEqual(self.wait_terminal(sid)['status'],'succeeded')
        for file in self.workspace.projects.path(self.pid).rglob('*'):
            if file.is_file():self.assertNotIn(b'hello',file.read_bytes(),'PTY input/output must not be copied into project records')

    def test_external_agent_cannot_inject_native_approval_bytes_or_read_terminal(self):
        sid=self.start();self.wait_for(sid,'READY')
        agent=InvocationContext(principal='external',source='external-agent',project_id=self.pid,
            permissions=frozenset({'read','execute','approve','ui'}),confirmed=True)
        for operation,parameters in [('native.terminal.start',{'backend':'codex'}),
            ('native.terminal.input',{'terminal_id':sid,'text':'yes\n'}),
            ('native.terminal.read',{'terminal_id':sid}),('native.terminal.resize',{'terminal_id':sid,'cols':100,'rows':30}),
            ('native.terminal.stop',{'terminal_id':sid})]:
            response=self.invoke(operation,parameters,agent)
            self.assertEqual(response['error']['code'],'PERMISSION_DENIED',response)
        read_only=InvocationContext(permissions=frozenset({'read','execute'}))
        self.assertEqual(self.invoke('native.terminal.input',{'terminal_id':sid,'text':'yes\n'},read_only)['error']['code'],'PERMISSION_DENIED')
        status=self.invoke('native.terminal.get',{'terminal_id':sid},agent)
        self.assertTrue(status['ok'],status)
        self.assertNotIn('text',status['data'])

    def test_project_scope_and_active_terminal_block_destructive_file_lifecycle(self):
        sid=self.start();self.wait_for(sid,'READY')
        other=self.workspace.projects.create('Other')['id']
        scoped=InvocationContext(project_id=other)
        self.assertEqual(self.invoke('native.terminal.get',{'terminal_id':sid},scoped)['error']['code'],'PERMISSION_DENIED')
        response=self.invoke('project.trash')
        self.assertFalse(response['ok'])
        self.assertIn('terminal',response['error']['message'].lower())
        self.assertFalse(self.workspace.projects.get(self.pid).get('trashed_at'))
        self.assertFalse(self.invoke('native.terminal.start',{'backend':'codex'})['ok'])
        self.assertTrue(self.invoke('native.terminal.stop',{'terminal_id':sid})['ok'])
        self.assertEqual(self.wait_terminal(sid)['status'],'cancelled')
        self.assertTrue(self.invoke('project.trash')['ok'])

    def test_stop_terminates_child_process_group_and_timeout_is_bounded(self):
        self.fixture('''
import subprocess
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
print('CHILD '+str(child.pid),flush=True)
while True: time.sleep(.05)
''')
        sid=self.start(runtime_seconds=1)
        data=self.wait_for(sid,'CHILD ')
        child=int(data['text'].split('CHILD ')[1].split()[0])
        self.assertEqual(self.wait_terminal(sid)['status'],'timed_out')
        try:
            stat=Path(f'/proc/{child}/stat').read_text().split(') ')[1].split()[0]
            self.assertEqual(stat,'Z','Live child process escaped terminal cancellation')
        except FileNotFoundError:pass

    def test_output_ring_reports_truncation_and_native_approval_is_not_automated(self):
        self.fixture("print('Approve write? [y/N]',flush=True)\nanswer=input()\nprint('ANSWER '+answer,flush=True)\nprint('x'*200000,flush=True)\ntime.sleep(.1)\n")
        sid=self.start();before=self.wait_for(sid,'Approve write?')
        self.assertNotIn('ANSWER',before['text'])
        self.invoke('native.terminal.input',{'terminal_id':sid,'text':'n\n'})
        self.wait_terminal(sid)
        read=self.read(sid)
        self.assertTrue(read['truncated'])
        self.assertLessEqual(len(read['text'].encode()),128*1024)
        self.assertGreater(read['start_offset'],0)
        self.assertEqual(self.read(sid,read['next_offset'])['text'],'')

    def test_imported_manifest_cannot_reclaim_a_process_or_terminal_bytes(self):
        from analog_factory.storage import atomic_json
        sid='a'*32
        path=self.workspace.projects.path(self.pid)/'.af/native-terminals'/f'{sid}.json'
        atomic_json(path,{'id':sid,'backend':'codex','status':'running','pid':os.getpid(),
                         'owner_pid':os.getpid(),'process_identity':'forged','text':'PRIVATE','argv':['sh','-c','bad']})
        record=self.invoke('native.terminal.get',{'terminal_id':sid})
        self.assertTrue(record['ok'],record)
        self.assertEqual(record['data']['status'],'interrupted')
        self.assertNotIn('text',record['data'])
        self.assertFalse(self.invoke('native.terminal.input',{'terminal_id':sid,'text':'yes\n'})['ok'])
        self.assertEqual(self.read(sid)['text'],'')
        self.assertTrue(self.invoke('native.terminal.stop',{'terminal_id':sid})['ok'])

    def test_claude_uses_manual_prompts_and_fixed_arguments_only(self):
        sid=self.start(backend='claude',mode='workspace-write',model='chosen-model')
        data=self.wait_for(sid,'ARGS ')
        self.assertIn('manual',data['text'])
        self.assertNotIn('bypassPermissions',data['text'])
        self.assertNotIn('dontAsk',data['text'])
        self.assertFalse(self.invoke('native.terminal.start',{'backend':'codex','argv':['sh']})['ok'])

    def test_raw_terminal_output_cannot_enter_durable_idempotency_receipts(self):
        sid=self.start();self.wait_for(sid,'READY')
        response=self.registry.invoke('native.terminal.read',{'project_id':self.pid,'terminal_id':sid},
                                      idempotency_key='do-not-persist-terminal-output')
        self.assertFalse(response['ok'])
        self.assertEqual(response['error']['code'],'INVALID_ARGUMENT')
        self.assertNotIn('READY',json.dumps(response))

    def test_generic_task_status_cancel_and_registry_shutdown_manage_real_pty(self):
        response=self.invoke('native.terminal.start',{'backend':'codex'})
        self.assertTrue(response['ok'],response)
        sid=response['data']['id'];task=response.get('task_id')
        self.assertEqual(task,f'terminal:{self.pid}:{sid}')
        self.wait_for(sid,'READY')
        self.assertEqual(self.registry.get_operation_status(task)['data']['id'],sid)
        agent=InvocationContext(source='external-agent',permissions=frozenset({'read','execute','approve'}),confirmed=True)
        self.assertEqual(self.registry.cancel_operation(task,agent)['error']['code'],'PERMISSION_DENIED')
        self.assertTrue(self.registry.cancel_operation(task)['ok'])
        sid=self.start();self.wait_for(sid,'READY')
        self.registry.close()
        self.assertEqual(self.manager.get(self.pid,sid)['status'],'cancelled')

    def test_frontend_terminal_commands_cannot_forward_agent_owned_approval_bytes(self):
        agent=InvocationContext(source='external-agent',permissions=frozenset({'read','execute','ui','approve'}),confirmed=True)
        commands=[name for name in self.registry.operations if name.startswith('ui.native_terminal.')]
        self.assertEqual(len(commands),8)
        for name in commands:
            operation=self.registry.operations[name]
            self.assertIn('approve',operation.permissions)
            result=self.registry.invoke(name,{'client_id':'forged'},agent)
            self.assertEqual(result['error']['code'],'PERMISSION_DENIED',name)


if __name__=='__main__':unittest.main()
