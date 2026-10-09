"""Reversible project/file lifecycle and bounded, atomic portable restoration.

Project trash is a durable tombstone: the original directory, source revisions,
and evidence remain intact. File trash holds content and history together. ZIPs
are fully validated in a private staging directory before one atomic publish.
"""
import hashlib
import io
import json
import os
import re
import shutil
import stat
import struct
import tempfile
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .storage import atomic_json, confined, read_json


BACKUP_MANIFEST = 'af-backup.json'
BACKUP_FORMAT = 'analog-factory-project-backup'
ACTIVE = {'queued', 'running', 'waiting_approval', 'cancelling'}
SECRET_KEYS = {'apikey', 'accesstoken', 'refreshtoken', 'password', 'authorization',
               'clientsecret', 'sessiontoken', 'privatekey', 'secret', 'token'}
SECRET_NAMES = {'credentials.json', 'secrets.json', 'id_rsa', 'id_ed25519',
                'id_ecdsa', 'id_dsa', 'settings.json'}
INTERNAL_CATEGORIES = {'runs', 'jobs', 'episodes', 'history', 'trash',
                       'agent-sessions', 'conversations', 'training', 'training-tasksets'}
DEVICE_NAME = re.compile(r'^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', re.I)


@dataclass(frozen=True)
class ArchiveLimits:
    # Base64 plus its operation envelope fits the service's 4 MiB request limit.
    compressed_bytes: int = 2 * 1024 * 1024
    expanded_bytes: int = 64 * 1024 * 1024
    file_bytes: int = 16 * 1024 * 1024
    entries: int = 4096
    ratio: int = 200


TRANSFER_LIMITS = ArchiveLimits(compressed_bytes=1024*1024*1024,
    expanded_bytes=2*1024*1024*1024, file_bytes=512*1024*1024, entries=16384)
CHUNK_BYTES = 256*1024
JSON_BYTES = 16*1024*1024


def _now():
    return datetime.now(timezone.utc).isoformat()


def _digest(data):
    if isinstance(data, Path):
        with data.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    return hashlib.sha256(data).hexdigest()


def _name(value):
    if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 100
            or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in value)):
        raise ValueError('Project name must contain 1–100 characters without controls')
    return value.strip()


def _visible_path(relative):
    if (not isinstance(relative, str) or not relative or len(relative) > 4096
            or '\\' in relative or ':' in relative
            or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in relative)):
        raise ValueError('Use a canonical visible project-relative file path')
    parts = relative.split('/')
    if len(parts) > 32 or any(not p or p.startswith('.') for p in parts):
        raise ValueError('Hidden paths, path aliases and traversal are not allowed')
    return relative


def _json(data):
    if isinstance(data, Path):
        if data.stat().st_size > JSON_BYTES:
            raise ValueError('Archive metadata exceeds the 16 MiB JSON limit')
        data = data.read_bytes()
    def constant(value):
        raise ValueError('Nonfinite JSON values are not supported')
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('Duplicate JSON keys are not supported')
            result[key] = value
        return result
    try:
        return json.loads(data, parse_constant=constant, object_pairs_hook=pairs)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('Archive JSON must be valid bounded UTF-8') from error


def _has_secrets(value, depth=0):
    if depth > 64:
        raise ValueError('Archive JSON nesting exceeds the supported limit')
    if isinstance(value, dict):
        return any(re.sub('[^a-z]', '', key.lower()) in SECRET_KEYS or _has_secrets(child, depth + 1)
                   for key, child in value.items())
    return isinstance(value, list) and any(_has_secrets(child, depth + 1) for child in value)


def _archive_name(name, *, backup):
    """Validate canonical portable names before touching the filesystem."""
    stripped = name[:-1] if name.endswith('/') else name
    parts = stripped.split('/')
    if (not stripped or len(stripped) > 4096 or len(parts) > 32 or '\\' in stripped or ':' in stripped
            or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in stripped)
            or any(not part or part in ('.', '..') or part.endswith(('.', ' ')) or DEVICE_NAME.match(part) for part in parts)
            or any(part.casefold() in SECRET_NAMES or part.lower().endswith(('.pem', '.key')) for part in parts)):
        raise ValueError('Archive contains an unsafe, secret or noncanonical path')
    hidden = [index for index, part in enumerate(parts) if part.startswith('.')]
    if hidden:
        if not backup or parts[0] != '.af' or len(parts) < 2 or parts[1] not in INTERNAL_CATEGORIES:
            raise ValueError('Archive contains protected hidden records')
        # Episode source snapshots may include their own run evidence, never
        # arbitrary hidden directories or worker credentials.
        if any(index != 0 and not (parts[1] == 'episodes' and index == 4
                                    and parts[index] == '.af' and len(parts) > 5
                                    and re.fullmatch('[a-f0-9]{32}', parts[2])
                                    and re.fullmatch('[0-9]+', parts[3])
                                    and parts[5] == 'runs') for index in hidden):
            raise ValueError('Archive contains protected hidden records')
    return stripped


def _record_kind(name):
    if re.fullmatch(r'\.af/(?:episodes/[a-f0-9]{32}/[0-9]+/\.af/)?runs/[a-f0-9]{32}/manifest.json', name):
        return 'run'
    match = re.fullmatch(r'\.af/(jobs|agent-sessions|conversations)/[a-f0-9]{32}\.json', name)
    return {'jobs': 'job', 'agent-sessions': 'agent', 'conversations': 'conversation'}.get(match[1]) if match else None


def _restored_record(record, kind):
    if record.get('status') in ACTIVE or kind == 'run' and record.get('status') == 'interrupted':
        record.update(status='failed' if kind == 'run' else 'interrupted',
                      error='Restored from backup; execution was not resumed')
    for field in ('owner_pid', 'owner_identity', 'pid', 'process_identity',
                  'backend_session_id', 'pending_approval'):
        if field in record:
            record[field] = None
    if kind == 'job' and record.get('kind') in ('training', 'training_evaluation', 'training_replay'):
        record['imported_evidence'] = True
        record['owner_identity'] = None
        record['owner_pid'] = None
    if kind == 'run':
        # ZIP checksums prove archive integrity, never provenance or authority to
        # read local dependencies, signal processes, or replay archived commands.
        # Preserve originals as inert evidence; normal operations only use the
        # trusted fields below. This applies to nested episode runs too.
        if record.get('imported_evidence') is not True or not isinstance(record.get('imported_provenance'), dict):
            record['imported_provenance'] = {field: record.get(field) for field in
                ('external_dependencies', 'command', 'command_template', 'environment', 'verification', 'provenance')}
        record['imported_evidence'] = True
        record['external_dependencies'] = {}
        record['command'] = []
        record['command_template'] = []
        record['environment'] = {}
        record['stale'] = True
        record['verification'] = {'status': 'unknown', 'reports': [],
                                  'reason': 'Imported backup evidence has not been verified in this workspace'}
        record['provenance'] = {'complete': False, 'coverage': 'Imported backup evidence',
                                'limitations': ['External dependencies, archived commands and verification claims are untrusted; start a fresh registered run.']}
    if kind == 'agent':
        for turn in record.get('turns', []):
            if isinstance(turn, dict) and turn.get('status') in ACTIVE:
                turn.update(status='interrupted', error='Restored from backup')
    return record


class ProjectLifecycle:
    def __init__(self, store, workspace=None, *, limits=None):
        self.store = store
        self.workspace = workspace
        self.limits = limits or ArchiveLimits()

    def _project(self, pid, *, allow_trashed=False):
        project = self.store.get(pid)
        if project.get('trashed_at') and not allow_trashed:
            raise ValueError('Project is in trash; restore it before changing its contents')
        return project

    def _idle(self, pid):
        if not self.workspace:
            return
        workspace = self.workspace
        root = self.store.path(pid)
        terminals = getattr(workspace, 'terminals', None)
        if terminals:
            with terminals.lock:
                if any(key[0] == pid for key in terminals.active):
                    raise RuntimeError('Project has an active native terminal; stop it before changing project files')
        # A cancelled worker can still be draining; its live ownership wins over
        # a terminal status in the durable record.
        with workspace.agents.lock:
            if any(key[0] == pid for key in workspace.agents.active):
                raise RuntimeError('Project has an active agent turn; wait for it to stop')
        with workspace.jobs.lock:
            if any(Path(key[0]).is_relative_to(root) for key in workspace.jobs.active):
                raise RuntimeError('Project has an active job; wait for it to stop')
        from .execution import _ACTIVE, _LOCK
        with _LOCK:
            if any(Path(key[0]).is_relative_to(root) for key in _ACTIVE):
                raise RuntimeError('Project has an active run; wait for it to stop')
        records = [*workspace.runs(pid), *workspace.jobs.list(root), *workspace.agents.list(pid)]
        if any(record.get('status') in ACTIVE for record in records):
            raise RuntimeError('Project has active work; wait for it to stop')

    def _touch(self, pid):
        project = self.store.get(pid)
        project.update(lifecycle_revision=uuid.uuid4().hex, updated_at=_now())
        atomic_json(self.store.path(pid) / 'project.json', project)

    def rename_project(self, pid, name):
        name = _name(name)
        with self.store.lock:
            project = self._project(pid)
            project.update(name=name, updated_at=_now())
            atomic_json(self.store.path(pid) / 'project.json', project)
            return project

    def trash_project(self, pid):
        with self.store.lock:
            project = self._project(pid)
            self._idle(pid)
            project.update(trashed_at=_now(), updated_at=_now())
            atomic_json(self.store.path(pid) / 'project.json', project)
            return project

    def trashed_projects(self):
        return [p for p in self.store.list(include_trashed=True) if p.get('trashed_at')]

    def restore_project(self, pid):
        with self.store.lock:
            project = self._project(pid, allow_trashed=True)
            if not project.get('trashed_at'):
                raise ValueError('Project is not in trash')
            self._idle(pid)
            project.pop('trashed_at')
            project['updated_at'] = _now()
            atomic_json(self.store.path(pid) / 'project.json', project)
            return project

    def _file(self, pid, relative, *, existing=True):
        _visible_path(relative)
        if relative in ('project.json', BACKUP_MANIFEST):
            raise ValueError('Project metadata and backup manifests are protected')
        self._project(pid)
        target = confined(self.store.path(pid), relative)
        if existing and not target.is_file():
            raise FileNotFoundError('Project file does not exist')
        return target

    def _history(self, pid, relative):
        return confined(self.store.path(pid), '.af/history/' + _digest(relative.encode()), hidden=True)

    def _copy_history(self, source, target, relative):
        """Copy validated revisions; return created paths for rollback."""
        created = []
        try:
            for metadata_path in sorted(source.glob('*.json')):
                metadata_path = confined(source, metadata_path.name, hidden=True)
                record = read_json(metadata_path)
                revision = record.get('id')
                if not isinstance(revision, str) or not re.fullmatch('[a-f0-9]{32}', revision):
                    raise ValueError('Invalid revision record')
                content = confined(source, revision + '.txt', hidden=True).read_bytes()
                if record.get('sha256') != _digest(content):
                    raise ValueError('Revision checksum does not match its content')
                target.mkdir(parents=True, exist_ok=True)
                for name in (revision + '.txt', revision + '.json'):
                    if confined(target, name, hidden=True).exists():
                        raise FileExistsError('Revision identifier conflicts with existing history')
                text_path = target / (revision + '.txt')
                with text_path.open('xb') as stream:
                    stream.write(content)
                created.append(text_path)
                record['path'] = relative
                record_path = target / (revision + '.json')
                atomic_json(record_path, record)
                created.append(record_path)
            return created
        except Exception:
            for path in created:
                path.unlink(missing_ok=True)
            raise

    def move_file(self, pid, source, destination):
        with self.store.lock:
            original = self._file(pid, source)
            target = self._file(pid, destination, existing=False)
            self._idle(pid)
            if target.exists():
                raise FileExistsError('Destination already exists; choose a different path')
            old_history, new_history = self._history(pid, source), self._history(pid, destination)
            target.parent.mkdir(parents=True, exist_ok=True)
            created = self._copy_history(old_history, new_history, destination)
            try:
                os.link(original, target)  # Fails atomically if destination appeared.
            except Exception:
                for path in created:
                    path.unlink(missing_ok=True)
                raise
            original.unlink()
            if old_history.exists():
                shutil.rmtree(old_history)
            self._touch(pid)
            return {'path': destination, 'previous_path': source, 'sha256': _digest(target.read_bytes())}

    def _trash_root(self, pid):
        return confined(self.store.path(pid), '.af/trash/files', hidden=True)

    def trash_file(self, pid, relative):
        with self.store.lock:
            source = self._file(pid, relative)
            self._idle(pid)
            directory = self._trash_root(pid) / uuid.uuid4().hex
            directory.mkdir(parents=True)
            record = {'id': directory.name, 'path': relative, 'trashed_at': _now(),
                      'sha256': _digest(source.read_bytes()), 'size': source.stat().st_size}
            history = self._history(pid, relative)
            try:
                self._copy_history(history, directory / 'history', relative)
                os.link(source, directory / 'content')
                atomic_json(directory / 'record.json', record)
            except Exception:
                shutil.rmtree(directory)
                raise
            source.unlink()
            if history.exists():
                shutil.rmtree(history)
            self._touch(pid)
            return record

    def trashed_files(self, pid):
        self._project(pid, allow_trashed=True)
        root = self._trash_root(pid)
        records = []
        for directory in sorted(root.glob('*')):
            if re.fullmatch('[a-f0-9]{32}', directory.name):
                record = read_json(confined(root, directory.name + '/record.json', hidden=True))
                if record:
                    records.append(record)
        return records

    def restore_file(self, pid, trash_id, destination=None):
        if not isinstance(trash_id, str) or not re.fullmatch('[a-f0-9]{32}', trash_id):
            raise ValueError('Invalid file trash identifier')
        with self.store.lock:
            directory = confined(self._trash_root(pid), trash_id, hidden=True)
            record = read_json(confined(directory, 'record.json', hidden=True))
            if not record:
                raise FileNotFoundError('File trash record does not exist')
            destination = destination or record['path']
            target = self._file(pid, destination, existing=False)
            self._idle(pid)
            if target.exists():
                raise FileExistsError('Destination already exists; restore to a different path')
            content = confined(directory, 'content', hidden=True)
            if _digest(content.read_bytes()) != record['sha256']:
                raise ValueError('Trashed file checksum does not match its content')
            target.parent.mkdir(parents=True, exist_ok=True)
            created = self._copy_history(confined(directory, 'history', hidden=True),
                                         self._history(pid, destination), destination)
            try:
                os.link(content, target)
            except Exception:
                for path in created:
                    path.unlink(missing_ok=True)
                raise
            shutil.rmtree(directory)
            self._touch(pid)
            return {'path': destination, 'sha256': record['sha256']}

    def _entries(self, blob, staging=None):
        size = blob.stat().st_size if isinstance(blob, Path) and blob.is_file() and not blob.is_symlink() else len(blob) if isinstance(blob, bytes) else -1
        if size < 0 or size > self.limits.compressed_bytes:
            raise ValueError('ZIP exceeds the compressed archive limit')
        # ZipFile eagerly loads its central directory. Bound that allocation
        # before opening even a hostile large upload (ZIP64 is unnecessary under
        # our <4 GiB archive and <65,535-entry limits).
        if isinstance(blob, Path):
            with blob.open('rb') as stream:
                stream.seek(max(0,size-65557));tail=stream.read(65557)
        else:
            tail=blob[-65557:]
        ending=tail.rfind(b'PK\x05\x06')
        if ending<0 or len(tail)-ending<22:
            raise ValueError('A valid ZIP archive is required')
        signature,disk,start_disk,disk_count,count,central_size,central_offset,comment_size=struct.unpack('<4s4H2IH',tail[ending:ending+22])
        if (disk or start_disk or count!=disk_count or count>self.limits.entries or central_size>16*1024*1024
                or central_offset+central_size>size or ending+22+comment_size!=len(tail)):
            raise ValueError('ZIP central directory exceeds supported bounds or uses unsupported split/ZIP64 records')
        try:
            archive = zipfile.ZipFile(blob if isinstance(blob, Path) else io.BytesIO(blob))
        except (zipfile.BadZipFile, OSError) as error:
            raise ValueError('A valid ZIP archive is required') from error
        with archive:
            infos = archive.infolist()
            if len(infos) > self.limits.entries:
                raise ValueError('ZIP has too many entries')
            backup = any(info.filename == BACKUP_MANIFEST for info in infos)
            seen, files, total, data = {}, set(), 0, {}
            for info in infos:
                if info.orig_filename != info.filename:
                    raise ValueError('ZIP contains an ambiguous filename')
                name = _archive_name(info.filename, backup=backup)
                folded = unicodedata.normalize('NFC', name).casefold()
                if folded in seen:
                    raise ValueError('ZIP contains duplicate or case-conflicting paths')
                seen[folded] = info.is_dir()
                if not info.is_dir():
                    files.add(folded)
                mode = stat.S_IFMT(info.external_attr >> 16)
                if (mode not in (0, stat.S_IFREG, stat.S_IFDIR)
                        or mode == stat.S_IFDIR and not info.is_dir()
                        or mode == stat.S_IFREG and info.is_dir() or info.flag_bits & 1
                        or info.is_dir() and info.file_size):
                    raise ValueError('ZIP symlinks, special files and encryption are not supported')
                total += info.file_size
                if (info.file_size > self.limits.file_bytes or total > self.limits.expanded_bytes
                        or info.file_size > max(info.compress_size, 1) * self.limits.ratio):
                    raise ValueError('ZIP exceeds decompression limits')
            for path in seen:
                if any(parent.as_posix() in files for parent in PurePosixPath(path).parents if str(parent) != '.'):
                    raise ValueError('ZIP file and directory paths conflict')
            for info in infos:
                if info.is_dir():
                    continue
                try:
                    with archive.open(info) as stream:
                        if staging is None:
                            content = stream.read(min(info.file_size, self.limits.file_bytes) + 1)
                            written = len(content)
                        else:
                            content = confined(staging, info.filename, hidden=backup)
                            content.parent.mkdir(parents=True, exist_ok=True)
                            written = 0
                            with content.open('xb') as output:
                                while chunk := stream.read(CHUNK_BYTES):
                                    written += len(chunk)
                                    if written > info.file_size or written > self.limits.file_bytes:
                                        raise ValueError('ZIP expanded size exceeds its declaration')
                                    output.write(chunk)
                    if written != info.file_size:
                        raise ValueError('ZIP expanded size does not match its declaration')
                    data[info.filename] = content
                except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as error:
                    raise ValueError('ZIP contents failed integrity validation') from error
            if not data:
                raise ValueError('ZIP contains no project files')
            return data, backup

    def _validate_backup(self, entries):
        manifest = _json(entries[BACKUP_MANIFEST])
        if (not isinstance(manifest, dict) or manifest.get('format') != BACKUP_FORMAT
                or manifest.get('schema_version') != 1 or not isinstance(manifest.get('files'), dict)):
            raise ValueError('Unsupported backup manifest')
        expected = {name: _digest(content) for name, content in entries.items() if name != BACKUP_MANIFEST}
        if manifest['files'] != expected:
            raise ValueError('Backup manifest checksums do not match archive contents')
        for name, content in entries.items():
            if name.startswith('.af/') and name.endswith('.json'):
                record = _json(content)
                # Tokenizer vocabularies/configs contain technical "token" keys;
                # they are inert training artifacts, not credential records.
                model_data = bool(re.match(r'^\.af/training/[a-f0-9]{32}/(?:checkpoint|optimizer)/', name))
                if not model_data and _has_secrets(record):
                    raise ValueError('Backup contains credential fields')
            kind = _record_kind(name)
            if kind:
                record = _json(content)
                record_id = name.split('/')[-2] if kind == 'run' else PurePosixPath(name).stem
                if (not isinstance(record, dict) or record.get('id') != record_id
                        or kind != 'conversation' and not isinstance(record.get('status'), str)):
                    raise ValueError('Invalid backup execution or conversation record')
                if kind in ('run', 'job') and (not isinstance(record.get('kind'), str)
                                               or not isinstance(record.get('created_at'), str)):
                    raise ValueError('Invalid backup execution metadata')
                if kind == 'run' and record['status'] not in ACTIVE | {'succeeded', 'failed', 'cancelled', 'timed_out', 'unavailable', 'interrupted'}:
                    raise ValueError('Unknown backup run status')
                if kind == 'agent' and (not isinstance(record.get('turns'), list)
                                        or any(not isinstance(turn, dict) for turn in record['turns'])
                                        or not all(isinstance(record.get(key), str) for key in
                                                   ('backend', 'title', 'created_at', 'updated_at'))):
                    raise ValueError('Invalid backup conversation metadata')
            for category in ('jobs', 'agent-sessions', 'conversations'):
                if name.startswith('.af/' + category + '/') and not kind:
                    raise ValueError('Invalid backup record path')
            if name.startswith('.af/runs/') and not re.match(r'^\.af/runs/[a-f0-9]{32}/', name):
                raise ValueError('Invalid backup run path')
            if name.startswith('.af/episodes/') and not re.match(r'^\.af/episodes/[a-f0-9]{32}/', name):
                raise ValueError('Invalid backup episode path')
            if name.startswith('.af/training-tasksets/') and not re.fullmatch(r'\.af/training-tasksets/taskset-[a-f0-9]{24}\.json', name):
                raise ValueError('Invalid training taskset backup path')
            if name.startswith('.af/training/') and not re.fullmatch(
                    r'\.af/training/[a-f0-9]{32}/(?:request\.json|progress\.json|result\.json|training-result\.json|worker\.log|'
                    r'(?:episodes|trajectories)/[0-9]+\.json|sources/[A-Za-z0-9_-]+\.py|'
                    r'checkpoint/[A-Za-z0-9_.-]+|optimizer/checkpoint-[0-9]+/[A-Za-z0-9_.-]+)', name):
                raise ValueError('Invalid training evidence backup path')
            match = re.fullmatch(r'\.af/history/([a-f0-9]{64})/([a-f0-9]{32})\.(json|txt)', name)
            if name.startswith('.af/history/'):
                if not match:
                    raise ValueError('Invalid backup revision path')
                if match[3] == 'json':
                    record = _json(content)
                    if (not isinstance(record, dict) or record.get('id') != match[2]
                            or not isinstance(record.get('path'), str) or not isinstance(record.get('time'), str)):
                        raise ValueError('Invalid backup revision metadata')
                    _visible_path(record['path'])
                    if _digest(record['path'].encode()) != match[1]:
                        raise ValueError('Backup revision belongs to a different path')
                    text = entries.get(name[:-5] + '.txt')
                    if text is None or _digest(text) != record.get('sha256'):
                        raise ValueError('Backup revision checksum is invalid')
                elif name[:-4] + '.json' not in entries:
                    raise ValueError('Backup revision metadata is missing')
            if name.startswith('.af/trash/'):
                if not re.fullmatch(r'\.af/trash/files/[a-f0-9]{32}/(?:content|record.json|history/[a-f0-9]{32}\.(?:json|txt))', name):
                    raise ValueError('Invalid file trash backup path')
                if name.endswith('/record.json'):
                    record = _json(content)
                    if not isinstance(record, dict) or record.get('id') != name.split('/')[3]:
                        raise ValueError('Invalid file trash metadata')
                    _visible_path(record.get('path'))
                    value = entries.get(name[:-11] + 'content')
                    if value is None or _digest(value) != record.get('sha256'):
                        raise ValueError('Invalid file trash checksum')
                prefix = '/'.join(name.split('/')[:4]) + '/'
                if prefix + 'record.json' not in entries or prefix + 'content' not in entries:
                    raise ValueError('Backup trash record or content is missing')
                if '/history/' in name:
                    if name.endswith('.txt') and name[:-4] + '.json' not in entries:
                        raise ValueError('Backup trash revision metadata is missing')
                    if name.endswith('.json'):
                        record = _json(content)
                        trash = _json(entries[prefix + 'record.json'])
                        if (not isinstance(record, dict) or record.get('id') != PurePosixPath(name).stem
                                or record.get('path') != trash.get('path') or not isinstance(record.get('time'), str)
                                or name[:-5] + '.txt' not in entries
                                or record.get('sha256') != _digest(entries[name[:-5] + '.txt'])):
                            raise ValueError('Backup trash revision is invalid')

    def import_archive(self, blob, name=None, *, configure_allowed=True):
        # Extraction and sanitization share one undiscoverable staging directory.
        # Even large ZIPs never materialize their contents in an in-memory map.
        with self.store.lock, tempfile.TemporaryDirectory(prefix='.import-', dir=self.store.root) as temporary:
            return self._import_staged(blob, Path(temporary), name, configure_allowed)

    def _import_staged(self, blob, staging, name, configure_allowed):
        entries, backup = self._entries(blob, staging)
        if backup:
            self._validate_backup(entries)
        metadata = _json(entries.get('project.json', b'{}'))
        if (not isinstance(metadata, dict) or not isinstance(metadata.get('config', {}), dict)
                or _has_secrets(metadata)):
            raise ValueError('Project metadata must be an object without credentials')
        if 'name' in metadata:
            _name(metadata['name'])
        config = metadata.get('config', {})
        if config and not configure_allowed:
            raise PermissionError('Importing project configuration overrides requires configure permission')
        from .operation_catalog import DESIGN_PATH_ROLES, PROJECT_CONFIG, PROJECT_DESIGN
        from .operation_schema import validate
        config = validate(config, PROJECT_CONFIG, 'project.config')
        project = {'schema_version': 1, 'id': uuid.uuid4().hex,
                   'name': _name(name if name is not None else metadata.get('name', 'Imported project')),
                   'template': metadata.get('template', 'imported'), 'created_at': _now(),
                   'config': config,
                   'imported_from': metadata.get('id'), 'imported_at': _now()}
        if not isinstance(project['template'], str) or len(project['template']) > 100:
            raise ValueError('Invalid imported template label')
        if 'design' in metadata:
            design = validate(metadata['design'], PROJECT_DESIGN, 'project.design')
            for role in DESIGN_PATH_ROLES:
                if role not in design:
                    continue
                relative = _visible_path(design[role])
                target = confined(staging, relative)
                if relative in ('project.json', BACKUP_MANIFEST) or target.exists() and not target.is_file():
                    raise ValueError('Project design roles must identify visible source or derived files')
            # These labels restore source selection, never qualification or
            # authority from the original machine. Derived files may be absent.
            project['design'] = design
        if self.workspace:
            self.workspace.validate_config(project['config'])
        for relative, content in entries.items():
            kind = _record_kind(relative) if backup else None
            if kind:
                atomic_json(content, _restored_record(_json(content), kind))
            if re.fullmatch(r'\.af/training/[a-f0-9]{32}/request.json', relative):
                record = _json(content)
                # Archived worker requests are evidence, never local launch
                # instructions. Remove original machine path authority entirely.
                for field in ('workspace', 'output', 'runtime', 'worker', 'project_root', 'base_model', 'checkpoint'):
                    record.pop(field, None)
                record['imported_evidence'] = True
                atomic_json(content, record)
        (staging / BACKUP_MANIFEST).unlink(missing_ok=True)
        atomic_json(staging / 'project.json', project)
        destination = self.store.root / project['id']
        if destination.exists():
            raise FileExistsError('Imported project identifier conflicts with an existing project')
        staging.rename(destination)
        return project

    def backup_to(self, pid, destination, *, mode='backup'):
        """Write a portable ZIP with bounded buffers; caller owns its private file."""
        if mode not in ('backup', 'export'):
            raise ValueError('Archive mode must be backup or export')
        with self.store.lock, tempfile.TemporaryDirectory(prefix='.archive-', dir=self.store.root) as temporary:
            self._project(pid, allow_trashed=True)
            self._idle(pid)
            root, entries, total, names = self.store.path(pid), {}, 0, set()
            for path in sorted(root.rglob('*')):
                if not path.is_file() or path.is_symlink():
                    continue
                relative = path.relative_to(root).as_posix()
                if relative.startswith('.af/') and (relative.endswith('.cancel') or relative.endswith('/cancel.requested')):
                    continue
                if relative == BACKUP_MANIFEST:
                    raise ValueError('Project contains a reserved backup manifest path')
                try:
                    _archive_name(relative, backup=True)
                    source = confined(root, relative, hidden=True)
                except ValueError:
                    continue
                size = source.stat().st_size
                if size > self.limits.file_bytes:
                    raise ValueError('Project file exceeds the bounded archive member limit')
                archive_name = relative
                if mode == 'export' and relative.startswith('.af/'):
                    category = relative.split('/')[1]
                    if category not in ('runs', 'jobs', 'episodes', 'training', 'training-tasksets'):
                        continue
                    archive_name = 'evidence/' + relative[4:]
                folded = unicodedata.normalize('NFC', archive_name).casefold()
                if folded in names or any(parent.as_posix() in names for parent in PurePosixPath(folded).parents if str(parent) != '.'):
                    raise ValueError('Project contains conflicting portable archive paths')
                if any(existing.startswith(folded + '/') for existing in names):
                    raise ValueError('Project contains conflicting portable archive paths')
                names.add(folded)
                if relative.endswith('.json') and (relative == 'project.json' or relative.startswith('.af/')):
                    record = _json(source)
                    model_data = bool(re.match(r'^\.af/training/[a-f0-9]{32}/(?:checkpoint|optimizer)/', relative))
                    if not model_data and _has_secrets(record):
                        raise ValueError('Project backup contains credential fields; remove them before exporting')
                    if _record_kind(relative) == 'agent':
                        record['backend_session_id'] = None
                        record['pending_approval'] = None
                        source = Path(temporary) / uuid.uuid4().hex
                        atomic_json(source, record)
                        size = source.stat().st_size
                entries[archive_name] = source
                total += size
                if len(entries) + 1 > self.limits.entries or total > self.limits.expanded_bytes:
                    raise ValueError('Project exceeds bounded archive limits')
            if mode == 'backup':
                manifest = {'format': BACKUP_FORMAT, 'schema_version': 1, 'created_at': _now(),
                            'files': {name: _digest(content) for name, content in entries.items()}}
                manifest_path = Path(temporary) / 'manifest.json'
                atomic_json(manifest_path, manifest)
                entries[BACKUP_MANIFEST] = manifest_path
                if total + manifest_path.stat().st_size > self.limits.expanded_bytes:
                    raise ValueError('Project and manifest exceed expanded archive limit')
                self._validate_backup(entries)
            # Stored members avoid creating a ZIP bomb ratio from a sparse tensor
            # or repeated waveform; transport remains bounded by the ZIP quota.
            try:
                with zipfile.ZipFile(destination, 'w', zipfile.ZIP_STORED, allowZip64=True) as archive:
                    for relative, source in entries.items():
                        with source.open('rb') as stream, archive.open(relative, 'w', force_zip64=True) as output:
                            copied, actual = 0, hashlib.sha256()
                            while chunk := stream.read(CHUNK_BYTES):
                                copied += len(chunk)
                                if copied > self.limits.file_bytes:
                                    raise ValueError('Project file grew beyond the archive limit')
                                output.write(chunk)
                                actual.update(chunk)
                                if Path(destination).stat().st_size > self.limits.compressed_bytes:
                                    raise ValueError('ZIP exceeds the archive limit; reduce project evidence before exporting')
                            if mode == 'backup' and relative != BACKUP_MANIFEST and actual.hexdigest() != manifest['files'][relative]:
                                raise ValueError('Project file changed while preparing backup; retry with stable sources')
                if Path(destination).stat().st_size > self.limits.compressed_bytes:
                    raise ValueError('ZIP exceeds the archive limit')
            except BaseException:
                Path(destination).unlink(missing_ok=True)
                raise
            return Path(destination)

    def backup(self, pid):
        # Legacy base64 callers remain small and bounded. Larger callers use
        # project.archive.prepare plus transfer.read / guarded HTTP streaming.
        with tempfile.TemporaryDirectory(prefix='.legacy-backup-', dir=self.store.root) as temporary:
            output = Path(temporary) / 'backup.zip'
            try:
                self.backup_to(pid, output)
            except ValueError as error:
                if 'limit' in str(error):
                    raise ValueError(str(error) + '; use project.archive.prepare for large archives') from error
                raise
            return output.read_bytes()
