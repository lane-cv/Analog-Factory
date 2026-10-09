import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from analog_factory.providers import Provider


class ProviderTransportTests(unittest.TestCase):
    def endpoint(self, responder, **config):
        self.requests = []
        requests = self.requests
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(payload)
                try:
                    responder(self, payload, len(requests))
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return Provider({'base_url': f'http://127.0.0.1:{server.server_port}', 'model': 'local-fixture', **config})

    @staticmethod
    def send(handler, data, *, protocol='openai-compatible', end=True):
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/x-ndjson' if protocol == 'ollama' else 'text/event-stream')
        handler.end_headers()
        for item in data:
            line = json.dumps(item) + '\n' if protocol == 'ollama' else 'data: ' + json.dumps(item) + '\n\n'
            handler.wfile.write(line.encode())
            handler.wfile.flush()
        if end and protocol != 'ollama':
            handler.wfile.write(b'data: [DONE]\n\n')

    def test_sse_stream_assembles_split_tool_arguments_and_preserves_reported_usage(self):
        chunks = [
            {'choices': [{'index': 0, 'delta': {'content': 'Inspect '}}]},
            {'choices': [{'index': 0, 'delta': {'content': 'the log.', 'tool_calls': [
                {'index': 0, 'id': 'call-1', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{"path":'}}]}}]},
            {'choices': [{'index': 0, 'delta': {'tool_calls': [
                {'index': 0, 'function': {'arguments': '"notes.txt"}'}}]}, 'finish_reason': 'tool_calls'}]},
            {'choices': [], 'usage': {'total_tokens': 9}},
        ]
        provider = self.endpoint(lambda h, p, n: self.send(h, chunks), stream=True,
                                 capabilities={'streaming': True, 'tools': True})
        events = []
        result = provider.complete([{'role': 'user', 'content': 'Inspect'}], tools=[], emit=events.append)
        message = result['choices'][0]['message']
        self.assertEqual(message['content'], 'Inspect the log.')
        self.assertEqual(message['tool_calls'][0]['function']['arguments'], '{"path":"notes.txt"}')
        self.assertEqual(result['usage'], {'total_tokens': 9})
        self.assertEqual(events[0]['text'], 'Inspect ')
        self.assertTrue(events[0]['streaming'])
        self.assertTrue(self.requests[0]['stream'])

    def test_ollama_ndjson_stream_uses_native_options_and_final_usage(self):
        chunks = [{'message': {'role': 'assistant', 'content': 'Bias '}, 'done': False},
                  {'message': {'role': 'assistant', 'content': 'checked.'}, 'done': True,
                   'done_reason': 'stop', 'eval_count': 7}]
        provider = self.endpoint(lambda h, p, n: self.send(h, chunks, protocol='ollama'), protocol='ollama',
            stream=True, capabilities={'streaming': True, 'generation_parameters': ['max_tokens', 'temperature']},
            generation={'max_tokens': 30, 'temperature': .2})
        result = provider.complete([{'role': 'user', 'content': 'Inspect'}])
        self.assertEqual(result['message']['content'], 'Bias checked.')
        self.assertEqual(result['eval_count'], 7)
        self.assertEqual(self.requests[0]['options'], {'num_predict': 30, 'temperature': .2})
        self.assertNotIn('temperature', self.requests[0])

    def test_partial_stream_is_never_retried_or_accepted_as_complete(self):
        provider = self.endpoint(lambda h, p, n: self.send(h, [
            {'choices': [{'index': 0, 'delta': {'content': 'incomplete'}}]}], end=False), retries=3,
            stream=True, capabilities={'streaming': True})
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            provider.complete([{'role': 'user', 'content': 'Inspect'}])
        self.assertEqual(len(self.requests), 1)

    def test_length_finished_stream_is_rejected_before_tool_execution(self):
        provider = self.endpoint(lambda h, p, n: self.send(h, [
            {'choices': [{'index': 0, 'delta': {'content': 'too long'}, 'finish_reason': 'length'}]}]),
            stream=True, capabilities={'streaming': True})
        with self.assertRaisesRegex(ValueError, 'incomplete|rejected'):
            provider.complete([{'role': 'user', 'content': 'Inspect'}])

    def test_explicit_transient_http_retry_is_bounded_and_emits_attempts(self):
        def respond(handler, payload, number):
            if number < 3:
                handler.send_response(503)
                handler.end_headers()
            else:
                self.send(handler, [{'choices': [{'index': 0, 'delta': {'content': 'ready'}, 'finish_reason': 'stop'}]}])
        provider = self.endpoint(respond, stream=True, capabilities={'streaming': True}, retries=2)
        events = []
        result = provider.complete([{'role': 'user', 'content': 'Inspect'}], emit=events.append)
        self.assertEqual(result['choices'][0]['message']['content'], 'ready')
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(len([event for event in events if event.get('status') == 'retrying']), 2)

    def test_stream_cancellation_closes_stalled_response_promptly(self):
        received, release = threading.Event(), threading.Event()
        def respond(handler, payload, number):
            handler.send_response(200)
            handler.send_header('Content-Type', 'text/event-stream')
            handler.end_headers()
            handler.wfile.write(b'data: {"choices":[{"index":0,"delta":{"content":"partial"}}]}\n\n')
            handler.wfile.flush()
            received.set()
            release.wait(4)
        provider = self.endpoint(respond, stream=True, capabilities={'streaming': True}, timeout=10)
        cancel, errors, events = threading.Event(), [], []
        def run():
            try:
                provider.complete([{'role': 'user', 'content': 'Inspect'}], cancel=cancel, emit=events.append)
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=run)
        thread.start()
        self.assertTrue(received.wait(2))
        time.sleep(.03)
        started = time.monotonic()
        cancel.set()
        thread.join(1)
        release.set()
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - started, 1)
        self.assertIsInstance(errors[0], InterruptedError)

    def test_endpoint_concurrency_is_shared_across_provider_instances(self):
        entered, release = threading.Event(), threading.Event()
        def respond(handler, payload, number):
            entered.set()
            release.wait(3)
            data = json.dumps({'choices': [{'message': {'content': 'done'}}]}).encode()
            handler.send_response(200)
            handler.send_header('Content-Length', str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
        first = self.endpoint(respond, concurrency=1)
        second = Provider(dict(first.config))
        results = []
        workers = [threading.Thread(target=lambda p=p: results.append(p.complete([{'role': 'user', 'content': 'Inspect'}])))
                   for p in (first, second)]
        workers[0].start()
        self.assertTrue(entered.wait(2))
        workers[1].start()
        time.sleep(.05)
        self.assertEqual(len(self.requests), 1)
        release.set()
        for worker in workers:
            worker.join(3)
        self.assertEqual(len(results), 2)

    def test_unknown_capabilities_and_unsupported_generation_parameters_fail_explicitly(self):
        base = {'base_url': 'http://127.0.0.1:1', 'model': 'custom'}
        for patch in ({'stream': True}, {'capabilities': {'images': 'yes'}},
                      {'generation': {'temperature': .2}}, {'retries': 4}, {'concurrency': 0},
                      {'capabilities': {'generation_parameters': ['arbitrary']}}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                Provider({**base, **patch})
        provider = Provider({**base, 'capabilities': {'images': True}})
        self.assertTrue(provider.capabilities['images'])
        alternate = provider.with_model('different')
        self.assertFalse(alternate.capabilities['images'])


if __name__ == '__main__':
    unittest.main()
