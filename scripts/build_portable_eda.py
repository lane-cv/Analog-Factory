#!/usr/bin/env python3
"""Build pinned Magic and Xschem with system development libraries, without root.

The prefix is specific to the machine where it is built. Run this script on each
target machine; it does not bundle system libraries or install system packages.
Interrupted or invalid installations are preserved for inspection and rejected.
Choose a fresh prefix after correcting a failed build.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
SOURCES_FILE = ROOT / 'deployment/ubuntu24/sources.json'
DEFAULT_PREFIX = ROOT / '.tools/eda/portable'
DEFAULT_ARCHIVES_DIR = ROOT / '.tools/deployment/downloads'
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 50_000
DOWNLOAD_TIMEOUT = 30
DOWNLOAD_DEADLINE = 300
PROBE_TIMEOUT = 20
BUILD_TIMEOUT = 3600
SOURCE_REPOSITORIES = {'magic': 'RTimothyEdwards/magic', 'xschem': 'StefanSchippers/xschem'}
BUILD_ENVIRONMENT = ('CC', 'CXX', 'CPP', 'CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS',
                     'LIBRARY_PATH', 'LD_LIBRARY_PATH', 'PKG_CONFIG_PATH', 'MAKEFLAGS')


class BuildError(RuntimeError):
    """A source, build, probe, or existing installation could not be verified."""


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_sources():
    try:
        sources = json.loads(SOURCES_FILE.read_text())
    except (OSError, ValueError) as exc:
        raise BuildError(f'Cannot read source pins: {SOURCES_FILE}: {exc}') from exc
    if not isinstance(sources, dict) or set(sources) != set(SOURCE_REPOSITORIES):
        raise BuildError('Source pins must contain exactly magic and xschem')
    for name, repository in SOURCE_REPOSITORIES.items():
        pin = sources[name]
        if not isinstance(pin, dict) or set(pin) != {'version', 'commit', 'sha256', 'url'}:
            raise BuildError(f'Invalid source metadata for {name}')
        for field, pattern in [('version', r'\d+\.\d+\.\d+'),
                               ('commit', r'[0-9a-f]{40}'), ('sha256', r'[0-9a-f]{64}')]:
            if not isinstance(pin[field], str) or not re.fullmatch(pattern, pin[field]):
                raise BuildError(f'Invalid source {field} for {name}')
        expected = f'https://codeload.github.com/{repository}/tar.gz/{pin["commit"]}'
        if pin['url'] != expected:
            raise BuildError(f'Unexpected source URL for {name}: expected {expected}')
    return sources


class _PinnedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if (target.scheme != 'https' or target.netloc != 'codeload.github.com'
                or target.username or target.password):
            raise BuildError(f'Refusing source download redirect to {newurl}')
        return super().redirect_request(request, response, code, message, headers, newurl)


def download_archive(metadata, destination):
    """Download through default TLS/proxy settings; publish only verified bytes."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{destination.name}-', suffix='.part',
                                             dir=destination.parent)
    temporary = Path(temporary)
    try:
        started = time.monotonic()
        # build_opener retains urllib's default environment proxy and TLS handlers.
        opener = urllib.request.build_opener(_PinnedRedirectHandler())
        with os.fdopen(descriptor, 'wb') as output:
            with opener.open(metadata['url'], timeout=DOWNLOAD_TIMEOUT) as response:
                length = response.headers.get('Content-Length')
                expected_size = int(length) if length is not None else None
                if expected_size is not None and not 0 <= expected_size <= MAX_ARCHIVE_BYTES:
                    raise BuildError(f'Source archive exceeds size limit: {destination.name}')
                size = 0
                checksum = hashlib.sha256()
                while True:
                    if time.monotonic() - started > DOWNLOAD_DEADLINE:
                        raise BuildError(f'Source download exceeded time limit: {destination.name}')
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_ARCHIVE_BYTES:
                        raise BuildError(f'Source archive exceeds size limit: {destination.name}')
                    checksum.update(chunk)
                    output.write(chunk)
                if expected_size is not None and size != expected_size:
                    raise BuildError(f'Incomplete source download: {destination.name}')
                if checksum.hexdigest() != metadata['sha256']:
                    raise BuildError(f'Source SHA256 checksum mismatch: {destination.name}')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise BuildError(f'Cannot download {destination.name}: {exc}') from exc
    finally:
        temporary.unlink(missing_ok=True)


def verify_archive(archive, metadata):
    if archive.is_symlink() or not archive.is_file():
        raise BuildError(f'Missing or unsafe source archive: {archive}')
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise BuildError(f'Source archive exceeds size limit: {archive}')
    if digest(archive) != metadata['sha256']:
        raise BuildError(f'Source SHA256 checksum mismatch: {archive}')


def extract_source(archive, destination, name, metadata):
    """Reject unexpected roots, traversal, devices, and links outside the root."""
    expected_root = f'{name}-{metadata["commit"]}'
    try:
        with tarfile.open(archive, 'r:gz') as contents:
            members = []
            names = set()
            size = 0
            for member in contents:
                path = PurePosixPath(member.name)
                if (path.is_absolute() or '..' in path.parts or '\\' in member.name
                        or not path.parts or path.parts[0] != expected_root
                        or str(path) in names):
                    raise BuildError(f'Unsafe or unexpected source archive member: {member.name}')
                names.add(str(path))
                if member.issym():
                    target = PurePosixPath(posixpath.normpath(str(path.parent / member.linkname)))
                    if (PurePosixPath(member.linkname).is_absolute() or not target.parts
                            or target.parts[0] != expected_root or '\\' in member.linkname):
                        raise BuildError(f'Unsafe source archive link: {member.name}')
                elif not (member.isfile() or member.isdir()):
                    raise BuildError(f'Unsupported source archive member: {member.name}')
                size += member.size
                members.append(member)
                if size > MAX_EXPANDED_BYTES or len(members) > MAX_ARCHIVE_MEMBERS:
                    raise BuildError(f'Source archive exceeds extraction limits: {archive}')
            contents.extractall(destination, members=members, filter='data')
    except (OSError, tarfile.TarError, ValueError) as exc:
        raise BuildError(f'Cannot extract source archive {archive}: {exc}') from exc
    source = destination / expected_root
    configure = source / 'configure'
    if configure.is_symlink() or not configure.is_file() or not os.access(configure, os.X_OK):
        raise BuildError(f'Missing executable configure in source archive: {archive}')
    return source


def probe(executable, name, expected_version=None):
    executable = Path(executable).absolute()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise BuildError(f'Missing executable for {name}: {executable}')
    command = [str(executable)]
    if name == 'xschem':
        # Xschem --version still runs Tcl_AppInit; -i/--no_rcload disables
        # system, working-directory, and user xschemrc initialization.
        command.append('--no_rcload')
    command.append('--version')
    try:
        with tempfile.TemporaryDirectory(prefix='portable-eda-probe-') as temporary:
            environment = {**os.environ, 'HOME': temporary, 'PWD': temporary}
            result = subprocess.run(command, cwd=temporary, env=environment,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, timeout=PROBE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise BuildError(f'{name} version probe failed: {exc}') from exc
    if result.returncode:
        raise BuildError(f'{name} version probe exited {result.returncode}: {result.stdout.strip()}')
    output = result.stdout.strip()
    if name == 'magic':
        pattern = r'^(\d+\.\d+\.\d+)$'
    elif name == 'xschem':
        pattern = r'^XSCHEM V(\d+\.\d+\.\d+)(?:\s|$)'
    else:
        pattern = r'\bngspice[- ](\d+(?:\.\d+)*)(?=[\s:]|$)'
    match = re.search(pattern, output, re.MULTILINE | re.IGNORECASE)
    if not match or (expected_version is not None and match.group(1) != expected_version):
        raise BuildError(f'{name} version mismatch: expected {expected_version or "ngspice version"}, '
                         f'received {output!r}')
    return {'path': str(executable), 'version': match.group(1),
            # Startup diagnostics can include random temporary paths or appear
            # only on first use. Retain the recognized version text for reuse.
            'version_output': match.group(0).strip(), 'sha256': digest(executable)}


def installed_files(prefix):
    """Hash installed resources as well as launchers and their native payloads."""
    records = {}
    for directory, dirs, files in os.walk(prefix, followlinks=False):
        if Path(directory) == prefix:
            dirs[:] = [name for name in dirs if name != 'logs']
            files = [name for name in files if name != 'installation.json']
        for name in sorted(dirs + files):
            path = Path(directory) / name
            relative = path.relative_to(prefix).as_posix()
            if path.is_symlink():
                try:
                    target = path.resolve(strict=True)
                except (OSError, RuntimeError) as exc:
                    raise BuildError(f'Invalid installed symlink: {path}') from exc
                if not target.is_relative_to(prefix):
                    raise BuildError(f'Installed symlink escapes prefix: {path}')
                records[relative] = {'symlink': str(path.readlink())}
            elif path.is_file():
                records[relative] = {'sha256': digest(path), 'mode': stat.S_IMODE(path.stat().st_mode)}
            elif not path.is_dir():
                raise BuildError(f'Unexpected installed file: {path}')
    return records


def _reuse(prefix, inputs, native):
    marker = prefix / 'installation.json'
    try:
        if marker.is_symlink():
            raise BuildError(f'Invalid installation manifest symlink: {marker}')
        manifest = json.loads(marker.read_text())
    except (OSError, ValueError) as exc:
        raise BuildError(f'Existing prefix has no verifiable installation: {prefix}; '
                         'preserved unchanged, choose a fresh prefix') from exc
    if not isinstance(manifest, dict) or manifest.get('schema_version') != 1:
        raise BuildError(f'Invalid installation manifest: {marker}')
    if manifest.get('inputs') != inputs:
        raise BuildError('Existing installation inputs do not match; choose a fresh prefix')
    if manifest.get('files') != installed_files(prefix):
        raise BuildError('Existing installation file integrity changed; choose a fresh prefix')
    tools = {'ngspice': native}
    for name, pin in inputs['sources'].items():
        tools[name] = probe(prefix / 'bin' / name, name, pin['version'])
    tools['ngspice-sky130'] = probe(prefix / 'bin/ngspice-sky130', 'ngspice-sky130', native['version'])
    if manifest.get('tools') != tools:
        raise BuildError('Existing installation executable versions or hashes changed; choose a fresh prefix')
    return manifest


def _run_phase(name, phase, command, source, logs):
    log = logs / f'{name}-{phase}.log'
    try:
        with log.open('w') as output:
            output.write(json.dumps(command) + '\n')
            output.flush()
            result = subprocess.run(command, cwd=source, stdout=output, stderr=subprocess.STDOUT,
                                    timeout=BUILD_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BuildError(f'{name} {phase} failed: {exc}; see {log}') from exc
    if result.returncode:
        raise BuildError(f'{name} {phase} exited {result.returncode}; see {log}')


def build(prefix=DEFAULT_PREFIX, archives_dir=DEFAULT_ARCHIVES_DIR, skip_download=False,
          jobs=2, ngspice='ngspice'):
    """Return a verified installation manifest, or raise BuildError without reuse."""
    if type(jobs) is not int or not 1 <= jobs <= 64:
        raise BuildError('jobs must be an integer from 1 to 64')
    requested_prefix = Path(prefix).expanduser()
    if requested_prefix.is_symlink():
        raise BuildError(f'Installation prefix must not be a symlink: {requested_prefix}')
    prefix = requested_prefix.resolve()
    archives_dir = Path(archives_dir).expanduser().resolve()
    sources = load_sources()
    if prefix.exists() and (not prefix.is_dir() or
                            (any(prefix.iterdir()) and not (prefix / 'installation.json').is_file())):
        raise BuildError(f'Existing prefix has no verified installation: {prefix}; '
                         'preserved unchanged, choose a fresh prefix')
    native_path = shutil.which(os.fspath(ngspice))
    if native_path is None:
        raise BuildError(f'Missing ngspice executable: {ngspice}')
    native = probe(Path(native_path).resolve(), 'ngspice')
    configure = {}
    for name in sources:
        configure[name] = ['./configure', f'--prefix={prefix}']
        if name == 'magic':
            configure[name] += ['--without-opengl', '--disable-magic-builddate']
    inputs = {'prefix': str(prefix), 'sources': sources, 'ngspice': native['path'],
              'configure': configure, 'jobs': jobs,
              'environment': {name: os.environ[name] for name in BUILD_ENVIRONMENT if name in os.environ}}
    if (prefix / 'installation.json').exists():
        return _reuse(prefix, inputs, native)
    if shutil.which('make') is None:
        raise BuildError('Missing make executable; install system development dependencies first')
    for name, pin in sources.items():
        archive = archives_dir / f'{name}.tar.gz'
        if not archive.exists() and not archive.is_symlink():
            if skip_download:
                raise BuildError(f'Missing source archive for {name}: {archive}')
            download_archive(pin, archive)
        verify_archive(archive, pin)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.portable-eda-', dir=prefix.parent) as temporary:
        work = Path(temporary)
        source_dirs = {name: extract_source(archives_dir / f'{name}.tar.gz', work, name, pin)
                       for name, pin in sources.items()}
        prefix.mkdir(exist_ok=True)
        logs = prefix / 'logs'
        logs.mkdir()
        tools = {'ngspice': native}
        for name, pin in sources.items():
            for phase, command in [('configure', configure[name]),
                                   ('build', ['make', f'-j{jobs}']), ('install', ['make', 'install'])]:
                _run_phase(name, phase, command, source_dirs[name], logs)
            tools[name] = probe(prefix / 'bin' / name, name, pin['version'])
        wrapper = prefix / 'bin/ngspice-sky130'
        wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(native['path']) +
                           ' -D ngbehavior=hsa -D skywaterpdk -D ng_nomodcheck "$@"\n')
        wrapper.chmod(0o755)
        tools['ngspice-sky130'] = probe(wrapper, 'ngspice-sky130', native['version'])
        # Reprobe the native tool so a changed simulator cannot earn a manifest.
        if probe(Path(native['path']), 'ngspice') != native:
            raise BuildError('Native ngspice changed during the build')
        manifest = {'schema_version': 1, 'inputs': inputs, 'tools': tools,
                    'files': installed_files(prefix)}
        marker = work / 'installation.json'
        marker.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        os.replace(marker, prefix / 'installation.json')
    return manifest


def _jobs(value):
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('jobs must be an integer from 1 to 64') from exc
    if not 1 <= parsed <= 64:
        raise argparse.ArgumentTypeError('jobs must be an integer from 1 to 64')
    return parsed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, default=DEFAULT_PREFIX)
    parser.add_argument('--archives-dir', type=Path, default=DEFAULT_ARCHIVES_DIR)
    parser.add_argument('--skip-download', action='store_true', help='Use only verified cached source archives')
    parser.add_argument('--jobs', type=_jobs, default=2, help='Parallel make jobs, 1 to 64 (default: 2)')
    parser.add_argument('--ngspice', default='ngspice', help='Native ngspice executable (default: ngspice)')
    args = parser.parse_args(argv)
    try:
        manifest = build(**vars(args))
    except (BuildError, OSError) as exc:
        parser.exit(1, f'Portable EDA build failed: {exc}\n')
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
