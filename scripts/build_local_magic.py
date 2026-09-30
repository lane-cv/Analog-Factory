#!/usr/bin/env python3
"""Build pinned Magic for SKY130 using project-local Ubuntu development packages.

Requires the Tcl/Tk runtime installed by configure_local_eda.py, GCC, make,
apt-get, and dpkg-deb. No system packages or workspace settings are changed.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
VERSION = '8.3.684'
COMMIT = '4f53bb3091d1e4a9b2009a58f157a8a4331d4c84'
SOURCE_SHA256 = 'd4dc0e873354fb13c802400124b757aff04372c9b8202af56409c33267ac46fd'
SOURCE_URL = f'https://codeload.github.com/RTimothyEdwards/magic/tar.gz/{COMMIT}'
PACKAGES = {
    'tcl8.6-dev': '8.6.17+dfsg-1build1',
    'tk8.6-dev': '8.6.17-1build1',
    'libx11-dev': '2:1.8.13-1',
    'libxau-dev': '1:1.0.11-1build2',
    'libxdmcp-dev': '1:1.1.5-2',
    'x11proto-dev': '2025.1-1',
    'xtrans-dev': '1.6.0-1build1',
    'libxcb1-dev': '1.17.0-2ubuntu1',
    'libxext-dev': '2:1.3.4-1build3',
    'libxss-dev': '1:1.2.3-1build4',
    'zlib1g-dev': '1:1.3.dfsg+really1.3.1-1ubuntu3',
}


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, default=min(os.cpu_count() or 1, 4))
    parser.add_argument('--skip-download', action='store_true', help='Reuse downloaded source and packages')
    args = parser.parse_args()
    if not 1 <= args.jobs <= 64:
        parser.error('--jobs must be between 1 and 64')
    for command in ('gcc', 'make', 'dpkg-deb'):
        if not shutil.which(command):
            parser.error(f'{command} is required')
    eda = REPO / '.tools/eda'
    base = eda / f'magic-{VERSION}'
    runtime = eda / 'root/usr'
    if not (runtime / 'bin/wish8.6').is_file():
        parser.error('Run scripts/configure_local_eda.py first to prepare the Tcl/Tk runtime')
    for directory in ('packages', 'dependencies', 'logs', 'bin'):
        (base / directory).mkdir(parents=True, exist_ok=True)
    archive = base / f'magic-{COMMIT}.tar.gz'
    if not archive.exists():
        if args.skip_download:
            parser.error(f'Missing source archive: {archive}')
        temporary = archive.with_suffix('.download')
        with urllib.request.urlopen(SOURCE_URL, timeout=60) as response, temporary.open('wb') as output:
            shutil.copyfileobj(response, output)
        temporary.replace(archive)
    if digest(archive) != SOURCE_SHA256:
        raise RuntimeError('Magic source archive checksum does not match the pinned source')
    source = base / f'magic-{COMMIT}'
    if not source.exists():
        with tarfile.open(archive) as contents:
            contents.extractall(base, filter='data')
    packages = []
    for name, version in PACKAGES.items():
        matches = sorted((base / 'packages').glob(f'{name}_*.deb'))
        matching = [p for p in matches if subprocess.check_output(
            ['dpkg-deb', '-f', str(p), 'Version'], text=True).strip() == version]
        if not matching:
            if args.skip_download:
                parser.error(f'Missing development package: {name}={version}')
            subprocess.run(['apt-get', 'download', f'{name}={version}'], cwd=base / 'packages', check=True)
            matching = sorted((base / 'packages').glob(f'{name}_*.deb'))
            matching = [p for p in matching if subprocess.check_output(
                ['dpkg-deb', '-f', str(p), 'Version'], text=True).strip() == version]
        if len(matching) != 1:
            raise RuntimeError(f'Expected one archive for {name}={version}, found {len(matching)}')
        package = matching[0]
        subprocess.run(['dpkg-deb', '-x', str(package), str(base / 'dependencies')], check=True)
        packages.append({'package': name, 'version': version, 'archive': package.name, 'sha256': digest(package)})
    headers = base / 'dependencies/usr'
    libraries = headers / 'lib/x86_64-linux-gnu'
    runtime_libraries = runtime / 'lib/x86_64-linux-gnu'
    # Development .so links normally point to versioned system runtime files.
    # Relocate only dangling links in our extraction, never system libraries.
    for link in libraries.glob('*.so'):
        if link.is_symlink() and not link.exists():
            for root in (Path('/usr/lib/x86_64-linux-gnu'), runtime_libraries):
                target = root / link.readlink()
                if target.exists():
                    link.unlink()
                    link.symlink_to(target)
                    break
    library_path = f'{libraries}:{runtime_libraries}'
    environment = {**os.environ,
                   'CFLAGS': '-O2 -std=gnu17',
                   'CPPFLAGS': f'-I{headers}/include',
                   'LDFLAGS': f'-L{libraries} -L{runtime_libraries}',
                   'LIBRARY_PATH': library_path,
                   'LD_LIBRARY_PATH': library_path}
    configure = ['./configure', f'--prefix={base}/install',
                 f'--with-tcl={libraries}/tcl8.6', f'--with-tk={libraries}/tk8.6',
                 f'--with-tclincls={headers}/include/tcl8.6', f'--with-tkincls={headers}/include/tcl8.6',
                 '--with-tcllibs=/usr/lib/x86_64-linux-gnu', f'--with-tklibs={runtime_libraries}',
                 f'--with-wish={runtime}/bin/wish8.6', f'--x-includes={headers}/include',
                 f'--x-libraries={libraries}', '--without-opengl', '--without-cairo',
                 '--disable-magic-builddate']
    for phase, command in (('configure', configure), ('build', ['make', f'-j{args.jobs}']),
                           ('install', ['make', 'install'])):
        log = base / 'logs' / f'{phase}.log'
        print(f'Magic {phase}: {log}', flush=True)
        with log.open('w') as output:
            result = subprocess.run(command, cwd=source, env=environment, stdout=output,
                                    stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f'Magic {phase} failed: see {log}')
    launcher = base / 'bin/magic'
    launcher.write_text('''#!/bin/bash
set -euo pipefail
AF_MAGIC_ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
AF_MAGIC_EDA_ROOT="$(cd -- "$AF_MAGIC_ROOT/.." && pwd)"
export LD_LIBRARY_PATH="$AF_MAGIC_EDA_ROOT/root/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export TK_LIBRARY="$AF_MAGIC_EDA_ROOT/root/usr/share/tcltk/tk8.6"
export MAGIC_WISH="$AF_MAGIC_EDA_ROOT/root/usr/bin/wish8.6"
export CAD_ROOT="$AF_MAGIC_ROOT/install/lib"
exec "$AF_MAGIC_ROOT/install/bin/magic" "$@"
''')
    launcher.chmod(0o755)
    version = subprocess.check_output([str(launcher), '--version'], text=True, timeout=15).strip()
    if version != VERSION:
        raise RuntimeError(f'Expected Magic {VERSION}, received {version}')
    manifest = {'version': version, 'commit': COMMIT, 'source_url': SOURCE_URL,
                'source_sha256': SOURCE_SHA256, 'development_packages': packages,
                'configure': configure, 'cflags': environment['CFLAGS'], 'launcher': str(launcher),
                'graphics': ['X11'], 'interpreter': 'Tcl/Tk 8.6'}
    (base / 'installation.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'magic': str(launcher), 'version': version}, indent=2))


if __name__ == '__main__':
    main()
