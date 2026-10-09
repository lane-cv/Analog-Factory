#!/usr/bin/env python3
"""Replay the real five-MOS SKY130 reference through workspace operations.

No tool availability, process exit, or synthetic result is treated as verification.
Artifacts remain under the requested dedicated workspace, never the user's project.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from analog_factory.service import Workspace
from analog_factory.operations import InvocationContext

ASSETS = ROOT / 'examples/ota5'
PINS = ('inp', 'inn', 'bias', 'vdd', 'vss', 'out')
TERMINAL = {'succeeded', 'failed', 'cancelled', 'timed_out', 'interrupted', 'unavailable'}


def configuration(path):
    """Use configured tools and PDK; no machine-specific model paths in assets."""
    settings = json.loads(Path(path).read_text())
    return {key: settings[key] for key in ('tools', 'pdk_root', 'pdk_variant', 'pdk_version') if key in settings}


def canonical_subcircuit(text):
    """Normalize Xschem's commented top wrapper and port order, preserving devices."""
    lines = []
    headers = 0
    for line in text.splitlines():
        match = re.match(r'^(?:\*\*)?\.subckt\s+ota5\s+(.+)$', line, re.I)
        if match:
            pins = match.group(1).split()
            if len(pins) != len(PINS) or set(pins) != set(PINS):
                raise ValueError(f'Unexpected OTA interface: {pins}')
            headers += 1
            lines.append('.subckt ota5 ' + ' '.join(PINS))
        elif re.match(r'^(?:\*\*)?\.ends\b', line, re.I):
            lines.append('.ends ota5')
        elif not re.match(r'^\.end\s*$', line, re.I):
            lines.append(line)
    if headers != 1:
        raise ValueError('Exactly one ota5 subcircuit wrapper is required')
    devices = [line for line in lines if re.match(r'^X\S+\s', line, re.I)]
    if len(devices) != 5 or any(not re.search(r'\bsky130_fd_pr__(?:n|p)fet_01v8\b', line) for line in devices):
        raise ValueError(f'Expected five MOS devices, found {len(devices)}')
    return '\n'.join(lines) + '\n'


def without_capacitors(text):
    """Derive the connectivity-only LVS view; retain unmodified PEX separately."""
    lines, skipping, count = [], False, 0
    for line in text.splitlines():
        if re.match(r'^\s*C\S*\s', line, re.I):
            skipping = True
            count += 1
        elif line.lstrip().startswith('+') and skipping:
            continue
        else:
            skipping = False
            lines.append(line)
    if not count:
        raise ValueError('Extraction contains no parasitic capacitors')
    return '\n'.join(lines) + '\n', count


def differential_gain_db(raw):
    """Independently check the 1 Hz complex output / (positive minus negative input)."""
    columns = {item['name'].lower(): index for index, item in enumerate(raw['variables'])}
    required = ('frequency', 'v(inp)', 'v(inn)', 'v(out)')
    if any(name not in columns for name in required) or not raw.get('complex_points'):
        raise ValueError('AC acceptance requires complex input and output waveforms')
    row = min(raw['complex_points'], key=lambda values: abs(values[columns['frequency']][0] - 1))
    if not math.isclose(row[columns['frequency']][0], 1, rel_tol=1e-9):
        raise ValueError('The AC waveform does not contain the 1 Hz acceptance point')
    positive = complex(*row[columns['v(inp)']])
    negative = complex(*row[columns['v(inn)']])
    differential = positive - negative
    if not math.isclose(abs(differential), 1, rel_tol=1e-9):
        raise ValueError('The testbench must apply exactly 1 V differential AC input')
    gain = abs(complex(*row[columns['v(out)']]) / differential)
    if not math.isfinite(gain) or gain <= 0:
        raise ValueError('Differential gain is not finite and positive')
    return 20 * math.log10(gain)


def validate(workspace_path, config, *, template=False):
    workspace_path = Path(workspace_path).resolve()
    if workspace_path == (ROOT / '.workspace').resolve():
        raise ValueError('Use a dedicated validation workspace, not .workspace')
    model = Path(config.get('pdk_root', '')) / config.get('pdk_variant', 'sky130A') / 'libs.tech/combined/sky130.lib.spice'
    if not model.is_file():
        raise FileNotFoundError(f'The configured scalable SKY130 model library is missing: {model}')
    workspace = Workspace(workspace_path)
    registry = workspace.operations
    context = InvocationContext(principal='ota5-reference-validation', source='cli')
    calls = []
    report = {'schema_version': 1, 'status': 'running', 'workspace': str(workspace_path),
              'conditions': {'vdd_v': 1.8, 'input_common_mode_v': 0.9, 'bias_v': 0.65,
                             'load_f': 1e-12, 'temperature_c': 27, 'corner': 'tt',
                             'differential_ac_v': 1, 'gain_frequency_hz': 1},
              'coverage': 'Magic local DRC; Netgen device/connectivity LVS; capacitance-inclusive extraction. '
                          'Distributed resistance, density/antenna signoff, mismatch and silicon validation are outside this reference.',
              'operations': calls}
    project_id = None

    def invoke(name, parameters):
        pid = parameters.get('project_id')
        envelope = registry.invoke(name, parameters, context, expected_version=registry.version(pid))
        calls.append({'operation_id': name, 'trace_id': envelope['trace_id'], 'ok': envelope['ok'],
                      'task_id': envelope.get('task_id')})
        if not envelope['ok']:
            raise RuntimeError(f'{name}: {envelope["error"]}')
        return envelope['data']

    def write(path, content):
        return invoke('file.write', {'project_id': project_id, 'path': path, 'content': content})

    def run(kind, **options):
        options.setdefault('conditions', report['conditions'])
        record = invoke('run.start', {'project_id': project_id, 'kind': kind,
                                     'options': options, 'timeout': 180})
        deadline = time.monotonic() + 200
        while record['status'] not in TERMINAL:
            if time.monotonic() > deadline:
                invoke('run.cancel', {'project_id': project_id, 'run_id': record['id']})
                raise TimeoutError(f'{kind} reference run exceeded deadline')
            time.sleep(0.1)
            # Poll through the same public registry operation used by UI/MCP.
            envelope = registry.invoke('run.get', {'project_id': project_id, 'run_id': record['id']}, context)
            record = envelope.get('data')
            if not record:
                raise RuntimeError(f'Cannot read {kind} run: {envelope["error"]}')
        if record['status'] != 'succeeded':
            raise RuntimeError(f'{kind} run {record["id"]}: {record.get("error") or record["status"]}')
        return record

    def output(record, name):
        return workspace.projects.path(project_id) / '.af/runs' / record['id'] / 'output' / name

    def require_verification(record, verdict):
        if record.get('verification', {}).get('status') != verdict:
            raise AssertionError(f'{record["kind"]} expected {verdict}: {record.get("verification")}')

    try:
        invoke('settings.update', {'settings': config})
        project = invoke('project.create', {'name': 'Five-transistor OTA acceptance', 'template': 'ota5' if template else 'sky130'})
        project_id = project['id']
        report['project_id'] = project_id
        report['project_path'] = str(workspace.projects.path(project_id))
        invoke('project.configure', {'project_id': project_id, 'config': config})
        if not template:
            write('specifications.json', (ASSETS / 'specifications.json').read_text())
        for directory in ('schematics', 'layout', 'netlists', 'testbenches'):
            for path in ([] if template else sorted((ASSETS / directory).glob('*'))):
                if path.suffix not in ('.sch', '.mag', '.spice', '.cir'):
                    continue
                content = path.read_text().replace('@MODEL_LIBRARY@', str(model.resolve()))
                write(path.relative_to(ASSETS).as_posix(), content)

        report['template'] = template
        report['xschem'] = run('xschem', **({'netlist_only':True} if template else {'schematic':'schematics/ota5.sch','netlist_only':True}))
        schematic = canonical_subcircuit(output(report['xschem'], 'ota5.spice').read_text())
        if template:
            report['schematic_publication'] = invoke('reference.derive_netlists', {'project_id':project_id,'run_id':report['xschem']['id']})
        else:
            write('netlists/schematic.spice', schematic)
        report['drc'] = run('drc', **({} if template else {'layout':'layout/ota5.mag'}))
        require_verification(report['drc'], 'pass')
        report['extract'] = run('extract', **({} if template else {'layout':'layout/ota5.mag'}))
        pex = canonical_subcircuit(output(report['extract'], 'extracted.spice').read_text())
        lvs, capacitor_count = without_capacitors(pex)
        report['parasitic_capacitors'] = capacitor_count
        if template:
            report['extraction_publication'] = invoke('reference.derive_netlists', {'project_id':project_id,'run_id':report['extract']['id']})
        else:
            write('netlists/extracted.spice', pex)
            write('netlists/extracted-lvs.spice', lvs)
        report['derivation'] = {
            'schematic': 'Xschem top wrapper enabled and six named ports put in documented order; all device records unchanged.',
            'pex': 'Magic six named ports put in documented order; all devices and capacitances unchanged.',
            'lvs': f'Remove only {capacitor_count} C records from PEX; keep MOS records, terminals and geometry unchanged.',
            'pex_sha256': hashlib.sha256(pex.encode()).hexdigest(),
            'lvs_sha256': hashlib.sha256(lvs.encode()).hexdigest()}
        report['lvs'] = run('lvs', **({} if template else {'layout_netlist':'netlists/extracted-lvs.spice','schematic_netlist':'netlists/schematic.spice','cell':'ota5'}))
        require_verification(report['lvs'], 'pass')
        # A second independent electrical reference guards against copying a
        # schematic connectivity mistake into layout acceptance.
        report['independent_lvs'] = run('lvs', layout_netlist='netlists/extracted-lvs.spice',
                                        schematic_netlist='netlists/ota5.spice', cell='ota5')
        require_verification(report['independent_lvs'], 'pass')
        wrong_width, substitutions = re.subn(r'\bW=4\b', 'W=5', schematic, count=1, flags=re.I)
        if substitutions != 1:
            raise ValueError('Cannot construct independent width-error negative control')
        write('netlists/wrong-width.spice', wrong_width)
        report['negative_lvs'] = run('lvs', layout_netlist='netlists/extracted-lvs.spice',
                                     schematic_netlist='netlists/wrong-width.spice', cell='ota5')
        require_verification(report['negative_lvs'], 'fail')
        source = (ASSETS / 'layout/ota5.mag').read_text()
        defective = source.replace('<< labels >>', '<< metal1 >>\nrect -1000 -1200 -998 -1198\n<< labels >>', 1)
        if defective == source:
            raise ValueError('Cannot construct minimum-width DRC negative control')
        write('layout/ota5-drc-negative.mag', defective)
        report['negative_drc'] = run('drc', layout='layout/ota5-drc-negative.mag')
        require_verification(report['negative_drc'], 'fail')

        for stage in ('pre', 'post'):
            record = run('simulate', **({} if template and stage == 'pre' else {'netlist':f'testbenches/{stage}.cir'}))
            metrics = record['metrics']
            gain = metrics.get('gain_db')
            unity = metrics.get('unity_gain_hz')
            if not isinstance(gain, (float, int)) or not math.isfinite(gain) or gain <= 30:
                raise AssertionError(f'{stage}-layout differential gain must exceed 30 dB: {gain}')
            if not isinstance(unity, (float, int)) or not math.isfinite(unity) or unity <= 0:
                raise AssertionError(f'{stage}-layout unity-gain frequency missing: {unity}')
            raw = invoke('run.waveforms', {'project_id': project_id, 'run_id': record['id']})
            checked_gain = differential_gain_db(raw)
            if not math.isclose(gain, checked_gain, abs_tol=0.001):
                raise AssertionError(f'{stage}-layout measured and complex-waveform gains disagree')
            report[stage] = {'run': record, 'gain_db': gain, 'waveform_gain_db': checked_gain, 'unity_gain_hz': unity}
            deck = (ASSETS / f'testbenches/{stage}.cir').read_text().replace('@MODEL_LIBRARY@', str(model.resolve()))
            deck = re.sub(r'^\.ac .*$', '.dc VP 0.899 0.901 0.001', deck, flags=re.M)
            deck = re.sub(r'^\.meas ac .*\n', '', deck, flags=re.M)
            deck = deck.replace('.end\n', '.meas dc output_dc_v find v(out) at=0.9\n'
                                          '.meas dc supply_current_a find i(VDD) at=0.9\n.end\n')
            write(f'testbenches/{stage}-bias.cir', deck)
            bias_record = run('simulate', netlist=f'testbenches/{stage}-bias.cir')
            bias_metrics = bias_record['metrics']
            if not (0.2 < bias_metrics.get('output_dc_v', math.nan) < 1.6):
                raise AssertionError(f'{stage}-layout bias point is outside useful headroom: {bias_metrics}')
            if not (-100e-6 < bias_metrics.get('supply_current_a', math.nan) < 0):
                raise AssertionError(f'{stage}-layout supply current is invalid: {bias_metrics}')
            report[stage].update(bias_run=bias_record, output_dc_v=bias_metrics['output_dc_v'],
                                 supply_current_a=bias_metrics['supply_current_a'])
        report['gain_delta_db'] = report['post']['gain_db'] - report['pre']['gain_db']
        report['status'] = 'pass'
    except Exception as error:
        report['status'] = 'fail'
        report['error'] = str(error)
        raise
    finally:
        workspace_path.mkdir(parents=True, exist_ok=True)
        (workspace_path / 'ota5-validation.json').write_text(json.dumps(report, indent=2) + '\n')
        registry.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings', type=Path, default=ROOT / '.validation/settings.json')
    parser.add_argument('--workspace', type=Path, default=ROOT / '.validation/ota5-reference')
    parser.add_argument('--template', action='store_true', help='Create the packaged OTA template and use its shared source defaults/publication operation')
    args = parser.parse_args()
    try:
        report = validate(args.workspace, configuration(args.settings), template=args.template)
    except Exception as error:
        print(f'OTA5 acceptance failed: {error}', file=sys.stderr)
        return 1
    print(json.dumps({'status': report['status'], 'report': str(args.workspace / 'ota5-validation.json'),
                      'pre_gain_db': report['pre']['gain_db'], 'post_gain_db': report['post']['gain_db'],
                      'parasitic_capacitors': report['parasitic_capacitors']}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
