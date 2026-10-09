"""Bounded disk-backed transfers; private handles never carry path authority."""
import base64
import binascii
import hashlib
import mimetypes
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import threading
import time
import uuid

from .project_lifecycle import CHUNK_BYTES, ProjectLifecycle, TRANSFER_LIMITS
from .storage import confined, atomic_json
from .execution import _process_identity


TTL = 1800
MAX_TRANSFERS = 8
DISK_QUOTA = 2*1024*1024*1024


def _revision(info):
    return hashlib.sha256(f'{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}:{info.st_ctime_ns}'.encode()).hexdigest()


def _open_regular(path):
    descriptor=os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    stream=os.fdopen(descriptor,'rb')
    info=os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_size>TRANSFER_LIMITS.expanded_bytes:
        stream.close();raise ValueError('Artifact must be a regular file of at most 2 GiB')
    return stream,info


def artifact_path(workspace,parameters):
    pid=parameters['project_id'];kind=parameters['kind'];relative=parameters['path']
    if kind=='run':
        if not parameters.get('run_id'):raise ValueError('A run artifact requires run_id')
        return workspace.artifact(pid,parameters['run_id'],relative)
    root=workspace.projects.path(pid)
    if kind=='training':
        jid=parameters.get('job_id')
        if not isinstance(jid,str) or not re.fullmatch('[a-f0-9]{32}',jid):raise ValueError('A training artifact requires job_id')
        root=confined(root,'.af/training/'+jid,hidden=True)
    return confined(root,relative)


def artifact_stat(workspace,parameters):
    path=artifact_path(workspace,parameters)
    stream,info=_open_regular(path)
    stream.close()
    filename=re.sub(r'[\x00-\x1f\x7f"\\]', '_', path.name)
    return {'filename':filename,'size':info.st_size,'artifact_version':_revision(info),
            'content_type':mimetypes.guess_type(path.name)[0] or 'application/octet-stream','chunk_bytes':CHUNK_BYTES}


def artifact_read(workspace,parameters):
    path=artifact_path(workspace,parameters)
    stream,info=_open_regular(path)
    with stream:
        if _revision(info)!=parameters['artifact_version']:raise ValueError('Artifact changed; refresh metadata before reading another chunk')
        offset=parameters.get('offset',0);limit=parameters.get('limit',CHUNK_BYTES)
        if offset>info.st_size:raise ValueError('Artifact offset exceeds file size')
        stream.seek(offset);data=stream.read(limit)
        if _revision(os.fstat(stream.fileno()))!=parameters['artifact_version']:raise ValueError('Artifact changed while reading')
    return {'content_base64':base64.b64encode(data).decode(),'offset':offset,'next_offset':offset+len(data),
            'size':info.st_size,'eof':offset+len(data)==info.st_size,'artifact_version':parameters['artifact_version']}


class Transfers:
    def __init__(self,workspace):
        self.workspace=workspace;self.lock=threading.RLock();self.rows={};self.closed=False
        root=workspace.root/'.transfers';root.mkdir(mode=0o700,exist_ok=True)
        if root.is_symlink():raise ValueError('Transfer storage cannot be a symlink')
        # Reap only directories belonging to a dead server identity. An active
        # second server's private transfer files are never adopted or removed.
        for old in root.glob('server-*'):
            owner=old/'owner.json'
            if old.is_symlink() or not old.is_dir() or not owner.is_file() or owner.is_symlink() or owner.stat().st_size>8192:continue
            try:
                identity=json.loads(owner.read_text())
                if identity.get('identity') and _process_identity(identity['pid'])!=identity['identity']:
                    shutil.rmtree(old)
            except (ValueError,OSError,KeyError,TypeError):continue
        self.directory=Path(tempfile.mkdtemp(prefix='server-',dir=root));self.directory.chmod(0o700)
        atomic_json(self.directory/'owner.json',{'pid':os.getpid(),'identity':_process_identity(os.getpid())})
        self.lifecycle=ProjectLifecycle(workspace.projects,workspace,limits=TRANSFER_LIMITS)

    def _prune(self):
        for identifier,row in list(self.rows.items()):
            if time.monotonic()-row['touched']>TTL:
                row['path'].unlink(missing_ok=True);self.rows.pop(identifier)

    def _new(self,context,size,pid,kind):
        self._prune()
        if self.closed:raise ValueError('Transfer service is closed')
        if len(self.rows)>=MAX_TRANSFERS or sum(row['reserved'] for row in self.rows.values())+size>DISK_QUOTA:
            raise ValueError('Transfer storage quota reached; release an existing transfer')
        identifier=uuid.uuid4().hex
        row={'id':identifier,'project_id':pid,'kind':kind,'status':'uploading' if kind=='import' else 'preparing',
            'size':size,'received':0,'reserved':size,'path':self.directory/(identifier+'.zip'),
            'principal':context.principal,'source':context.source,'scope':context.project_id,'touched':time.monotonic(),
            'filename':'project.zip','content_type':'application/zip','chunk_bytes':CHUNK_BYTES,'expires_in':TTL}
        self.rows[identifier]=row;return row

    def _get(self,context,parameters):
        self._prune();row=self.rows.get(parameters['transfer_id'])
        if not row:raise FileNotFoundError('Transfer is absent or expired; begin again')
        if (context.principal,context.source,context.project_id)!=(row['principal'],row['source'],row['scope']) or parameters.get('project_id')!=row['project_id']:
            raise PermissionError('Transfer belongs to a different caller or project scope')
        row['touched']=time.monotonic();return row

    def _public(self,row):
        return {key:row[key] for key in ('id','project_id','kind','status','size','received','filename','content_type','chunk_bytes','expires_in')}

    def begin(self,context,size):
        if context.project_id:raise PermissionError('Project-scoped callers cannot import a different project')
        with self.lock:
            row=self._new(context,size,None,'import');row['path'].touch(mode=0o600)
            return self._public(row)

    def prepare(self,context,pid,mode):
        with self.lock:
            row=self._new(context,TRANSFER_LIMITS.compressed_bytes,pid,mode)
            try:
                self.lifecycle.backup_to(pid,row['path'],mode=mode)
                row.update(status='ready',size=row['path'].stat().st_size,received=row['path'].stat().st_size)
                row['reserved']=row['size'];row['filename']=f'analog-factory-{mode}-{pid[:8]}.zip'
                return self._public(row)
            except BaseException:
                row['path'].unlink(missing_ok=True);self.rows.pop(row['id'],None);raise

    def append(self,context,parameters):
        with self.lock:
            row=self._get(context,parameters)
            if row['kind']!='import' or row['status']!='uploading':raise ValueError('Transfer does not accept upload chunks')
            try:data=base64.b64decode(parameters['content_base64'],validate=True)
            except (ValueError,binascii.Error):raise ValueError('Chunk must contain valid base64') from None
            if not data or len(data)>CHUNK_BYTES:raise ValueError('Upload chunk must contain 1–256 KiB')
            offset=parameters['offset']
            if offset!=row['received'] or offset+len(data)>row['size']:raise ValueError('Upload offset or declared ZIP size does not match')
            with row['path'].open('r+b') as stream:
                stream.seek(offset);stream.write(data)
            row['received']+=len(data);return self._public(row)

    def finish(self,context,parameters):
        if context.project_id:raise PermissionError('Project-scoped callers cannot import a different project')
        with self.lock:
            row=self._get(context,parameters)
            if row['kind']!='import' or row['received']!=row['size']:raise ValueError('Complete the declared ZIP upload before importing')
            project=self.lifecycle.import_archive(row['path'],parameters.get('name'),configure_allowed='configure' in context.permissions)
            row['path'].unlink(missing_ok=True);self.rows.pop(row['id']);return project

    def get(self,context,parameters):
        with self.lock:return self._public(self._get(context,parameters))

    def read(self,context,parameters):
        with self.lock:
            row=self._get(context,parameters)
            if row['status']!='ready':raise ValueError('Transfer is not a prepared download')
            offset=parameters.get('offset',0)
            if offset>row['size']:raise ValueError('Transfer offset exceeds file size')
            with row['path'].open('rb') as stream:
                stream.seek(offset);data=stream.read(parameters.get('limit',CHUNK_BYTES))
            return {'content_base64':base64.b64encode(data).decode(),'offset':offset,'next_offset':offset+len(data),
                    'size':row['size'],'eof':offset+len(data)==row['size']}

    def release(self,context,parameters):
        with self.lock:
            row=self._get(context,parameters);row['path'].unlink(missing_ok=True);self.rows.pop(row['id'])
            return {'id':row['id'],'released':True}

    def close(self):
        with self.lock:
            self.closed=True;self.rows.clear();shutil.rmtree(self.directory,ignore_errors=True)
