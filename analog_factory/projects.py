"""Project files, atomic revisions, and portable archives."""
import hashlib
import io
import json
import re
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import templates
from .storage import atomic_json, confined, read_json

MAX_FILE_BYTES = 2 * 1024 * 1024


class ProjectStore:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()
        self.root = self.workspace / 'projects'
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def path(self, project_id: str) -> Path:
        if not re.fullmatch(r'[a-f0-9]{32}', str(project_id)):
            raise ValueError('Invalid project identifier')
        path = confined(self.root, project_id)
        if not (path / 'project.json').is_file():
            raise FileNotFoundError('Project does not exist')
        return path

    def create(self, name: str, template: str = 'amplifier') -> dict:
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError('Project name must contain 1–100 characters')
        contents = templates.files(template)
        project = {'schema_version': 1, 'id': uuid.uuid4().hex, 'name': name.strip(),
                   'template': template, 'created_at': datetime.now(timezone.utc).isoformat(), 'config': {}}
        with self.lock:
            path = self.root / project['id']
            path.mkdir()
            atomic_json(path / 'project.json', project)
            for relative, content in contents.items():
                target = confined(path, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
        return project

    def list(self) -> list:
        result = []
        for path in sorted(self.root.iterdir()):
            if not path.is_symlink() and path.is_dir() and re.fullmatch(r'[a-f0-9]{32}', path.name):
                try:
                    result.append(self.get(path.name))
                except (ValueError, OSError):
                    continue
        return result

    def get(self, project_id: str) -> dict:
        return read_json(self.path(project_id) / 'project.json')

    def configure(self, project_id: str, config: dict) -> dict:
        with self.lock:
            project = self.get(project_id)
            project['config'] = config
            atomic_json(self.path(project_id) / 'project.json', project)
        return project

    def files(self, project_id: str) -> list:
        root = self.path(project_id)
        result = []
        for path in sorted(root.rglob('*')):
            relative = path.relative_to(root).as_posix()
            if any(part.startswith('.') for part in path.relative_to(root).parts):
                continue
            try:
                target = confined(root, relative)
                if target.is_file():
                    result.append({'path': relative, 'size': target.stat().st_size})
            except ValueError:
                continue
        return result

    def read(self, project_id: str, relative: str) -> dict:
        path = confined(self.path(project_id), relative)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError('File exceeds the 2 MiB editor limit')
        return {'path': relative, 'content': path.read_text()}

    def write(self, project_id: str, relative: str, content: str) -> dict:
        if not isinstance(content, str) or len(content.encode()) > MAX_FILE_BYTES:
            raise ValueError('Text content must fit the 2 MiB editor limit')
        if Path(relative).as_posix() == 'project.json':
            raise ValueError('Use project settings to edit project configuration')
        with self.lock:
            root = self.path(project_id)
            target = confined(root, relative)
            if target.exists():
                old = target.read_bytes()
                revision_id = uuid.uuid4().hex
                history = root / '.af' / 'history' / hashlib.sha256(relative.encode()).hexdigest()
                history.mkdir(parents=True, exist_ok=True)
                (history / f'{revision_id}.txt').write_bytes(old)
                atomic_json(history / f'{revision_id}.json', {'id': revision_id, 'path': relative,
                    'sha256': hashlib.sha256(old).hexdigest(), 'time': datetime.now(timezone.utc).isoformat()})
            target.parent.mkdir(parents=True, exist_ok=True)
            import os
            import tempfile
            fd, temporary = tempfile.mkstemp(dir=target.parent, prefix='.edit-')
            try:
                with os.fdopen(fd, 'w') as output:
                    output.write(content)
                os.replace(temporary, target)
            finally:
                if Path(temporary).exists():
                    Path(temporary).unlink()
        return {'path': relative, 'sha256': hashlib.sha256(content.encode()).hexdigest()}

    def history(self, project_id: str, relative: str) -> list:
        root = self.path(project_id)
        confined(root, relative)
        directory = root / '.af/history' / hashlib.sha256(relative.encode()).hexdigest()
        return sorted([read_json(p) for p in directory.glob('*.json')], key=lambda r: r['time'], reverse=True)

    def create_file_if_missing(self, project_id: str, relative: str, content: str) -> bool:
        """Publish a complete new editor file without replacing an existing drawing."""
        import os
        import tempfile
        with self.lock:
            root = self.path(project_id)
            target = confined(root, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=target.parent, prefix='.edit-')
            try:
                with os.fdopen(fd, 'w') as stream:
                    stream.write(content)
                try:
                    os.link(temporary, target)
                    return True
                except FileExistsError:
                    if not confined(root, relative).is_file():
                        raise ValueError('Editor source must be a regular project file')
                    return False
            finally:
                Path(temporary).unlink(missing_ok=True)

    def restore(self, project_id: str, relative: str, revision: str) -> dict:
        available = {item['id'] for item in self.history(project_id, relative)}
        if revision not in available:
            raise ValueError('Revision does not belong to this file')
        path = self.path(project_id) / '.af/history' / hashlib.sha256(relative.encode()).hexdigest() / f'{revision}.txt'
        return self.write(project_id, relative, path.read_text())

    def export(self, project_id: str) -> bytes:
        root = self.path(project_id)
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for item in self.files(project_id):
                archive.write(confined(root, item['path']), item['path'])
            # Evidence is included, but revision history and credentials are not.
            for category in ('runs', 'jobs', 'episodes'):
                evidence = confined(root, '.af/' + category, hidden=True)
                for path in sorted(evidence.rglob('*')):
                    if path.is_file() and not path.is_symlink():
                        safe = confined(evidence, path.relative_to(evidence).as_posix(), hidden=True)
                        archive.write(safe, f'evidence/{category}/' + path.relative_to(evidence).as_posix())
        return output.getvalue()
