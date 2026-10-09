#!/usr/bin/env python3
"""Build and install private release candidates; never publish or copy user state.

The optional developer dependencies are ``build`` and setuptools>=77. Product
runtime dependencies remain unchanged. Run this from the checkout or pass --source.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = {'.workspace', '.validation', '.tools', '.af', '.git', '.aws', '.codex',
           '.agents', '.credentials', '.superpowers', '__pycache__', 'node_modules',
           'credentials.json', 'auth.json', 'settings.json'}
MAX_FILES = 10000
MAX_BYTES = 1024**3


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_name(name):
    value = name.removesuffix('/')
    parts = tuple(part.casefold() for part in PurePosixPath(value).parts)
    if (not value or '\\' in value or ':' in value or value.startswith('/')
            or any(part in ('', '.', '..') for part in value.split('/'))
            or any(part in PRIVATE or part.startswith('.env') for part in parts)
            or value.casefold().endswith(('.pyc', '.pyo', '.pem', '.key'))):
        raise ValueError(f'Unsafe or private release member: {name}')
    return value


def stage_source(source, destination):
    """Copy the allowlisted current package, including uncommitted runtime assets."""
    source, destination = Path(source).resolve(), Path(destination)
    config = tomllib.loads((source/'pyproject.toml').read_text())
    if config.get('project', {}).get('name') != 'analog-factory':
        raise ValueError('Source must be an Analog Factory checkout')
    destination.mkdir(parents=True, exist_ok=False)
    for name in ('pyproject.toml', 'README.md', 'LICENSE'):
        path = source/name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Release source must be a regular file: {name}')
        shutil.copy2(path, destination/name)
    package = source/'analog_factory'
    if package.is_symlink() or not package.is_dir():
        raise ValueError('Package source must be a regular directory')
    expected = {}
    for path in sorted(package.rglob('*')):
        relative = path.relative_to(source)
        if '__pycache__' in relative.parts or path.suffix in ('.pyc', '.pyo'):
            continue
        if path.is_symlink():
            raise ValueError(f'Release source contains a symbolic link: {relative}')
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f'Release source must be regular: {relative}')
        safe_name(relative.as_posix())
        target = destination/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        expected[relative.as_posix()] = digest(target)
    if 'analog_factory/__init__.py' not in expected:
        raise ValueError('Missing package initializer')
    return expected


def inspect_archive(path, expected):
    """Check archive safety and byte equality without extracting any members."""
    path = Path(path)
    hashes, seen, size = {}, set(), 0
    def member(name, length, regular, directory, stream):
        nonlocal size
        name = safe_name(name)
        if name in seen:
            raise ValueError(f'Duplicate release member: {name}')
        seen.add(name)
        if not (regular or directory):
            raise ValueError(f'Release member is not regular: {name}')
        size += length
        if len(seen) > MAX_FILES or size > MAX_BYTES:
            raise ValueError('Release archive exceeds inspection limits')
        if regular:
            with stream() as content:
                hashes[name] = hashlib.file_digest(content, 'sha256').hexdigest()
    if path.suffix == '.whl':
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                mode = info.external_attr >> 16
                kind = stat.S_IFMT(mode)
                member(info.filename, info.file_size,
                       not info.is_dir() and kind in (0, stat.S_IFREG), info.is_dir() and kind in (0, stat.S_IFDIR),
                       lambda info=info: archive.open(info))
        format_name = 'wheel'
    elif path.name.endswith('.tar.gz'):
        with tarfile.open(path, 'r:gz') as archive:
            for info in archive:
                member(info.name, info.size, info.isfile(), info.isdir(),
                       lambda info=info: archive.extractfile(info))
        prefixes = {name.split('/')[0] for name in hashes}
        if len(prefixes) != 1 or any('/' not in name for name in hashes):
            raise ValueError('Source archive requires one enclosing directory')
        hashes = {name.split('/', 1)[1]: value for name, value in hashes.items()}
        format_name = 'sdist'
    else:
        raise ValueError('Unsupported release archive')
    missing = sorted(set(expected) - hashes.keys())
    if missing:
        raise ValueError(f'Missing runtime assets: {missing}')
    changed = sorted(name for name, value in expected.items() if hashes[name] != value)
    if changed:
        raise ValueError(f'Runtime asset bytes changed: {changed}')
    return {'format': format_name, 'sha256': digest(path), 'members': len(seen),
            'verified_runtime_files': len(expected), 'size_bytes': path.stat().st_size}


SMOKE = r'''
import hashlib, json, pathlib, sys, threading, urllib.request
from analog_factory.service import Workspace
from analog_factory.server import create_server
import analog_factory
manifest=json.loads(pathlib.Path(sys.argv[1]).read_text())
site=pathlib.Path(analog_factory.__file__).resolve().parent.parent
assert site.is_relative_to(pathlib.Path(sys.prefix)), 'Package imported outside isolated environment'
for name, expected in manifest.items():
    path=site/name
    assert path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==expected, name
workspace=Workspace(pathlib.Path(sys.argv[2]))
registry=workspace.operations
server=None
try:
    created=registry.invoke('project.create', {'name':'Installed release smoke','template':'amplifier'})
    assert created['ok'], created
    pid=created['data']['id']
    detail=registry.invoke('project.detail', {'project_id':pid})
    assert detail['ok'] and detail['data']['files'], detail
    read=registry.invoke('file.read', {'project_id':pid,'path':'testbenches/pre.cir'})
    assert read['ok'] and '.end' in read['data']['content'].lower(), read
    server=create_server(workspace,port=0,desktop_url='')
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    with urllib.request.urlopen(base+'/',timeout=5) as response:
        assert b'Analog-factory' in response.read()
    with urllib.request.urlopen(base+'/app.js',timeout=5) as response:
        assert hashlib.sha256(response.read()).hexdigest()==manifest['analog_factory/static/app.js']
    with urllib.request.urlopen(base+'/api/operations?module=project',timeout=5) as response:
        assert response.status==200 and b'project.create' in response.read()
    print(json.dumps({'status':'pass','registered_operations':len(registry.operations),
        'verified_runtime_files':len(manifest),'http_checks':['index','app.js','project discovery'],
        'package_path':str(site/'analog_factory')}))
finally:
    if server:
        server.shutdown();server.server_close();thread.join(timeout=5)
    registry.close()
'''


def validate_release(source, output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix='release-', dir=output))
    report = {'status': 'running', 'python': sys.version.split()[0], 'commands': [], 'artifacts': {},
              'build_tools': {}}
    for package in ('build', 'setuptools', 'wheel', 'packaging', 'pyproject_hooks'):
        try:
            report['build_tools'][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            report['build_tools'][package] = None
    def save():
        (evidence/'release-validation.json').write_text(json.dumps(report, indent=2)+'\n')
    def command(name, args, cwd, env):
        with (evidence/f'{name}.log').open('w') as log:
            result = subprocess.run(args, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=300)
        report['commands'].append({'name':name,'exit_code':result.returncode,'log':f'{name}.log'})
        save()
        if result.returncode:
            raise RuntimeError(f'{name} failed; inspect {evidence/name}.log')
    save()
    try:
        with tempfile.TemporaryDirectory(prefix='af-release-') as scratch:
            scratch = Path(scratch)
            staged = scratch/'source'
            expected = stage_source(source, staged)
            manifest = evidence/'runtime-manifest.json'
            manifest.write_text(json.dumps(expected, indent=2)+'\n')
            report['source_manifest_sha256'] = digest(manifest)
            env = {key:value for key,value in os.environ.items() if key not in ('PYTHONPATH','PYTHONHOME')}
            env.update({'PYTHONNOUSERSITE':'1','PIP_NO_INDEX':'1','PIP_DISABLE_PIP_VERSION_CHECK':'1',
                        'SOURCE_DATE_EPOCH':'0'})
            artifacts = evidence/'artifacts'
            # Developer build tools may be installed in the interpreter's user
            # site. Only the installed-product smoke must exclude that site.
            build_env = {key:value for key,value in env.items() if key != 'PYTHONNOUSERSITE'}
            command('build', [sys.executable,'-m','build','--no-isolation','--outdir',str(artifacts)], staged, build_env)
            wheels, sources = list(artifacts.glob('*.whl')), list(artifacts.glob('*.tar.gz'))
            if len(wheels) != 1 or len(sources) != 1:
                raise ValueError('Build must produce exactly one wheel and one source distribution')
            for artifact in [*wheels,*sources]:
                report['artifacts'][artifact.name] = inspect_archive(artifact, expected)
            virtual = scratch/'venv'
            command('venv', [sys.executable,'-m','venv',str(virtual)], scratch, env)
            python = virtual/'bin/python'
            command('install', [str(python),'-m','pip','install','--no-deps','--no-index',str(wheels[0])], scratch, env)
            command('cli', [str(virtual/'bin/analog-factory'),'--help'], scratch, env)
            command('installed-smoke', [str(python),'-I','-c',SMOKE,str(manifest),str(scratch/'workspace')], scratch, env)
            report['smoke'] = json.loads((evidence/'installed-smoke.log').read_text())
            report['status'] = 'pass'
    except Exception as error:
        report['status'], report['error'] = 'fail', str(error)
    save()
    return evidence, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, default=ROOT/'.validation/releases')
    args = parser.parse_args()
    evidence, report = validate_release(args.source, args.output)
    print(json.dumps({'status':report['status'],'error':report.get('error'),'evidence':str(evidence)}))
    return 0 if report['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
