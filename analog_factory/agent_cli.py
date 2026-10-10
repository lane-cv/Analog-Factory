"""Bounded JSONL drivers for authenticated, installed Codex and Claude CLIs.

Codex supplies its own OS sandbox. Claude requires ``--restricted`` support and
uses a confined file-tool allowlist; that is not an OS sandbox. The optional
Codex app-server adapter delegates approvals to Codex's official reviewer.
POSIX process groups let cancellation include CLI
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
import sys
import time

from .agent_effort import validate_effort


_MAX_LINE = 1024 * 1024
_MAX_OUTPUT = 8 * 1024 * 1024
_MAX_EVENTS = 10000
_MAX_STDERR = 16 * 1024
_SESSION_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,511}\Z')
_CODEX_OFFICIAL_POLICY = {'granular': {
    'mcp_elicitations': True, 'rules': False, 'sandbox_approval': False,
    'request_permissions': False, 'skill_approval': False}}
_CLAUDE_FAST_MODELS = re.compile(r'(?:opus|claude-opus-(?:5-5|5|4-8))(?:\[1m\])?\Z')
_CLAUDE_CONNECTION_ENV = frozenset({
    'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL',
    'ANTHROPIC_CUSTOM_HEADERS', 'CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_MODEL',
    'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_SONNET_MODEL',
    'ANTHROPIC_DEFAULT_HAIKU_MODEL', 'ANTHROPIC_SMALL_FAST_MODEL',
})


def _claude_configuration():
    """Restore user connection settings skipped by --restricted, in memory only.

    Never load project settings, hooks, permissions, or arbitrary environment
    overrides. The native CLI still owns OAuth storage and managed policy.
    """
    environment = dict(os.environ)
    if environment.get('CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST'):
        return environment, None
    default_model = None
    config_dir = Path(environment.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude').expanduser()
    try:
        with (config_dir / 'settings.json').open('rb') as stream:
            contents = stream.read(_MAX_LINE + 1)
        if len(contents) > _MAX_LINE:
            raise ValueError('Settings exceed size limit')
        settings = json.loads(contents)
        if not isinstance(settings, dict) or not isinstance(settings.get('env', {}), dict):
            raise ValueError('Invalid settings object')
        for key in _CLAUDE_CONNECTION_ENV:
            if key in settings.get('env', {}):
                value = settings['env'][key]
                if not isinstance(value, str) or '\0' in value:
                    raise ValueError('Invalid environment value')
                # Claude's user settings.env normally overrides the shell.
                environment[key] = value
        model = settings.get('model')
        if model is not None and not environment.get('ANTHROPIC_MODEL'):
            if not isinstance(model, str) or '\0' in model:
                raise ValueError('Invalid model setting')
            # Keep this as a settings value, below managed policy; converting it
            # to ANTHROPIC_MODEL would incorrectly raise its precedence.
            default_model = model
    except FileNotFoundError:
        pass  # Ordinary native login or inherited environment needs no bridge.
    except (OSError, ValueError, RecursionError):
        # JSON errors can quote settings; never expose their credential content.
        raise RuntimeError('Could not read Claude user settings; check settings.json in the Claude config directory') from None
    return environment, default_model


def validate_fast_mode(backend, model, fast_mode):
    """Validate an explicit preference without changing the selected model."""
    if not isinstance(fast_mode, bool):
        raise ValueError('Fast mode must be a boolean')
    if fast_mode and backend not in {'codex', 'claude'}:
        raise ValueError('Fast mode is only supported by CLI backends')
    if fast_mode and backend == 'claude' and not _CLAUDE_FAST_MODELS.fullmatch(model or ''):
        raise ValueError('Claude Fast mode requires a supported Opus model; select Opus first')


def _command(backend, executable, model, mode, session_id, fast_mode=None, *, default_model=None,
             operation_bridge=None, effort=None, model_environment=None, official_approval=False):
    if backend not in {'codex', 'claude'}:
        raise ValueError('Unknown CLI backend')
    if mode not in {'read-only', 'workspace-write', 'automatic'} or mode == 'automatic' and not operation_bridge:
        raise ValueError('CLI agents support read-only/workspace-write, or automatic through the operation bridge')
    if session_id is not None and (not isinstance(session_id, str)
                                   or not _SESSION_ID.fullmatch(session_id)):
        raise ValueError('Invalid backend session ID')
    if model is not None and (not isinstance(model, str) or len(model) > 256
                              or any(ord(char) < 32 for char in model)):
        raise ValueError('Invalid CLI model')
    if fast_mode is not None:
        validate_fast_mode(backend, model, fast_mode)
    validate_effort(backend, model, effort, environment=model_environment)
    if backend == 'codex':
        argv = ([str(executable), 'app-server', '--listen', 'stdio://'] if official_approval
                else [str(executable), 'exec'])
        if session_id and not official_approval:
            argv.append('resume')
        native_mode = 'read-only' if operation_bridge else mode
        if not official_approval:
            argv += ['--json', '--skip-git-repo-check']
        argv += ['-c', f'sandbox_mode="{native_mode}"', '-c',
                 'approval_policy={granular={mcp_elicitations=true,rules=false,sandbox_approval=false,'
                 'request_permissions=false,skill_approval=false}}' if official_approval else 'approval_policy="never"']
        if official_approval:
            argv += ['-c', 'approvals_reviewer="auto_review"', '-c', 'features.guardian_approval=true']
        if operation_bridge:
            from .operation_mcp import NAMES
            # Only these scoped server callbacks may enter without a native
            # terminal prompt. The workspace still checks permissions and waits
            # for its own human approval before executing business mutations.
            tool_policy = ','.join(json.dumps(name) + '={approval_mode="' +
                                   ('prompt' if official_approval and name in
                                    {'invoke_operation', 'cancel_operation'} else 'approve') + '"}'
                                   for name in sorted(NAMES))
            config = ('mcp_servers={analog_factory={command=' + json.dumps(sys.executable) +
                      ',args=[' + json.dumps(str(Path(__file__).with_name('operation_mcp.py'))) +
                      '],env_vars=["AF_OPERATION_URL","AF_OPERATION_TOKEN"],tool_timeout_sec=650' +
                      ',enabled_tools=' + json.dumps(sorted(NAMES)) +
                      ',default_tools_approval_mode="prompt",tools={' + tool_policy + '}}}')
            argv += ['-c', config]
            for feature in ('shell_tool', 'apps', 'plugins', 'hooks', 'multi_agent',
                            'browser_use', 'computer_use', 'view_image'):
                argv += ['-c', f'features.{feature}=false']
        if fast_mode is not None:
            # Codex's explicit default sentinel overrides a configured Fast tier;
            # omitting the setting would inherit the CLI's global preference.
            argv += ['-c', 'service_tier="fast"' if fast_mode else 'service_tier="default"']
        if model:
            argv += ['-c', 'model=' + json.dumps(model)] if official_approval else ['--model', model]
        if effort is not None:
            argv += ['-c', 'model_reasoning_effort=' + json.dumps(effort)]
        if official_approval:
            return argv
        if session_id:
            argv.append(session_id)
        return argv + ['-']
    tools = '' if operation_bridge else 'Read,Glob,Grep' + (',Edit,Write' if mode == 'workspace-write' else '')
    allowed = 'mcp__analog_factory' if operation_bridge else tools
    argv = [str(executable), '-p', '--output-format', 'stream-json', '--verbose',
            '--restricted', '--strict-mcp-config', '--disable-slash-commands',
            '--permission-mode', 'dontAsk', '--tools', tools, '--allowedTools', allowed]
    if operation_bridge:
        argv += ['--mcp-config', json.dumps({'mcpServers': {'analog_factory': {
            'command': sys.executable, 'args': [str(Path(__file__).with_name('operation_mcp.py'))]}}},
            separators=(',', ':'))]
    settings = {'model': default_model} if default_model and not model else {}
    if fast_mode is not None:
        settings['fastMode'] = fast_mode
    if settings:
        argv += ['--settings', json.dumps(settings, separators=(',', ':'))]
    if model:
        argv += ['--model', model]
    if effort is not None:
        argv += ['--effort', effort]
    if session_id:
        argv += ['--resume', session_id]
    return argv


def _json_text(value):
    from .agent_api import _redact
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return value
        if not isinstance(parsed, (dict, list)):
            return value
        value = parsed
    return json.dumps(_redact(value), ensure_ascii=False)


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
        if kind in {'system', 'result'}:
            state = event.get('fast_mode_state')
            reason = event.get('fast_mode_disabled_reason')
            fast_status = {}
            if isinstance(state, str) and state in {'on', 'off', 'cooldown'}:
                fast_status['fast_mode_state'] = state
            if isinstance(reason, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,119}', reason):
                fast_status['fast_mode_disabled_reason'] = reason
            if fast_status:
                text = 'Fast mode: ' + fast_status.get('fast_mode_state', 'unavailable')
                if 'fast_mode_disabled_reason' in fast_status:
                    text += ' (' + reason.replace('_', ' ') + ')'
                self.emit({'type': 'status', 'status': 'warning' if reason or state == 'cooldown'
                           else 'info', 'text': text, **fast_status})
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
                 backend_session_id, prompt, emit, cancel, timeout=300, *, fast_mode=None,
                 operation_bridge=None, effort=None, official_approval=None):
    """Run one turn, emitting normalized events; EOF alone never means success.

    ``cancel`` is a threading.Event. Cancellation raises InterruptedError and
    deadlines raise TimeoutError. Backend/protocol failures raise RuntimeError.
    Requires a POSIX host and, for Claude, a CLI with ``--restricted`` support.
    """
    if official_approval is not None:
        from .agent_codex import run_codex_turn
        try:
            if backend != 'codex':
                raise ValueError('Official approval requires the Codex backend')
            return run_codex_turn(executable, project_root, model, mode, backend_session_id,
                                  prompt, emit, cancel, timeout, fast_mode=fast_mode,
                                  operation_bridge=operation_bridge, effort=effort,
                                  official_approval=official_approval)
        finally:
            official_approval.close()
    if os.name != 'posix':
        raise RuntimeError('CLI agents require POSIX process-group support')
    if not isinstance(prompt, str) or len(prompt.encode('utf-8')) > _MAX_LINE:
        raise ValueError('CLI prompt exceeds size limit or is not text')
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('CLI timeout must be positive and finite')
    if cancel.is_set():
        raise InterruptedError('CLI turn cancelled')
    environment, default_model = _claude_configuration() if backend == 'claude' else (None, None)
    if operation_bridge:
        if not isinstance(operation_bridge, dict) or not all(
                isinstance(operation_bridge.get(key), str) and operation_bridge[key]
                for key in ('url', 'token')):
            raise ValueError('Operation bridge requires a server URL and a turn credential')
        environment = dict(os.environ) if environment is None else environment
        environment.update(AF_OPERATION_URL=operation_bridge['url'], AF_OPERATION_TOKEN=operation_bridge['token'])
    argv = _command(backend, executable, model, mode, backend_session_id, fast_mode,
                    default_model=default_model, operation_bridge=operation_bridge, effort=effort,
                    model_environment=environment)
    if backend == 'claude' and effort is not None:
        # Claude resolves this environment preference before --effort. Keep the
        # child in agreement with the explicit session selection; Default still
        # inherits it. The native CLI retains its own managed-policy limits.
        environment['CLAUDE_CODE_EFFORT_LEVEL'] = effort
    parser = _Events(backend, backend_session_id, emit)
    try:
        process = subprocess.Popen(argv, cwd=Path(project_root), stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True, bufsize=0, env=environment)
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
