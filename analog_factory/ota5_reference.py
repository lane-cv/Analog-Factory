"""Packaged native five-MOS reference and explicit actual-output derivation."""
from __future__ import annotations

import hashlib
from importlib.resources import files
import json
from pathlib import Path
import re

from .storage import confined

PINS = ('inp', 'inn', 'bias', 'vdd', 'vss', 'out')
DESIGN = {'family': 'ota5', 'cell': 'ota5', 'schematic': 'schematics/ota5.sch',
          'layout': 'layout/ota5.mag', 'netlist': 'testbenches/pre.cir',
          'post_netlist': 'testbenches/post.cir', 'schematic_netlist': 'netlists/schematic.spice',
          'layout_netlist': 'netlists/extracted-lvs.spice', 'ports': list(PINS)}
ASSETS = ('specifications.json', 'schematics/ota5.sch', 'layout/ota5.mag',
          'netlists/ota5.spice', 'testbenches/pre.cir', 'testbenches/post.cir')


def materialize(config):
    config = config or {}
    if not config.get('pdk_root') or config.get('pdk_variant', 'sky130A') != 'sky130A':
        raise ValueError('The OTA reference requires a configured prepared sky130A PDK')
    pdk = Path(config['pdk_root']).expanduser().resolve()/'sky130A'
    required = ('libs.tech/combined/sky130.lib.spice', 'libs.tech/xschem/xschemrc',
                'libs.tech/magic/sky130A.magicrc', 'libs.tech/netgen/sky130A_setup.tcl')
    missing = [name for name in required if not (pdk/name).is_file()]
    if missing:
        raise ValueError('The OTA reference requires prepared PDK assets: '+', '.join(missing))
    model = str(pdk/required[0])
    if any(character in model for character in '\r\n\0"'):
        raise ValueError('PDK model path cannot contain quotes or control characters')
    assets = files('analog_factory').joinpath('assets/ota5')
    result = {name: assets.joinpath(name).read_text().replace('@MODEL_LIBRARY@', model) for name in ASSETS}
    result['netlists/schematic.spice'] = ('* Initial hand-authored electrical source matching the supplied schematic.\n'
        '* Run Xschem netlisting and publish its real output after schematic edits.\n' + result['netlists/ota5.spice'])
    result['reports/README.txt'] = '''Five-transistor OTA native reference; no run results are supplied.
1. Run pre-layout simulation from testbenches/pre.cir.
2. Edit schematics/ota5.sch in Xschem, netlist it, and publish the actual output.
3. Edit layout/ota5.mag in Magic, then run DRC and extraction.
4. Publish the actual extraction to create PEX and connectivity-only LVS sources.
5. Run LVS (cell ota5), then simulate testbenches/post.cir.
The independent netlists/ota5.spice remains the original five-device source.
Model library paths were materialized from workspace PDK configuration at creation.
External bias is a testbench voltage source, not a sixth MOS device.
Capacitance extraction does not establish distributed-RC or foundry signoff.
'''
    return result


def canonical_subcircuit(text):
    lines, headers = [], 0
    for line in text.splitlines():
        match = re.match(r'^(?:\*\*)?\.subckt\s+ota5\s+(.+)$', line, re.I)
        if match:
            pins = match.group(1).split()
            if len(pins) != len(PINS) or set(pins) != set(PINS):
                raise ValueError('Unexpected six-pin OTA interface')
            headers += 1
            lines.append('.subckt ota5 ' + ' '.join(PINS))
        elif re.match(r'^(?:\*\*)?\.ends\b', line, re.I):
            lines.append('.ends ota5')
        elif not re.match(r'^\.end\s*$', line, re.I):
            lines.append(line)
    devices = [line for line in lines if re.match(r'^\s*[XM]\S+\s', line, re.I)]
    if headers != 1 or len(devices) != 5 or any(not re.match(r'^\s*X', line, re.I) or not re.search(r'\bsky130_fd_pr__(?:n|p)fet_01v8\b', line) for line in devices):
        raise ValueError('The reference output must contain exactly five SKY130 MOS devices and one OTA wrapper')
    return '\n'.join(lines)+'\n'


def without_capacitors(text):
    lines, skipping, count = [], False, 0
    for line in text.splitlines():
        if re.match(r'^\s*C\S*\s', line, re.I):
            skipping, count = True, count+1
        elif line.lstrip().startswith('+') and skipping:
            continue
        else:
            skipping = False
            lines.append(line)
    if not count:
        raise ValueError('Actual extraction contains no parasitic capacitors')
    return '\n'.join(lines)+'\n', count


def derive_netlists(workspace, project_id, run_id):
    project = workspace.projects.get(project_id)
    if project.get('template') != 'ota5':
        raise ValueError('This derivation is scoped to the native five-MOS OTA reference')
    run_root = workspace.run_root(project_id, run_id)
    record = workspace.runner.get(run_root, run_id)
    if (record['kind'] not in ('xschem', 'extract') or record['status'] != 'succeeded'
            or record.get('imported_evidence') or not record.get('owner_identity') or record.get('stale')):
        raise ValueError('Publish requires a current successful local Xschem netlist or Magic extraction')
    key = 'schematic' if record['kind'] == 'xschem' else 'layout'
    if record.get('metadata', {}).get('sources', {}).get(key) != DESIGN[key]:
        raise ValueError('This run did not use the reference native source')
    filename = 'ota5.spice' if record['kind'] == 'xschem' else 'extracted.spice'
    path = confined(run_root, f'.af/runs/{run_id}/output/{filename}', hidden=True)
    if path.stat().st_size > 2*1024**2:
        raise ValueError('Reference netlist exceeds the bounded text limit')
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if record.get('artifact_hashes', {}).get(filename) != digest:
        raise ValueError('Actual tool output changed after the run completed')
    text = canonical_subcircuit(content.decode())
    outputs, capacitor_count = {}, 0
    if record['kind'] == 'xschem':
        outputs['netlists/schematic.spice'] = text
    else:
        lvs, capacitor_count = without_capacitors(text)
        outputs.update({'netlists/extracted.spice': text, 'netlists/extracted-lvs.spice': lvs})
    provenance = {'run_id': run_id, 'kind': record['kind'], 'artifact': filename,
                  'artifact_sha256': digest, 'parasitic_capacitors': capacitor_count,
                  'derivation': 'Canonical six named ports; device records preserved. LVS view removes only capacitance records.',
                  'outputs': {name: hashlib.sha256(value.encode()).hexdigest() for name, value in outputs.items()}}
    report_path = f'reports/ota5-{record["kind"]}-derivation.json'
    for name in [*outputs, report_path]:
        target = confined(workspace.projects.path(project_id), name)
        if target.exists() and not target.is_file():
            raise ValueError('Derived netlist destination must be a regular project file')
    # All content and destinations were validated before the first versioned write.
    for name, value in outputs.items():
        workspace.projects.write(project_id, name, value)
    workspace.projects.write(project_id, report_path, json.dumps(provenance, indent=2)+'\n')
    return provenance
