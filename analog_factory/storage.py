"""Small atomic JSON and path utilities shared by the workspace."""
import json
import os
import tempfile
from pathlib import Path


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.write-')
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, indent=2, allow_nan=False)
            output.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def confined(root: Path, relative: str, *, hidden=False) -> Path:
    if not isinstance(relative, str) or not relative or '\x00' in relative:
        raise ValueError('A relative file path is required')
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or (not hidden and any(p.startswith('.') for p in path.parts)):
        raise ValueError('Path must remain inside the project; internal files are protected')
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('Symbolic links are not accepted as project paths')
    if not current.resolve().is_relative_to(root.resolve()):
        raise ValueError('Path escapes the project')
    return current
