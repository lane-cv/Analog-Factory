"""Human-created automatic-action grants, kept outside importable project data."""
import copy
import os
import re
import uuid
from pathlib import PurePosixPath

from .storage import atomic_json, confined, read_json


OPERATIONS = frozenset({'file.write', 'run.start'})


def validate_scope(scope):
    if not isinstance(scope, dict) or set(scope) != {'operations', 'paths', 'max_actions', 'max_runs'}:
        raise ValueError('Automatic scope requires operations, paths, max_actions and max_runs')
    if not isinstance(scope['operations'], list) or not scope['operations'] or any(value not in OPERATIONS for value in scope['operations']):
        raise ValueError('Automatic operations may include only file.write and run.start')
    paths = scope['paths']
    if not isinstance(paths, list) or not 1 <= len(paths) <= 20:
        raise ValueError('Automatic scope requires 1–20 visible file paths or directory prefixes')
    for path in paths:
        if (not isinstance(path, str) or not 1 <= len(path) <= 500 or path.startswith('/') or '\\' in path
                or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in path)
                or path == 'project.json' or any(part in ('', '.', '..') or part.startswith('.') for part in path.rstrip('/').split('/'))):
            raise ValueError('Automatic scope paths must be explicit visible project paths')
    if type(scope['max_actions']) is not int or not 1 <= scope['max_actions'] <= 100:
        raise ValueError('Automatic action limit must be 1–100')
    if type(scope['max_runs']) is not int or not 0 <= scope['max_runs'] <= 20:
        raise ValueError('Automatic run limit must be 0–20')
    return copy.deepcopy(scope)


def _directory(workspace):
    path = workspace.root / '.agent-scopes'
    if path.is_symlink():
        raise PermissionError('Automatic scope storage must not be a symbolic link')
    path.mkdir(mode=0o700, exist_ok=True)
    path.chmod(0o700)
    return path


def _save(path, record):
    atomic_json(path, record)
    # An action is charged before execution. Do not restore a spent budget after
    # a crash between an EDA/file effect and the normal session record update.
    with path.open('rb') as stream:
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def grant(workspace, pid, sid, scope):
    scope = validate_scope(scope)
    root = workspace.projects.path(pid)
    for path in scope['paths']:
        confined(root, path.rstrip('/'))
    directory = _directory(workspace)
    if sum(1 for _ in directory.glob('*.json')) >= 10000:
        raise ValueError('Automatic scope grant limit reached')
    identifier = uuid.uuid4().hex
    _save(directory / (identifier + '.json'), {'project_id': pid, 'session_id': sid,
          'scope': scope, 'actions': 0, 'runs': 0})
    return identifier


def current(workspace, pid, record):
    identifier = record.get('scope_grant')
    if not isinstance(identifier, str) or not re.fullmatch('[a-f0-9]{32}', identifier):
        raise PermissionError('Automatic scope has no local human grant')
    path = _directory(workspace) / (identifier + '.json')
    if path.is_symlink():
        raise PermissionError('Automatic scope grant must not be a symbolic link')
    approved = read_json(path)
    if (not isinstance(approved, dict) or approved.get('project_id') != pid
            or approved.get('session_id') != record['id'] or approved.get('scope') != record.get('scope')):
        raise PermissionError('Automatic scope does not match its local human grant')
    return path, approved


def consume(workspace, pid, record, tool, arguments):
    """Return true only after durably charging one precisely matching action."""
    path, approved = current(workspace, pid, record)
    if tool == 'write_file':
        operation, params = 'file.write', arguments
    elif tool == 'run_simulation':
        operation, params = 'run.start', {'kind': 'simulate', 'options': {'netlist': arguments.get('netlist')}}
    elif tool == 'invoke_operation':
        operation, params = arguments.get('operation_id'), arguments.get('parameters', {})
    else:
        return False
    scope = approved['scope']
    if operation not in scope['operations'] or params.get('project_id', pid) != pid:
        return False
    if operation == 'file.write':
        files = [params.get('path')]
    else:
        # Native GUI/editor processes, retries, nested jobs and metadata injection
        # are outside an automatic grant. They still use the human approval path.
        kind = params.get('kind', 'simulate')
        if kind not in ('simulate', 'drc', 'lvs', 'extract') or params.get('metadata'):
            return False
        options = params.get('options') or {}
        defaults = {'simulate': {'netlist': 'testbenches/pre.cir'},
                    'drc': {'layout': 'layout/amplifier.mag'}, 'extract': {'layout': 'layout/amplifier.mag'},
                    'lvs': {'layout_netlist': 'netlists/extracted.spice', 'schematic_netlist': 'netlists/schematic.spice'}}
        # Match EDA preparation: explicit options override project template
        # defaults, which override the original educational amplifier paths.
        design = workspace.projects.get(pid).get('design', {})
        if not isinstance(design, dict):
            raise ValueError('Project design defaults must be an object')
        files = [options.get(key, design.get(key, value)) for key, value in defaults[kind].items()]
        files.extend(value for key, value in options.items() if key in
                     ('netlist', 'layout', 'schematic', 'layout_netlist', 'schematic_netlist'))
    for filename in files:
        if not isinstance(filename, str):
            return False
        confined(workspace.projects.path(pid), filename)
        normalized = PurePosixPath(filename).as_posix()
        if normalized == 'project.json' or not any(normalized == prefix or prefix.endswith('/') and normalized.startswith(prefix)
                                                   for prefix in scope['paths']):
            return False
    is_run = operation == 'run.start'
    if approved['actions'] >= scope['max_actions'] or is_run and approved['runs'] >= scope['max_runs']:
        return False
    approved['actions'] += 1
    approved['runs'] += int(is_run)
    _save(path, approved)
    return True
