"""Configure a fresh workspace using the IIC-OSIC container toolchain."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT))
from install_ubuntu import atomic_json, bootstrap_workspace, validate_pdk
from run_deployment import validate_config, desktop_dependencies, child_environment

workspace = ROOT / '.workspace'
if workspace.is_symlink() or (workspace.exists() and any(workspace.iterdir())):
    raise SystemExit('Bootstrap requires a new, empty workspace')
if (ROOT / '.tools/deployment/config.json').exists():
    raise SystemExit('A deployment configuration already exists; keep using it')
pdk = Path('/foss/pdks')
validate_pdk(pdk)
wrapper = ROOT / '.tools/eda/bin/ngspice-sky130'
wrapper.parent.mkdir(parents=True, exist_ok=True)
wrapper.write_text('#!/bin/sh\nexec /foss/tools/bin/ngspice -D ngbehavior=hsa -D skywaterpdk -D ng_nomodcheck "$@"\n')
wrapper.chmod(0o755)
settings = {
    'tools': {'ngspice': str(wrapper), 'xschem': '/foss/tools/bin/xschem',
              'magic': '/foss/tools/bin/magic', 'netgen': '/foss/tools/bin/netgen'},
    'pdk_root': str(pdk), 'pdk_variant': 'sky130A',
    'pdk_version': '026824c7969ce6f4fc9678e6ca04b0a06a596c4b',
    'ui': {'language': 'zh-CN', 'theme': 'dark'},
}
config = validate_config({
    'schema_version': 1, 'workspace': str(workspace),
    'python': str(ROOT / '.venv/bin/python'), 'host': '127.0.0.1', 'port': 8765,
    'desktop': True, 'desktop_port': 6080, 'pdk_root': str(pdk),
    'extra_path': [str(wrapper.parent), '/foss/tools/bin'],
})
desktop_dependencies(child_environment(config))
bootstrap_workspace(workspace, settings)
atomic_json(ROOT / '.tools/deployment/config.json', config)
atomic_json(ROOT / '.tools/deployment/container-installation.json', {
    'mode': 'container-manual', 'base_image': 'hpretl/iic-osic-tools:2026.08',
    'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
    'workspace': str(workspace), 'settings': settings, 'config': config,
    'python': sys.version,
})
print(json.dumps({'workspace': str(workspace), 'url': 'http://127.0.0.1:8765'}, indent=2))
