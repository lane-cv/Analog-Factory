"""Real localhost streaming with registry authorization and bounded reads."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch

from analog_factory.server import create_server
from analog_factory.service import Workspace


class TransferHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.workspace=Workspace(Path(self.tmp.name));self.server=create_server(self.workspace,port=0)
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'
        self.pid=self.workspace.projects.create('HTTP transfer')['id']

    def invoke(self,name,parameters):
        request=urllib.request.Request(self.base+'/api/operations/invoke',data=json.dumps({'operation_id':name,'parameters':parameters}).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request) as response:return json.load(response)['data']

    def test_streaming_artifact_and_zip_are_bounded_and_guarded(self):
        root=self.workspace.projects.path(self.pid)
        file=root/'large.bin';file.write_bytes(os.urandom(3*1024*1024))
        with file.open('rb') as stream:expected=hashlib.file_digest(stream,'sha256').hexdigest()
        original=Path.read_bytes
        def bounded(path):
            if path.stat().st_size>2*1024*1024:raise AssertionError('HTTP download loaded whole large artifact')
            return original(path)
        with patch.object(Path,'read_bytes',bounded):
            params={'project_id':self.pid,'kind':'file','path':'large.bin'}
            metadata=self.invoke('artifact.stat',params)
            url=self.base+'/api/artifacts/download?'+urllib.parse.urlencode({**params,'artifact_version':metadata['artifact_version']})
            actual=hashlib.sha256();total=0
            with urllib.request.urlopen(url) as response:
                self.assertEqual(response.headers['Content-Length'],str(file.stat().st_size))
                while chunk:=response.read(65536):actual.update(chunk);total+=len(chunk)
            self.assertEqual(actual.hexdigest(),expected);self.assertEqual(total,file.stat().st_size)
            prepared=self.invoke('project.archive.prepare',{'project_id':self.pid,'mode':'backup'})
            url=self.base+'/api/transfers/download?'+urllib.parse.urlencode({'project_id':self.pid,'transfer_id':prepared['id']})
            with patch.dict(os.environ,{'AF_AGENT_TOKEN':'fixture-token','AF_AGENT_PERMISSIONS':'read'}):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(urllib.request.Request(url,headers={'Authorization':'Bearer fixture-token'}))
                self.assertEqual(caught.exception.code,403)
                caught.exception.close()
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(urllib.request.Request(url,headers={'Origin':'http://foreign.invalid'}))
            self.assertEqual(caught.exception.code,403)
            caught.exception.close()
            with urllib.request.urlopen(url) as response:
                self.assertEqual(response.read(4),b'PK\x03\x04')
                while response.read(65536):pass


if __name__=='__main__':unittest.main()
