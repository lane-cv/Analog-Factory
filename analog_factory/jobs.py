"""Persistent, cancellable orchestration jobs above individual EDA runs."""
import copy
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .execution import _process_identity
from .storage import atomic_json, confined, read_json


class _Cancellation:
    """Cooperative event readable by callbacks and other local processes."""

    def __init__(self, path):
        self.event = threading.Event()
        self.path = path

    def set(self):
        self.event.set()

    def is_set(self):
        if self.path.is_symlink():
            raise ValueError('Job cancellation files cannot be symbolic links')
        return self.event.is_set() or self.path.exists()


def _root(project):
    project = Path(project).resolve()
    if not project.is_dir():
        raise ValueError('Project directory does not exist')
    return confined(project, '.af/jobs', hidden=True)


class Jobs:
    def __init__(self):
        self.lock = threading.RLock()
        self.active = {}

    def _path(self, project, job_id):
        if not re.fullmatch(r'[a-f0-9]{32}', str(job_id)):
            raise ValueError('Invalid job identifier')
        return confined(_root(project), f'{job_id}.json')

    def _cancel_path(self, project, job_id):
        self._path(project, job_id)
        return confined(_root(project), f'{job_id}.cancel')

    def start(self, project, kind, function, initial=None):
        project = Path(project).resolve()
        job_id = uuid.uuid4().hex
        path = self._path(project, job_id)
        event = _Cancellation(self._cancel_path(project, job_id))
        record = {**copy.deepcopy(initial or {}), 'id': job_id, 'kind': kind, 'status': 'queued',
                  'iterations': copy.deepcopy((initial or {}).get('iterations', [])),
                  'created_at': datetime.now(timezone.utc).isoformat(), 'ended_at': None,
                  'owner_pid': os.getpid(), 'owner_identity': _process_identity(os.getpid())}
        def update(**values):
            with self.lock:
                for name in ('id', 'kind', 'owner_pid', 'owner_identity', 'created_at'):
                    values.pop(name, None)
                record.update(values)
                atomic_json(self._path(project, job_id), record)
        def execute():
            outcome = {}
            try:
                update(status='running')
                result = {} if event.is_set() else function(copy.deepcopy(record), event, update) or {}
                outcome = {**result, 'status': 'cancelled' if event.is_set() else result.get('status', 'succeeded')}
            except Exception as error:
                try:
                    cancelled = event.is_set()
                except ValueError:
                    cancelled = False
                outcome = {'status': 'cancelled' if cancelled else 'failed', 'error': str(error)}
            finally:
                with self.lock:
                    try:
                        update(**{**outcome, 'ended_at': datetime.now(timezone.utc).isoformat()})
                    finally:
                        self.active.pop((str(project), job_id), None)
        with self.lock:
            atomic_json(path, record)
            self.active[(str(project), job_id)] = event
            thread = threading.Thread(target=execute, name='af-job-' + job_id[:8], daemon=True)
            thread.start()
        return self.get(project, job_id)

    def get(self, project, job_id):
        project = Path(project).resolve()
        with self.lock:
            result = read_json(self._path(project, job_id))
            if result is None:
                raise FileNotFoundError('Job does not exist')
            if result['status'] in ('queued', 'running') and (str(project), job_id) not in self.active:
                owner = _process_identity(result.get('owner_pid'))
                if owner is None or owner != result.get('owner_identity'):
                    result.update(status='interrupted', error='Service stopped before the job completed',
                                  ended_at=datetime.now(timezone.utc).isoformat())
                    atomic_json(self._path(project, job_id), result)
            return result

    def cancel(self, project, job_id):
        project = Path(project).resolve()
        result = self.get(project, job_id)
        if result['status'] not in ('queued', 'running'):
            return result
        with self.lock:
            event = self.active.get((str(project), job_id))
            if event:
                event.set()
            else:
                path = self._cancel_path(project, job_id)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                os.close(fd)
        return self.get(project, job_id)

    def wait(self, project, job_id, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.get(project, job_id)
            if result['status'] not in ('queued', 'running'):
                return result
            time.sleep(.025)
        raise TimeoutError('Job is still running')

    def list(self, project):
        return [self.get(project, p.stem) for p in sorted(_root(project).glob('*.json'))]
