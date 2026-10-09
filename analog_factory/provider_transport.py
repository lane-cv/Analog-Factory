"""Bounded provider HTTP and explicit Chat Completions/Ollama stream framing."""
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

MAX_RESPONSE = 4 * 1024 * 1024
MAX_LINE = 128 * 1024
_condition = threading.Condition()
_active = {}


def _check(cancel, deadline):
    if cancel is not None and cancel.is_set():
        raise InterruptedError('Agent turn cancelled')
    if time.monotonic() >= deadline:
        raise TimeoutError('Provider request exceeded its configured timeout')


@contextmanager
def _slot(provider, cancel, deadline):
    key = (provider.protocol, provider.base_url)
    with _condition:
        while True:
            _check(cancel, deadline)
            limits = _active.get(key, [])
            if len(limits) < min([provider.concurrency, *limits]):
                _active.setdefault(key, []).append(provider.concurrency)
                break
            _condition.wait(min(.05, max(0, deadline - time.monotonic())))
    try:
        yield
    finally:
        with _condition:
            _active[key].remove(provider.concurrency)
            if not _active[key]:
                del _active[key]
            _condition.notify_all()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


@contextmanager
def _interruptible(response, cancel, deadline):
    done = threading.Event()
    # Closing a buffered reader can wait for its active read lock. Shutdown its
    # underlying socket first so a silent streaming peer cannot hold cancellation.
    sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
    def watch():
        while not done.wait(.05):
            if (cancel is not None and cancel.is_set()) or time.monotonic() >= deadline:
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                return
    watcher = threading.Thread(target=watch, name='af-provider-cancel', daemon=True)
    watcher.start()
    try:
        yield response
        _check(cancel, deadline)
    finally:
        done.set()


def _records(response, protocol, cancel, deadline):
    total, frame = 0, []
    while True:
        _check(cancel, deadline)
        line = response.readline(MAX_LINE + 1)
        _check(cancel, deadline)
        total += len(line)
        if len(line) > MAX_LINE or total > MAX_RESPONSE:
            raise ValueError('Provider stream exceeds its output limit')
        if not line:
            raise ValueError('Provider stream was incomplete')
        try:
            line = line.decode('utf-8').rstrip('\r\n')
        except UnicodeError:
            raise ValueError('Provider stream contains invalid UTF-8') from None
        if protocol == 'ollama':
            if line:
                yield json.loads(line)
        elif line.startswith('data:'):
            frame.append(line[5:].lstrip(' '))
        elif line == '' and frame:
            value, frame = '\n'.join(frame), []
            if value == '[DONE]':
                yield None
                return
            yield json.loads(value)


def _stream(response, protocol, emit, cancel, deadline):
    content, thinking, calls, usage, finished, done = '', '', {}, None, None, False
    for record in _records(response, protocol, cancel, deadline):
        if record is None:
            done = True
            break
        if not isinstance(record, dict) or record.get('error'):
            raise ValueError('Provider stream reported an error or malformed event')
        if protocol == 'ollama':
            delta = record.get('message') or {}
            fragments = delta.get('tool_calls') or []
            for call in fragments:
                if not isinstance(call, dict):
                    raise ValueError('Malformed provider tool call')
                index = len(calls)
                if index >= 8:
                    raise ValueError('Provider tool call limit exceeded')
                calls[index] = call
            if record.get('done') is True:
                done = True
                finished = record.get('done_reason', 'stop')
                usage = {key: record[key] for key in ('prompt_eval_count', 'eval_count') if key in record}
        else:
            choices = record.get('choices')
            if not isinstance(choices, list):
                raise ValueError('Provider stream has malformed choices')
            if record.get('usage') is not None:
                usage = record['usage']
            if not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, dict) or choice.get('index', 0) != 0:
                raise ValueError('Provider stream has unexpected choice index')
            delta = choice.get('delta') or {}
            if choice.get('finish_reason') is not None:
                finished = choice['finish_reason']
            for part in delta.get('tool_calls') or []:
                index = part.get('index') if isinstance(part, dict) else None
                if type(index) is not int or not 0 <= index < 8:
                    raise ValueError('Malformed provider tool call index')
                call = calls.setdefault(index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                function = part.get('function') or {}
                if not isinstance(function, dict) or part.get('type', 'function') != 'function':
                    raise ValueError('Malformed provider tool call')
                for key, target in (('id', call), ('name', call['function']), ('arguments', call['function'])):
                    value = part.get(key) if key == 'id' else function.get(key)
                    if value is not None:
                        if not isinstance(value, str):
                            raise ValueError('Malformed provider tool call fragment')
                        target[key] += value
        if not isinstance(delta, dict) or delta.get('refusal'):
            raise ValueError('Provider response was rejected or malformed')
        for key in ('content', 'thinking'):
            value = delta.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError('Provider stream contains a non-text message')
        if delta.get('content'):
            content += delta['content']
            if emit:
                emit({'type': 'message', 'role': 'assistant', 'text': content, 'streaming': True})
        thinking += delta.get('thinking') or ''
        if protocol == 'ollama' and done:
            break
    if not done or finished not in ('stop', 'tool_calls', 'function_call'):
        raise ValueError('Provider stream was incomplete or rejected')
    message = {'role': 'assistant', 'content': content}
    if calls:
        message['tool_calls'] = [calls[index] for index in sorted(calls)]
    if protocol == 'ollama':
        if thinking:
            message['thinking'] = thinking
        return {'message': message, 'done': True, 'done_reason': finished, **(usage or {})}
    return {'choices': [{'message': message, 'finish_reason': finished}], 'usage': usage}


def request(provider, path, payload=None, *, cancel=None, emit=None, streaming=False):
    if not provider.base_url:
        raise ValueError('Configure a model provider first; manual design remains available')
    headers = provider._headers()
    data = None if payload is None else json.dumps(payload, allow_nan=False).encode()
    deadline = time.monotonic() + provider.timeout
    with _slot(provider, cancel, deadline):
        for attempt in range(provider.retries + 1):
            _check(cancel, deadline)
            req = urllib.request.Request(provider.base_url + path, data=data, headers=headers)
            try:
                with urllib.request.build_opener(_NoRedirect).open(req, timeout=max(.01, deadline - time.monotonic())) as response:
                    with _interruptible(response, cancel, deadline):
                        if streaming:
                            return _stream(response, provider.protocol, emit, cancel, deadline)
                        raw = response.read(MAX_RESPONSE + 1)
                        _check(cancel, deadline)
                        if len(raw) > MAX_RESPONSE:
                            raise ValueError('Provider response exceeds 4 MiB')
                        return json.loads(raw)
            except urllib.error.HTTPError as error:
                status = error.code
                error.close()
                if status not in (429, 500, 502, 503, 504) or attempt >= provider.retries:
                    raise ValueError(f'Provider returned HTTP {status}; check endpoint, model, and authentication') from None
                if emit:
                    emit({'type': 'status', 'status': 'retrying', 'attempt': attempt + 2,
                          'text': f'Provider HTTP {status}; retrying request ({attempt + 2}/{provider.retries + 1})'})
                until = min(deadline, time.monotonic() + .2 * 2 ** attempt)
                while time.monotonic() < until:
                    _check(cancel, deadline)
                    time.sleep(min(.05, max(0, until - time.monotonic())))
            except InterruptedError:
                raise
            except (urllib.error.URLError, TimeoutError, OSError):
                _check(cancel, deadline)
                # A broken connection may have consumed inference. Never retry
                # an ambiguous transport outcome, particularly a partial stream.
                raise ValueError('Provider connection failed or timed out; check endpoint and network') from None
