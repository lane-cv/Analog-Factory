"""Human-owned, bounded native CLI PTYs; process authority never comes from disk."""
import base64
import copy
from datetime import datetime, timezone
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
import uuid

from .execution import _process_identity
from .storage import atomic_json, confined, read_json


RING_BYTES = 128*1024
MAX_OUTPUT = 8*1024*1024
MAX_ACTIVE = 8
MAX_RETAINED = 32
ACTIVE = frozenset(('queued','running'))
PUBLIC_FIELDS = frozenset(('id','backend','mode','model','status','created_at','ended_at','exit_code','error',
                          'cols','rows','runtime_seconds','output_bytes','output_available','project_id'))


def now():return datetime.now(timezone.utc).isoformat()


def _help(executable):
    """Bounded local discovery, with no model turn or authentication handoff."""
    process = subprocess.Popen([executable,'--help'],stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
    selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ)
    chunks=[];total=0;deadline=time.monotonic()+3
    try:
        while time.monotonic()<deadline:
            for key,_ in selector.select(.05):
                data=os.read(key.fd,65536)
                if not data:
                    selector.unregister(key.fileobj)
                    return b''.join(chunks).decode(errors='replace')
                chunks.append(data);total+=len(data)
                if total>256*1024:raise ValueError('Native CLI help exceeded its bounded output limit')
            if process.poll() is not None and not selector.get_map():break
        raise ValueError('Native CLI capability probe timed out')
    finally:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        process.wait(timeout=2);selector.close();process.stdout.close()


def _executable(workspace,pid,backend):
    if backend not in ('codex','claude'):raise ValueError('Native terminals support Codex or Claude CLI')
    executable=shutil.which(workspace.config(pid).get('tools',{}).get(backend) or backend)
    if not executable:raise ValueError('The selected native CLI is unavailable; configure its executable in Settings')
    return executable


def capabilities(workspace,pid):
    backends=[]
    for backend in ('codex','claude'):
        try:
            executable=_executable(workspace,pid,backend);help_text=_help(executable)
            required=('--sandbox','--ask-for-approval','--no-alt-screen','--no-daemon') if backend=='codex' else ('--permission-mode','manual','plan')
            available=all(flag in help_text for flag in required)
            reason='Native interactive flags detected; the CLI owns login and human approval' if available else 'Installed CLI does not advertise the required interactive approval controls'
        except (ValueError,OSError) as error:
            available=False;reason=str(error)
        backends.append({'id':backend,'available':available,'diagnostic':reason,'modes':['read-only','workspace-write']})
    return {'backends':backends,'human_control_required':True,'pty_supported':os.name=='posix',
            'output_retention':'128 KiB in-memory tail; terminal bytes are not saved to projects',
            'scope':'Native CLI permissions and the local user account; project API confinement does not sandbox the terminal'}


class NativeTerminals:
    def __init__(self,workspace):
        self.workspace=workspace;self.lock=threading.RLock();self.active={};self.buffers={};self.closed=False

    def _path(self,pid,identifier):
        if not isinstance(identifier,str) or not re.fullmatch(r'[a-f0-9]{32}',identifier):raise ValueError('Invalid terminal identifier')
        return confined(self.workspace.projects.path(pid),f'.af/native-terminals/{identifier}.json',hidden=True)

    def _save(self,pid,record):atomic_json(self._path(pid,record['id']),record)

    def _public(self,pid,record):
        value={key:copy.deepcopy(data) for key,data in record.items() if key in PUBLIC_FIELDS}
        value['project_id']=pid;value['output_available']=(pid,record['id']) in self.buffers
        return value

    def get(self,pid,identifier):
        with self.lock:
            record=read_json(self._path(pid,identifier))
            if not isinstance(record,dict):raise FileNotFoundError('Native terminal does not exist in this project')
            if record.get('status') in ACTIVE and (pid,identifier) not in self.active:
                # Never signal a PID or adopt authority supplied by a manifest.
                record.update(status='interrupted',ended_at=now(),error='This server no longer owns the native terminal')
                self._save(pid,record)
            return self._public(pid,record)

    def list(self,pid):
        root=confined(self.workspace.projects.path(pid),'.af/native-terminals',hidden=True)
        if not root.exists():return []
        return sorted((self.get(pid,path.stem) for path in root.glob('*.json') if re.fullmatch(r'[a-f0-9]{32}',path.stem)),
                      key=lambda row:row.get('created_at',''),reverse=True)

    def start(self,pid,backend,mode='read-only',model='',cols=100,rows=30,runtime_seconds=3600):
        if mode not in ('read-only','workspace-write'):raise ValueError('Unsupported native terminal mode')
        if not isinstance(model,str) or len(model)>256 or any(ord(c)<32 for c in model):raise ValueError('Invalid model name')
        if not isinstance(cols,int) or not 20<=cols<=240 or not isinstance(rows,int) or not 5<=rows<=100:raise ValueError('Terminal dimensions exceed supported bounds')
        if type(runtime_seconds) not in (int,float) or not 1<=runtime_seconds<=14400:raise ValueError('Terminal runtime must be 1–14,400 seconds')
        executable=_executable(self.workspace,pid,backend)
        help_text=_help(executable)
        if backend=='codex':
            if not all(flag in help_text for flag in ('--sandbox','--ask-for-approval','--no-alt-screen','--no-daemon')):
                raise ValueError('Installed Codex CLI lacks required interactive lifecycle flags')
            argv=[executable,'--sandbox',mode,'--ask-for-approval','on-request','--no-alt-screen','--no-daemon',
                  '-c','approvals_reviewer="user"']
        else:
            if not all(flag in help_text for flag in ('--permission-mode','manual','plan')):
                raise ValueError('Installed Claude CLI lacks required manual interactive approval modes')
            argv=[executable,'--permission-mode','plan' if mode=='read-only' else 'manual']
        if model:argv += ['--model',model]
        root=self.workspace.projects.path(pid)
        with self.workspace.projects.lock,self.lock:
            if self.closed:raise ValueError('Native terminal manager is closed')
            if any(key[0]==pid for key in self.active):raise ValueError('A native terminal is already active in this project')
            if len(self.active)>=MAX_ACTIVE:raise ValueError('Workspace already has eight active native terminals')
            if self.workspace.projects.get(pid).get('trashed_at'):raise ValueError('Restore this project before starting a terminal')
            for expired in list(self.buffers):
                if len(self.buffers)<MAX_RETAINED:break
                if expired not in self.active:self.buffers.pop(expired)
            identifier=uuid.uuid4().hex;master,slave=os.openpty()
            try:
                fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',rows,cols,0,0))
                environment=dict(os.environ)
                # A human PTY must not inherit a registry capability minted for
                # an agent or service bridge. Native CLI authentication stays native.
                for key in tuple(environment):
                    if key.startswith(('AF_OPERATION_','AF_AGENT_')):environment.pop(key)
                environment.update(TERM='xterm-256color',COLORTERM='truecolor')
                process=subprocess.Popen([sys.executable,'-I',str(Path(__file__).with_name('native_terminal_child.py')),*argv],
                    cwd=root,stdin=slave,stdout=slave,stderr=slave,start_new_session=True,env=environment,close_fds=True)
            except BaseException:
                os.close(master);raise
            finally:os.close(slave)
            os.set_blocking(master,False)
            record={'id':identifier,'backend':backend,'mode':mode,'model':model,'status':'running','created_at':now(),
                'ended_at':None,'exit_code':None,'error':None,'cols':cols,'rows':rows,'runtime_seconds':runtime_seconds,
                'output_bytes':0,'owner_pid':os.getpid(),'owner_identity':_process_identity(os.getpid())}
            entry={'record':record,'process':process,'master':master,'stop':threading.Event(),'done':threading.Event()}
            key=(pid,identifier);self.active[key]=entry;self.buffers[key]={'data':b'','total':0}
            try:self._save(pid,record)
            except BaseException:
                self.active.pop(key,None);self.buffers.pop(key,None);os.close(master)
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait(timeout=2);raise
            entry['thread']=threading.Thread(target=self._run,args=(pid,key,entry),daemon=True,name='af-terminal-'+identifier[:8])
            entry['thread'].start()
            return self._public(pid,record)

    def _run(self,pid,key,entry):
        process,master,record=entry['process'],entry['master'],entry['record']
        deadline=time.monotonic()+record['runtime_seconds'];selector=selectors.DefaultSelector();selector.register(master,selectors.EVENT_READ)
        status='succeeded';error=None
        try:
            while True:
                if entry['stop'].is_set():status='cancelled';break
                if time.monotonic()>=deadline:status='timed_out';error='Native terminal runtime limit reached';break
                ready=selector.select(.05)
                for event,_ in ready:
                    try:data=os.read(event.fd,65536)
                    except OSError as failure:
                        if failure.errno==errno.EIO:data=b''
                        else:raise
                    if not data:selector.unregister(master);break
                    with self.lock:
                        buffer=self.buffers[key];buffer['total']+=len(data);buffer['data']=(buffer['data']+data)[-RING_BYTES:]
                        record['output_bytes']=buffer['total']
                    if buffer['total']>MAX_OUTPUT:status='failed';error='Native terminal exceeded its 8 MiB output budget';break
                if error or not selector.get_map():break
                if process.poll() is not None and not ready:break
        except Exception:
            status='failed';error='Native terminal process I/O failed'
        finally:
            # Always clean the owned process group, including children that
            # outlive the CLI leader. Never use PIDs loaded from disk.
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:process.wait(timeout=.25)
            except subprocess.TimeoutExpired:pass
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            process.wait(timeout=2);selector.close()
            with self.lock:
                os.close(master)
                if status=='succeeded' and process.returncode:status='failed';error='Native CLI exited with a nonzero status'
                record.update(status=status,error=error,exit_code=process.returncode,ended_at=now())
                try:self._save(pid,record)
                finally:self.active.pop(key,None);entry['done'].set()

    def read(self,pid,identifier,after=0):
        if type(after) is not int or after<0:raise ValueError('Terminal output offset must be a nonnegative integer')
        record=self.get(pid,identifier)
        with self.lock:
            buffer=self.buffers.get((pid,identifier),{'data':b'','total':0})
            start=buffer['total']-len(buffer['data']);offset=max(after,start)
            data=buffer['data'][max(0,offset-start):]
            return {'id':identifier,'status':record['status'],'text':data.decode(errors='replace'),
                'content_base64':base64.b64encode(data).decode(),'start_offset':min(offset,buffer['total']),
                'next_offset':buffer['total'],'truncated':after<start,'output_available':record['output_available']}

    def input(self,pid,identifier,text):
        if not isinstance(text,str) or not text or len(text.encode())>8192:raise ValueError('Terminal input must contain 1–8192 UTF-8 bytes')
        self.get(pid,identifier)
        with self.lock:
            entry=self.active.get((pid,identifier))
            if not entry:raise ValueError('This native terminal is no longer active in this server')
            payload=text.encode();sent=0
            while sent<len(payload):
                try:count=os.write(entry['master'],payload[sent:])
                except BlockingIOError:break
                sent+=count
            return {'id':identifier,'accepted_bytes':sent,'complete':sent==len(payload)}

    def resize(self,pid,identifier,cols,rows):
        if type(cols) is not int or not 20<=cols<=240 or type(rows) is not int or not 5<=rows<=100:raise ValueError('Terminal dimensions exceed supported bounds')
        self.get(pid,identifier)
        with self.lock:
            entry=self.active.get((pid,identifier))
            if not entry:raise ValueError('This native terminal is no longer active in this server')
            fcntl.ioctl(entry['master'],termios.TIOCSWINSZ,struct.pack('HHHH',rows,cols,0,0))
            entry['record'].update(cols=cols,rows=rows);self._save(pid,entry['record'])
            return self._public(pid,entry['record'])

    def stop(self,pid,identifier):
        record=self.get(pid,identifier)
        with self.lock:
            entry=self.active.get((pid,identifier))
            if entry:entry['stop'].set()
        # Release the manager lock so finalization can persist its terminal state.
        if entry:entry['done'].wait(3)
        return self.get(pid,identifier)

    def close(self):
        with self.lock:
            self.closed=True;entries=list(self.active.values())
            for entry in entries:entry['stop'].set()
        for entry in entries:entry['done'].wait(3)
