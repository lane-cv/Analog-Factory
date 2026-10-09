"""Bounded filesystem transfers, archive round trips and scope denial."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from analog_factory.service import Workspace
from analog_factory.operations import InvocationContext
from analog_factory.storage import atomic_json


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.workspace=Workspace(Path(self.tmp.name));self.addCleanup(self.workspace.agents.close)
        self.registry=self.workspace.operations;self.addCleanup(self.registry.close)
        self.pid=self.workspace.projects.create('Transfer fixture')['id'];self.root=self.workspace.projects.path(self.pid)

    def call(self,name,parameters=None,context=None):
        result=self.registry.invoke(name,parameters or {},context,expected_version=self.registry.version((parameters or {}).get('project_id')))
        self.assertTrue(result['ok'],result);return result['data']

    def upload(self,path,context=None):
        row=self.call('project.import.begin',{'size':path.stat().st_size},context)
        with path.open('rb') as stream:
            offset=0
            while data:=stream.read(256*1024):
                row=self.call('project.import.append',{'transfer_id':row['id'],'offset':offset,'content_base64':base64.b64encode(data).decode()},context)
                offset+=len(data)
        return row

    def test_large_backup_roundtrip_is_disk_backed_and_training_evidence_is_inert(self):
        jid='a'*32;tid='taskset-'+'b'*24
        atomic_json(self.root/f'.af/jobs/{jid}.json',{'id':jid,'kind':'training','status':'succeeded',
            'created_at':'2026-10-08','owner_pid':os.getpid(),'owner_identity':'original','training_updated':True})
        atomic_json(self.root/f'.af/training/{jid}/request.json',{'workspace':'/original/private','output':'/original/checkpoint','runtime':'/original/python'})
        atomic_json(self.root/f'.af/training/{jid}/checkpoint/tokenizer.json',{'token':'technical vocabulary, not a credential'})
        optimizer=self.root/f'.af/training/{jid}/optimizer/checkpoint-1/optimizer.pt'
        optimizer.parent.mkdir(parents=True);optimizer.write_bytes(b'opaque archived optimizer; never unpickle')
        source=self.root/f'.af/training/{jid}/sources/worker.py';source.parent.mkdir();source.write_text('raise RuntimeError("archived source must stay inert")')
        atomic_json(self.root/f'.af/training-tasksets/{tid}.json',{'id':tid,'specification':{'name':'inert taskset fixture'}})
        checkpoint=self.root/f'.af/training/{jid}/checkpoint/adapter_model.safetensors'
        # >64 MiB expanded without large Python allocations, exceeding the old archive limit.
        with checkpoint.open('wb') as stream:
            for _ in range(68):stream.write(bytes(range(256))*4096)
        with checkpoint.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        original_read=Path.read_bytes
        def bounded_read(path):
            if path.stat().st_size>16*1024*1024:raise AssertionError('Whole artifact read into RAM')
            return original_read(path)
        with patch.object(Path,'read_bytes',bounded_read):
            prepared=self.call('project.archive.prepare',{'project_id':self.pid,'mode':'backup'})
            target=Path(self.tmp.name)/'download.zip'
            with target.open('wb') as output:
                offset=0
                while offset<prepared['size']:
                    row=self.call('transfer.read',{'project_id':self.pid,'transfer_id':prepared['id'],'offset':offset,'limit':256*1024})
                    data=base64.b64decode(row['content_base64']);output.write(data);offset=row['next_offset']
            uploaded=self.upload(target)
            restored=self.call('project.import.finish',{'transfer_id':uploaded['id'],'name':'Restored training'})
        newroot=self.workspace.projects.path(restored['id'])
        with (newroot/checkpoint.relative_to(self.root)).open('rb') as stream:self.assertEqual(hashlib.file_digest(stream,'sha256').hexdigest(),digest)
        record=json.loads((newroot/f'.af/jobs/{jid}.json').read_text())
        self.assertTrue(record['imported_evidence']);self.assertIsNone(record['owner_identity']);self.assertIsNone(record['owner_pid'])
        request=json.loads((newroot/f'.af/training/{jid}/request.json').read_text())
        self.assertTrue(request['imported_evidence']);self.assertNotIn('/original/private',json.dumps(request))
        self.assertTrue((newroot/f'.af/training-tasksets/{tid}.json').is_file())
        self.assertEqual((newroot/optimizer.relative_to(self.root)).read_bytes(),optimizer.read_bytes())
        self.assertEqual((newroot/source.relative_to(self.root)).read_text(),source.read_text())
        from analog_factory.training.manager import start
        with patch('analog_factory.training.manager.resources',return_value={'frameworks':{'trl_cpu':{'available':True}}}):
            with self.assertRaisesRegex(ValueError,'local|imported'):
                start(self.workspace,restored['id'],checkpoint_job_id=jid)

    def test_transfer_owner_scope_offsets_and_incomplete_import_are_enforced(self):
        row=self.call('project.import.begin',{'size':4})
        denied=InvocationContext(principal='other',source='external-agent',permissions=frozenset({'read','write'}),confirmed=True)
        for op,args in [('transfer.get',{}),('project.import.append',{'offset':0,'content_base64':'eA=='}),('transfer.release',{})]:
            response=self.registry.invoke(op,{'transfer_id':row['id'],**args},denied)
            self.assertEqual(response['error']['code'],'PERMISSION_DENIED',response)
        for args in [{'offset':1,'content_base64':'eA=='},{'offset':0,'content_base64':'eHh4eHg='}]:
            self.assertFalse(self.registry.invoke('project.import.append',{'transfer_id':row['id'],**args})['ok'])
        self.assertFalse(self.registry.invoke('project.import.finish',{'transfer_id':row['id']},expected_version=self.registry.version())['ok'])
        self.assertEqual(len(self.workspace.projects.list()),1)
        scoped=InvocationContext(project_id=self.pid,confirmed=True)
        self.assertEqual(self.registry.invoke('project.import.begin',{'size':4},scoped)['error']['code'],'PERMISSION_DENIED')

    def test_artifact_chunk_revision_detects_changes_and_rejects_escape_or_fifo(self):
        jid='a'*32;artifact=self.root/f'.af/training/{jid}/checkpoint/adapter_model.safetensors';artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b'0123456789')
        params={'project_id':self.pid,'kind':'training','job_id':jid,'path':'checkpoint/adapter_model.safetensors'}
        row=self.call('artifact.stat',params)
        data=self.call('artifact.read',{**params,'artifact_version':row['artifact_version'],'offset':2,'limit':4})
        self.assertEqual(base64.b64decode(data['content_base64']),b'2345')
        artifact.write_bytes(b'abcdefghij')
        changed=self.registry.invoke('artifact.read',{**params,'artifact_version':row['artifact_version']})
        self.assertFalse(changed['ok']);self.assertIn('changed',changed['error']['message'])
        fifo=artifact.parent/'fifo';os.mkfifo(fifo)
        for path in ('../../../../settings.json','checkpoint/fifo'):
            self.assertFalse(self.registry.invoke('artifact.stat',{**params,'path':path})['ok'])
        (artifact.parent/'link').symlink_to('/etc/passwd')
        self.assertFalse(self.registry.invoke('artifact.stat',{**params,'path':'checkpoint/link'})['ok'])

    def test_streamed_import_reuses_archive_validation_and_configuration_permission(self):
        target=Path(self.tmp.name)/'bad.zip'
        for name,data in [('../outside',b'bad'),('project.json',json.dumps({'name':'Bad','config':{'tools':{'ngspice':'/bin/true'}}}).encode())]:
            with zipfile.ZipFile(target,'w') as archive:archive.writestr(name,data)
            context=InvocationContext(principal='limited',permissions=frozenset({'read','write'}),confirmed=True)
            uploaded=self.upload(target,context)
            response=self.registry.invoke('project.import.finish',{'transfer_id':uploaded['id']},context,expected_version=self.registry.version())
            self.assertFalse(response['ok']);self.assertEqual(len(self.workspace.projects.list()),1)

    def test_large_visible_file_revision_hashing_is_streamed(self):
        visible=self.root/'binary.dat'
        with visible.open('wb') as stream:stream.truncate(3*1024*1024)
        original=Path.read_bytes
        with patch.object(Path,'read_bytes',lambda p:(_ for _ in ()).throw(AssertionError('Unbounded visible-file read')) if p==visible else original(p)):
            first=self.registry.version(self.pid)
            with visible.open('r+b') as stream:stream.write(b'changed')
            self.assertNotEqual(first,self.registry.version(self.pid))

    def test_forwarded_import_cannot_borrow_browser_configuration_permission(self):
        self.registry.sessions.timeout=.01
        client=self.registry.sessions.connect({'project_id':self.pid,'lifecycle':{'open':True,'action':'import','project_id':self.pid}})
        context=InvocationContext(principal='writer',source='external-agent',permissions=frozenset({'read','write','ui'}),confirmed=True)
        result=self.registry.invoke('ui.lifecycle.submit',{'client_id':client['client_id']},context,expected_version=self.registry.version())
        self.assertEqual(result['error']['code'],'PERMISSION_DENIED',result)
        self.assertEqual(self.registry.sessions.sessions[client['client_id']]['commands'],{})

    def test_artifact_chunk_queries_do_not_hash_the_entire_project(self):
        (self.root/'binary.dat').write_bytes(b'fixture')
        params={'project_id':self.pid,'kind':'file','path':'binary.dat'}
        with patch.object(self.registry,'version',side_effect=AssertionError('Chunk query hashed all project sources')):
            metadata=self.registry.invoke('artifact.stat',params)
            self.assertTrue(metadata['ok'],metadata);self.assertIsNone(metadata['version'])
            result=self.registry.invoke('artifact.read',{**params,'artifact_version':metadata['data']['artifact_version']})
            self.assertTrue(result['ok'],result);self.assertIsNone(result['version'])

    def test_streamed_import_finish_checks_workspace_revision_before_publication(self):
        target=Path(self.tmp.name)/'source.zip'
        with zipfile.ZipFile(target,'w') as archive:archive.writestr('readme.txt','fixture')
        row=self.upload(target);before=self.registry.version()
        self.workspace.projects.create('Concurrent membership change')
        result=self.registry.invoke('project.import.finish',{'transfer_id':row['id']},expected_version=before)
        self.assertEqual(result['error']['code'],'VERSION_CONFLICT')
        self.assertEqual(len(self.workspace.projects.list()),2)
        restored=self.call('project.import.finish',{'transfer_id':row['id']})
        self.assertEqual(self.workspace.projects.read(restored['id'],'readme.txt')['content'],'fixture')

    def test_disk_quota_release_expiry_and_dead_server_cleanup(self):
        from analog_factory.transfers import Transfers
        first=self.call('project.import.begin',{'size':1024**3})
        second=self.call('project.import.begin',{'size':1024**3})
        self.assertFalse(self.registry.invoke('project.import.begin',{'size':1})['ok'])
        self.call('transfer.release',{'transfer_id':first['id']})
        self.workspace.transfers.rows[second['id']]['touched']-=1801
        self.assertFalse(self.registry.invoke('transfer.get',{'transfer_id':second['id']})['ok'])
        orphan=Path(self.tmp.name)/'.transfers/server-dead';orphan.mkdir()
        atomic_json(orphan/'owner.json',{'pid':999999999,'identity':'dead-server-fixture'})
        (orphan/'orphan.zip').write_bytes(b'private abandoned fixture')
        manager=Transfers(self.workspace)
        try:
            self.assertFalse(orphan.exists())
            self.assertTrue(self.workspace.transfers.directory.exists(),'A live owner directory must remain intact')
        finally:manager.close()

    def test_backup_rejects_portable_path_collision_before_creating_download(self):
        (self.root/'Notes.txt').write_text('one');(self.root/'notes.txt').write_text('two')
        response=self.registry.invoke('project.archive.prepare',{'project_id':self.pid,'mode':'backup'})
        self.assertFalse(response['ok']);self.assertIn('conflicting',response['error']['message'])
        self.assertEqual(self.workspace.transfers.rows,{})


if __name__=='__main__':unittest.main()
