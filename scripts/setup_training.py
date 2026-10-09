#!/usr/bin/env python3
"""Opt-in isolated CPU trainer installation; no system packages or paid services."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
UV_VERSION = '0.12.23'
UV_SHA256 = '9167d72b3319674b6303c4cbe071854bba13ebdf3d76b1a7cbdc175471fb66d6'
PYTHON_VERSION = '3.12.15'
MODEL_ID = 'Qwen/Qwen2.5-0.5B-Instruct'
MODEL_REVISION = '7ae557604adf67be50417f59c2c2f167def9a775'
MODEL_SHA256 = 'fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT / '.tools/training')
    args = parser.parse_args()
    root = args.directory.resolve()
    for name in ('bin', 'downloads', 'cache'):
        (root / name).mkdir(parents=True, exist_ok=True)
    archive = root / 'downloads' / f'uv-{UV_VERSION}.tar.gz'
    if not archive.exists():
        urllib.request.urlretrieve(f'https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz', archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != UV_SHA256:
        raise SystemExit('uv archive SHA-256 mismatch')
    with tarfile.open(archive) as package:
        member = package.getmember('uv-x86_64-unknown-linux-gnu/uv')
        member.name = 'uv'
        package.extract(member, root / 'bin', filter='data')
    uv = str(root / 'bin/uv')
    env = {**os.environ, 'UV_CACHE_DIR': str(root / 'cache'),
           'UV_PYTHON_INSTALL_DIR': str(root / 'python'), 'HF_HOME': str(root / 'cache/huggingface'),
           'HF_HUB_DISABLE_IMPLICIT_TOKEN': '1', 'HF_HUB_DISABLE_TELEMETRY': '1'}
    def command(argv):
        subprocess.run(argv, check=True, cwd=ROOT, env=env)
    command([uv, 'python', 'install', '--no-bin', PYTHON_VERSION])
    interpreter = root / 'python' / f'cpython-{PYTHON_VERSION}-linux-x86_64-gnu/bin/python3.12'
    python = root / 'venv/bin/python'
    if not python.exists():
        command([uv, 'venv', '--python', str(interpreter), str(root / 'venv')])
    command([uv, 'pip', 'install', '--python', str(python), '--index-url',
             'https://download.pytorch.org/whl/cpu', 'torch==2.9.1'])
    command([uv, 'pip', 'install', '--python', str(python), '-r',
             str(ROOT / 'examples/training/requirements-cpu.lock')])
    download = '''
from huggingface_hub import snapshot_download
from pathlib import Path
import hashlib, importlib.metadata as metadata, json, platform, sys
root=Path(sys.argv[1]); model_id=sys.argv[2]; revision=sys.argv[3]; expected=sys.argv[4]
snapshot_download(model_id,revision=revision,local_dir=root/'model',token=False,
                  allow_patterns=['*.json','*.safetensors','*.txt','LICENSE'])
source={'model_id':model_id,'revision':revision,'license':'Apache-2.0',
        'safetensors_sha256':hashlib.sha256((root/'model/model.safetensors').read_bytes()).hexdigest()}
if source['safetensors_sha256'] != expected:
    raise RuntimeError('Pinned model SHA-256 mismatch')
(root/'model-source.json').write_text(json.dumps(source,indent=2)+'\\n')
runtime={'python':platform.python_version(),'device':'cpu',
 'packages':{k:metadata.version(k) for k in ['torch','transformers','trl','peft','accelerate','datasets','jmespath']}}
from trl import GRPOTrainer, GRPOConfig
from transformers import AutoModelForCausalLM
(root/'runtime.json').write_text(json.dumps(runtime,indent=2)+'\\n')
print(json.dumps({'installed':runtime,'model':source,'qualification':'not yet trained'}))
'''
    command([str(python), '-c', download, str(root), MODEL_ID, MODEL_REVISION, MODEL_SHA256])
    print('Optional CPU trainer installed. Run training.start to qualify actual updates and evaluation.')


if __name__ == '__main__':
    main()
