#!/usr/bin/env python3
"""Render or explicitly manage an Analog Factory systemd user service."""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile

# Also support loading this helper using importlib outside the scripts directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_deployment import ROOT, absolute_path, load_config

MARKER = '# Managed by Analog Factory deployment helper v1\n'


def unit_name(workspace):
    digest = hashlib.sha256(str(Path(workspace).resolve()).encode()).hexdigest()[:12]
    return f'analog-factory-{digest}.service'


def unit_quote(value, executable=False):
    value = absolute_path(str(value), 'systemd path')
    value = value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    if executable:
        value = value.replace('$', '$$')
    return '"' + value + '"'


def render_unit(config, config_path, root=ROOT):
    # systemd rejects literal quotes in an executable name even after parsing a
    # quoted token. env execs the configured interpreter without a shell.
    command = ' '.join(['/usr/bin/env', unit_quote(config['python'], executable=True),
                        unit_quote(root / 'scripts/run_deployment.py', executable=True),
                        '--config', unit_quote(config_path, executable=True)])
    # WorkingDirectory is a literal path, not an ExecStart-style quoted word.
    # A trailing slash preserves trailing spaces and backslashes in the name.
    working_directory = absolute_path(str(root), 'systemd path').replace('%', '%%') + '/'
    return (MARKER + '[Unit]\nDescription=Analog Factory workspace\n\n'
            '[Service]\nType=simple\n'
            f'WorkingDirectory={working_directory}\nExecStart={command}\n'
            'KillSignal=SIGINT\nKillMode=control-group\nTimeoutStopSec=15\n'
            'Restart=on-failure\nRestartSec=3\nUMask=0077\n\n'
            '[Install]\nWantedBy=default.target\n')


def require_owned(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'No owned unit at {path}; run install first (foreign symlinks are refused)')
    with path.open() as source:
        if source.readline() != MARKER:
            raise ValueError(f'Refusing to change foreign unit: {path}')


def install_unit(path, content):
    if path.exists() or path.is_symlink():
        require_owned(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as target:
            target.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def systemctl(*arguments):
    return subprocess.run(['systemctl', '--user', *arguments], check=False).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('print', 'install', 'enable', 'disable',
                                           'start', 'stop', 'status', 'restart', 'uninstall'))
    parser.add_argument('--config', required=True, type=Path, help='Deployment JSON config')
    args = parser.parse_args(argv)
    try:
        if not sys.platform.startswith('linux'):
            raise ValueError('systemd user services require Linux')
        config_path = args.config.resolve()
        config = load_config(config_path)
        name = unit_name(config['workspace'])
        if args.command == 'print':
            print(render_unit(config, config_path), end='')
            return 0
        config_home = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
        absolute_path(str(config_home), 'XDG_CONFIG_HOME')
        target = config_home / 'systemd/user' / name
        if args.command == 'install':
            install_unit(target, render_unit(config, config_path))
            print(f'Installed {target}; start explicitly with this helper or systemctl --user start {name}')
            return systemctl('daemon-reload')
        require_owned(target)
        if args.command == 'uninstall':
            status = systemctl('disable', '--now', name)
            if status:
                return status
            target.unlink()
            return systemctl('daemon-reload')
        return systemctl(args.command, name)
    except (OSError, ValueError) as error:
        print(f'Service command failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
