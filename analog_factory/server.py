"""Single-user HTTP transport: loopback by default, explicit trusted LAN opt-in."""
import ipaddress
import json
import mimetypes
import os
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .storage import confined
from .desktop import BROWSER_URL, DesktopProxy, DesktopUnavailable

STATIC = Path(__file__).parent / 'static'
BODY_LIMIT = 4 * 1024 * 1024


def create_server(workspace, port=8765, desktop_url=None, *, host='127.0.0.1'):
    address = ipaddress.IPv4Address(host)
    if address.is_unspecified or address.is_multicast or host == '255.255.255.255':
        raise ValueError('Choose a concrete local IPv4 address, not a wildcard or broadcast address')
    desktop_url = os.environ.get('AF_DESKTOP_URL', '') if desktop_url is None else desktop_url
    desktop = DesktopProxy(desktop_url) if desktop_url else None

    class Handler(BaseHTTPRequestHandler):
        server_version = 'AnalogFactory/0.1'
        # Upgrade bytes must remain in the socket for the WebSocket relay.
        rbufsize = 0

        def log_message(self, format, *args):
            # No query strings, prompts, credentials or file contents in access logs.
            pass

        def _headers(self, status, content_type, length, filename=None):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(length))
            self.send_header('Cache-Control', 'no-store')
            if getattr(self, '_operation_version', None):
                self.send_header('ETag', '"' + self._operation_version + '"')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            if filename:
                self.send_header('Content-Disposition', 'attachment; filename="' + filename.replace('"', '') + '"')
            self.end_headers()

        def _json(self, value, status=200):
            data = json.dumps(value, allow_nan=False).encode()
            self._headers(status, 'application/json; charset=utf-8', len(data))
            self.wfile.write(data)

        def _bytes(self, data, content_type, filename=None):
            self._headers(200, content_type, len(data), filename)
            self.wfile.write(data)

        def _file(self, path, download=False):
            self._headers(200, mimetypes.guess_type(path.name)[0] or 'application/octet-stream', path.stat().st_size, path.name if download else None)
            with path.open('rb') as source:
                while chunk := source.read(65536):
                    self.wfile.write(chunk)

        def _guard(self):
            hosts = {f'{name}:{self.server.server_port}'
                     for name in ('127.0.0.1', 'localhost', self.server.server_address[0])}
            if self.headers.get('Host') not in hosts:
                raise PermissionError('Only this local workspace origin is allowed')
            origin = self.headers.get('Origin')
            if origin and origin not in {'http://' + host for host in hosts}:
                raise PermissionError('Cross-origin access is not allowed')
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                raise PermissionError('Cross-site access is not allowed')

        def _body(self):
            if self.headers.get_content_type() != 'application/json':
                raise ValueError('Send application/json')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= BODY_LIMIT:
                raise ValueError('JSON request must contain 1 byte to 4 MiB')
            def invalid_constant(value):
                raise ValueError('Nonfinite JSON numbers are not allowed')
            chunks = []
            remaining = length
            while remaining:
                chunk = self.rfile.read(remaining)
                if not chunk:
                    raise ValueError('Incomplete JSON request body')
                chunks.append(chunk)
                remaining -= len(chunk)
            value = json.loads(b''.join(chunks), parse_constant=invalid_constant)
            if not isinstance(value, dict):
                raise ValueError('JSON body must be an object')
            return value

        def do_GET(self):
            self._dispatch('GET')

        def do_POST(self):
            self._dispatch('POST')

        def do_PUT(self):
            self._dispatch('PUT')

        def do_DELETE(self):
            self._dispatch('DELETE')

        def _dispatch(self, method):
            try:
                self._guard()
                parsed = urllib.parse.urlsplit(self.path)
                path = urllib.parse.unquote(parsed.path)
                query = urllib.parse.parse_qs(parsed.query)
                arg = lambda name, default='': query.get(name, [default])[0]
                body = self._body() if method in ('POST', 'PUT') else {}
                if path == '/desktop' or path.startswith('/desktop/'):
                    if not desktop or method != 'GET':
                        raise FileNotFoundError('Desktop viewer is not configured')
                    return desktop.serve(self)
                if not path.startswith('/api/'):
                    if method != 'GET':
                        raise FileNotFoundError('Route not found')
                    relative = path.lstrip('/') or 'index.html'
                    target = confined(STATIC, relative)
                    if not target.is_file():
                        raise FileNotFoundError('File not found')
                    return self._file(target)
                from .operation_http import serve_api
                return serve_api(self, workspace, method, path, query, body, desktop)
            except DesktopUnavailable as error:
                self._json({'error': str(error)}, 502)
            except PermissionError as error:
                self._json({'error': str(error)}, 403)
            except FileNotFoundError as error:
                self._json({'error': str(error)}, 404)
            except (ValueError, TypeError, KeyError, UnicodeError) as error:
                self._json({'error': str(error)}, 400)
            except RuntimeError as error:
                self._json({'error': str(error)}, 409)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                self._json({'error': 'Unexpected local service error; inspect the service console'}, 500)
                traceback.print_exc()
    class WorkspaceServer(ThreadingHTTPServer):
        def server_close(self):
            try:
                if workspace._operations is not None:
                    workspace._operations.close()
                agents = getattr(workspace, 'agents', None)
                if agents:
                    agents.close()
            finally:
                try:
                    if desktop:
                        desktop.close()
                finally:
                    super().server_close()

    server = WorkspaceServer((str(address), port), Handler)
    workspace.operation_url = f'http://{server.server_address[0]}:{server.server_port}'
    return server
