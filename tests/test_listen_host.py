import http.client
import json
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import threading
import unittest

from analog_factory.server import create_server
from analog_factory.service import Workspace


class ListenHostTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def start_server(self, **kwargs):
        server = create_server(Workspace(self.root), port=0, desktop_url='', **kwargs)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        return server

    def request(self, server, path='/', headers=None, body=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=3)
        self.addCleanup(connection.close)
        connection.request('POST' if body else 'GET', path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read()

    def test_default_remains_loopback(self):
        server = self.start_server()
        self.assertEqual(server.server_address[0], '127.0.0.1')
        self.assertEqual(self.request(server)[0], 200)

    def test_explicit_address_serves_page_and_same_origin_mutation(self):
        # A second loopback address exercises explicit binding without requiring a LAN.
        server = self.start_server(host='127.0.0.2')
        self.assertEqual(server.server_address[0], '127.0.0.2')
        status, body = self.request(server)
        self.assertEqual(status, 200)
        self.assertIn(b'<html', body)
        status, body = self.request(server, '/api/projects',
            headers={'Origin': f'http://127.0.0.2:{server.server_port}',
                     'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin'},
            body=json.dumps({'name': 'Explicit host project'}))
        self.assertEqual(status, 201, body)
        self.assertEqual(json.loads(body)['name'], 'Explicit host project')

    def test_explicit_address_still_rejects_foreign_requests(self):
        server = self.start_server(host='127.0.0.2')
        for headers in ({'Host': 'evil.example'}, {'Origin': 'http://evil.example'},
                        {'Host': f'127.0.0.3:{server.server_port}'},
                        {'Sec-Fetch-Site': 'cross-site'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request(server, headers=headers)[0], 403)

    def test_host_requires_concrete_ipv4_address(self):
        for host in ('0.0.0.0', '::', '::1', 'example.com', '', '224.0.0.1', '255.255.255.255'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                self.start_server(host=host)

    def test_cli_listens_on_requested_address_and_prints_usable_url(self):
        with subprocess.Popen([sys.executable, '-m', 'analog_factory', '--workspace',
                               str(self.root), 'serve', '--host', '127.0.0.2', '--port', '0'],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            try:
                ready, _, _ = select.select([process.stdout], [], [], 5)
                self.assertTrue(ready, 'The CLI did not print its listening address')
                line = process.stdout.readline().strip()
                self.assertTrue(line.startswith('Analog-factory: http://127.0.0.2:'), line)
                port = int(line.rsplit(':', 1)[1])
                connection = http.client.HTTPConnection('127.0.0.2', port, timeout=3)
                self.addCleanup(connection.close)
                connection.request('GET', '/')
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
            finally:
                process.terminate()
                process.communicate(timeout=5)
