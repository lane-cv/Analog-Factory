import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from analog_factory.providers import Provider, public_config


class ProviderTests(unittest.TestCase):
    def test_secrets_not_exported(self):
        result = public_config({'provider': {'api_key': 'secret', 'api_key_env': 'AF_KEY'}, 'token': 'secret'})
        self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(result['provider']['api_key_env'], 'AF_KEY')

    def test_unconfigured_provider_does_not_fake_response(self):
        with self.assertRaises(ValueError):
            Provider({}).chat('Hello', [])

    def test_protocols_not_assumed_interchangeable(self):
        with self.assertRaises(ValueError):
            Provider({'protocol': 'unknown', 'base_url': 'http://localhost:8000', 'model': 'x'})

    def test_openai_request_and_unknown_usage(self):
        received = []
        class Endpoint(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                payload = json.dumps({'choices': [{'message': {'content': 'Use the run log.'}}]}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        provider = Provider({'protocol': 'openai-compatible', 'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'model': 'test'})
        reply = provider.chat('Why?', [{'path': 'a.cir', 'content': 'R1 in 0 1k'}])
        self.assertEqual(reply['reply'], 'Use the run log.')
        self.assertIsNone(reply['usage'])
        self.assertEqual(received[0][0], '/v1/chat/completions')
        self.assertIn('R1 in 0 1k', json.dumps(received[0][1]))
