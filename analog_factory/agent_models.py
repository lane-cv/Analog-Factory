"""Discover CLI model metadata without opening a conversation or model turn.

Credentials and provider configuration remain owned by each CLI. Discovery only
uses Codex's JSONL metadata protocol or Claude's version command; errors exposed
to the browser are authored here, never copied from CLI output.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import threading
import time

from .agent_effort import EFFORT_LEVELS, CLAUDE_LEVELS, claude_effort_levels, validate_effort


_TIMEOUT = 8
_MAX_OUTPUT = 2 * 1024 * 1024
_MAX_LINE = 256 * 1024
_MAX_MODELS = 200
_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()


class _DiscoveryError(Exception):
    pass


class _Process:
    """One deadline and output budget cover handshake, pages, and stderr."""

    def __init__(self, argv, cwd):
        self.deadline = time.monotonic() + _TIMEOUT
        self.process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        start_new_session=True)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ, 'stdout')
        self.selector.register(self.process.stderr, selectors.EVENT_READ, 'stderr')
        self.buffer = b''
        self.lines = deque()
        self.total = 0

    def close(self):
        # Kill the process group even when its leader has already exited, so a
        # faulty executable cannot leave a child holding the pipe open.
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            self.process.wait(timeout=.15)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.process.wait(timeout=1)
        self.selector.close()
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()

    def send(self, value):
        self.process.stdin.write(json.dumps(value).encode() + b'\n')
        self.process.stdin.flush()

    def line(self):
        while not self.lines:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise _DiscoveryError('timeout')
            if not self.selector.get_map():
                if self.buffer:
                    line, self.buffer = self.buffer, b''
                    return line
                return None
            events = self.selector.select(remaining)
            if not events:
                raise _DiscoveryError('timeout')
            for key, _ in events:
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    self.selector.unregister(key.fileobj)
                    continue
                self.total += len(chunk)
                if self.total > _MAX_OUTPUT:
                    raise _DiscoveryError('output limit')
                if key.data == 'stderr':
                    continue
                self.buffer += chunk
                while b'\n' in self.buffer:
                    line, self.buffer = self.buffer.split(b'\n', 1)
                    if len(line) > _MAX_LINE:
                        raise _DiscoveryError('line limit')
                    if line.strip():
                        self.lines.append(line)
                if len(self.buffer) > _MAX_LINE:
                    raise _DiscoveryError('line limit')
        return self.lines.popleft()

    def request(self, request_id, method, params):
        self.send({'id': request_id, 'method': method, 'params': params})
        while True:
            line = self.line()
            if line is None:
                raise _DiscoveryError('unexpected exit')
            message = json.loads(line)
            if not isinstance(message, dict):
                raise _DiscoveryError('malformed protocol')
            # Metadata discovery must not answer auth or approval requests.
            if 'method' in message and 'id' in message:
                raise _DiscoveryError('unexpected server request')
            if message.get('id') != request_id:
                continue
            if 'error' in message or not isinstance(message.get('result'), dict):
                raise _DiscoveryError('metadata unavailable')
            return message['result']


def _text(value, limit):
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        return ''
    return value.strip()[:limit]


def _codex_effort(row):
    entries = row.get('supportedReasoningEfforts', [])
    levels = []
    if isinstance(entries, list):
        for item in entries[:32]:
            value = item.get('reasoningEffort') if isinstance(item, dict) else None
            if isinstance(value, str) and value in EFFORT_LEVELS and value not in levels:
                levels.append(value)
    default = row.get('defaultReasoningEffort')
    return {'effort_levels': levels, 'default_effort': default if default in levels else None}


def _codex(executable, project_root):
    process = _Process([str(executable), 'app-server', '--stdio'], project_root)
    try:
        process.request(1, 'initialize', {
            'clientInfo': {'name': 'analog-factory', 'version': '1'},
            'capabilities': {'experimentalApi': True},
        })
        process.send({'method': 'initialized', 'params': {}})
        models, identifiers, cursors = [], set(), set()
        cursor = None
        for page in range(20):
            result = process.request(page + 2, 'model/list', {
                'limit': min(100, _MAX_MODELS - len(models)),
                'cursor': cursor, 'includeHidden': False,
            })
            rows = result.get('data')
            if not isinstance(rows, list) or len(rows) > _MAX_MODELS:
                raise _DiscoveryError('malformed catalog')
            for row in rows:
                if not isinstance(row, dict) or row.get('hidden'):
                    continue
                raw_id = row.get('model') or row.get('id')
                identifier = _text(raw_id, 256)
                if not identifier or len(raw_id) > 256 or identifier in identifiers:
                    continue
                tiers = row.get('serviceTiers', [])
                legacy = row.get('additionalSpeedTiers', [])
                fast = (isinstance(tiers, list) and any(
                    isinstance(tier, dict) and tier.get('id') in ('fast', 'priority')
                    for tier in tiers)) or (isinstance(legacy, list) and 'fast' in legacy)
                models.append({'id': identifier,
                               'label': _text(row.get('displayName'), 120) or identifier,
                               'description': _text(row.get('description'), 500),
                               'is_default': row.get('isDefault') is True,
                               **_codex_effort(row),
                               'fast_mode': bool(fast)})
                identifiers.add(identifier)
                if len(models) >= _MAX_MODELS:
                    break
            cursor = result.get('nextCursor')
            if not cursor or len(models) >= _MAX_MODELS:
                break
            if not isinstance(cursor, str) or len(cursor) > 1024 or cursor in cursors:
                raise _DiscoveryError('invalid pagination')
            cursors.add(cursor)
        else:
            raise _DiscoveryError('pagination limit')
        if not models:
            raise _DiscoveryError('empty catalog')
        return {'backend': 'codex', 'models': models, 'source': 'cli', 'diagnostic': '',
                'fast_mode': {'supported': any(model['fast_mode'] for model in models)}}
    finally:
        process.close()


def _claude_help_effort(executable, project_root, deadline):
    """Inspect the local CLI's advertised flag without opening a model turn."""
    process = _Process([str(executable), '--help'], project_root)
    process.deadline = min(process.deadline, deadline)
    try:
        lines = []
        while (line := process.line()) is not None:
            lines.append(line.decode('utf-8', errors='replace'))
        if process.process.wait(timeout=max(.01, process.deadline - time.monotonic())) != 0:
            return []
        match = re.search(r'--effort\s+<\w+>[^\n]*(?:\n[^\n]*){0,2}', '\n'.join(lines))
        if not match:
            return []
        return [level for level in CLAUDE_LEVELS if re.search(r'\b' + level + r'\b', match[0])]
    except (OSError, ValueError, _DiscoveryError, subprocess.SubprocessError):
        return []
    finally:
        process.close()


def _claude(executable, project_root):
    process = _Process([str(executable), '--version'], project_root)
    try:
        output = []
        while (line := process.line()) is not None:
            output.append(line.decode('utf-8', errors='replace'))
        # A successfully printed version alone is insufficient if the executable
        # subsequently fails; --version is expected to exit without inference.
        remaining = max(.01, process.deadline - time.monotonic())
        if process.process.wait(timeout=remaining) != 0:
            raise _DiscoveryError('version failed')
        match = re.search(r'\b(\d+)\.(\d+)\.(\d+)\b', '\n'.join(output))
        if not match:
            raise _DiscoveryError('unknown version')
        version = tuple(int(part) for part in match.groups())
        disabled = os.environ.get('CLAUDE_CODE_DISABLE_FAST_MODE') == '1'
        supported = version >= (2, 1, 205) and not disabled
        fast_mode = {'supported': supported}
        if disabled:
            fast_mode['reason'] = 'Fast mode is disabled by the CLI environment.'
        elif not supported:
            fast_mode['reason'] = 'Fast mode requires Claude Code 2.1.205 or newer.'
        effort_levels = _claude_help_effort(executable, project_root, process.deadline)
        return {'backend': 'claude', 'source': 'builtin',
                'diagnostic': 'Built-in CLI aliases; availability depends on your CLI account.',
                'models': [{'id': name, 'label': name.title(), 'is_default': False,
                            'effort_levels': [level for level in claude_effort_levels(name) if level in effort_levels],
                            'default_effort': None,
                            'fast_mode': supported and name == 'opus'}
                           for name in ('opus', 'sonnet', 'haiku')],
                'effort_levels': effort_levels,
                'fast_mode': fast_mode}
    finally:
        process.close()


def _discover(backend, executable, project_root):
    try:
        return (_codex if backend == 'codex' else _claude)(executable, project_root)
    except (OSError, ValueError, _DiscoveryError, subprocess.SubprocessError, RecursionError):
        return {'backend': backend, 'models': [], 'source': 'unavailable',
                'diagnostic': 'CLI model discovery is unavailable. Use the CLI default or enter a model ID.',
                'fast_mode': {'supported': False,
                              'reason': 'CLI model capabilities could not be verified.'}}


def _selected_model_metadata(result, backend, model):
    if backend == 'claude' and result.get('source') != 'unavailable':
        from .agent_cli import _claude_configuration
        try:
            environment, _ = _claude_configuration()
        except RuntimeError:
            environment = None
        syntax = result.get('effort_levels', []) if environment is not None else []
        for row in result.get('models', []):
            row['effort_levels'] = [level for level in claude_effort_levels(row['id'], environment)
                                    if level in syntax]
        if model is not None:
            label = next((row['label'] for row in result.get('models', []) if row['id'] == model), model)
            result['selected_model'] = {'id': model, 'label': label, 'default_effort': None,
                'effort_levels': [level for level in claude_effort_levels(model, environment) if level in syntax]}
    elif model is not None:
        row = next((row for row in result.get('models', []) if model and row['id'] == model), {})
        result['selected_model'] = {'id': model, 'label': row.get('label', model),
                                    'effort_levels': row.get('effort_levels', []),
                                    'default_effort': row.get('default_effort')}
    return result


def catalog(backend, executable, project_root, refresh=False, *, model=None):
    """Return bounded public metadata, cached per executable and project scope."""
    if backend not in {'codex', 'claude'}:
        raise ValueError('Unknown CLI backend')
    if model is not None and (not isinstance(model, str) or len(model) > 200
                              or any(ord(c) < 32 for c in model)):
        raise ValueError('Agent model must be a string of up to 200 characters')
    executable = Path(executable or '/nonexistent-analog-factory-cli').resolve()
    project_root = Path(project_root).resolve()
    try:
        stat = executable.stat()
        fingerprint = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    except OSError:
        fingerprint = None
    key = (backend, str(executable), fingerprint, str(project_root),
           os.environ.get('CLAUDE_CODE_DISABLE_FAST_MODE') if backend == 'claude' else None)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if not refresh and cached and cached[0] > now:
            _CACHE.move_to_end(key)
            return _selected_model_metadata(deepcopy(cached[1]), backend, model)
    result = _discover(backend, executable, project_root)
    ttl = 30 if result['source'] == 'unavailable' else 300
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic() + ttl, deepcopy(result))
        _CACHE.move_to_end(key)
        while len(_CACHE) > 128:
            _CACHE.popitem(last=False)
    return _selected_model_metadata(result, backend, model)


def validate_session_effort(backend, model, effort, executable, project_root):
    """Verify explicit preferences against discoverable CLI/model capabilities."""
    environment = None
    if backend == 'claude' and effort is not None:
        from .agent_cli import _claude_configuration
        environment, _ = _claude_configuration()
    validate_effort(backend, model, effort, environment=environment)
    if effort is None:
        return
    if not model:
        raise ValueError('Effort requires an explicit model; the CLI default model can vary')
    metadata = catalog(backend, executable, project_root, model=model)
    if backend == 'claude':
        levels = [level for level in claude_effort_levels(model, environment)
                  if level in metadata.get('effort_levels', [])]
    else:
        row = next((row for row in metadata.get('models', []) if row.get('id') == model), {})
        levels = row.get('effort_levels', [])
    if effort not in levels:
        raise ValueError('Effort is not advertised for this CLI/model; choose Default or refresh the model catalog')
