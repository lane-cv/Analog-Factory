#!/usr/bin/env python3
"""Install a pinned, checksum-verified SKY130A analog PDK into this checkout.

Requires Python 3.12+ and zstd. Downloads only tool configuration and analog
primitive libraries; it does not alter workspace settings or system packages.
"""
import argparse
import hashlib
import json
import shlex
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REVISION = '1689ac3f2dc763876eaf967227c7dfe831b031ae'
BASE_URL = f'https://github.com/fossi-foundation/ciel-releases/releases/download/sky130-{REVISION}'
ARCHIVES = {
    'common.tar.zst': '92e3deed352b9a1aa53d47bc8f3de75e40dec013809c109d36ed4e8d13f74036',
    'sky130_fd_pr.tar.zst': '79ac105aa2710acf358572aa3762b065bcdd9ed408dc64f3c32d9fb71cdd287e',
}
REQUIRED = ('libs.tech/xschem/xschemrc', 'libs.tech/magic/sky130A.magicrc',
            'libs.tech/netgen/sky130A_setup.tcl', 'libs.tech/ngspice/sky130.lib.spice')


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-download', action='store_true', help='Use already downloaded archives')
    parser.add_argument('--ngspice', default='/usr/local/bin/ngspice', help='Native ngspice executable for the SKY130 launcher')
    args = parser.parse_args()
    if not shutil.which('zstd'):
        parser.error('zstd is required to decompress the official PDK archives')
    simulator = shutil.which(args.ngspice)
    if not simulator:
        parser.error(f'ngspice executable not found: {args.ngspice}')
    downloads = REPO / '.tools/pdks/downloads' / f'sky130-{REVISION}'
    target = REPO / '.tools/pdks/sky130' / REVISION
    downloads.mkdir(parents=True, exist_ok=True)
    records = []
    for filename, digest in ARCHIVES.items():
        archive = downloads / filename
        if not archive.exists():
            if args.skip_download:
                parser.error(f'Missing archive: {archive}')
            temporary = archive.with_suffix('.part')
            with urllib.request.urlopen(f'{BASE_URL}/{filename}', timeout=60) as source, temporary.open('wb') as output:
                shutil.copyfileobj(source, output)
            if sha256(temporary) != digest:
                raise RuntimeError(f'Checksum mismatch: {filename}')
            temporary.replace(archive)
        if sha256(archive) != digest:
            raise RuntimeError(f'Checksum mismatch: {filename}')
        records.append({'file': filename, 'url': f'{BASE_URL}/{filename}',
                        'sha256': digest, 'bytes': archive.stat().st_size})
    marker = target / 'installation.json'
    if not marker.exists():
        # Preserve revision directories. A failed extraction can be retried into
        # the same directory; only a complete installation receives the marker.
        target.mkdir(parents=True, exist_ok=True)
        for record in records:
            with subprocess.Popen(['zstd', '-dc', str(downloads / record['file'])], stdout=subprocess.PIPE) as process:
                with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
                    archive.extractall(target, members=(member for member in archive
                                       if member.name.startswith('sky130A/')), filter='data')
                if process.wait() != 0:
                    raise RuntimeError(f'Decompression failed: {record["file"]}')
    paths = {relative: target / 'sky130A' / relative for relative in REQUIRED}
    for path in paths.values():
        if not path.is_file():
            raise RuntimeError(f'Missing required PDK file: {path}')
    manifest = {'pdk': 'sky130A', 'open_pdks_revision': REVISION,
                'pdk_root': str(target), 'libraries': ['sky130_fd_pr'],
                'release': BASE_URL.replace('/download/', '/tag/'), 'archives': records,
                'configuration_sha256': {name: sha256(path) for name, path in paths.items()}}
    if marker.exists() and json.loads(marker.read_text()) != manifest:
        raise RuntimeError('Existing PDK manifest differs; inspect the pinned installation before replacing it')
    marker.write_text(json.dumps(manifest, indent=2) + '\n')
    launcher = REPO / '.tools/eda/bin/ngspice-sky130'
    if Path(simulator).resolve() == launcher.resolve():
        raise RuntimeError('--ngspice must point to the native executable, not this launcher')
    launcher.parent.mkdir(parents=True, exist_ok=True)
    # These are the three settings supplied by this PDK's ngspice/spinit.
    # Command-line definitions still apply when the application uses -n.
    launcher.write_text('#!/bin/sh\nset -eu\nexec ' + shlex.quote(str(Path(simulator).resolve()))
                        + ' -D ngbehavior=hsa -D skywaterpdk -D ng_nomodcheck "$@"\n')
    launcher.chmod(0o755)
    environment = REPO / '.tools/pdks/sky130-env.sh'
    binary_paths = [REPO / '.tools/eda/xschem-3.4.7/bin', REPO / '.tools/eda/magic-8.3.684/bin', REPO / '.tools/eda/bin']
    environment.write_text('export PDK_ROOT=' + shlex.quote(str(target)) + '\n'
                           + 'export PDK=sky130A\nexport PDKPATH="$PDK_ROOT/$PDK"\n'
                           + 'export PATH=' + shlex.quote(':'.join(map(str, binary_paths))) + ':"$PATH"\n')
    print(json.dumps({'pdk_root': str(target), 'pdk_variant': 'sky130A', 'pdk_version': REVISION,
                      'ngspice': str(launcher)}, indent=2))


if __name__ == '__main__':
    main()
