import json
import http.client
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from analog_factory.server import create_server
from analog_factory.service import Workspace


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server = create_server(Workspace(Path(self.tmp.name)), port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def request(self, path, value=None, headers=None):
        request = urllib.request.Request(self.url + path, data=None if value is None else json.dumps(value).encode(), headers={'Content-Type': 'application/json', **(headers or {})})
        return urllib.request.urlopen(request)

    def test_create_project_and_read_file(self):
        with self.request('/api/projects', {'name': 'From HTTP'}) as response:
            project = json.load(response)
        with self.request('/api/projects/' + project['id']) as response:
            details = json.load(response)
        self.assertIn('testbenches/pre.cir', [f['path'] for f in details['files']])

    def test_cross_origin_mutation_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request('/api/projects', {'name': 'Malicious origin'}, {'Origin': 'https://evil.example'})
        self.assertEqual(raised.exception.code, 403)
        raised.exception.close()

    def test_dns_rebinding_host_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request('/api/projects', headers={'Host': 'evil.example'})
        self.assertEqual(raised.exception.code, 403)
        raised.exception.close()

    def test_unknown_endpoint_is_json_404(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request('/api/does-not-exist')
        self.assertEqual(raised.exception.code, 404)
        raised.exception.close()

    def test_json_body_split_across_packets_is_read_completely(self):
        body = json.dumps({'name': 'Split packet project'}).encode()
        with socket.create_connection(('127.0.0.1', self.server.server_port), timeout=3) as connection:
            headers = (f'POST /api/projects HTTP/1.1\r\nHost: 127.0.0.1:{self.server.server_port}\r\n'
                       f'Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n')
            connection.sendall(headers.encode() + body[:8])
            # Separate network writes must not be mistaken for a complete JSON body.
            time.sleep(0.05)
            connection.sendall(body[8:])
            response = http.client.HTTPResponse(connection)
            response.begin()
            result = json.loads(response.read())
            self.assertEqual(response.status, 201, result)
            self.assertEqual(result['name'], 'Split packet project')

    def test_json_body_ending_before_content_length_is_rejected(self):
        with socket.create_connection(('127.0.0.1', self.server.server_port), timeout=3) as connection:
            headers = (f'POST /api/projects HTTP/1.1\r\nHost: 127.0.0.1:{self.server.server_port}\r\n'
                       'Content-Type: application/json\r\nContent-Length: 100\r\n\r\n')
            connection.sendall(headers.encode() + b'{"name": "Incomplete"}')
            connection.shutdown(socket.SHUT_WR)
            response = http.client.HTTPResponse(connection)
            response.begin()
            result = json.loads(response.read())
            self.assertEqual(response.status, 400, result)
            self.assertIn('Incomplete', result['error'])
