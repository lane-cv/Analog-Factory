#!/usr/bin/env python3
"""Build official Xschem 3.4.7 locally for current SKY130 symbol expressions.

Requires configure_local_eda.py's Tcl/Tk runtime and build_local_magic.py's
development headers, plus GCC, make, bison, flex, apt-get, and dpkg-deb.
Installs only under .tools/eda/xschem-3.4.7; leaves workspace settings untouched.
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
VERSION = '3.4.7'
COMMIT = '92dd8fe5f4d5c1057489710d8a22f18fdc9d7ed0'
SOURCE_SHA256 = 'db5250690bc193bb2874e7a3b43d7bfc3499c882feb7fa885080434cc9a81c7c'
SOURCE_URL = f'https://codeload.github.com/StefanSchippers/xschem/tar.gz/{COMMIT}'
PACKAGES = {
    'libxpm-dev': '1:3.5.17-1ubuntu0.26.04.1',
    'libcairo2-dev': '1.18.4-3',
    'libxrender-dev': '1:0.9.12-1build1',
    'libx11-xcb-dev': '2:1.8.13-1',
    'libxcb-render0-dev': '1.17.0-2ubuntu1',
    'libjpeg-turbo8-dev': '2.1.5-4ubuntu4',
    'libfontconfig-dev': '2.17.1-3ubuntu1',
    'libfreetype-dev': '2.14.2+dfsg-1ubuntu0.1',
    'libpng-dev': '1.6.57-1',
    'libxcb-shm0-dev': '1.17.0-2ubuntu1',
    'libpixman-1-dev': '0.46.4-1',
    'libbrotli-dev': '1.2.0-3build1',
    'libexpat1-dev': '2.7.4-1',
    'libbz2-dev': '1.0.8-6build2',
}


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, default=min(os.cpu_count() or 1, 4))
    parser.add_argument('--skip-download', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.jobs <= 64:
        parser.error('--jobs must be between 1 and 64')
    for command in ('gcc', 'make', 'bison', 'flex', 'dpkg-deb'):
        if not shutil.which(command):
            parser.error(f'{command} is required')
    eda = REPO / '.tools/eda'
    base = eda / f'xschem-{VERSION}'
    runtime = eda / 'root/usr'
    shared = eda / 'magic-8.3.684/dependencies/usr'
    if not (runtime / 'bin/wish8.6').is_file():
        parser.error('Run scripts/configure_local_eda.py first for the Tcl/Tk runtime')
    if not (shared / 'include/tcl8.6/tk.h').is_file():
        parser.error('Run scripts/build_local_magic.py first for local Tcl/Tk/X11 headers')
    for directory in ('packages', 'dependencies', 'logs', 'bin', 'config'):
        (base / directory).mkdir(parents=True, exist_ok=True)
    archive = base / f'xschem-{COMMIT}.tar.gz'
    if not archive.exists():
        if args.skip_download:
            parser.error(f'Missing source archive: {archive}')
        temporary = archive.with_suffix('.download')
        with urllib.request.urlopen(SOURCE_URL, timeout=60) as response, temporary.open('wb') as output:
            shutil.copyfileobj(response, output)
        temporary.replace(archive)
    if digest(archive) != SOURCE_SHA256:
        raise RuntimeError('Xschem source checksum does not match the pinned source')
    source = base / f'xschem-{COMMIT}'
    if not source.exists():
        with tarfile.open(archive) as contents:
            contents.extractall(base, filter='data')
    packages = []
    for name, version in PACKAGES.items():
        def matching_archives():
            return [p for p in sorted((base / 'packages').glob(f'{name}_*.deb'))
                    if subprocess.check_output(['dpkg-deb', '-f', str(p), 'Version'], text=True).strip() == version]
        matching = matching_archives()
        if not matching:
            if args.skip_download:
                parser.error(f'Missing development package: {name}={version}')
            subprocess.run(['apt-get', 'download', f'{name}={version}'], cwd=base / 'packages', check=True)
            matching = matching_archives()
        if len(matching) != 1:
            raise RuntimeError(f'Expected one archive for {name}={version}, found {len(matching)}')
        package = matching[0]
        subprocess.run(['dpkg-deb', '-x', str(package), str(base / 'dependencies')], check=True)
        packages.append({'package': name, 'version': version, 'archive': package.name, 'sha256': digest(package)})
    headers = base / 'dependencies/usr'
    libraries = headers / 'lib/x86_64-linux-gnu'
    shared_libraries = shared / 'lib/x86_64-linux-gnu'
    runtime_libraries = runtime / 'lib/x86_64-linux-gnu'
    # Select dynamic Tcl/Tk before the static archives in the shared dev tree.
    for name, target in (('libtcl8.6.so', Path('/usr/lib/x86_64-linux-gnu/libtcl8.6.so')),
                         ('libtk8.6.so', runtime_libraries / 'libtk8.6.so')):
        if not target.is_file():
            raise RuntimeError(f'Missing Tcl/Tk runtime: {target}')
        link = libraries / name
        if link.is_symlink():
            link.unlink()
        link.symlink_to(target)
    for link in libraries.glob('*.so'):
        if link.is_symlink() and not link.exists():
            for root in (Path('/usr/lib/x86_64-linux-gnu'), runtime_libraries):
                target = root / link.readlink()
                if target.exists():
                    link.unlink()
                    link.symlink_to(target)
                    break
    library_path = f'{libraries}:{shared_libraries}:{runtime_libraries}'
    cflags = f'-O2 -std=gnu17 -I{headers}/include -I{headers}/include/x86_64-linux-gnu -I{headers}/include/cairo -I{shared}/include -I{shared}/include/tcl8.6'
    ldflags = f'-L{libraries} -L{shared_libraries} -L{runtime_libraries}'
    environment = {**os.environ, 'LIBRARY_PATH': library_path, 'LD_LIBRARY_PATH': library_path,
                   'PKG_CONFIG_PATH': f'{libraries}/pkgconfig:{headers}/share/pkgconfig:{shared_libraries}/pkgconfig:{shared}/share/pkgconfig'}
    configure = ['./configure', f'--prefix={base}/install', f'--CFLAGS={cflags}',
                 f'--LDFLAGS={ldflags}', '/arg/tk-version=8.6']
    for phase, command in (('configure', configure), ('build', ['make', f'-j{args.jobs}']),
                           ('install', ['make', 'install'])):
        log = base / 'logs' / f'{phase}.log'
        print(f'Xschem {phase}: {log}', flush=True)
        with log.open('w') as output:
            result = subprocess.run(command, cwd=source, env=environment,
                                    stdout=output, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f'Xschem {phase} failed: see {log}')
        if phase == 'configure' and 'cairo:     yes' not in log.read_text():
            raise RuntimeError(f'Xschem Cairo support was not detected: see {log}')
    launcher = base / 'bin/xschem'
    launcher.write_text('''#!/bin/bash
set -euo pipefail
AF_XSCHEM_ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
AF_XSCHEM_EDA_ROOT="$(cd -- "$AF_XSCHEM_ROOT/.." && pwd)"
export AF_XSCHEM_ROOT
export LD_LIBRARY_PATH="$AF_XSCHEM_EDA_ROOT/root/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export TK_LIBRARY="$AF_XSCHEM_EDA_ROOT/root/usr/share/tcltk/tk8.6"
exec "$AF_XSCHEM_ROOT/install/bin/xschem" --preinit 'set USER_CONF_DIR [file join $env(AF_XSCHEM_ROOT) config]; set XSCHEM_SHAREDIR [file join $env(AF_XSCHEM_ROOT) install share xschem]; set XSCHEM_LIBRARY_PATH "$XSCHEM_SHAREDIR/xschem_library:$XSCHEM_SHAREDIR/xschem_library/devices"' "$@"
''')
    launcher.chmod(0o755)
    version_output = subprocess.check_output([str(launcher), '--version'], text=True, timeout=15).strip()
    if version_output.splitlines()[0] != f'XSCHEM V{VERSION}':
        raise RuntimeError(f'Expected Xschem {VERSION}, received {version_output}')
    manifest = {'version': VERSION, 'version_output': version_output, 'commit': COMMIT, 'source_url': SOURCE_URL,
                'source_sha256': SOURCE_SHA256, 'development_packages': packages,
                'shared_dependencies': str(shared), 'configure': configure,
                'launcher': str(launcher), 'graphics': ['X11', 'Cairo'], 'interpreter': 'Tcl/Tk 8.6'}
    (base / 'installation.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'xschem': str(launcher), 'version': VERSION}, indent=2))


if __name__ == '__main__':
    main()
