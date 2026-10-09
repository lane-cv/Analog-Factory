#!/usr/bin/env python3
"""Build and replay a pinned Ubuntu 24.04 environment, without mounting user data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = ('analog_factory', 'tests', 'scripts', 'examples', 'docs', 'deployment',
                'pyproject.toml', 'README.md', 'LICENSE', 'analog-factory-project-prompt-en.md')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy_source(root, target):
    """Copy explicit source trees, refusing symlinks and hidden local state."""
    target.mkdir(parents=True, exist_ok=False)
    manifest = {}
    for name in SOURCE_ROOTS:
        source = root / name
        paths = source.rglob('*') if source.is_dir() and not source.is_symlink() else [source]
        for path in paths:
            relative = path.relative_to(root)
            if (not path.is_file() or path.is_symlink()
                    or any(part.startswith('.') or part in ('__pycache__', 'node_modules') for part in relative.parts)):
                continue
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            manifest[str(relative)] = digest(destination)
    return manifest


def runtime_command(source, pdk, output, image):
    return ['docker', 'run', '--rm', '--init', '--network=none', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '--pids-limit=256', '--memory=6g', '--cpus=2',
            '--user', f'{os.getuid()}:{os.getgid()}',
            '-e', 'PYTHONDONTWRITEBYTECODE=1',
            '-v', f'{source}:/app:ro', '-v', f'{pdk}:/pdk:ro', '-v', f'{output}:/evidence',
            image, 'xvfb-run', '-a', 'python3', 'scripts/ubuntu24_acceptance.py']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdk-root', type=Path, required=True, help='Prepared directory containing sky130A')
    parser.add_argument('--output', type=Path, default=ROOT / '.validation/ubuntu24')
    parser.add_argument('--image', default='analog-factory-ubuntu24:validation')
    parser.add_argument('--skip-build', action='store_true')
    parser.add_argument('--ca-bundle', type=Path, default=Path('/etc/ssl/certs/ca-certificates.crt'))
    args = parser.parse_args()
    pdk = args.pdk_root.resolve(strict=True)
    if not (pdk / 'sky130A/libs.tech/combined/sky130.lib.spice').is_file():
        parser.error('Prepared scalable SKY130 models are required')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix='replay-', dir=output))
    source = run / 'source'
    manifest = copy_source(ROOT, source)
    (run / 'source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    context = run / 'build'
    shutil.copytree(ROOT / 'deployment/ubuntu24', context, ignore=shutil.ignore_patterns('__pycache__'))
    (context / 'archives').mkdir()
    trust_bundle = args.ca_bundle.resolve(strict=True)
    shutil.copy2(trust_bundle, context / 'ca-certificates.crt')
    pins = json.loads((context / 'sources.json').read_text())
    for name, metadata in pins.items():
        destination = context / 'archives' / f'{name}.tar.gz'
        cached = ROOT / '.tools/eda' / f'{name}-{metadata["version"]}' / f'{name}-{metadata["commit"]}.tar.gz'
        if cached.is_file():
            shutil.copy2(cached, destination)
        else:
            with urllib.request.urlopen(metadata['url'], timeout=120) as response, destination.open('wb') as stream:
                shutil.copyfileobj(response, stream)
        if digest(destination) != metadata['sha256']:
            raise ValueError(f'{name}: source checksum mismatch')
    if not args.skip_build:
        with (run / 'build.log').open('w') as log:
            print(f'Building Ubuntu 24.04; log: {run / "build.log"}', flush=True)
            subprocess.run(['docker', 'build', '--progress=plain', '-t', args.image, str(context)],
                           stdout=log, stderr=subprocess.STDOUT, check=True)
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', args.image], text=True))[0]
    metadata = {'image_id': image['Id'], 'trust_bundle_sha256': digest(trust_bundle),
                'source_manifest_sha256': digest(run / 'source-manifest.json'), 'pdk_input': str(pdk),
                'pdk_manifest': json.loads((pdk / 'installation.json').read_text()) if (pdk / 'installation.json').is_file() else None}
    (run / 'environment-inputs.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(f'Replaying without network; evidence: {run}', flush=True)
    with (run / 'acceptance.log').open('w') as log:
        result = subprocess.run(runtime_command(source, pdk, run, args.image), stdout=log, stderr=subprocess.STDOUT)
    print(f'Replay exit {result.returncode}; evidence: {run}', flush=True)
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
