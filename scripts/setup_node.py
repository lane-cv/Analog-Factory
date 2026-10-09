#!/usr/bin/env python3
"""Install a verified, pinned Linux x64 Node runtime locally without changing PATH.

SHA256 source: https://nodejs.org/download/release/v22.23.3/SHASUMS256.txt
The returned manifest identifies the local node executable and bin directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import re
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIRECTORY = ROOT / '.tools/node'
DEFAULT_ARCHIVES_DIR = ROOT / '.tools/deployment/downloads'
VERSION = '22.23.3'
ARCHIVE_ROOT = f'node-v{VERSION}-linux-x64'
ARCHIVE_NAME = f'{ARCHIVE_ROOT}.tar.xz'
SOURCE_URL = f'https://nodejs.org/download/release/v{VERSION}/{ARCHIVE_NAME}'
SOURCE_SHA256 = 'df450af89261115ef9f9e3830c3eeb2cc9213b63c720b1af623cb5dcbe2e02de'
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 50_000
DOWNLOAD_TIMEOUT = 30
DOWNLOAD_DEADLINE = 300
PROBE_TIMEOUT = 30


class SetupError(RuntimeError):
    """Node source, executable, or installation verification failed."""


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class _NodeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if target.scheme != 'https' or target.netloc != 'nodejs.org':
            raise SetupError(f'Refusing Node download redirect to {newurl}')
        return super().redirect_request(request, response, code, message, headers, newurl)


def download_archive(destination):
    """Use environment proxies and default TLS trust; publish only verified bytes."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{destination.name}-', suffix='.part',
                                             dir=destination.parent)
    temporary = Path(temporary)
    try:
        started = time.monotonic()
        opener = urllib.request.build_opener(_NodeRedirectHandler())
        with os.fdopen(descriptor, 'wb') as output:
            with opener.open(SOURCE_URL, timeout=DOWNLOAD_TIMEOUT) as response:
                header = response.headers.get('Content-Length')
                expected_size = int(header) if header is not None else None
                if expected_size is not None and not 0 <= expected_size <= MAX_ARCHIVE_BYTES:
                    raise SetupError('Node archive exceeds download size limit')
                size = 0
                checksum = hashlib.sha256()
                while True:
                    if time.monotonic() - started > DOWNLOAD_DEADLINE:
                        raise SetupError('Node download exceeded time limit')
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_ARCHIVE_BYTES:
                        raise SetupError('Node archive exceeds download size limit')
                    checksum.update(chunk)
                    output.write(chunk)
                if expected_size is not None and size != expected_size:
                    raise SetupError('Incomplete Node archive download')
                if checksum.hexdigest() != SOURCE_SHA256:
                    raise SetupError('Node archive SHA256 checksum mismatch')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise SetupError(f'Cannot download Node archive: {exc}') from exc
    finally:
        temporary.unlink(missing_ok=True)


def extract_archive(archive, work):
    try:
        with tarfile.open(archive, 'r:xz') as contents:
            members = []
            names = set()
            size = 0
            for member in contents:
                path = PurePosixPath(member.name)
                if (path.is_absolute() or '..' in path.parts or '\\' in member.name
                        or not path.parts or path.parts[0] != ARCHIVE_ROOT or str(path) in names):
                    raise SetupError(f'Unsafe or unexpected Node archive member: {member.name}')
                names.add(str(path))
                if member.issym() or member.islnk():
                    link = PurePosixPath(member.linkname)
                    target = path.parent / link if member.issym() else link
                    target = PurePosixPath(posixpath.normpath(str(target)))
                    if (link.is_absolute() or '\\' in member.linkname or not target.parts
                            or target.parts[0] != ARCHIVE_ROOT):
                        raise SetupError(f'Unsafe Node archive link: {member.name}')
                elif not (member.isfile() or member.isdir()):
                    raise SetupError(f'Unsupported Node archive member: {member.name}')
                size += member.size
                members.append(member)
                if size > MAX_EXPANDED_BYTES or len(members) > MAX_ARCHIVE_MEMBERS:
                    raise SetupError('Node archive exceeds extraction limits')
            contents.extractall(work, members=members, filter='data')
    except (OSError, ValueError, tarfile.TarError) as exc:
        raise SetupError(f'Cannot extract Node archive: {exc}') from exc
    source = work / ARCHIVE_ROOT
    if not source.is_dir() or source.is_symlink():
        raise SetupError('Missing expected Node archive directory')
    return source


def probe(directory):
    """npm's env-node shebang must select this installation's Node executable."""
    nodebin = directory / 'bin'
    environment = {**os.environ, 'PATH': str(nodebin) + os.pathsep + os.environ.get('PATH', os.defpath)}
    tools = {}
    for name in ('node', 'npm'):
        executable = nodebin / name
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise SetupError(f'Missing {name} executable: {executable}')
        if not executable.resolve().is_relative_to(directory):
            raise SetupError(f'{name} executable escapes installation directory')
        try:
            result = subprocess.run([str(executable), '--version'], env=environment,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    timeout=PROBE_TIMEOUT)
        except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
            raise SetupError(f'{name} version probe failed: {exc}') from exc
        output = result.stdout.strip()
        if result.returncode:
            raise SetupError(f'{name} version probe exited {result.returncode}: {output}')
        if name == 'node':
            valid = output == f'v{VERSION}'
            version = output.removeprefix('v')
        else:
            valid = bool(re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?', output))
            version = output
        if not valid:
            raise SetupError(f'Unexpected {name} version: {output!r}')
        tools[name] = {'path': str(executable), 'version': version,
                       'version_output': output, 'sha256': digest(executable)}
    return tools


def installed_files(directory):
    records = {}
    for current, dirs, files in os.walk(directory, followlinks=False):
        if Path(current) == directory:
            files = [name for name in files if name != 'installation.json']
        for name in sorted(dirs + files):
            path = Path(current) / name
            relative = path.relative_to(directory).as_posix()
            if path.is_symlink():
                try:
                    target = path.resolve(strict=True)
                except (OSError, RuntimeError) as exc:
                    raise SetupError(f'Invalid installed Node symlink: {path}') from exc
                if not target.is_relative_to(directory):
                    raise SetupError(f'Installed Node symlink escapes directory: {path}')
                records[relative] = {'symlink': str(path.readlink())}
            elif path.is_file():
                records[relative] = {'sha256': digest(path), 'mode': stat.S_IMODE(path.stat().st_mode)}
            elif not path.is_dir():
                raise SetupError(f'Unexpected installed Node file: {path}')
    return records


def _reuse(directory, inputs):
    marker = directory / 'installation.json'
    try:
        if marker.is_symlink():
            raise SetupError('Node installation manifest must not be a symlink')
        manifest = json.loads(marker.read_text())
    except (OSError, ValueError) as exc:
        raise SetupError(f'Unverifiable Node installation: {directory}') from exc
    if not isinstance(manifest, dict) or manifest.get('schema_version') != 1:
        raise SetupError('Invalid Node installation manifest')
    if manifest.get('inputs') != inputs:
        raise SetupError('Node installation inputs do not match; choose a fresh directory')
    if manifest.get('files') != installed_files(directory):
        raise SetupError('Node installation file integrity changed; choose a fresh directory')
    tools = probe(directory)
    if (manifest.get('tools') != tools or manifest.get('node') != tools['node']['path']
            or manifest.get('nodebin') != str(directory / 'bin') or manifest.get('version') != VERSION
            or manifest.get('npm_version') != tools['npm']['version']):
        raise SetupError('Node executable versions or hashes changed; choose a fresh directory')
    return manifest


def install(directory=DEFAULT_DIRECTORY, archives_dir=DEFAULT_ARCHIVES_DIR, skip_download=False):
    """Install locally or return an existing, freshly verified installation manifest."""
    if platform.system() != 'Linux' or platform.machine().lower() not in ('x86_64', 'amd64'):
        raise SetupError('This pinned Node package supports Linux x64 only')
    requested = Path(directory).expanduser()
    if requested.is_symlink():
        raise SetupError(f'Node installation directory must not be a symlink: {requested}')
    directory = requested.resolve()
    archives_dir = Path(archives_dir).expanduser().resolve()
    inputs = {'directory': str(directory), 'version': VERSION, 'platform': 'linux-x64',
              'source_url': SOURCE_URL, 'source_sha256': SOURCE_SHA256}
    if directory.exists():
        if not directory.is_dir():
            raise SetupError(f'Existing Node installation path is not a directory: {directory}')
        if (directory / 'installation.json').exists():
            return _reuse(directory, inputs)
        if any(directory.iterdir()):
            raise SetupError(f'Existing nonempty directory has no verified Node installation: {directory}; '
                             'preserved unchanged, choose a fresh directory')
    archive = archives_dir / ARCHIVE_NAME
    if not archive.exists() and not archive.is_symlink():
        if skip_download:
            raise SetupError(f'Missing Node archive: {archive}')
        download_archive(archive)
    if archive.is_symlink() or not archive.is_file():
        raise SetupError(f'Missing or unsafe Node archive: {archive}')
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise SetupError('Node archive exceeds size limit')
    if digest(archive) != SOURCE_SHA256:
        raise SetupError('Node archive SHA256 checksum mismatch')
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.node-install-', dir=directory.parent) as temporary:
        work = Path(temporary)
        source = extract_archive(archive, work)
        # Check all links/resources before executing the archive's node or npm.
        installed_files(source)
        probe(source)
        os.replace(source, directory)
        tools = probe(directory)
        manifest = {'schema_version': 1, 'inputs': inputs, 'node': tools['node']['path'],
                    'nodebin': str(directory / 'bin'), 'version': VERSION,
                    'npm_version': tools['npm']['version'], 'tools': tools,
                    'files': installed_files(directory)}
        marker = work / 'installation.json'
        marker.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        os.replace(marker, directory / 'installation.json')
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument('--archives-dir', type=Path, default=DEFAULT_ARCHIVES_DIR)
    parser.add_argument('--skip-download', action='store_true')
    args = parser.parse_args(argv)
    try:
        manifest = install(**vars(args))
    except (SetupError, OSError) as exc:
        parser.exit(1, f'Node setup failed: {exc}\n')
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
