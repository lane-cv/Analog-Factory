"""Bounded JSONL drivers for authenticated, installed Codex and Claude CLIs.

Codex supplies its own OS sandbox. Claude requires ``--restricted`` support and
uses a confined file-tool allowlist; that is not an OS sandbox. Neither adapter
offers interactive approval. POSIX process groups let cancellation include CLI
children. Prompts go through stdin and credentials stay with the installed CLI.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import time


_MAX_LINE = 1024 * 1024
_MAX_OUTPUT = 8 * 1024 * 1024
_MAX_EVENTS = 10000
_MAX_STDERR = 16 * 1024
_SESSION_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,511}\Z')


def _command(backend, executable, model, mode, session_id):
    if backend not in {'codex', 'claude'}:
        raise ValueError('Unknown CLI backend')
    if mode not in {'read-only', 'workspace-write'}:
        raise ValueError('CLI agents support read-only or workspace-write mode')
    if session_id is not None and (not isinstance(session_id, str)
                                   or not _SESSION_ID.fullmatch(session_id)):
        raise ValueError('Invalid backend session ID')
    if model is not None and (not isinstance(model, str) or len(model) > 256
                              or any(ord(char) < 32 for char in model)):
        raise ValueError('Invalid CLI model')
    if backend == 'codex':
        argv = [str(executable), 'exec']
        if session_id:
            argv.append('resume')
        argv += ['--json', '--skip-git-repo-check',
                 '-c', f'sandbox_mode="{mode}"', '-c', 'approval_policy="never"']
        if model:
            argv += ['--model', model]
        if session_id:
            argv.append(session_id)
        return argv + ['-']
    tools = 'Read,Glob,Grep' + (',Edit,Write' if mode == 'workspace-write' else '')
    argv = [str(executable), '-p', '--output-format', 'stream-json', '--verbose',
            '--restricted', '--strict-mcp-config', '--disable-slash-commands',
            '--permission-mode', 'dontAsk', '--tools', tools, '--allowedTools', tools]
    if model:
        argv += ['--model', model]
    if session_id:
        argv += ['--resume', session_id]
    return argv


def _json_text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _required(value, kind, name):
    if not isinstance(value, kind):
        raise RuntimeError(f'Malformed CLI event: invalid {name}')
    return value


class _Events:
    def __init__(self, backend, session_id, emit):
        self.backend = backend
        self.session_id = session_id
        self.emit = emit
        self.reply = None
        self.usage = {}
        self.completed = False
        self.last_error = ''
        self.count = 0
        self.tools = {}

    def session(self, value):
        if not isinstance(value, str) or not _SESSION_ID.fullmatch(value):
            raise RuntimeError('Malformed CLI event: invalid session ID')
        if value != self.session_id:
            self.session_id = value
            self.emit({'type': 'session', 'backend_session_id': value})

    def message(self, text):
        _required(text, str, 'message text')
        self.reply = text
        self.emit({'type': 'message', 'text': text})

    def complete(self, usage):
        self.usage = _required(usage, dict, 'usage') or None
        self.completed = True
        self.emit({'type': 'usage', 'usage': self.usage})

    def accept(self, line):
        if not line.strip():
            return
        self.count += 1
        if self.count > _MAX_EVENTS:
            raise RuntimeError('CLI event count limit exceeded')
        try:
            def invalid_constant(value):
                raise ValueError(f'Invalid JSON constant {value}')
            event = json.loads(line, parse_constant=invalid_constant)
        except (ValueError, UnicodeError, RecursionError) as error:
            raise RuntimeError('CLI returned malformed JSON') from error
        _required(event, dict, 'object')
        _required(event.get('type'), str, 'type')
        if self.backend == 'codex':
            self.codex(event)
        else:
            self.claude(event)

    def codex(self, event):
        kind = event['type']
        if kind == 'thread.started':
            self.session(event.get('thread_id'))
        elif kind == 'turn.failed':
            raise RuntimeError('Codex failed: ' + _json_text(
                event.get('error') or event.get('message') or 'Unknown backend error'))
        elif kind == 'error':
            # Codex also emits this event while reconnecting. Its own retry loop
            # remains active; only turn.failed, a bad exit, or missing completion
            # makes the turn fail. Raising here would kill the recovering CLI.
            self.last_error = _json_text(event.get('message') or event.get('error') or 'Codex connection error')
            self.emit({'type': 'status', 'status': 'warning', 'text': self.last_error})
        elif kind == 'turn.started':
            self.emit({'type': 'status', 'status': 'running', 'text': 'Codex is working'})
        elif kind == 'turn.completed':
            self.complete(event.get('usage', {}))
        elif kind in {'item.started', 'item.updated', 'item.completed'}:
            item = _required(event.get('item'), dict, 'item')
            item_type = _required(item.get('type'), str, 'item type')
            if item_type == 'agent_message' and kind == 'item.completed':
                self.message(item.get('text'))
            elif item_type in {'command_execution', 'file_change', 'mcp_tool_call',
                               'web_search', 'collab_tool_call'}:
                tool = item_type
                if item_type == 'mcp_tool_call':
                    tool = str(item.get('server', 'mcp')) + '/' + str(item.get('tool', 'tool'))
                if kind == 'item.completed':
                    detail = item.get('aggregated_output', item.get('result',
                             item.get('changes', item.get('query', item.get('error', '')))))
                    event_type = 'tool_result'
                else:
                    detail = item.get('command', item.get('arguments',
                             item.get('changes', item.get('query', ''))))
                    event_type = 'tool_call' if kind == 'item.started' else 'status'
                normalized = {'type': event_type, 'tool': tool, 'text': _json_text(detail),
                              'status': str(item.get('status', 'completed' if kind == 'item.completed'
                                                   else 'in_progress'))}
                if isinstance(item.get('id'), str) and item['id']:
                    normalized['tool_call_id'] = item['id']
                self.emit(normalized)

    def claude(self, event):
        kind = event['type']
        if event.get('session_id') is not None:
            self.session(event['session_id'])
        if kind == 'result':
            is_error = _required(event.get('is_error', False), bool, 'result error flag')
            if event.get('subtype') != 'success' or is_error:
                raise RuntimeError('Claude failed: ' + _json_text(
                    event.get('errors') or event.get('result') or event.get('subtype', 'Unknown backend error')))
            result = _required(event.get('result'), str, 'result text')
            if result != self.reply:
                self.message(result)
            self.complete(event.get('usage', {}))
        elif kind in {'assistant', 'user'}:
            message = _required(event.get('message'), dict, 'message')
            content = _required(message.get('content'), list, 'message content')
            texts = []
            for block in content:
                _required(block, dict, 'content block')
                block_type = _required(block.get('type'), str, 'content block type')
                if block_type == 'text' and kind == 'assistant':
                    texts.append(_required(block.get('text'), str, 'message text'))
                elif block_type == 'tool_use':
                    tool = _required(block.get('name'), str, 'tool name')
                    tool_id = _required(block.get('id'), str, 'tool ID')
                    arguments = _required(block.get('input'), dict, 'tool input')
                    self.tools[tool_id] = tool
                    self.emit({'type': 'tool_call', 'tool': tool,
                               'tool_call_id': tool_id, 'text': _json_text(arguments), 'status': 'in_progress'})
                elif block_type == 'tool_result':
                    tool_id = _required(block.get('tool_use_id'), str, 'tool result ID')
                    self.emit({'type': 'tool_result', 'tool': self.tools.get(tool_id, tool_id),
                               'tool_call_id': tool_id, 'text': _json_text(block.get('content', '')),
                               'status': 'failed' if block.get('is_error') else 'completed'})
            if texts:
                self.message('\n'.join(texts))
        elif kind == 'system' and event.get('subtype') == 'permission_denied':
            self.emit({'type': 'status', 'status': 'denied', 'text': _json_text(
                event.get('message', 'Claude denied a tool request'))})


def _terminate_group(process):
    # Send to the group even if the leader has exited: grandchildren may still
    # hold a pipe or ignore SIGTERM. The second signal closes that escape hatch.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=2)


def run_cli_turn(backend, executable, project_root, model, mode,
                 backend_session_id, prompt, emit, cancel, timeout=300):
    """Run one turn, emitting normalized events; EOF alone never means success.

    ``cancel`` is a threading.Event. Cancellation raises InterruptedError and
    deadlines raise TimeoutError. Backend/protocol failures raise RuntimeError.
    Requires a POSIX host and, for Claude, a CLI with ``--restricted`` support.
    """
    argv = _command(backend, executable, model, mode, backend_session_id)
    if os.name != 'posix':
        raise RuntimeError('CLI agents require POSIX process-group support')
    if not isinstance(prompt, str) or len(prompt.encode('utf-8')) > _MAX_LINE:
        raise ValueError('CLI prompt exceeds size limit or is not text')
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('CLI timeout must be positive and finite')
    if cancel.is_set():
        raise InterruptedError('CLI turn cancelled')
    parser = _Events(backend, backend_session_id, emit)
    try:
        process = subprocess.Popen(argv, cwd=Path(project_root), stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True, bufsize=0)
    except OSError as error:
        raise RuntimeError(f'Could not start {backend}: {error}') from error
    deadline = time.monotonic() + timeout
    pending_input = memoryview(prompt.encode('utf-8'))
    output = bytearray()
    stderr = bytearray()
    total = 0
    try:
        with selectors.DefaultSelector() as selector:
            for stream, mask in [(process.stdout, selectors.EVENT_READ),
                                 (process.stderr, selectors.EVENT_READ),
                                 (process.stdin, selectors.EVENT_WRITE)]:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, mask)
            while selector.get_map() or process.poll() is None:
                if cancel.is_set():
                    raise InterruptedError('CLI turn cancelled')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f'{backend} CLI turn timed out after {timeout:g}s')
                for key, mask in selector.select(min(0.05, remaining)):
                    stream = key.fileobj
                    if mask & selectors.EVENT_WRITE:
                        try:
                            if pending_input:
                                count = os.write(stream.fileno(), pending_input[:65536])
                                pending_input = pending_input[count:]
                        except BrokenPipeError:
                            pending_input = memoryview(b'')
                        except BlockingIOError:
                            continue
                        if not pending_input:
                            selector.unregister(stream)
                            stream.close()
                        continue
                    try:
                        chunk = os.read(stream.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(stream)
                        stream.close()
                        if stream is process.stdout and output:
                            parser.accept(bytes(output))
                            output.clear()
                        continue
                    if stream is process.stderr:
                        stderr.extend(chunk)
                        del stderr[:-_MAX_STDERR]
                        continue
                    total += len(chunk)
                    if total > _MAX_OUTPUT:
                        raise RuntimeError('CLI output size limit exceeded')
                    output.extend(chunk)
                    while b'\n' in output:
                        line, _, tail = output.partition(b'\n')
                        if len(line) > _MAX_LINE:
                            raise RuntimeError('CLI event size limit exceeded')
                        parser.accept(bytes(line))
                        output = bytearray(tail)
                    if len(output) > _MAX_LINE:
                        raise RuntimeError('CLI event size limit exceeded')
        if cancel.is_set():
            raise InterruptedError('CLI turn cancelled')
        if process.returncode != 0:
            detail = stderr.decode('utf-8', errors='replace').strip() or parser.last_error
            raise RuntimeError(f'{backend} CLI exited with code {process.returncode}'
                               + (f': {detail}' if detail else ''))
        if not parser.completed:
            detail = stderr.decode('utf-8', errors='replace').strip() or parser.last_error
            raise RuntimeError(f'{backend} CLI ended without a successful completion event'
                               + (f': {detail}' if detail else ''))
        if parser.reply is None or not parser.session_id:
            raise RuntimeError(f'{backend} CLI completion is missing reply or session ID')
        return {'reply': parser.reply, 'usage': parser.usage,
                'backend_session_id': parser.session_id}
    finally:
        _terminate_group(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            if not stream.closed:
                stream.close()
