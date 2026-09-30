"""Single-user loopback transport. Never expose this server on a public interface."""
import json
import mimetypes
import os
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .storage import confined
from .providers import public_config
from .desktop import BROWSER_URL, DesktopProxy, DesktopUnavailable

STATIC = Path(__file__).parent / 'static'
BODY_LIMIT = 4 * 1024 * 1024


def create_server(workspace, port=8765, desktop_url=None):
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
            hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
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
                if path == '/api/status' and method == 'GET':
                    status = workspace.status()
                    if desktop:
                        status['toolchain']['desktop']['browser_url'] = BROWSER_URL
                    return self._json(status)
                if path == '/api/settings':
                    if method == 'GET':
                        return self._json(workspace.settings())
                    if method == 'PUT':
                        return self._json(workspace.save_settings(body))
                if path == '/api/provider/test' and method == 'POST':
                    return self._json(workspace.provider_test())
                if path == '/api/projects':
                    if method == 'GET':
                        return self._json(workspace.projects.list())
                    if method == 'POST':
                        return self._json(workspace.projects.create(body.get('name'), body.get('template', 'amplifier')), 201)
                parts = path.strip('/').split('/')
                if len(parts) < 3 or parts[:2] != ['api', 'projects']:
                    raise FileNotFoundError('API route not found')
                pid = parts[2]
                root = workspace.projects.path(pid)
                suffix = parts[3:]
                if not suffix and method == 'GET':
                    return self._json(workspace.detail(pid))
                if suffix == ['config'] and method == 'PUT':
                    return self._json(workspace.configure_project(pid, body))
                if suffix == ['files']:
                    if method == 'GET':
                        return self._json(workspace.projects.read(pid, arg('path')))
                    if method == 'PUT':
                        return self._json(workspace.projects.write(pid, arg('path'), body.get('content')))
                if suffix == ['history'] and method == 'GET':
                    return self._json(workspace.projects.history(pid, arg('path')))
                if suffix == ['restore'] and method == 'POST':
                    return self._json(workspace.projects.restore(pid, body.get('path'), body.get('revision')))
                if suffix == ['simulation'] and method == 'POST':
                    return self._json(workspace.configure_simulation(pid, body))
                if suffix == ['export'] and method == 'GET':
                    return self._bytes(workspace.projects.export(pid), 'application/zip', f'analog-factory-{pid[:8]}.zip')
                if suffix == ['chat'] and method == 'POST':
                    return self._json(workspace.chat(pid, body.get('message'), body.get('files', [])))
                if suffix == ['workflows']:
                    if method == 'GET':
                        return self._json(workspace.workflows(pid))
                    if method == 'POST':
                        return self._json(workspace.save_workflow(pid, body.get('workflow', {})))
                if suffix == ['workflows', 'run'] and method == 'POST':
                    return self._json(workspace.start_workflow(pid, body.get('workflow', {})), 202)
                if suffix == ['evaluate'] and method == 'POST':
                    return self._json(workspace.evaluate(pid, body), 202)
                if suffix[:1] == ['jobs']:
                    if len(suffix) == 1 and method == 'GET':
                        return self._json(workspace.jobs.list(root))
                    if len(suffix) == 2 and method == 'GET':
                        return self._json(workspace.jobs.get(root, suffix[1]))
                    if len(suffix) == 3 and suffix[2] == 'cancel' and method == 'POST':
                        return self._json(workspace.jobs.cancel(root, suffix[1]))
                if suffix == ['runs']:
                    if method == 'GET':
                        return self._json(workspace.runs(pid))
                    if method == 'POST':
                        return self._json(workspace.run(pid, body.get('kind', 'simulate'), body.get('options', {}), body.get('timeout')), 202)
                if suffix[:1] == ['runs'] and len(suffix) >= 2:
                    rid = suffix[1]
                    run_root = workspace.run_root(pid, rid)
                    if len(suffix) == 2 and method == 'GET':
                        return self._json(workspace.runner.get(run_root, rid))
                    if len(suffix) == 3:
                        action = suffix[2]
                        if method == 'POST' and action == 'cancel':
                            return self._json(workspace.runner.cancel(run_root, rid))
                        if method == 'POST' and action == 'retry':
                            return self._json(workspace.runner.retry(run_root, rid), 202)
                        if method == 'GET' and action == 'log':
                            return self._json(workspace.log(pid, rid))
                        if method == 'GET' and action == 'waveforms':
                            return self._json(workspace.waveforms(pid, rid))
                        if method == 'GET' and action == 'artifact':
                            return self._file(workspace.artifact(pid, rid, arg('path')), download=True)
                raise FileNotFoundError('API route not found')
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
            if desktop:
                desktop.close()
            super().server_close()

    return WorkspaceServer(('127.0.0.1', port), Handler)
