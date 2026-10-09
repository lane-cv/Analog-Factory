"""Asynchronous local processes with per-run snapshots and auditable manifests.

Commands are argv lists, never shell strings. ``{input}`` and ``{output}``
placeholders expand to the run directories. ``metadata.generated_files`` maps
relative input paths to generated text, including overrides for parameter tuning.
``metadata.input_aliases`` copies effective inputs to adapter-controlled filenames.
The runner records source and effective input hashes separately. Local tools are
trusted programs, not sandboxed: snapshots protect normal workflows from edits,
but this API does not sandbox arbitrary executables or executable design files.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import threading
import time
import uuid


TERMINAL = frozenset({'succeeded', 'failed', 'cancelled', 'timed_out', 'unavailable'})
SAFE_ENV = frozenset({'PATH', 'LANG', 'LC_ALL', 'PDK_ROOT', 'PDK', 'PDKPATH',
                     'SPICE_ASCIIRAWFILE', 'DISPLAY', 'WAYLAND_DISPLAY',
                     'XDG_RUNTIME_DIR', 'XAUTHORITY', 'HOME'})
_LOCK = threading.RLock()
_ACTIVE: dict[tuple[str, str], dict] = {}
_NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eEdD][-+]?\d+)?'


def _now():
    return datetime.now(timezone.utc).isoformat()


def _process_identity(pid):
    """Linux boot id and process birth tick protect against recycled PIDs."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        return boot + ':' + fields[19]
    except (OSError, IndexError):
        return None


def _relative(value):
    path = Path(value)
    if path.is_absolute() or not path.parts or any(p in {'.', '..'} or p.startswith('.') for p in path.parts):
        raise ValueError('Input path must be relative and cannot contain hidden or parent directories')
    return path


def _sources(project):
    """Read regular, visible files without following symlinks."""
    for root, dirs, files in os.walk(project, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and not (Path(root) / d).is_symlink())
        for name in sorted(files):
            path = Path(root) / name
            if name.startswith('.') or path.is_symlink():
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                continue
            with os.fdopen(fd, 'rb') as stream:
                if stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    yield path.relative_to(project).as_posix(), stream.read()


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _write(path, manifest):
    temporary = path.with_name(f'.manifest-{uuid.uuid4().hex}.tmp')
    temporary.write_text(json.dumps(manifest, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def _run_path(project, run_id=None):
    project = Path(project).resolve()
    if not project.is_dir():
        raise ValueError('Project directory does not exist')
    for relative in ['.af', '.af/runs']:
        if (project / relative).is_symlink():
            raise ValueError('Run storage cannot be a symbolic link')
    root = project / '.af/runs'
    if run_id is None:
        return root
    if not isinstance(run_id, str) or not re.fullmatch(r'[0-9a-f]{32}', run_id):
        raise ValueError('Invalid run id')
    path = root / run_id
    if path.is_symlink() or (path / 'manifest.json').is_symlink():
        raise ValueError('Run storage cannot contain symbolic links')
    return path


def _stop(process):
    """Terminate the entire process group, including children ignoring SIGTERM."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=0.25)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _results(directory, manifest):
    output = directory / 'output'
    logs = {}
    for name in ['process.log', 'ngspice.log', 'drc.txt', 'lvs.txt', 'extraction.txt']:
        path = output / name
        if path.is_file() and not path.is_symlink():
            logs[name] = path.read_text(errors='replace')
    definitions = {}
    for name, data in _sources(directory / 'input'):
        if Path(name).suffix.lower() not in {'.cir', '.spice', '.sp', '.ckt'}:
            continue
        for line, text in enumerate(data.decode(errors='replace').splitlines(), 1):
            match = re.match(r'\s*\.meas(?:ure)?\s+\S+\s+(\w+)\b', text, re.I)
            if match:
                definitions[match[1].lower()] = {'path': name, 'line': line, 'text': text}
    for name, content in logs.items():
        for match in re.finditer(r'^\s*([A-Za-z_]\w*)\s*=\s*(' + _NUMBER + r')(?:\s|$)', content, re.M):
            metric, raw = match.groups()
            value = float(raw.replace('D', 'e').replace('d', 'e'))
            if not math.isfinite(value) or metric.startswith('AF_'):
                continue
            manifest['metrics'][metric] = value
            manifest['metric_provenance'][metric] = {
                'source': name, 'definition': definitions.get(metric.lower()),
                'conditions': manifest['metadata'].get('conditions', {}),
                'run_id': manifest['id'],
            }
    errors = []
    if manifest['kind'] == 'simulate':
        errors = re.findall(r'^.*(?:\berror\b\s*[:!]|fatal error|simulation(?:s)? (?:aborted|interrupted)|timestep too small|no such vector|unknown subckt|no \.measure possible|\.meas\S*.*\bfailed\b).*$', '\n'.join(logs.values()), re.I | re.M)
        if errors and manifest['status'] == 'succeeded':
            manifest['status'] = 'failed'
            manifest['error'] = errors[0].strip()
    verification = {'status': 'unknown', 'reason': 'No recognized verification report', 'reports': []}
    if manifest['kind'] == 'drc':
        report = logs.get('drc.txt', '')
        count = re.search(r'^AF_DRC_COUNT=(\d+)\s*$', report, re.M)
        if count and re.search(r'^AF_DRC_COMPLETE=1\s*$', report, re.M) and manifest['status'] == 'succeeded':
            number = int(count[1])
            verification = {'status': 'pass' if number == 0 else 'fail', 'error_count': number,
                            'reports': ['drc.txt'], 'reason': 'Magic DRC completed; see rule coverage in the PDK'}
    elif manifest['kind'] == 'lvs':
        report = logs.get('lvs.txt', '')
        if re.search(r'(?:netlists|circuits) (?:do not match|mismatch)|property errors|failed (?:to match|pin matching|matching)|port errors|no (?:matching pin in|netlist match for) cell\b', report, re.I):
            verification = {'status': 'fail', 'reports': ['lvs.txt'], 'reason': 'Netgen reported a mismatch'}
        elif manifest['status'] == 'succeeded':
            final_match = re.search(r'^Final result:\s*(?:netlists|circuits) match uniquely\.\s*$', report, re.I | re.M)
            # Netgen 1.5.133 writes the final verdict without a prefix. Its
            # earlier topology matches are insufficient: require completed
            # pin matching in the final circuit, not a matched child, and
            # the final process-log completion as well. Its final topology
            # verdict does not include the result of `equate pins`.
            process_log = logs.get('process.log', '')
            final_circuit = re.split(r'^Subcircuit summary:[ \t]*$', report, flags=re.I | re.M)[-1]
            pin_sections = re.split(r'^Subcircuit pins:[ \t]*$', final_circuit, flags=re.I | re.M)
            final_pins = pin_sections[-1] if len(pin_sections) > 1 else ''
            legacy_match = (
                re.search(r'^Circuits match uniquely\.\s*\Z', report, re.I | re.M)
                and re.search(r'^Cell pin lists are equivalent\.\s*$', final_pins, re.I | re.M)
                and re.search(r'^Result:\s*Circuits match uniquely\.\s*$', process_log, re.I | re.M)
                and re.search(r'^LVS Done\.\s*$', process_log, re.I | re.M)
            )
            if final_match or legacy_match:
                verification = {'status': 'pass', 'reports': ['lvs.txt'], 'reason': 'Netgen reported unique matching; see report and setup coverage'}
    manifest['verification'] = verification
    manifest['artifact_hashes'] = {name: _hash(data) for name, data in _sources(output)}
    manifest['artifacts'] = [{'path': name, 'sha256': value} for name, value in manifest['artifact_hashes'].items()]


class RunManager:
    """Manage run records across callers in one server process.

    ``start`` snapshots before returning, then launches asynchronously. ``wait``
    returns a terminal record or raises TimeoutError without cancelling the run.
    Lost in-memory workers after restart become interrupted failures; such records
    never imply completion. ``retry`` uses a fresh snapshot and a new UUID.
    """

    def start(self, project: Path, kind: str, command: list[str], timeout: float = 60,
              metadata: dict | None = None, env: dict | None = None,
              parent_id: str | None = None) -> dict:
        if not isinstance(command, list) or (not command and not (metadata or {}).get('unavailable')) or any(not isinstance(arg, str) or '\0' in arg for arg in command):
            raise ValueError('Command must be a nonempty argv list')
        if not math.isfinite(float(timeout)) or timeout <= 0:
            raise ValueError('Timeout must be positive and finite')
        if command and not Path(command[0]).is_absolute() and not command[0].startswith('{input}'):
            _relative(command[0])
        metadata = json.loads(json.dumps(metadata or {}, allow_nan=False))
        generated = metadata.get('generated_files', {})
        if not isinstance(generated, dict):
            raise ValueError('generated_files must map relative paths to text')
        for name, content in generated.items():
            _relative(name)
            if not isinstance(content, str):
                raise ValueError('Generated input must be text')
        aliases = metadata.get('input_aliases', {})
        if not isinstance(aliases, dict):
            raise ValueError('input_aliases must map relative destination paths to source paths')
        for destination, source in aliases.items():
            _relative(destination)
            _relative(source)
        project = Path(project).resolve()
        run_id = uuid.uuid4().hex
        directory = _run_path(project, run_id)
        (directory / 'input').mkdir(parents=True)
        (directory / 'output').mkdir()
        source_hashes = {}
        for name, data in _sources(project):
            destination = directory / 'input' / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            destination.chmod(0o700 if (project / name).stat(follow_symlinks=False).st_mode & 0o111 else 0o600)
            source_hashes[name] = _hash(data)
        for name, content in generated.items():
            destination = directory / 'input' / _relative(name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content)
        for name, source in aliases.items():
            destination = directory / 'input' / _relative(name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((directory / 'input' / _relative(source)).read_bytes())
        resolved = [arg.replace('{input}', str(directory / 'input')).replace('{output}', str(directory / 'output')) for arg in command]
        environment = dict(os.environ)
        environment.update({str(key): str(value) for key, value in (env or {}).items()})
        dependencies = {'external_dependencies': {}, 'provenance': {
            'complete': False, 'coverage': 'Project snapshot only',
            'limitations': ['External dependencies of arbitrary commands are not interpreted']}}
        netlist = metadata.get('sources', {}).get('netlist')
        if kind == 'simulate' and netlist:
            from .eda import input_dependencies
            dependencies = input_dependencies(directory / 'input', netlist, environment)
        manifest = {
            'schema_version': 1, 'id': run_id, 'kind': kind, 'status': 'queued',
            'project': str(project), 'parent_id': parent_id, 'created_at': _now(),
            'started_at': None, 'ended_at': None, 'exit_code': None, 'error': None,
            'command': resolved, 'command_template': command, 'timeout': timeout,
            'environment': {key: value for key, value in environment.items() if key in SAFE_ENV},
            'source_hashes': source_hashes,
            'input_hashes': {name: _hash(data) for name, data in _sources(directory / 'input')},
            'artifact_hashes': {}, 'artifacts': [], 'metrics': {}, 'metric_provenance': {},
            'verification': {'status': 'unknown', 'reason': 'Run has not completed', 'reports': []},
            'metadata': metadata, 'tool_versions': metadata.get('tool_versions', {}),
            'pdk': metadata.get('pdk', {}), 'owner_pid': os.getpid(), 'pid': None,
            'owner_identity': _process_identity(os.getpid()), 'process_identity': None,
            **dependencies,
        }
        key = (str(project), run_id)
        state = {'cancel': threading.Event(), 'done': threading.Event(), 'process': None}
        with _LOCK:
            _write(directory / 'manifest.json', manifest)
            _ACTIVE[key] = state
            thread = threading.Thread(target=self._execute, args=(directory, manifest, state, environment, key), daemon=True)
            thread.start()
        return self.get(project, run_id)

    def _execute(self, directory, manifest, state, environment, key):
        path = directory / 'manifest.json'
        try:
            if manifest['metadata'].get('unavailable'):
                manifest.update(status='unavailable', error=str(manifest['metadata']['unavailable']))
            elif state['cancel'].is_set() or (directory / 'cancel.requested').exists():
                manifest.update(status='cancelled')
            else:
                with (directory / 'output/process.log').open('wb') as log:
                    process = subprocess.Popen(manifest['command'], cwd=directory / 'input', env=environment,
                                               stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                               start_new_session=True)
                    with _LOCK:
                        state['process'] = process
                        manifest.update(status='running', started_at=_now(), pid=process.pid,
                                        process_identity=_process_identity(process.pid))
                        _write(path, manifest)
                    deadline = time.monotonic() + manifest['timeout']
                    while True:
                        if state['cancel'].is_set() or (directory / 'cancel.requested').exists():
                            _stop(process)
                            manifest['status'] = 'cancelled'
                            break
                        if process.poll() is not None:
                            manifest['status'] = 'succeeded' if process.returncode == 0 else 'failed'
                            if process.returncode != 0:
                                manifest['error'] = f'Process exited with code {process.returncode}; see process.log'
                            break
                        if time.monotonic() >= deadline:
                            _stop(process)
                            manifest.update(status='timed_out', error=f'Timeout after {manifest["timeout"]} seconds')
                            break
                        state['cancel'].wait(0.025)
                    manifest['exit_code'] = process.returncode
        except FileNotFoundError as exc:
            manifest.update(status='unavailable', error=str(exc))
        except Exception as exc:
            if state['process'] is not None and state['process'].poll() is None:
                _stop(state['process'])
            manifest.update(status='failed', error=str(exc))
        finally:
            manifest['ended_at'] = _now()
            try:
                _results(directory, manifest)
            except Exception as exc:
                manifest.update(status='failed', error=f'Result collection failed: {exc}')
            with _LOCK:
                _write(path, manifest)
                state['done'].set()
                _ACTIVE.pop(key, None)

    def get(self, project: Path, run_id: str) -> dict:
        directory = _run_path(project, run_id)
        project = Path(project).resolve()
        with _LOCK:
            manifest = json.loads((directory / 'manifest.json').read_text())
            owner = _process_identity(manifest.get('owner_pid'))
            if (manifest['status'] not in TERMINAL and (str(project), run_id) not in _ACTIVE
                    and (owner is None or owner != manifest.get('owner_identity'))):
                # A server restart can leave its subprocess behind. Kill only
                # a positively identified process, never a recycled PID.
                identity = _process_identity(manifest.get('pid'))
                if identity is not None and identity == manifest.get('process_identity'):
                    try:
                        os.killpg(manifest['pid'], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                manifest.update(status='failed', error='Execution interrupted: worker unavailable after restart', ended_at=_now())
                _results(directory, manifest)
                _write(directory / 'manifest.json', manifest)
        current = {name: _hash(data) for name, data in _sources(project)}
        manifest['stale'] = bool(manifest.get('imported_evidence')) or current != manifest.get('source_hashes', manifest.get('input_hashes', {}))
        dependencies = {} if manifest.get('imported_evidence') else manifest.get('external_dependencies', {})
        for name, original_hash in dependencies.items():
            try:
                current_hash = _hash(Path(name).read_bytes())
            except OSError:
                current_hash = None
            if current_hash != original_hash:
                manifest['stale'] = True
        return manifest

    def list(self, project: Path) -> list[dict]:
        root = _run_path(project)
        if not root.exists():
            return []
        records = [self.get(project, path.name) for path in root.iterdir()
                   if re.fullmatch(r'[0-9a-f]{32}', path.name) and (path / 'manifest.json').is_file()]
        return sorted(records, key=lambda record: record['created_at'], reverse=True)

    def cancel(self, project: Path, run_id: str) -> dict:
        manifest = self.get(project, run_id)
        if manifest['status'] in TERMINAL:
            return manifest
        with _LOCK:
            state = _ACTIVE.get((str(Path(project).resolve()), run_id))
            if state:
                state['cancel'].set()
            else:
                (_run_path(project, run_id) / 'cancel.requested').touch()
        return self.get(project, run_id)

    def retry(self, project: Path, run_id: str) -> dict:
        manifest = self.get(project, run_id)
        if manifest.get('imported_evidence'):
            raise ValueError('Imported run evidence cannot replay archived commands; start a fresh registered run')
        if manifest['status'] not in TERMINAL:
            raise ValueError('Only a completed run can be retried')
        return self.start(project, manifest['kind'], manifest['command_template'], timeout=manifest['timeout'],
                          metadata=manifest['metadata'], env=manifest['environment'], parent_id=run_id)

    def wait(self, project: Path, run_id: str, timeout: float = 120) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            manifest = self.get(project, run_id)
            if manifest['status'] in TERMINAL:
                return manifest
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f'Run {run_id} is still active')
            with _LOCK:
                state = _ACTIVE.get((str(Path(project).resolve()), run_id))
            if state:
                state['done'].wait(min(remaining, 0.1))
            else:
                time.sleep(min(remaining, 0.05))
