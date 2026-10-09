"""Independent electrical scoring for bounded design-agent acceptance.

This scorer does not establish live-model provenance: only the registry driver
can do that. It deliberately ignores every natural-language success claim.
"""
import hashlib
import math
import re

DESIGN_PATH = 'netlists/agent.spice'
AC_PATH = 'testbenches/agent-ac.cir'
DC_PATH = 'testbenches/agent-dc.cir'
INITIAL_SOURCE = '* Agent must design five MOS devices; dimensions in um.\n.subckt ota5 inp inn bias vdd vss out\n.ends ota5\n'


def validate_limits(max_actions, max_runs, runtime):
    if type(max_actions) is not int or not 1 <= max_actions <= 100:
        raise ValueError('max_actions must be an integer from 1 to 100')
    if type(max_runs) is not int or not 1 <= max_runs <= 20:
        raise ValueError('max_runs must be an integer from 1 to 20')
    if type(runtime) not in (int, float) or not math.isfinite(runtime) or not 1 <= runtime <= 900:
        raise ValueError('runtime must be finite and between 1 and 900 seconds')


def validate_netlist(source):
    """Permit only a five-device OTA subcircuit, preventing deck injection."""
    lines = [line.strip() for line in source.splitlines() if line.strip() and not line.lstrip().startswith('*')]
    if len(lines) != 7 or lines[0].lower() != '.subckt ota5 inp inn bias vdd vss out' or lines[-1].lower() != '.ends ota5':
        raise ValueError('Expected exactly five MOS devices inside the specified ota5 interface')
    names, models = set(), []
    nodes = {'inp', 'inn', 'bias', 'vdd', 'vss', 'out', 'tail', 'nmirror'}
    for line in lines[1:-1]:
        parts = line.lower().split()
        if len(parts) != 8 or not re.fullmatch(r'x[\w]+', parts[0]) or parts[0] in names:
            raise ValueError('Five unique X devices with four terminals, model, l and w required')
        names.add(parts[0])
        if set(parts[1:5]) - nodes or not re.fullmatch(r'sky130_fd_pr__[np]fet_01v8', parts[5]):
            raise ValueError('Only the public SKY130 MOS models and OTA nodes are allowed')
        models.append(parts[5])
        parameters = {}
        for parameter in parts[6:]:
            match = re.fullmatch(r'([lw])=([0-9]+(?:\.[0-9]*)?(?:e[+-]?[0-9]+)?)', parameter)
            if not match or match[1] in parameters:
                raise ValueError('MOS dimensions must be explicit finite l/w numbers in um')
            parameters[match[1]] = float(match[2])
        if set(parameters) != {'l', 'w'} or any(not .15 <= value <= 100 for value in parameters.values()):
            raise ValueError('MOS dimensions must be between 0.15 and 100 um')
    if models.count('sky130_fd_pr__nfet_01v8') != 3 or models.count('sky130_fd_pr__pfet_01v8') != 2:
        raise ValueError('The five-MOS target requires three NMOS and two PMOS devices')


def _columns(raw):
    return {item['name'].lower(): index for index, item in enumerate(raw['variables'])}


def electrical_metrics(ac, dc):
    columns = _columns(ac)
    required = ('frequency', 'v(inp)', 'v(inn)', 'v(out)')
    if any(name not in columns for name in required) or not ac.get('complex_points'):
        raise ValueError('Complex differential AC waveforms are missing')
    gains = []
    for row in ac['complex_points']:
        frequency = row[columns['frequency']][0]
        differential = complex(*row[columns['v(inp)']]) - complex(*row[columns['v(inn)']])
        if not math.isclose(abs(differential), 1, rel_tol=1e-8):
            raise ValueError('Actual differential AC stimulus must be 1 V')
        gain = abs(complex(*row[columns['v(out)']]) / differential)
        if not math.isfinite(gain) or gain <= 0 or not math.isfinite(frequency) or frequency <= 0:
            raise ValueError('AC data must contain finite positive frequencies and gains')
        gains.append((frequency, 20 * math.log10(gain)))
    gains.sort()
    if not math.isclose(gains[0][0], 1, rel_tol=1e-8):
        raise ValueError('AC data must start at the 1 Hz target point')
    unity = None
    for (first, gain1), (second, gain2) in zip(gains, gains[1:]):
        if gain1 > 0 >= gain2:
            unity = 10 ** (math.log10(first) + gain1 / (gain1 - gain2) * math.log10(second / first))
            break
    if unity is None:
        raise ValueError('No measured downward unity-gain crossing in AC data')
    columns = _columns(dc)
    if any(name not in columns for name in ('v(v-sweep)', 'v(out)', 'i(vdd)')) or not dc.get('points'):
        raise ValueError('Actual DC output and supply-current waveforms are missing')
    row = min(dc['points'], key=lambda values: abs(values[columns['v(v-sweep)']] - .9))
    if not math.isclose(row[columns['v(v-sweep)']], .9, abs_tol=1e-8):
        raise ValueError('DC waveform lacks the 0.9 V common-mode target')
    output, current = row[columns['v(out)']], row[columns['i(vdd)']]
    if not all(math.isfinite(value) for value in (output, current)):
        raise ValueError('DC metrics must be finite')
    return {'gain_db': gains[0][1], 'unity_gain_hz': unity, 'output_dc_v': output,
            'supply_current_a': current, 'supply_power_w': -1.8 * current}


def evaluate(*, session, runs, waveforms, source, initial_source, stop_reason=None):
    failures, metrics = [], {}
    if stop_reason:
        failures.append(stop_reason)
    if session.get('status') != 'succeeded' or not session.get('turns') or any(
            turn.get('status') != 'succeeded' for turn in session['turns']):
        failures.append('Agent session did not complete successfully')
    if source == initial_source:
        failures.append('Agent did not change the design source')
    try:
        validate_netlist(source)
    except ValueError as error:
        failures.append(str(error))
    digest = hashlib.sha256(source.encode()).hexdigest()
    for stage in ('ac', 'dc'):
        record = runs.get(stage, {})
        if record.get('status') != 'succeeded' or record.get('kind') != 'simulate' or record.get('exit_code') != 0:
            failures.append(f'{stage}: successful actual simulator evidence is missing')
        if record.get('stale') is not False or record.get('source_hashes', {}).get(DESIGN_PATH) != digest:
            failures.append(f'{stage}: source evidence is stale or does not match final design')
        if not record.get('artifact_hashes', {}).get('waveforms.raw'):
            failures.append(f'{stage}: waveform artifact evidence is absent')
    try:
        metrics = electrical_metrics(waveforms.get('ac', {}), waveforms.get('dc', {}))
        if metrics['gain_db'] <= 30:
            failures.append('Differential gain must exceed 30 dB at 1 Hz')
        if not .2 < metrics['output_dc_v'] < 1.6:
            failures.append('DC output must be inside 0.2–1.6 V')
        if not -100e-6 < metrics['supply_current_a'] < 0:
            failures.append('Supply current must be between -100 uA and 0')
    except (ValueError, KeyError, TypeError, IndexError, OverflowError) as error:
        failures.append(f'Independent waveform reconstruction failed: {error}')
    return {'status': 'fail' if failures else 'pass', 'failures': failures, 'metrics': metrics}
