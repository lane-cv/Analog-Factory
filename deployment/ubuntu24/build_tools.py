"""Build verified native tool sources inside the isolated Ubuntu image."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parent


def main():
    sources = json.loads((ROOT / 'sources.json').read_text())
    work = ROOT / 'work'
    work.mkdir(exist_ok=True)
    records = []
    for name, metadata in sources.items():
        archive = ROOT / 'archives' / f'{name}.tar.gz'
        with archive.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != metadata['sha256']:
                raise ValueError(f'Unverified source archive: {name}')
        with tarfile.open(archive) as contents:
            contents.extractall(work, filter='data')
        source = work / f'{name}-{metadata["commit"]}'
        configure = ['./configure', '--prefix=/opt/eda']
        if name == 'magic':
            configure += ['--without-opengl', '--disable-magic-builddate']
        for command in (configure, ['make', '-j2'], ['make', 'install']):
            subprocess.run(command, cwd=source, check=True)
        records.append({'tool': name, **metadata, 'configure': configure})
    wrapper = Path('/opt/eda/bin/ngspice-sky130')
    wrapper.write_text('#!/bin/sh\nexec /usr/bin/ngspice -D ngbehavior=hsa -D skywaterpdk -D ng_nomodcheck "$@"\n')
    wrapper.chmod(0o755)
    Path('/opt/eda/sources.json').write_text(json.dumps(records, indent=2) + '\n')


if __name__ == '__main__':
    main()
