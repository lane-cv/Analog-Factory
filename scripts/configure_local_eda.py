#!/usr/bin/env python3
"""Configure Ubuntu EDA packages in .tools/eda/packages without administrator access."""
import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from analog_factory.service import Workspace
from analog_factory.storage import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=REPO / '.workspace')
    args = parser.parse_args()
    installation = REPO / '.tools/eda'
    root = installation / 'root'
    packages = sorted((installation / 'packages').glob('*.deb'))
    if not packages:
        parser.error('Download Ubuntu EDA packages into .tools/eda/packages first')
    root.mkdir(parents=True, exist_ok=True)
    for package in packages:
        subprocess.run(['dpkg-deb', '--extract', str(package), str(root)], check=True)
    magic = root / 'usr/lib/x86_64-linux-gnu/magic/tcl'
    netgen = root / 'usr/lib/netgen/tcl'
    for directory in (magic, netgen):
        if not directory.is_dir():
            parser.error(f'Missing runtime directory: {directory}')
        # Relocate only extracted Tcl startup files, preserving original archives.
        for script in directory.glob('*.tcl'):
            text = script.read_text()
            text = text.replace('/usr/lib/x86_64-linux-gnu/magic', str(root / 'usr/lib/x86_64-linux-gnu/magic'))
            text = text.replace('/usr/lib/netgen', str(root / 'usr/lib/netgen'))
            script.write_text(text)
    (installation / 'config/xschem').mkdir(parents=True, exist_ok=True)
    for tool in ('magic', 'netgen'):
        tail = 'usr lib x86_64-linux-gnu magic tcl magic.tcl' if tool == 'magic' else 'usr lib netgen tcl netgen.tcl'
        (installation / f'{tool}-startup.tcl').write_text('source [file join $env(AF_EDA_ROOT) root ' + tail + ']\n')
    common = '''#!/bin/bash
set -euo pipefail
AF_EDA_ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
export AF_EDA_ROOT
export LD_LIBRARY_PATH="$AF_EDA_ROOT/root/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export TK_LIBRARY="$AF_EDA_ROOT/root/usr/share/tcltk/tk8.6"
'''
    commands = {
        'xschem': '''exec "$AF_EDA_ROOT/root/usr/bin/xschem" --preinit 'set USER_CONF_DIR [file join $env(AF_EDA_ROOT) config xschem]; set XSCHEM_SHAREDIR [file join $env(AF_EDA_ROOT) root usr share xschem]; set XSCHEM_LIBRARY_PATH "$XSCHEM_SHAREDIR/xschem_library:$XSCHEM_SHAREDIR/xschem_library/devices"' "$@"
''',
        'magic': '''export CAD_ROOT="$AF_EDA_ROOT/root/usr/lib/x86_64-linux-gnu"
for argument in "$@"; do
  case "$argument" in
    --version|--prefix|-dnull) exec /usr/bin/tclsh8.6 "$AF_EDA_ROOT/magic-startup.tcl" "$@" ;;
  esac
done
exec "$AF_EDA_ROOT/root/usr/bin/wish8.6" "$AF_EDA_ROOT/magic-startup.tcl" "$@"
''',
        'netgen': '''export CAD_ROOT="$AF_EDA_ROOT/root/usr/lib"
exec /usr/bin/tclsh8.6 "$AF_EDA_ROOT/netgen-startup.tcl" "$@"
''',
    }
    paths = {}
    (installation / 'bin').mkdir(exist_ok=True)
    for name, command in commands.items():
        wrapper = installation / 'bin' / name
        wrapper.write_text(common + command)
        wrapper.chmod(0o755)
        paths[name] = str(wrapper)
    probes = {}
    for name, flags in [('xschem', ['--version']), ('magic', ['--version']), ('netgen', ['-batch'])]:
        probe = subprocess.run([paths[name], *flags], input='', text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
        if probe.returncode != 0:
            raise RuntimeError(f'{name} startup failed (exit {probe.returncode}): {probe.stdout}')
        probes[name] = probe.stdout.strip()
    (installation / 'env.sh').write_text('export PATH=' + shlex.quote(str(installation / 'bin')) + ':"$PATH"\n')
    workspace = Workspace(args.workspace)
    workspace.save_settings({'tools': {**workspace.settings().get('tools', {}), **paths}})
    packages_manifest = []
    for package in packages:
        fields = subprocess.check_output(['dpkg-deb', '-f', str(package), 'Package', 'Version', 'Architecture'], text=True).strip()
        packages_manifest.append({'file': package.name, 'metadata': fields,
                                  'sha256': hashlib.sha256(package.read_bytes()).hexdigest()})
    atomic_json(installation / 'installation.json', {'configured_at': datetime.now(timezone.utc).isoformat(),
        'source': 'Ubuntu 26.04 APT repositories', 'packages': packages_manifest,
        'tools': paths, 'version_probes': probes,
        'relocation': 'Tcl load paths and launch environment; original archives retained'})
    print(json.dumps({'workspace': str(workspace.root), 'tools': paths}, indent=2))


if __name__ == '__main__':
    main()
