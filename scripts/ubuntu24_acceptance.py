#!/usr/bin/env python3
"""Acceptance runner inside the pinned container; called by validate_ubuntu24.py."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from analog_factory.operations import InvocationContext
from analog_factory.service import Workspace
from validate_ota5 import validate


def main():
    output = Path('/evidence')
    os_release = Path('/etc/os-release').read_text()
    if 'VERSION_ID="24.04"' not in os_release:
        raise ValueError('This acceptance run requires Ubuntu 24.04')
    report = {'status': 'running', 'os_release': os_release, 'python': sys.version, 'checks': {}}
    record = output / 'ubuntu24-acceptance.json'
    record.write_text(json.dumps(report, indent=2) + '\n')
    shutil.copy2('/opt/ubuntu24-packages.txt', output / 'packages.txt')
    shutil.copy2('/opt/eda/sources.json', output / 'eda-sources.json')
    commands = {
        'python_contracts': [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py'],
        'waveform_contracts': ['node', 'tests/waveform-view.test.cjs'],
        'provider_contracts': ['node', 'tests/provider-fields.test.cjs'],
        'frontend_state_contracts': ['node', 'tests/operations-state.test.cjs'],
        'editor_recovery_contracts': ['node', 'tests/editor-recovery-contracts.cjs'],
        'editor_recovery_variants': ['node', 'tests/editor-recovery-variants.cjs'],
        'source_patch_contracts': ['node', 'tests/graph-source-patch.test.cjs'],
        'native_target_races': ['node', 'tests/native-canvas-races.cjs'],
        'training_budget_controls': ['node', 'tests/training-controls-budget.cjs'],
        'agent_cli_controls': ['node', 'tests/agent-cli-controls.cjs'],
        'registry_contracts': [sys.executable, 'scripts/check_operations.py'],
    }
    for name, command in commands.items():
        print(f'Checking {name}', flush=True)
        with (output / f'{name}.log').open('w') as log:
            result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        report['checks'][name] = {'exit_code': result.returncode, 'log': f'{name}.log'}
    config = {'tools': {'magic': '/opt/eda/bin/magic', 'xschem': '/opt/eda/bin/xschem',
                         'ngspice': '/opt/eda/bin/ngspice-sky130',
                         'netgen': shutil.which('netgen-lvs') or shutil.which('netgen')},
              'pdk_root': '/pdk', 'pdk_variant': 'sky130A', 'pdk_version': '1689ac3f2dc763876eaf967227c7dfe831b031ae'}
    (output / 'settings.json').write_text(json.dumps(config, indent=2) + '\n')
    print('Replaying actual OTA reference and negative controls', flush=True)
    try:
        reference = validate(output / 'ota5', config)
        report['checks']['ota5'] = {'status': reference['status'], 'report': 'ota5/ota5-validation.json',
                                   'pre': reference['pre'], 'post': reference['post']}
        workspace = Workspace(output / 'gui')
        registry = workspace.operations
        context = InvocationContext(principal='ubuntu24-acceptance', source='cli')
        def invoke(name, params):
            result = registry.invoke(name, params, context, expected_version=registry.version(params.get('project_id')))
            if not result['ok']:
                raise RuntimeError(f'{name}: {result["error"]}')
            return result['data']
        invoke('settings.update', {'settings': config})
        project = invoke('project.create', {'name': 'Native GUI acceptance', 'template': 'sky130'})
        report['checks']['native_gui'] = []
        for kind, relative in (('xschem', 'schematics/ota5.sch'), ('magic', 'layout/ota5.mag')):
            invoke('file.write', {'project_id': project['id'], 'path': relative,
                                 'content': (ROOT / 'examples/ota5' / relative).read_text()})
            options = {'schematic' if kind == 'xschem' else 'layout': relative}
            run = invoke('run.start', {'project_id': project['id'], 'kind': kind, 'options': options, 'timeout': 30})
            time.sleep(3)
            run = invoke('run.get', {'project_id': project['id'], 'run_id': run['id']})
            if run['status'] != 'running':
                raise RuntimeError(f'{kind} GUI did not remain active: {run}')
            invoke('run.cancel', {'project_id': project['id'], 'run_id': run['id']})
            deadline = time.monotonic() + 10
            while run['status'] in ('queued', 'running') and time.monotonic() < deadline:
                time.sleep(.1)
                run = invoke('run.get', {'project_id': project['id'], 'run_id': run['id']})
            if run['status'] != 'cancelled':
                raise RuntimeError(f'{kind} GUI cancellation not acknowledged: {run["status"]}')
            report['checks']['native_gui'].append({'tool': kind, 'run_id': run['id'], 'status': run['status']})
        registry.close()
        workspace.agents.close()
        print('Checking actual managed native geometry, save receipts and isolation', flush=True)
        native_output = output / 'native-canvas'
        with (output / 'native-canvas.log').open('w') as log:
            native = subprocess.run(
                [sys.executable, 'scripts/validate_native_canvas.py', '--output', str(native_output), '--pdk-root', '/pdk'],
                cwd=ROOT, env={**os.environ, 'HOME': str(output / 'native-home')},
                stdout=log, stderr=subprocess.STDOUT, timeout=180)
        report['checks']['native_canvas'] = {'exit_code': native.returncode,
            'log': 'native-canvas.log', 'report': 'native-canvas/native-canvas-validation.json'}
    except Exception as error:
        report['error'] = str(error)
    report['status'] = 'pass' if not report.get('error') and all(
        value.get('exit_code', 0) == 0 for value in report['checks'].values() if isinstance(value, dict)) else 'fail'
    report['limits'] = 'Xvfb validates owned GUI lifecycle and bounded actual native semantic edits/saves. Browser/noVNC interaction, GPU training, silicon and distributed-RC signoff are separate acceptance scopes.'
    record.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'error': report.get('error'), 'report': str(record)}), flush=True)
    raise SystemExit(0 if report['status'] == 'pass' else 1)


if __name__ == '__main__':
    main()
