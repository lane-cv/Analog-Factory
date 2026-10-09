"""Bounded, complete source-change evidence shared by approved writers.

Preparing a change never grants permission to apply it. The registered
``file.write`` operation remains the only writer used by these consumers.
"""
import difflib
import hashlib
import json
import os
import re
import stat

from .project_lifecycle import SECRET_NAMES, _has_secrets
from .operations import OperationError
from .storage import confined

MAX_SOURCE_BYTES = 64000
MAX_PROPOSAL_BYTES = 256 * 1024


def _regular_identity(target):
    info = target.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Project text reads require a regular file')
    return tuple(getattr(info, key) for key in ('st_dev', 'st_ino', 'st_mtime_ns', 'st_ctime_ns', 'st_size'))


def read_consistent(workspace, project_id, path):
    """Bind the existing editor read to its checked source version.

This preserves ProjectStore.read's path/text/2 MiB policy, including project.json.
The writer lock coordinates registered writers; checks detect native writes
during this read, not arbitrary writes after its last consistency check.
"""
    with workspace.projects.lock:
        root = workspace.projects.path(project_id)
        identity = _regular_identity(confined(root, path))
        version = workspace.operations.version(project_id)
        try:
            record = workspace.projects.read(project_id, path)
            checked_version = workspace.operations.version(project_id)
            checked_identity = _regular_identity(confined(root, path))
        except OSError:
            raise OperationError('VERSION_CONFLICT', 'Source changed during the read; read the latest source again') from None
        if checked_version != version or checked_identity != identity:
            raise OperationError('VERSION_CONFLICT', 'Source changed during the read; read the latest source again')
        return {**record, 'source_version': version}


def complete_diff(current, candidate, path):
    lines = difflib.unified_diff(current.splitlines(True), candidate.splitlines(True), fromfile=path, tofile=path)
    return ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n' for line in lines)


def source_path(workspace, project_id, relative):
    if (not isinstance(relative, str) or not relative or len(relative) > 500
            or '\\' in relative or ':' in relative
            or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in relative)
            or any(not p or p in ('.', '..') or p.startswith('.') or p.endswith((' ', '.'))
                   or p.casefold() in SECRET_NAMES or p.lower().endswith(('.pem', '.key'))
                   for p in relative.split('/'))
            or relative == 'project.json'):
        raise ValueError('Source path must be canonical, visible and cannot name credentials or configuration')
    return confined(workspace.projects.path(project_id), relative)


def _text(value, path):
    if not isinstance(value, str) or len(value.encode('utf-8')) > MAX_SOURCE_BYTES:
        raise ValueError('Source preview text must fit the 64000 UTF-8 byte limit')
    if any(ord(c) < 32 and c not in '\n\r\t' for c in value):
        raise ValueError('Source preview requires text without binary control characters')
    if path.lower().endswith('.json'):
        try:
            record = json.loads(value)
        except (ValueError, RecursionError):
            return
        if _has_secrets(record):
            raise ValueError('Source preview cannot expose or propose credential fields')


def _read(target, relative):
    descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SOURCE_BYTES:
            raise ValueError('Source preview requires an existing regular file of at most 64000 bytes')
        data = stream.read(MAX_SOURCE_BYTES + 1)
        identity = tuple(getattr(info, key) for key in ('st_dev', 'st_ino', 'st_mtime_ns', 'st_ctime_ns', 'st_size'))
        if identity != tuple(getattr(os.fstat(stream.fileno()), key) for key in
                             ('st_dev', 'st_ino', 'st_mtime_ns', 'st_ctime_ns', 'st_size')):
            raise ValueError('Source changed while preparing its preview')
    current = data.decode('utf-8')
    _text(current, relative)
    return current, identity


def prepare_change(workspace, project_id, path, content, expected_sha256=None):
    """Return exact public proposal and private identity under the writer lock."""
    with workspace.projects.lock:
        target = source_path(workspace, project_id, path)
        _text(content, path)
        current, identity = _read(target, path)
        digest = hashlib.sha256(current.encode()).hexdigest()
        if expected_sha256 is not None and (not isinstance(expected_sha256, str)
                or not re.fullmatch('[a-f0-9]{64}', expected_sha256) or expected_sha256 != digest):
            raise ValueError('Source hash changed; read the current source before proposing its replacement')
        proposal = {'path': path, 'current_content': current, 'candidate_content': content,
                    'current_sha256': digest, 'candidate_sha256': hashlib.sha256(content.encode()).hexdigest(),
                    'diff': complete_diff(current, content, path),
                    'project_version': workspace.operations.version(project_id)}
        checked_content, checked_identity = _read(source_path(workspace, project_id, path), path)
        if checked_content != current or checked_identity != identity:
            raise ValueError('Source changed while preparing its preview; read it again')
        proposal['proposal_sha256'] = hashlib.sha256(json.dumps(proposal, ensure_ascii=False,
            sort_keys=True, allow_nan=False, separators=(',', ':')).encode()).hexdigest()
        if proposal_bytes(proposal) > MAX_PROPOSAL_BYTES:
            raise ValueError('Complete source proposal exceeds the 256 KiB limit; propose a smaller edit')
        return proposal, identity


def proposal_bytes(proposal):
    return len(json.dumps(proposal, ensure_ascii=False, allow_nan=False).encode())


def verify_change(workspace, project_id, proposal, identity):
    """Revalidate a frozen preview; caller holds the lock through file.write."""
    target = source_path(workspace, project_id, proposal['path'])
    current, actual_identity = _read(target, proposal['path'])
    if current != proposal['current_content'] or actual_identity != identity:
        raise ValueError('Source changed while review was pending; request a new review')
    if workspace.operations.version(project_id) != proposal['project_version']:
        raise ValueError('Project version changed while review was pending; request a new review')
