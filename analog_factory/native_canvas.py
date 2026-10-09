"""Owned native editor sessions and bounded literal-data acknowledgment transport.

Only fixed adapters loaded in a newly launched process have authority. Neither
archived run files nor a request written on disk constitute an acknowledgment.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import threading
import time
import uuid

from .operations import OperationError

MAX_PACKET=128*1024
MAX_DOCUMENT=2*1024*1024
FIELDS=frozenset(('nonce','command_id','verb','expected_revision','instance_id','direction','grid_steps',
    'from_instance','from_pin','to_instance','to_pin','route','box_token','layer',
    'ok','error_code','error','revision','source','modified','instances','selected','box','layers',
    'document','tool','complete','commit','document_sha256','busy'))


def encode_packet(value):
    if not isinstance(value,dict) or set(value)-FIELDS:raise ValueError('Unknown native packet field')
    data=''.join(f'{key}={str(item).encode().hex()}\n' for key,item in value.items()).encode('ascii')
    if len(data)>MAX_PACKET:raise ValueError('Native packet exceeds its 128 KiB bound')
    return data


def decode_packet(data):
    if len(data)>MAX_PACKET:raise ValueError('Native packet exceeds its 128 KiB bound')
    result={}
    try:
        for line in data.decode('ascii').splitlines():
            key,hexvalue=line.split('=',1)
            if key not in FIELDS or key in result or not re.fullmatch(r'(?:[a-f0-9]{2})*',hexvalue):
                raise ValueError('Malformed native packet field')
            result[key]=bytes.fromhex(hexvalue).decode('utf-8')
    except (UnicodeError,ValueError) as error:raise ValueError('Malformed native packet') from error
    return result


def read_private(path,limit):
    path=Path(path)
    if path.is_symlink():raise ValueError('Native private output must not be a symlink')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size>limit:raise ValueError('Native private output exceeds its regular-file bound')
        data=os.read(fd,limit+1)
        if len(data)>limit:raise ValueError('Native private output exceeds its bound')
        return data
    finally:os.close(fd)


def extract_magic_document(text,source):
    """Only one editable root cell is supported; never silently omit a child."""
    lines=text.splitlines(keepends=True)
    files=[i for i,line in enumerate(lines) if line.startswith('file ')]
    if len(files)!=1 or files[0]!=0:raise ValueError('Native save requires exactly one editable layout cell')
    expected=str(Path(source).with_suffix(''))
    if lines[0].removeprefix('file ').strip()!=expected:raise ValueError('Native archive source does not match the bound layout')
    if not lines[-1].startswith('end ') or lines[1]!='magic\n' or lines[-2]!='<< end >>\n':
        raise ValueError('Native archive is incomplete')
    return ''.join(lines[1:-1])


def write_private(path,data):
    path=Path(path)
    if path.exists() or path.is_symlink():raise ValueError('Native command identity already exists')
    temporary=path.with_suffix(path.suffix+'.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as output:output.write(data)
        os.replace(temporary,path)
    finally:
        if temporary.exists():temporary.unlink()


class NativeCanvas:
    def __init__(self,workspace):
        self.workspace=workspace
        self.sessions={}
        self.lock=threading.RLock()
        self.private_root=Path(workspace.root)/'.native-canvas'/uuid.uuid4().hex

    def resolve(self,pid,sid,rid):
        session=self.sessions.get(sid)
        if not session:raise OperationError('FRONTEND_DISCONNECTED','This server does not own the native editor session')
        if session['project_id']!=pid or session['run_id']!=rid:
            raise OperationError('NOT_FOUND','Native session does not belong to this project and run')
        row=self.workspace.runner.get(self.workspace.projects.path(pid),rid)
        if row.get('imported_evidence') or row['status'] not in ('queued','running'):
            raise OperationError('FRONTEND_DISCONNECTED','Managed native process is no longer active')
        if not row.get('owner_identity'):
            raise OperationError('FRONTEND_DISCONNECTED','Native process ownership is unavailable')
        return session

    def start(self,pid,tool,source,timeout=900):
        from . import eda
        from .storage import confined
        if tool not in ('xschem','magic'):raise ValueError('Native canvas requires Xschem or Magic')
        root=self.workspace.projects.path(pid)
        target=confined(root,source)
        if not target.is_file() or target.suffix!=('.sch' if tool=='xschem' else '.mag'):
            raise ValueError('Choose an existing source of the selected native tool')
        if len(read_private(target,MAX_DOCUMENT))>MAX_DOCUMENT:raise ValueError('Native source exceeds 2 MiB')
        with self.lock:
            active=0
            for item in self.sessions.values():
                try:
                    self.resolve(item['project_id'],item['id'],item['run_id']);active+=1
                except OperationError:pass
            if active>=8:raise ValueError('Eight managed native canvases are already active')
            sid=uuid.uuid4().hex;nonce=secrets.token_hex(32)
            directory=self.private_root/sid
            directory.mkdir(parents=True,mode=0o700)
            directory.chmod(0o700)
            write_private(directory/'nonce',(nonce+'\n').encode())
            home=directory/'home'
            home.mkdir(parents=True,mode=0o700)
            prepared=eda.prepare(root,tool,{'schematic' if tool=='xschem' else 'layout':source,'timeout':timeout},self.workspace.config(pid))
            adapter_root=Path(__file__).parent
            body=(f'set af_root {eda._tcl(directory)}\nset af_source {eda._tcl(target)}\n'
                  'set af_nonce_file [open [file join $af_root nonce] r]\n'
                  'set af_nonce [string trim [read $af_nonce_file]]\nclose $af_nonce_file\n')
            if tool=='magic':body+=f'load {eda._tcl(target)}\n'
            body+=(adapter_root/f'native_canvas_{tool}.tcl').read_text()+'\n'+(adapter_root/'native_canvas_common.tcl').read_text()
            script='af_scripts/native-canvas.tcl'
            prepared['metadata']['generated_files'][script]=body
            prepared['metadata']['native_canvas']={'session_id':sid,'source':source,'tool':tool,
                'coverage':'Fixed managed semantic verbs; existing unmanaged windows and arbitrary native UI are unsupported'}
            prepared['env']['HOME']=str(home)
            command=prepared['command']
            if tool=='xschem':command[1:1]=['-r','--script',script]
            else:command[-1]=script;command.insert(1,'-noconsole')
            row=self.workspace.runner.start(root,tool,**prepared)
            self.sessions[sid]={'id':sid,'project_id':pid,'run_id':row['id'],'tool':tool,'source':source,
                'target':target,'nonce':nonce,'directory':directory,'lock':threading.RLock(),'commands':0}
            row['native_session_id']=sid
            return row

    def live_target(self,pid,sid,rid):
        session=self.resolve(pid,sid,rid)
        ready=session['directory']/('0'*32+'.response')
        if not ready.exists():raise OperationError('FRONTEND_DISCONNECTED','Native editor has not acknowledged readiness; inspect its startup status')
        packet=decode_packet(read_private(ready,MAX_PACKET))
        if packet.get('nonce')!=session['nonce'] or packet.get('ok')!='1' or packet.get('complete')!='1':
            raise OperationError('INVALID_RESULT','Native ready acknowledgment does not belong to this server')
        return {'state':{'project_id':pid,'run_id':rid,'session_id':sid,'tool':session['tool'],'connected':True}}

    def close(self):
        for session in self.sessions.values():
            try:self.workspace.runner.cancel(self.workspace.projects.path(session['project_id']),session['run_id'])
            except (FileNotFoundError,ValueError):pass
        shutil.rmtree(self.private_root,ignore_errors=True)

    def _response(self,session,command_id,deadline,phase=None):
        path=session['directory']/f'{command_id}.response'
        while time.monotonic()<deadline:
            row=self.workspace.runner.get(self.workspace.projects.path(session['project_id']),session['run_id'])
            if row['status'] not in ('queued','running'):
                raise OperationError('FRONTEND_DISCONNECTED','Native process disconnected before acknowledgment')
            if path.exists():
                packet=decode_packet(read_private(path,MAX_PACKET))
                if (packet.get('nonce')!=session['nonce'] or packet.get('command_id')!=command_id or
                        packet.get('complete')!='1'):
                    raise OperationError('INVALID_RESULT','Native acknowledgment identity does not match this command')
                if packet.get('ok')!='1':
                    raise OperationError(packet.get('error_code') or 'EXECUTION_FAILED',packet.get('error') or 'Native command failed')
                if phase is None or packet.get('commit') in phase:return packet
            startup=session['directory']/'startup-error.txt'
            if startup.exists():raise OperationError('UNAVAILABLE',read_private(startup,4096).decode(errors='replace').strip())
            time.sleep(.01)
        raise OperationError('OUTCOME_UNKNOWN','Native command acknowledgment timed out; command must not be retried')

    def _state(self,session,packet):
        if packet.get('source')!=str(session['target']):raise OperationError('INVALID_RESULT','Native acknowledgment names another source')
        revision=int(packet['revision'])
        if not 0<revision<10**12:raise ValueError('Invalid native revision')
        instances=json.loads(packet['instances']);selected=json.loads(packet['selected'])
        box=json.loads(packet['box']);layers=json.loads(packet['layers'])
        if len(instances)>512 or len(selected)>4096 or len(layers)>512 or len(box) not in (0,4):raise ValueError('Native state exceeds its bound')
        return {'project_id':session['project_id'],'run_id':session['run_id'],'session_id':session['id'],
            'tool':session['tool'],'source':session['source'],'native_revision':revision,'modified':packet['modified']=='1',
            'instances':[dict(zip(('id','symbol','type','x','y','rotation','flip','pins'),item)) for item in instances],
            'selected':selected,'box':box,'box_token':','.join(str(n) for n in box),'layers':layers,'connected':True,
            'source_sha256':hashlib.sha256(read_private(session['target'],MAX_DOCUMENT)).hexdigest()}

    def get(self,pid,sid,rid):
        return self.command(pid,sid,rid,'inspect',0,{})

    def command(self,pid,sid,rid,verb,revision,parameters):
        session=self.resolve(pid,sid,rid)
        with session['lock']:
            if session['commands']>=255:raise OperationError('UNAVAILABLE','Native session command limit reached; open a new managed session')
            session['commands']+=1
            ready='0'*32
            self._response(session,ready,time.monotonic()+3)
            command_id=uuid.uuid4().hex
            packet={'nonce':session['nonce'],'command_id':command_id,'verb':verb,'expected_revision':revision,**parameters}
            request=session['directory']/f'{command_id}.request'
            write_private(request,encode_packet(packet))
            response=self._response(session,command_id,time.monotonic()+3)
            state=self._state(session,response)
            return {'state':state,'command_id':command_id,'acknowledged':True,'run_id':rid,'session_id':sid}

    def save(self,context,pid,sid,rid,revision,source_sha256):
        session=self.resolve(pid,sid,rid)
        registry=self.workspace.operations
        initial_version=registry.version(pid)
        with session['lock']:
            if session['commands']>=255:raise OperationError('UNAVAILABLE','Native session command limit reached')
            session['commands']+=1
            command_id=uuid.uuid4().hex
            write_private(session['directory']/f'{command_id}.request',encode_packet({'nonce':session['nonce'],
                'command_id':command_id,'verb':'save_prepare','expected_revision':revision}))
            prepared=self._response(session,command_id,time.monotonic()+3,{'prepared'})
            document_name=prepared.get('document')
            if document_name!=f'{command_id}.document':raise OperationError('INVALID_RESULT','Native exported document identity is invalid')
            text=read_private(session['directory']/document_name,MAX_DOCUMENT).decode()
            if session['tool']=='magic':text=extract_magic_document(text,session['target'])
            receipt=None;commit='cancelled';failure=None
            try:
                with self.workspace.projects.lock:
                    if registry.version(pid)!=initial_version or hashlib.sha256(read_private(session['target'],MAX_DOCUMENT)).hexdigest()!=source_sha256:
                        raise OperationError('VERSION_CONFLICT','Saved source changed during native export; native unsaved work remains intact')
                    # If native callback already expired, do not deliver a write.
                    latest=decode_packet(read_private(session['directory']/f'{command_id}.response',MAX_PACKET))
                    if latest.get('commit')!='prepared':raise OperationError('OUTCOME_UNKNOWN','Native save hold expired before source commit; nothing was written')
                    receipt=registry.invoke('file.write',{'project_id':pid,'path':session['source'],'content':text},context,
                        expected_version=initial_version)
                    if not receipt['ok']:raise OperationError(receipt['error']['code'],receipt['error']['message'])
                    if receipt['data']['sha256']!=hashlib.sha256(text.encode()).hexdigest() or read_private(session['target'],MAX_DOCUMENT)!=text.encode():
                        raise OperationError('OUTCOME_UNKNOWN','Source commit acknowledgment does not match the exported native snapshot')
                    commit='saved'
            except (OperationError,ValueError,OSError) as error:
                if receipt is None:raise
                failure=error
            finally:
                write_private(session['directory']/f'{command_id}.commit',encode_packet({'nonce':session['nonce'],
                    'command_id':command_id,'commit':commit}))
            if failure is not None:
                return {'state':self._state(session,prepared),'command_id':command_id,'acknowledged':False,
                    'run_id':rid,'session_id':sid,'write_receipt':receipt,'saved':None,'status':'interrupted',
                    'error':str(failure),'note':'Registered source write returned an uncertain or failed receipt; native work remains modified. Inspect sources and history before any further save.'}
            try:
                final=self._response(session,command_id,time.monotonic()+3,{'saved','cancelled','expired'})
                if final.get('commit')!='saved':raise OperationError('OUTCOME_UNKNOWN','Source was written but native save hold expired; editor remains modified')
                return {'state':self._state(session,final),'command_id':command_id,'acknowledged':True,
                    'run_id':rid,'session_id':sid,'write_receipt':receipt,'saved':True,
                    'note':'Project history contains this native snapshot; the editor keeps its modified flag to preserve native save/timestamp semantics'}
            except (OperationError,ValueError,OSError) as error:
                # A real write cannot be erased by an acknowledgment failure.
                return {'state':self._state(session,prepared),'command_id':command_id,'acknowledged':False,
                    'run_id':rid,'session_id':sid,'write_receipt':receipt,'saved':True,'status':'interrupted',
                    'error':str(error),'note':'Source commit completed; native receipt is uncertain. Do not retry the save.'}
