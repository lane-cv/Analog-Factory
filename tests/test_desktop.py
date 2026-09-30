import base64
import hashlib
import http.client
import json
import os
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from analog_factory.server import create_server


class WorkspaceStub:
    def status(self):
        return {'toolchain': {'desktop': {'available': True}}}


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.disconnected = threading.Event()
        owner = self

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                owner.requests.append((self.path, dict(self.headers)))
                if self.path == '/websockify':
                    accept = base64.b64encode(hashlib.sha1((self.headers['Sec-WebSocket-Key'] +
                        '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
                    self.send_response(101)
                    self.send_header('Upgrade', 'websocket')
                    self.send_header('Connection', 'Upgrade')
                    self.send_header('Sec-WebSocket-Accept', accept)
                    self.end_headers()
                    self.wfile.write(b'\x82\x0cRFB 003.008\n')
                    self.wfile.flush()
                    self.connection.settimeout(3)
                    try:
                        while data := self.connection.recv(65536):
                            self.connection.sendall(data)
                    except OSError:
                        pass
                    finally:
                        owner.disconnected.set()
                    return
                if self.path == '/app/redirect.js':
                    self.send_response(302)
                    self.send_header('Location', 'http://example.invalid/')
                    self.end_headers()
                    return
                body = b'<html>Existing noVNC login</html>' if self.path.startswith('/vnc.html') else b'export default 1;'
                self.send_response(200)
                self.send_header('Content-Type', 'text/html' if self.path.startswith('/vnc.html') else 'text/javascript')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        threading.Thread(target=self.upstream.serve_forever, daemon=True).start()
        self.addCleanup(self.upstream.server_close)
        self.addCleanup(self.upstream.shutdown)
        self.upstream_url = f'http://127.0.0.1:{self.upstream.server_port}/'
        self.server = self.start_workspace(self.upstream_url)

    def start_workspace(self, desktop_url):
        server = create_server(WorkspaceStub(), port=0, desktop_url=desktop_url)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def request(self, path, headers=None, server=None):
        connection = http.client.HTTPConnection('127.0.0.1', (server or self.server).server_port, timeout=3)
        self.addCleanup(connection.close)
        connection.request('GET', path, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    def test_assets_and_status_use_same_origin_without_changing_parent_policy(self):
        status, headers, body = self.request('/desktop/vnc.html?autoconnect=true&resize=scale&path=desktop/websockify')
        self.assertEqual(status, 200)
        self.assertIn(b'Existing noVNC login', body)
        self.assertEqual(self.requests[-1][0], '/vnc.html?autoconnect=true&resize=scale&path=desktop/websockify')
        self.assertIn("frame-ancestors 'self'", headers['Content-Security-Policy'])
        self.assertNotIn("script-src 'self' 'unsafe-inline'", headers['Content-Security-Policy'])
        self.assertEqual(self.request('/desktop/app/ui.js')[2], b'export default 1;')
        status, headers, body = self.request('/api/status')
        self.assertEqual(json.loads(body)['toolchain']['desktop']['browser_url'],
                         '/desktop/vnc.html?autoconnect=true&resize=scale&path=desktop/websockify')
        self.assertIn("frame-src 'self'", headers['Content-Security-Policy'])
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])

    def test_unconfigured_desktop_is_not_advertised_or_proxied(self):
        server = self.start_workspace('')
        self.assertNotIn('browser_url', json.loads(self.request('/api/status', server=server)[2])['toolchain']['desktop'])
        self.assertEqual(self.request('/desktop/vnc.html', server=server)[0], 404)

    def test_environment_configuration_and_explicit_disable(self):
        with patch.dict(os.environ, {'AF_DESKTOP_URL': self.upstream_url}):
            enabled = self.start_workspace(None)
            disabled = self.start_workspace('')
        self.assertEqual(self.request('/desktop/vnc.html', server=enabled)[0], 200)
        self.assertEqual(self.request('/desktop/vnc.html', server=disabled)[0], 404)

    def test_remote_or_ambiguous_upstreams_are_rejected(self):
        for url in ('https://127.0.0.1:8081/', 'http://example.com:8081/', 'http://127.0.0.2:8081/',
                    'http://user:password@127.0.0.1:8081/', 'http://127.0.0.1:8081/path',
                    'http://127.0.0.1:8081/?token=secret', 'http://127.0.0.1:8081/#fragment',
                    'http://127.0.0.1/', 'http://127.0.0.1:0/', ' http://127.0.0.1:8081/'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                create_server(WorkspaceStub(), port=0, desktop_url=url)

    def test_path_traversal_arbitrary_routes_and_redirects_are_rejected(self):
        for path in ('/desktop/../settings.json', '/desktop/%2e%2e/settings.json',
                     '/desktop/%252e%252e/settings.json', '/desktop/app/../../secret',
                     '/desktop/app/%5c..%5csecret', '/desktop/http://example.invalid/',
                     '/desktop/api/status'):
            with self.subTest(path=path):
                self.assertIn(self.request(path)[0], (400, 404))
        self.assertEqual(self.requests, [])
        self.assertEqual(self.request('/desktop/app/redirect.js')[0], 502)

    def test_http_and_upgrade_keep_origin_and_host_guards(self):
        for extra in ({'Origin': 'https://evil.example'}, {'Host': 'evil.example'}, {'Sec-Fetch-Site': 'cross-site'}):
            for upgrade in (False, True):
                with self.subTest(extra=extra, upgrade=upgrade):
                    headers = {**({'Upgrade': 'websocket', 'Connection': 'Upgrade'} if upgrade else {}), **extra}
                    self.assertEqual(self.request('/desktop/websockify' if upgrade else '/desktop/vnc.html', headers)[0], 403)
        self.assertEqual(self.requests, [])

    def test_unreachable_upstream_returns_502(self):
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0))
            server = self.start_workspace(f'http://127.0.0.1:{reserved.getsockname()[1]}/')
            self.assertEqual(self.request('/desktop/vnc.html', server=server)[0], 502)

    def websocket(self):
        connection = socket.create_connection(('127.0.0.1', self.server.server_port), timeout=3)
        self.addCleanup(connection.close)
        request = (f'GET /desktop/websockify HTTP/1.1\r\nHost: 127.0.0.1:{self.server.server_port}\r\n'
                   f'Origin: http://127.0.0.1:{self.server.server_port}\r\nUpgrade: websocket\r\n'
                   'Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n'
                   'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n')
        connection.sendall(request.encode())
        received = b''
        while b'\r\n\r\n' not in received:
            received += connection.recv(1)
        self.assertTrue(received.startswith(b'HTTP/1.1 101 Switching Protocols\r\n'), received)
        return connection

    def test_websocket_preserves_protocol_bytes_and_closes_upstream(self):
        connection = self.websocket()
        self.assertEqual(connection.recv(14), b'\x82\x0cRFB 003.008\n')
        payload = b'\x82\x84\x12\x34\x56\x78\x13\x36\x55\x7c'
        connection.sendall(payload)
        self.assertEqual(connection.recv(len(payload)), payload)
        self.assertEqual(self.requests[-1][0], '/websockify')
        connection.close()
        self.assertTrue(self.disconnected.wait(2))

    def test_upgrade_only_accepts_valid_websockify_handshake(self):
        headers = {'Upgrade': 'websocket', 'Connection': 'Upgrade', 'Sec-WebSocket-Version': '13',
                   'Sec-WebSocket-Key': 'dGhlIHNhbXBsZSBub25jZQ=='}
        for path, changes in (('/desktop/vnc.html', {}), ('/desktop/websockify?target=elsewhere', {}),
                              ('/desktop/websockify', {'Sec-WebSocket-Key': 'invalid'}),
                              ('/desktop/websockify', {'Sec-WebSocket-Version': '12'})):
            with self.subTest(path=path, changes=changes):
                self.assertIn(self.request(path, {**headers, **changes})[0], (400, 404))
        self.assertEqual(self.requests, [])

    def test_closing_server_ends_active_desktop_connection(self):
        connection = self.websocket()
        self.assertEqual(connection.recv(14), b'\x82\x0cRFB 003.008\n')
        self.server.shutdown()
        self.server.server_close()
        self.assertEqual(connection.recv(1), b'')
        self.assertTrue(self.disconnected.wait(2))


if __name__ == '__main__':
    unittest.main()
