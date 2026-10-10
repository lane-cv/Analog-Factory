"""Bounded Codex app-server transport for native automatic approval review.

Only stdout from our native child can mint approval evidence. Session history,
tool output strings and notifications from other turns never enter the ledger.
The bridge still enforces the application's independent permission boundary.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import selectors
import subprocess
import time

from .agent_cli import (_command, _CODEX_OFFICIAL_POLICY, _Events, _json_text, _MAX_EVENTS, _MAX_LINE,
                        _MAX_OUTPUT, _MAX_STDERR, _SESSION_ID, _terminate_group)


_EVIDENCE = frozenset({'item/started', 'item/completed',
                       'item/autoApprovalReview/started', 'item/autoApprovalReview/completed'})


def _identifier(value):
    if not isinstance(value, str) or not _SESSION_ID.fullmatch(value):
        raise RuntimeError('Malformed Codex app-server identifier')
    return value


class _Protocol:
    def __init__(self, project_root, model, session_id, prompt, emit, ledger, send, *, effort, fast_mode):
        self.root = str(Path(project_root).resolve())
        self.model = model
        self.effort = effort
        self.fast_mode = fast_mode
        self.previous_session = session_id
        self.prompt = prompt
        self.emit = emit
        self.ledger = ledger
        self.send = send
        self.events = _Events('codex', session_id, emit)
        self.thread_id = self.turn_id = None
        self.expected_response = 1
        self.usage = None
        self.completed = False
        self.count = 0
        self.before_turn = []

    def start(self):
        self.send({'id': 1, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'analog_factory', 'version': '1.0'},
            'capabilities': {'experimentalApi': True}}})

    def response(self, message):
        request_id = message.get('id')
        if type(request_id) is not int or request_id != self.expected_response:
            raise RuntimeError('Unexpected Codex app-server response')
        if 'error' in message:
            # RPC/config errors may quote settings containing credentials.
            raise RuntimeError('Codex rejected the official approval request; check native policy and configuration')
        result = message.get('result')
        if not isinstance(result, dict):
            raise RuntimeError('Malformed Codex app-server response')
        self.expected_response = None
        if request_id == 1:
            self.send({'method': 'initialized'})
            params = {'cwd': self.root, 'sandbox': 'read-only',
                      'approvalPolicy': _CODEX_OFFICIAL_POLICY, 'approvalsReviewer': 'auto_review'}
            if self.model:
                params['model'] = self.model
            if self.previous_session:
                params['threadId'] = self.previous_session
            self.expected_response = 2
            self.send({'id': 2, 'method': 'thread/resume' if self.previous_session else 'thread/start',
                       'params': params})
        elif request_id == 2:
            sandbox = result.get('sandbox')
            policy = result.get('approvalPolicy')
            valid_policy = (policy == _CODEX_OFFICIAL_POLICY and
                            all(type(value) is bool for value in policy['granular'].values()))
            if (result.get('approvalsReviewer') != 'auto_review'
                    or not valid_policy
                    or not isinstance(sandbox, dict) or sandbox.get('type') != 'readOnly'
                    or sandbox.get('networkAccess', False) is not False):
                raise RuntimeError('Codex did not activate the required official reviewer and read-only sandbox policy')
            thread = result.get('thread')
            if not isinstance(thread, dict):
                raise RuntimeError('Codex thread response is missing its thread')
            self.thread_id = _identifier(thread.get('id'))
            if self.previous_session and self.thread_id != self.previous_session:
                raise RuntimeError('Codex resumed a different thread')
            self.events.session(self.thread_id)
            self.expected_response = 3
            params = {
                'threadId': self.thread_id, 'approvalsReviewer': 'auto_review',
                'approvalPolicy': _CODEX_OFFICIAL_POLICY,
                'input': [{'type': 'text', 'text': self.prompt}]}
            # Resumed threads can retain their own preferences. Pin explicit
            # selections on this turn instead of relying on process defaults.
            if self.model:
                params['model'] = self.model
            if self.effort is not None:
                params['effort'] = self.effort
            if self.fast_mode is not None:
                params['serviceTier'] = 'fast' if self.fast_mode else 'default'
            self.send({'id': 3, 'method': 'turn/start', 'params': params})
        elif request_id == 3:
            turn = result.get('turn')
            if not isinstance(turn, dict) or turn.get('status') not in {'inProgress', 'completed'}:
                raise RuntimeError('Codex did not start the requested turn')
            self.turn_id = _identifier(turn.get('id'))
            self.ledger.bind(self.thread_id, self.turn_id)
            self.emit({'type': 'status', 'status': 'running', 'text': 'Codex is working with official Auto-review'})
            queued, self.before_turn = self.before_turn, []
            for notification in queued:
                if self.completed:
                    break
                self.notification(notification)

    def notification(self, message):
        if self.completed:
            return
        method, params = message['method'], message.get('params', {})
        if not isinstance(params, dict):
            raise RuntimeError('Malformed Codex app-server notification')
        if not self.turn_id:
            # A live turn notification can race the turn/start response. Keep
            # bounded envelopes until that response gives the authoritative ID.
            # No history in thread/start/resume responses is replayed here.
            if self.expected_response == 3:
                self.before_turn.append(message)
            return
        if params.get('threadId') != self.thread_id:
            return
        turn = params.get('turn')
        turn_id = turn.get('id') if method in {'turn/started', 'turn/completed'} and isinstance(turn, dict) else params.get('turnId')
        if turn_id != self.turn_id:
            return
        if method in _EVIDENCE:
            self.ledger.accept(message)
        if method in {'item/started', 'item/completed'}:
            item = params.get('item')
            if not isinstance(item, dict):
                raise RuntimeError('Malformed Codex app-server item')
            kind = item.get('type')
            if kind == 'agentMessage' and method == 'item/completed':
                self.events.message(item.get('text'))
            elif kind == 'mcpToolCall':
                tool = str(item.get('server', 'mcp')) + '/' + str(item.get('tool', 'tool'))
                completed = method == 'item/completed'
                self.emit({'type': 'tool_result' if completed else 'tool_call', 'tool': tool,
                           'tool_call_id': _identifier(item.get('id')),
                           'status': str(item.get('status', 'completed' if completed else 'inProgress')),
                           'text': _json_text((item.get('error') or item.get('result', '')) if completed
                                              else item.get('arguments', {}))})
        elif method == 'thread/tokenUsage/updated':
            usage = params.get('tokenUsage')
            usage = usage.get('last') if isinstance(usage, dict) else None
            if not isinstance(usage, dict):
                raise RuntimeError('Malformed Codex usage event')
            translated = {}
            for native, local in [('inputTokens', 'input_tokens'), ('cachedInputTokens', 'cached_input_tokens'),
                                  ('outputTokens', 'output_tokens')]:
                value = usage.get(native)
                if value is not None:
                    if type(value) is not int or value < 0:
                        raise RuntimeError('Malformed Codex usage counter')
                    translated[local] = value
            self.usage = translated or None
        elif method == 'error':
            self.emit({'type': 'status', 'status': 'warning',
                       'text': 'Codex reported an error; waiting for native recovery.'})
        elif method == 'turn/completed':
            self.ledger.close()
            if turn.get('status') != 'completed':
                raise RuntimeError('Codex official approval turn failed or was interrupted')
            self.completed = True
            self.events.complete(self.usage or {})

    def accept(self, line):
        if not line.strip():
            return
        self.count += 1
        if self.count > _MAX_EVENTS:
            raise RuntimeError('CLI event count limit exceeded')
        try:
            def invalid(value):
                raise ValueError('Nonfinite JSON number')
            message = json.loads(line, parse_constant=invalid)
        except (ValueError, UnicodeError, RecursionError):
            raise RuntimeError('Codex app-server returned malformed JSON') from None
        if not isinstance(message, dict):
            raise RuntimeError('Malformed Codex app-server envelope')
        if 'method' in message:
            if not isinstance(message['method'], str):
                raise RuntimeError('Malformed Codex app-server method')
            if 'id' in message:
                # Official review cannot silently become an application-side
                # automatic answer to an approval, login, or ordinary question.
                raise RuntimeError('Codex requires human input. Stop, disable automatic approval, and retry in manual mode.')
            self.notification(message)
        else:
            self.response(message)


def run_codex_turn(executable, project_root, model, mode, backend_session_id, prompt,
                   emit, cancel, timeout, *, fast_mode, operation_bridge, effort, official_approval):
    if os.name != 'posix':
        raise RuntimeError('CLI agents require POSIX process-group support')
    if not isinstance(prompt, str) or len(prompt.encode('utf-8')) > _MAX_LINE:
        raise ValueError('CLI prompt exceeds size limit or is not text')
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('CLI timeout must be positive and finite')
    if cancel.is_set():
        raise InterruptedError('CLI turn cancelled')
    if not isinstance(operation_bridge, dict) or not all(
            isinstance(operation_bridge.get(key), str) and operation_bridge[key] for key in ('url', 'token')):
        raise ValueError('Official approval requires the scoped operation bridge')
    environment = dict(os.environ, AF_OPERATION_URL=operation_bridge['url'], AF_OPERATION_TOKEN=operation_bridge['token'])
    argv = _command('codex', executable, model, mode, backend_session_id, fast_mode,
                    operation_bridge=operation_bridge, effort=effort, model_environment=environment,
                    official_approval=True)
    try:
        process = subprocess.Popen(argv, cwd=Path(project_root), stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True, bufsize=0, env=environment)
    except OSError:
        raise RuntimeError('Could not start Codex app-server') from None
    deadline = time.monotonic() + timeout
    output, outgoing, stderr = bytearray(), bytearray(), bytearray()
    total = 0
    try:
        with selectors.DefaultSelector() as selector:
            for stream in (process.stdout, process.stderr, process.stdin):
                os.set_blocking(stream.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
            selector.register(process.stderr, selectors.EVENT_READ)

            def send(message):
                encoded = (json.dumps(message, ensure_ascii=False) + '\n').encode('utf-8')
                if len(outgoing) + len(encoded) > _MAX_OUTPUT:
                    raise RuntimeError('CLI input size limit exceeded')
                outgoing.extend(encoded)
                try:
                    selector.get_key(process.stdin)
                except KeyError:
                    selector.register(process.stdin, selectors.EVENT_WRITE)

            protocol = _Protocol(project_root, model, backend_session_id, prompt, emit, official_approval, send,
                                 effort=effort, fast_mode=fast_mode)
            protocol.start()
            while not protocol.completed:
                if cancel.is_set():
                    raise InterruptedError('CLI turn cancelled')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f'Codex CLI turn timed out after {timeout:g}s')
                if not selector.get_map():
                    raise RuntimeError('Codex app-server ended without a successful completion event')
                for key, mask in selector.select(min(.05, remaining)):
                    stream = key.fileobj
                    if mask & selectors.EVENT_WRITE:
                        try:
                            written = os.write(stream.fileno(), outgoing[:65536])
                            del outgoing[:written]
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            raise RuntimeError('Codex app-server closed its input') from None
                        if not outgoing:
                            selector.unregister(stream)
                        continue
                    try:
                        chunk = os.read(stream.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(stream)
                        if stream is process.stdout:
                            if output:
                                raise RuntimeError('Codex app-server ended with an incomplete event')
                            if not protocol.completed:
                                raise RuntimeError('Codex app-server ended without a successful completion event')
                        continue
                    if stream is process.stderr:
                        # Drain bounded diagnostics but never expose native
                        # stderr: it can quote environment/config credentials.
                        stderr.extend(chunk)
                        del stderr[:-_MAX_STDERR]
                        continue
                    total += len(chunk)
                    if total > _MAX_OUTPUT:
                        raise RuntimeError('CLI output size limit exceeded')
                    output.extend(chunk)
                    while b'\n' in output:
                        line, _, rest = output.partition(b'\n')
                        if len(line) > _MAX_LINE:
                            raise RuntimeError('CLI event size limit exceeded')
                        output = bytearray(rest)
                        protocol.accept(bytes(line))
                        if protocol.completed:
                            break
                    if len(output) > _MAX_LINE:
                        raise RuntimeError('CLI event size limit exceeded')
            if cancel.is_set():
                raise InterruptedError('CLI turn cancelled')
            if protocol.events.reply is None:
                raise RuntimeError('Codex CLI completion is missing reply')
            return {'reply': protocol.events.reply, 'usage': protocol.usage,
                    'backend_session_id': protocol.thread_id}
    finally:
        # Revoke authority before terminating the transport, including on
        # success, so late bridge callbacks cannot spend leftover approvals.
        try:
            official_approval.close()
        finally:
            try:
                _terminate_group(process)
            finally:
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
