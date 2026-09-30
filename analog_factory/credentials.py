"""Private workspace credentials, separate from settings and project exports."""
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

from .providers import validate_api_key
from .storage import confined


class CredentialStore:
    def __init__(self, root: Path):
        self.root = root

    def _path(self):
        return confined(self.root, '.credentials/provider.json', hidden=True)

    def load(self, binding):
        path = self._path()
        if not path.exists():
            return None
        if not path.is_file() or path.stat().st_size > 16384:
            raise ValueError('Invalid local provider credential file')
        try:
            record = json.loads(path.read_text())
            if (not isinstance(record, dict) or record.get('version') != 1
                    or not isinstance(record.get('binding'), list) or len(record['binding']) != 2
                    or not all(isinstance(value, str) for value in record['binding'])):
                raise ValueError
            validate_api_key(record.get('api_key'))
        except (ValueError, UnicodeError):
            raise ValueError('Invalid local provider credential file') from None
        path.parent.chmod(0o700)
        path.chmod(0o600)
        if tuple(record['binding']) != binding:
            return None
        return record['api_key'] or None

    def save(self, binding, secret):
        path = self._path()
        path.parent.mkdir(mode=0o700, exist_ok=True)
        path.parent.chmod(0o700)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.provider-')
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump({'version': 1, 'binding': list(binding), 'api_key': secret}, stream)
                stream.write('\n')
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def clear(self):
        self._path().unlink(missing_ok=True)

    @contextmanager
    def transaction(self):
        """Restore the previous file if the associated settings save fails."""
        path = self._path()
        backup = None
        if path.exists():
            backup = path.parent / ('.rollback-' + uuid.uuid4().hex)
            # Retain the previous inode: rollback needs no secret rewrite or free
            # data blocks when the settings save fails because storage is full.
            os.link(path, backup)
        try:
            yield
        except BaseException:
            if backup is not None:
                os.replace(backup, path)
            else:
                path.unlink(missing_ok=True)
            raise
        finally:
            if backup is not None:
                backup.unlink(missing_ok=True)
