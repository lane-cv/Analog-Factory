"""Leased frontend sessions and acknowledged semantic commands."""
import copy
import secrets
import threading
import time
import uuid


class FrontendSessions:
    def __init__(self, lease=15, timeout=10):
        self.lease, self.timeout = lease, timeout
        self.lock = threading.Condition(threading.RLock())
        self.sessions = {}

    @staticmethod
    def clean_state(state):
        if not isinstance(state, dict):
            raise ValueError('Frontend state must be an object')
        # Frontend publishers are also responsible for omitting secret input values.
        def clean(value):
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items() if not any(s in k.lower() for s in ('secret', 'password', 'api_key', 'token', 'authorization', 'credential'))}
            if isinstance(value, list):
                return [clean(v) for v in value]
            return value
        return clean(copy.deepcopy(state))

    def connect(self, state):
        with self.lock:
            cid, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
            self.sessions[cid] = dict(token=token, state=self.clean_state(state), version=0,
                                      seen=time.monotonic(), commands={})
            # Expired sessions have no live UI; bound retained session state.
            for key, item in list(self.sessions.items()):
                if time.monotonic() - item['seen'] > 3600:
                    del self.sessions[key]
            return {'client_id': cid, 'token': token, 'version': 0}

    def _get(self, cid, token=None):
        session = self.sessions.get(cid)
        if not session or time.monotonic() - session['seen'] > self.lease:
            raise FileNotFoundError('Target frontend is disconnected or does not exist')
        if token is not None and not secrets.compare_digest(session['token'], str(token)):
            raise PermissionError('Invalid frontend session credential')
        return session

    def list(self):
        with self.lock:
            return [{'client_id': cid, 'state': copy.deepcopy(s['state']), 'version': s['version']}
                    for cid, s in self.sessions.items() if time.monotonic() - s['seen'] <= self.lease]

    def poll(self, cid, token, state, version=None):
        with self.lock:
            session = self._get(cid, token)
            session['seen'] = time.monotonic()
            cleaned = self.clean_state(state)
            if cleaned != session['state']:
                session['state'] = cleaned
                session['version'] += 1
            commands = []
            for command in session['commands'].values():
                if command['status'] == 'queued' and command['deadline'] > time.monotonic():
                    if command['expected_version'] is not None and command['expected_version'] != session['version']:
                        command.update(status='acknowledged', ok=False,
                                       error={'code': 'VERSION_CONFLICT', 'message': 'Frontend state version conflict before command delivery'})
                        self.lock.notify_all()
                        continue
                    command['status'] = 'delivered'
                    packet = {k: command[k] for k in ('command_id', 'operation_id', 'parameters')}
                    if command['expected_version'] is not None:
                        packet['expected_state'] = copy.deepcopy(session['state'])
                    commands.append(packet)
            return {'commands': commands, 'version': session['version']}

    def ack(self, cid, token, command_id, ok, data=None, error=None, state=None):
        with self.lock:
            session = self._get(cid, token)
            command = session['commands'].get(command_id)
            if not command or command['status'] != 'delivered' or command['deadline'] <= time.monotonic():
                raise ValueError('Command is unknown, expired, or already acknowledged')
            if type(ok) is not bool:
                raise ValueError('Acknowledgement ok must be boolean')
            session['state'] = self.clean_state(state or {})
            session['version'] += 1
            session['seen'] = time.monotonic()
            command.update(status='acknowledged', ok=ok, data=self.clean_state({'data': data})['data'],
                           error=error, state=copy.deepcopy(session['state']), version=session['version'])
            self.lock.notify_all()
            return {'acknowledged': True, 'version': session['version']}

    def command(self, cid, operation_id, parameters, expected_version=None):
        with self.lock:
            session = self._get(cid)
            if expected_version is not None and session['version'] != expected_version:
                raise RuntimeError('Frontend state version conflict; read the session again')
            command_id = uuid.uuid4().hex
            command = dict(command_id=command_id, operation_id=operation_id, parameters=copy.deepcopy(parameters),
                           status='queued', deadline=time.monotonic() + self.timeout, expected_version=expected_version)
            session['commands'][command_id] = command
            try:
                while command['status'] != 'acknowledged':
                    remaining = command['deadline'] - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('Frontend did not acknowledge the command; outcome is unknown')
                    self.lock.wait(min(remaining, .25))
                    self._get(cid)
                if not command['ok']:
                    error = command.get('error')
                    if isinstance(error, dict) and error.get('code') == 'VERSION_CONFLICT':
                        raise RuntimeError(error['message'])
                    raise ValueError(error.get('message', 'Frontend command failed') if isinstance(error, dict) else str(error or 'Frontend command failed'))
                return {'client_id': cid, 'data': command['data'], 'state': command['state'], 'version': command['version']}
            finally:
                session['commands'].pop(command_id, None)
