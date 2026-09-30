"""A fixed, local noVNC origin, including its authenticated WebSocket stream."""
import base64
import binascii
import hashlib
import http.client
import io
import re
import select
import socket
import threading
import urllib.parse


BROWSER_URL = '/desktop/vnc.html?autoconnect=true&resize=scale&path=desktop/websockify'
MAX_ASSET = 32 * 1024 * 1024


class DesktopUnavailable(Exception):
    pass


class DesktopProxy:
    def __init__(self, url):
        # This is deliberately not a general URL proxy. Never follow redirects
        # or accept a destination supplied in a browser request.
        match = re.fullmatch(r'http://(127\.0\.0\.1|localhost):([0-9]{1,5})/?', url)
        if not match or not 1 <= int(match[2]) <= 65535:
            raise ValueError('Desktop URL must be http://127.0.0.1:PORT/ or http://localhost:PORT/')
        self.port = int(match[2])
        self.stopping = threading.Event()

    def close(self):
        self.stopping.set()

    @staticmethod
    def target(raw_path):
        parsed = urllib.parse.urlsplit(raw_path)
        path = urllib.parse.unquote(parsed.path)
        if (parsed.scheme or parsed.netloc or parsed.fragment or not path.startswith('/desktop/')
                or any(ord(char) < 32 for char in raw_path + path) or '\\' in path or '%' in path):
            raise ValueError('Invalid desktop path')
        relative = path[len('/desktop/'):]
        parts = relative.split('/')
        if any(part in ('', '.', '..') for part in parts):
            raise ValueError('Invalid desktop path')
        if relative not in ('vnc.html', 'websockify') and parts[0] not in ('app', 'core', 'vendor'):
            raise FileNotFoundError('Desktop asset not found')
        return '/' + relative, parsed.query

    def serve(self, handler):
        target, query = self.target(handler.path)
        upgrade = handler.headers.get('Upgrade', '').lower() == 'websocket'
        if target == '/websockify':
            if not upgrade or query:
                raise ValueError('A WebSocket upgrade is required at the desktop endpoint')
            return self.websocket(handler)
        if upgrade:
            raise ValueError('WebSocket upgrades are only supported at the desktop endpoint')
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            connection.request('GET', target + ('?' + query if query else ''), headers={'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            if 300 <= response.status < 400 or response.status < 200:
                raise DesktopUnavailable('Desktop server returned an unsupported response')
            body = response.read(MAX_ASSET + 1)
            if len(body) > MAX_ASSET:
                raise DesktopUnavailable('Desktop asset exceeds the size limit')
            handler.send_response(response.status)
            handler.send_header('Content-Type', response.getheader('Content-Type', 'application/octet-stream'))
            handler.send_header('Content-Length', str(len(body)))
            if response.getheader('Content-Encoding'):
                handler.send_header('Content-Encoding', response.getheader('Content-Encoding'))
            handler.send_header('Cache-Control', 'no-store')
            handler.send_header('X-Content-Type-Options', 'nosniff')
            handler.send_header('Referrer-Policy', 'no-referrer')
            # The workspace Host guard has already validated this authority.
            authority = handler.headers['Host']
            handler.send_header('Content-Security-Policy',
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; "
                f"connect-src 'self' ws://{authority}; "
                "frame-ancestors 'self'; base-uri 'none'; form-action 'self'")
            handler.end_headers()
            handler.wfile.write(body)
        except (OSError, http.client.HTTPException) as error:
            raise DesktopUnavailable('The configured desktop server is unavailable') from error
        finally:
            connection.close()

    def websocket(self, handler):
        key = handler.headers.get('Sec-WebSocket-Key', '')
        try:
            valid_key = len(base64.b64decode(key, validate=True)) == 16
        except (ValueError, binascii.Error):
            valid_key = False
        if (not valid_key or handler.headers.get('Sec-WebSocket-Version') != '13'
                or 'upgrade' not in {part.strip().lower() for part in handler.headers.get('Connection', '').split(',')}):
            raise ValueError('Invalid desktop WebSocket handshake')
        protocols = handler.headers.get('Sec-WebSocket-Protocol', '')
        if protocols and not re.fullmatch(r'[A-Za-z0-9!#$%&\x27*+.^_`|~,- ]+', protocols):
            raise ValueError('Invalid WebSocket protocol')
        upstream = None
        try:
            upstream = socket.create_connection(('127.0.0.1', self.port), timeout=5)
            headers = [f'GET /websockify HTTP/1.1', f'Host: 127.0.0.1:{self.port}',
                       'Upgrade: websocket', 'Connection: Upgrade', 'Sec-WebSocket-Version: 13',
                       f'Sec-WebSocket-Key: {key}', f'Origin: http://127.0.0.1:{self.port}']
            if protocols:
                headers.append(f'Sec-WebSocket-Protocol: {protocols}')
            upstream.sendall(('\r\n'.join(headers) + '\r\n\r\n').encode('ascii'))
            response = b''
            while b'\r\n\r\n' not in response:
                chunk = upstream.recv(4096)
                if not chunk or len(response) + len(chunk) > 65536:
                    raise DesktopUnavailable('Desktop WebSocket handshake failed')
                response += chunk
            head, pending = response.split(b'\r\n\r\n', 1)
            status_line, header_data = head.split(b'\r\n', 1)
            status = status_line.split(b' ', 2)
            response_headers = http.client.parse_headers(io.BytesIO(header_data + b'\r\n\r\n'))
            expected = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            selected = response_headers.get('Sec-WebSocket-Protocol')
            if (len(status) < 2 or status[1] != b'101'
                    or response_headers.get('Upgrade', '').lower() != 'websocket'
                    or response_headers.get('Sec-WebSocket-Accept') != expected
                    or (selected and selected not in [part.strip() for part in protocols.split(',')])):
                raise DesktopUnavailable('Desktop WebSocket handshake failed')
            handler.protocol_version = 'HTTP/1.1'
            handler.send_response_only(101, 'Switching Protocols')
            handler.send_header('Upgrade', 'websocket')
            handler.send_header('Connection', 'Upgrade')
            handler.send_header('Sec-WebSocket-Accept', expected)
            if selected:
                handler.send_header('Sec-WebSocket-Protocol', selected)
            handler.end_headers()
            handler.wfile.flush()
        except (OSError, http.client.HTTPException, ValueError) as error:
            if upstream:
                upstream.close()
            raise DesktopUnavailable('The configured desktop WebSocket is unavailable') from error
        except DesktopUnavailable:
            if upstream:
                upstream.close()
            raise
        handler.close_connection = True
        try:
            self.relay(handler.connection, upstream, pending)
        except OSError:
            # Closing either browser or desktop is a normal end to a session.
            pass
        finally:
            upstream.close()

    def relay(self, client, upstream, pending):
        # Buffers are bounded and writes are nonblocking. A slow browser cannot
        # stall the server or accumulate an unlimited desktop framebuffer.
        peers = {client: upstream, upstream: client}
        buffers = {client: bytearray(pending), upstream: bytearray()}
        for peer in peers:
            peer.setblocking(False)
        ended = False
        while not self.stopping.is_set():
            writers = [peer for peer in peers if buffers[peer]]
            if ended and not writers:
                return
            readers = [] if ended else [peer for peer in peers if len(buffers[peers[peer]]) < 256 * 1024]
            readable, writable, _ = select.select(readers, writers, [], 1)
            for peer in readable:
                try:
                    data = peer.recv(65536)
                except BlockingIOError:
                    continue
                if not data:
                    ended = True
                    continue
                buffers[peers[peer]].extend(data)
            for peer in writable:
                try:
                    written = peer.send(buffers[peer])
                except BlockingIOError:
                    continue
                if not written:
                    return
                del buffers[peer][:written]
