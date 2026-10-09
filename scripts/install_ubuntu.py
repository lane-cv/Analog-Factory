#!/usr/bin/env python3
"""Install this checkout on Ubuntu without copying another device's state.

Run as the normal account that will own the application. Only apt uses sudo.
The default profile installs native EDA and the pinned prepared SKY130 PDK.
Use --dry-run to inspect the plan without downloads, writes or sudo.
"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from run_deployment import validate_config

BASE_PACKAGES = ('python3', 'python3-venv', 'ca-certificates', 'git')
EDA_PACKAGES = ('build-essential', 'flex', 'bison', 'pkg-config', 'tcl8.6-dev',
                'tk8.6-dev', 'libx11-dev', 'libxext-dev', 'libxrender-dev',
                'libxpm-dev', 'libcairo2-dev', 'libjpeg-dev', 'libxaw7-dev',
                'libreadline-dev', 'zlib1g-dev', 'ngspice', 'netgen-lvs', 'zstd')
DESKTOP_PACKAGES = ('xvfb', 'xauth', 'fluxbox', 'x11vnc', 'websockify', 'novnc',
                    'fonts-dejavu-core')


def parser(root=ROOT):
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--profile', choices=('core', 'eda', 'desktop'), default='eda')
    result.add_argument('--workspace', type=Path, default=root / '.workspace')
    result.add_argument('--host', default='127.0.0.1')
    result.add_argument('--port', type=int, default=8765)
    result.add_argument('--desktop-port', type=int, default=6080)
    result.add_argument('--jobs', type=int, default=2)
    result.add_argument('--pdk-root', type=Path, help='Use an existing prepared SKY130 root')
    result.add_argument('--with-agents', action='store_true', help='Install local Node, Codex and Claude; no login')
    result.add_argument('--with-training', action='store_true', help='Run the separate pinned CPU training installer')
    result.add_argument('--skip-system-packages', action='store_true', help='Dependencies are already installed')
    result.add_argument('--skip-download', action='store_true', help='Require cached EDA/PDK/Node archives (pip/npm still need their own caches)')
    result.add_argument('--dry-run', action='store_true')
    return result


def make_plan(args, root=ROOT):
    if not 1 <= args.jobs <= 64:
        raise ValueError('jobs must be from 1 to 64')
    root = Path(root).resolve()
    workspace = args.workspace.expanduser().resolve()
    if workspace == root or workspace in root.parents:
        raise ValueError('workspace must not contain the source checkout')
    if args.profile == 'core' and args.pdk_root:
        raise ValueError('--pdk-root requires the eda or desktop profile')
    config = validate_config({'schema_version': 1, 'workspace': str(workspace),
        'python': str(root / '.venv/bin/python'), 'host': args.host, 'port': args.port,
        'desktop': args.profile == 'desktop', 'desktop_port': args.desktop_port, 'extra_path': []})
    if config['desktop'] and args.port == args.desktop_port:
        raise ValueError('app and desktop ports must differ')
    return {**config, 'profile': args.profile, 'jobs': args.jobs,
        'apt_packages': list(BASE_PACKAGES + (EDA_PACKAGES if args.profile != 'core' else ())
                             + (DESKTOP_PACKAGES if config['desktop'] else ())),
        'with_agents': args.with_agents, 'with_training': args.with_training,
        'skip_system_packages': args.skip_system_packages, 'skip_download': args.skip_download,
        'requested_pdk_root': str(args.pdk_root.expanduser().resolve()) if args.pdk_root else None}


def check_platform():
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'amd64'):
        raise ValueError('This installer supports Linux x86_64 only')
    if sys.version_info < (3, 12):
        raise ValueError('Python 3.12 or newer is required')
    release = platform.freedesktop_os_release()
    if release.get('ID') != 'ubuntu' or release.get('VERSION_ID') not in ('24.04', '26.04'):
        raise ValueError('Use Ubuntu 24.04 or 26.04; other Linux distributions require manual dependency installation')


def check_destination(plan, root=ROOT):
    marker = root / '.tools/deployment/installation.json'
    if marker.is_symlink():
        raise ValueError('Deployment marker must not be a symlink')
    if marker.exists():
        existing = json.loads(marker.read_text())
        if (existing.get('schema_version') != 1 or existing.get('root') != str(root)
                or existing.get('workspace') != plan['workspace']):
            raise ValueError('Deployment belongs to another checkout/workspace; install into a fresh clone')
        # Upgrading an existing installation needs a deliberate settings migration.
        if existing.get('plan') != plan:
            raise ValueError('Installation options changed; keep the original options or use a fresh clone')
        if 'workspace' in existing.get('phases', []):
            if not (Path(plan['workspace']) / 'settings.json').is_file():
                raise ValueError('Installed workspace settings are missing; restore the workspace before retrying')
        if 'configuration' in existing.get('phases', []):
            from run_deployment import load_config
            config = load_config(root / '.tools/deployment/config.json')
            if config['workspace'] != plan['workspace'] or config['python'] != plan['python']:
                raise ValueError('Runtime configuration targets another workspace/Python; inspect it before retrying')
        return existing
    workspace = Path(plan['workspace'])
    if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
        raise ValueError('Refusing to adopt an existing workspace; use a fresh clone/workspace and import projects')
    for relative in ('.venv', '.tools/deployment/config.json'):
        if (root / relative).exists():
            raise ValueError(f'Unknown existing {relative}; preserve it and use a fresh clone')
    return None


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(descriptor, 'w') as output:
            json.dump(data, output, indent=2, allow_nan=False)
            output.write('\n')
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def run(command, *, root=ROOT, env=None):
    print('Running: ' + ' '.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=root, env=env, check=True)


def validate_pdk(root):
    from install_sky130 import REQUIRED
    root = Path(root).resolve()
    for relative in (*REQUIRED, 'libs.tech/combined/sky130.lib.spice'):
        if not (root / 'sky130A' / relative).is_file():
            raise ValueError(f'Missing prepared PDK file: {root / "sky130A" / relative}')
    marker = root / 'installation.json'
    if marker.is_file():
        metadata = json.loads(marker.read_text())
        return str(metadata.get('open_pdks_revision', 'prepared-local'))
    return 'prepared-local'


def find_netgen():
    for name in ('netgen-lvs', '/usr/lib/netgen/bin/netgen'):
        executable = shutil.which(name)
        if executable and os.access(executable, os.X_OK):
            return str(Path(executable).resolve())
    raise ValueError('LVS netgen is missing; install netgen-lvs (not the mesh generator)')


def validate_training(root=ROOT):
    from setup_training import MODEL_REVISION, MODEL_SHA256, PYTHON_VERSION
    training = root / '.tools/training'
    for relative in ('venv/bin/python', 'model/model.safetensors', 'model/config.json',
                     'model/tokenizer.json', 'model-source.json', 'runtime.json'):
        if not (training / relative).is_file():
            raise ValueError(f'Training installation is incomplete: missing {relative}')
    source = json.loads((training / 'model-source.json').read_text())
    with (training / 'model/model.safetensors').open('rb') as model:
        checksum = hashlib.file_digest(model, 'sha256').hexdigest()
    if source.get('revision') != MODEL_REVISION or checksum != MODEL_SHA256:
        raise ValueError('Training model differs from the pinned installation')
    run([training / 'venv/bin/python', '-c',
         'import json, platform, importlib.metadata as m, sys; '
         'from trl import GRPOTrainer, GRPOConfig; '
         'from transformers import AutoModelForCausalLM; '
         'r=json.load(open(sys.argv[1])); '
         'assert platform.python_version() == sys.argv[2] == r["python"]; '
         'assert all(m.version(k) == v for k,v in r["packages"].items())',
         training / 'runtime.json', PYTHON_VERSION], root=root)


def bootstrap_workspace(directory, settings):
    """A new workspace only; business settings follow the shared registry."""
    directory = Path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError('Workspace bootstrap requires an empty directory')
    from analog_factory.operations import InvocationContext
    from analog_factory.service import Workspace
    workspace = Workspace(directory)
    registry = workspace.operations
    try:
        current = registry.invoke('settings.get')
        if not current['ok']:
            raise ValueError(current['error']['message'])
        result = registry.invoke('settings.update', {'settings': settings},
            InvocationContext(principal='deployment-bootstrap', source='cli'),
            expected_version=current['version'])
        if not result['ok']:
            raise ValueError(result['error']['message'])
    finally:
        registry.close()
        workspace.agents.close()


def install(plan, root=ROOT):
    check_platform()
    # Do not launch or configure an existing live workspace from an offline tool.
    existing = check_destination(plan, root)
    from run_deployment import assert_port_free, workspace_lock
    active_config = plan
    if existing and 'configuration' in existing.get('phases', []):
        from run_deployment import load_config
        active_config = load_config(root / '.tools/deployment/config.json')
    assert_port_free(active_config['host'], active_config['port'])
    if active_config['desktop']:
        assert_port_free('127.0.0.1', active_config['desktop_port'])
    directory = root / '.tools/deployment'
    directory.mkdir(parents=True, exist_ok=True)
    # Lock outside workspace: bootstrap checks require an empty destination.
    existing_workspace_lock = (workspace_lock(plan['workspace'])
        if existing and 'workspace' in existing.get('phases', []) else contextlib.nullcontext())
    with workspace_lock(directory), existing_workspace_lock:
        existing = check_destination(plan, root)
        marker = directory / 'installation.json'
        record = existing or {'schema_version': 1, 'root': str(root),
            'workspace': plan['workspace'], 'plan': plan, 'phases': [], 'complete': False}
        atomic_json(marker, record)

        def phase(name, action):
            if name not in record['phases']:
                action()
                record['phases'].append(name)
                atomic_json(marker, record)

        def system_packages():
            if not plan['skip_system_packages']:
                prefix = [] if os.geteuid() == 0 else ['sudo']
                run([*prefix, 'apt-get', 'update'], root=root)
                run([*prefix, 'apt-get', 'install', '-y', *plan['apt_packages']], root=root)
        phase('system_packages', system_packages)

        def python_packages():
            if not (root / '.venv/bin/python').exists():
                run([sys.executable, '-m', 'venv', root / '.venv'], root=root)
            run([root / '.venv/bin/python', '-m', 'pip', 'install', '.[yaml]'], root=root)
        phase('python_packages', python_packages)
        if not (root / '.venv/bin/python').is_file():
            raise ValueError('Installed Python is missing; restore it or use a fresh clone')
        run([root / '.venv/bin/python', '-c',
             'import sys, analog_factory, yaml; assert sys.version_info >= (3, 12)'], root=root)

        config = {key: plan[key] for key in ('schema_version', 'workspace', 'python', 'host',
                                            'port', 'desktop', 'desktop_port')}
        config['extra_path'] = []
        settings = {'tools': {}}
        if plan['profile'] != 'core':
            from build_portable_eda import build
            native = shutil.which('ngspice')
            if native is None:
                raise ValueError('ngspice is missing; install the system EDA dependencies')
            prefix = root / '.tools/eda/portable'
            build(prefix=prefix, archives_dir=directory / 'downloads',
                  skip_download=plan['skip_download'], jobs=plan['jobs'], ngspice=native)
            settings['tools'].update({name: str(prefix / 'bin' / name) for name in ('magic', 'xschem')})
            settings['tools'].update({'ngspice': str(prefix / 'bin/ngspice-sky130'), 'netgen': find_netgen()})
            config['extra_path'].append(str(prefix / 'bin'))
            from install_sky130 import REVISION
            pdk = Path(plan['requested_pdk_root']) if plan['requested_pdk_root'] else root / '.tools/pdks/sky130' / REVISION
            if not plan['requested_pdk_root']:
                run([sys.executable, root / 'scripts/install_sky130.py', '--ngspice', native,
                     *(['--skip-download'] if plan['skip_download'] else [])], root=root)
            version = validate_pdk(pdk)
            config['pdk_root'] = str(pdk)
            settings.update(pdk_root=str(pdk), pdk_variant='sky130A', pdk_version=version)

        if plan['with_agents']:
            from setup_node import install as install_node
            from setup_agent_clis import install as install_agents
            node = install_node(directory=root / '.tools/node', archives_dir=directory / 'downloads',
                                skip_download=plan['skip_download'])
            agents = install_agents(directory=root / '.tools/agent-clis', node=node['node'])
            config['extra_path'] += [str(Path(node['node']).parent), str(root / '.tools/agent-clis/bin')]
            settings['tools'].update({name: agents['clis'][name]['path'] for name in ('codex', 'claude')})
        if plan['with_training']:
            phase('training', lambda: run([sys.executable, root / 'scripts/setup_training.py'], root=root))
            validate_training(root)
        if plan['desktop']:
            from run_deployment import desktop_dependencies, child_environment
            desktop_dependencies(child_environment(validate_config(config)))
        phase('workspace', lambda: bootstrap_workspace(Path(plan['workspace']), settings))
        # Repeat installs never overwrite edited runtime configuration or settings.
        phase('configuration', lambda: atomic_json(directory / 'config.json', validate_config(config)))
        record['complete'] = True
        atomic_json(marker, record)
    print(f'Installed. Start: python3 scripts/run_deployment.py --config {directory / "config.json"}')
    return record


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        plan = make_plan(args)
        if args.dry_run:
            print(json.dumps(plan, indent=2))
            return 0
        install(plan)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'Installation failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
