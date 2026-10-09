"""Private, bounded SQLite operation receipts for the single serving process.

Only hashes of requests are stored. A reservation is committed before the
handler is entered; an unfinished reservation is evidence of an unknown
outcome, never permission to execute it again. Ambiguous receipts are pinned:
when they fill capacity, new keyed requests fail before their handlers run.
"""
from __future__ import annotations

import copy
import json
import os
import sqlite3
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path


SECRET_FIELDS = frozenset({'secret', 'password', 'api_key', 'token', 'authorization',
                           'credential', 'credentials', 'access_token', 'refresh_token',
                           'session_token', 'client_secret', 'private_key'})


def secret_values(value):
    """Find explicitly named credential inputs without persisting them."""
    values = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in SECRET_FIELDS and isinstance(item, str) and item:
                values.append(item)
            values.extend(secret_values(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(secret_values(item))
    return values


def unknown_response(envelope, reason='The original operation was interrupted before its outcome was durably recorded'):
    """Never retain a partial payload that might contain credentials."""
    return dict(ok=False, operation_id=envelope.get('operation_id'), trace_id=envelope.get('trace_id'),
                data=None, error={'code': 'OUTCOME_UNKNOWN', 'message': reason +
                '; inspect the affected state or task before issuing a new request key'},
                task_id=envelope.get('task_id'), status='unknown', progress=None,
                artifacts=[], version=None)


class OperationJournal:
    def __init__(self, root, *, replay_limit=1000, event_limit=2000, task_limit=10000,
                 claim_limit=10000, replay_seconds=30 * 86400, response_bytes=1024 * 1024):
        self.directory = Path(root) / '.operations'
        if self.directory.is_symlink():
            raise OSError('Operation journal directory must not be a symbolic link')
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        self.path = self.directory / 'journal.sqlite3'
        # Do not follow attacker-supplied database links or broaden permissions.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError('Operation journal must be a regular file')
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        self.lock = threading.RLock()
        self.replay_limit, self.event_limit, self.task_limit = replay_limit, event_limit, task_limit
        self.claim_limit, self.replay_seconds, self.response_bytes = claim_limit, replay_seconds, response_bytes
        if min(replay_limit, event_limit, task_limit, claim_limit, replay_seconds, response_bytes) <= 0:
            raise ValueError('Journal retention limits must be positive')
        with self._transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS requests (
                    key_hash TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL, response TEXT NOT NULL, updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS requests_retention ON requests(state, updated);
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated REAL NOT NULL);
            ''')
            self._prune_requests(db)

    @contextmanager
    def _transaction(self):
        """Short owned connections make close nonblocking and avoid leaked FDs."""
        with self.lock:
            db = None
            try:
                if self.path.is_symlink() or self.directory.is_symlink():
                    raise OSError('Operation journal must not follow symbolic links')
                db = sqlite3.connect(self.path, timeout=5)
                db.execute('PRAGMA synchronous=FULL')
                db.execute('PRAGMA secure_delete=ON')
                db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            except sqlite3.Error as error:
                if db is not None:
                    db.rollback()
                raise OSError('Operation journal storage is unavailable') from error
            except BaseException:
                if db is not None:
                    db.rollback()
                raise
            finally:
                if db is not None:
                    db.close()

    @staticmethod
    def _json(value):
        return json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':'))

    def _prune_requests(self, db, *, room=False):
        # Only acknowledged completed responses expire. Unresolved mutations do
        # not become executable again because of age, load, or a process restart.
        db.execute("DELETE FROM requests WHERE state = 'completed' AND updated < ?",
                   (time.time() - self.replay_seconds,))
        db.execute("DELETE FROM requests WHERE key_hash IN (SELECT key_hash FROM requests "
                   "WHERE state = 'completed' ORDER BY updated DESC, rowid DESC LIMIT -1 OFFSET ?)",
                   (self.replay_limit,))
        if room:
            count = db.execute('SELECT count(*) FROM requests').fetchone()[0]
            if count >= self.claim_limit:
                db.execute("DELETE FROM requests WHERE key_hash IN (SELECT key_hash FROM requests "
                           "WHERE state = 'completed' ORDER BY updated, rowid LIMIT ?)",
                           (count - self.claim_limit + 1,))

    def reserve(self, key_hash, fingerprint, envelope):
        with self._transaction() as db:
            self._prune_requests(db)
            existing = db.execute('SELECT fingerprint, response FROM requests WHERE key_hash = ?', (key_hash,)).fetchone()
            if existing:
                return {'acquired': False, 'fingerprint': existing[0], 'response': json.loads(existing[1])}
            self._prune_requests(db, room=True)
            if db.execute('SELECT count(*) FROM requests').fetchone()[0] >= self.claim_limit:
                raise OSError('Operation journal is full of unresolved requests; no new keyed mutation was started')
            db.execute('INSERT INTO requests VALUES (?, ?, ?, ?, ?)',
                       (key_hash, fingerprint, 'claimed', self._json(unknown_response(envelope)), time.time()))
            return {'acquired': True}

    def finalize(self, key_hash, fingerprint, envelope, *, secrets=()):
        response = self._json(envelope)
        unsafe = bool(secret_values(envelope)) or any(secret in response for secret in secrets)
        oversized = len(response.encode()) > self.response_bytes
        uncertain = (envelope.get('error') or {}).get('code') == 'OUTCOME_UNKNOWN'
        if unsafe or oversized:
            response = self._json(unknown_response(envelope,
                'The operation completed but its sensitive or oversized response cannot be durably replayed'))
        with self._transaction() as db:
            changed = db.execute('UPDATE requests SET state = ?, response = ?, updated = ? '
                'WHERE key_hash = ? AND fingerprint = ?',
                ('unknown' if unsafe or oversized or uncertain else 'completed', response,
                 time.time(), key_hash, fingerprint)).rowcount
            if changed != 1:
                raise OSError('Operation journal reservation was lost')
            self._prune_requests(db)

    def append_event(self, event):
        # Sequence and event payload commit together; sqlite_sequence preserves
        # the high-water mark after bounded event retention deletes old rows.
        payload = {key: value for key, value in event.items() if key != 'sequence'}
        with self._transaction() as db:
            sequence = db.execute('INSERT INTO events(payload) VALUES (?)', (self._json(payload),)).lastrowid
            db.execute('DELETE FROM events WHERE sequence <= ?', (sequence - self.event_limit,))
        return {**copy.deepcopy(payload), 'sequence': sequence}

    def save_task(self, task_id, trace):
        with self._transaction() as db:
            db.execute('INSERT OR REPLACE INTO tasks VALUES (?, ?, ?)',
                       (task_id, self._json(trace), time.time()))
            db.execute('DELETE FROM tasks WHERE task_id IN '
                       '(SELECT task_id FROM tasks ORDER BY updated DESC, rowid DESC LIMIT -1 OFFSET ?)',
                       (self.task_limit,))

    def snapshot(self):
        with self._transaction() as db:
            events = [{**json.loads(payload), 'sequence': sequence}
                      for sequence, payload in db.execute('SELECT sequence, payload FROM events ORDER BY sequence')]
            cursor = db.execute("SELECT seq FROM sqlite_sequence WHERE name = 'events'").fetchone()
            tasks = {task_id: json.loads(payload) for task_id, payload in db.execute('SELECT task_id, payload FROM tasks')}
            return {'events': events, 'cursor': cursor[0] if cursor else 0, 'tasks': tasks}
