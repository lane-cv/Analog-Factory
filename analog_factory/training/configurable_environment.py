"""Trusted electrical implementations for immutable configurable tasksets."""
from __future__ import annotations

import copy
import hashlib
import math
from pathlib import Path
import time

from ..storage import confined
from ..workflows import invoke_engine
from .environment import digest


def parameters(taskset, action):
    grids = taskset['specification']['actions']
    if not isinstance(action, dict) or set(action) != {name + '_index' for name in grids}:
        raise ValueError('Action must contain exactly the declared parameter indices')
    result = {}
    for name, values in grids.items():
        index = action[name + '_index']
        if type(index) is not int or not 0 <= index < len(values):
            raise ValueError(f'{name}_index must be an integer from 0 to {len(values)-1}')
        result[name] = values[index]
    return result


def score(taskset, task, metrics, *, calls, failure=None, compute_seconds=0):
    spec, c, t = taskset['specification'], task['conditions'], task['targets']
    safe_metrics = {key: value if type(value) in (int, float) and math.isfinite(value) else None
                    for key, value in metrics.items()}
    result = {'reward': -1., 'satisfied': False, 'failure': failure, 'constraints': {},
              'objectives': {}, 'metrics': safe_metrics,
              'cost': {'simulator_calls': calls, 'compute_seconds': compute_seconds, 'monetary_usd': None}}
    required = (('gain_db', 'vout_dc', 'supply_current') if spec['family'] == 'amplifier'
                else ('output_current', 'reference_current', 'gate_voltage'))
    if failure or any(safe_metrics.get(key) is None for key in required):
        result['failure'] = failure or 'missing_or_nonfinite_measurement'
        return result
    limits, weights = spec['constraints'], spec['scoring']
    if spec['family'] == 'amplifier':
        gain, fraction, current = metrics['gain_db'], metrics['vout_dc'] / c['vdd'], abs(metrics['supply_current'])
        result['constraints'] = {'gain_target': gain >= t['gain_db'],
            'output_target': abs(fraction-t['output_fraction']) <= limits['output_tolerance'],
            'output_range': limits['output_min'] <= fraction <= limits['output_max'],
            'current_limit': current <= limits['current_limit']}
        objectives = {'gain_deficit': min(1., max(0., t['gain_db']-gain)/t['gain_db']),
                      'bias_error': min(1., abs(fraction-t['output_fraction'])/.5),
                      'power_excess': min(1., max(0., current/limits['current_limit']-1))}
        penalty = weights['gain_weight']*objectives['gain_deficit'] + weights['bias_weight']*objectives['bias_error'] + weights['power_weight']*objectives['power_excess']
    else:
        current, reference = metrics['output_current'], metrics['reference_current']
        if reference <= 0:
            result['failure'] = 'invalid_reference_current'
            return result
        ratio = current / reference
        error = abs(ratio/t['ratio']-1)
        result['constraints'] = {'ratio_target': error <= limits['ratio_tolerance'],
            'positive_output_current': current > 0,
            'gate_range': limits['gate_min'] <= metrics['gate_voltage'] <= limits['gate_max_fraction']*c['vdd'],
            'current_limit': max(abs(current), reference) <= limits['current_limit']}
        objectives = {'ratio_error': min(1., error),
                      'power_excess': min(1., max(0., max(abs(current), reference)/limits['current_limit']-1))}
        result['metrics']['measured_ratio'] = ratio
        penalty = weights['ratio_weight']*objectives['ratio_error'] + weights['power_weight']*objectives['power_excess']
    result['objectives'] = objectives
    result['satisfied'] = all(result['constraints'].values())
    result['reward'] = float(result['satisfied']) - penalty - calls*spec['cost']['simulator_call_penalty']
    return result


def deck(taskset, task, action):
    spec, c, p = taskset['specification'], task['conditions'], parameters(taskset, action)
    prefix = f'''Trusted educational {spec['family']}; fixed level-1 NMOS model
VDD vdd 0 {c['vdd']:.12g}
.model NTRAIN NMOS level=1 VTO=0.5 KP=200u LAMBDA=0.05
.temp {c['temperature']:.12g}
'''
    if spec['family'] == 'amplifier':
        body = f'''VIN gate 0 dc {p['bias']:.12g} ac 1
RD vdd out {p['rd']:.12g}
M1 out gate 0 0 NTRAIN W={p['width']:.12g}u L=1u
CL out 0 {c['load_pf']:.12g}p
.control
set noaskquit
op
let vout_dc = v(out)
let supply_current = i(VDD)
print vout_dc supply_current
ac dec 5 1 1Meg
let gain = vdb(out)
meas ac gain_db find gain at=1
write ac.raw all
'''
    else:
        body = f'''IREF vdd gate dc {c['reference_ua']:.12g}u
MREF gate gate 0 0 NTRAIN W={p['reference_width']:.12g}u L={p['length']:.12g}u
MOUT out gate 0 0 NTRAIN W={p['output_width']:.12g}u L={p['length']:.12g}u
VOUT out 0 {c['vout']:.12g}
.control
set noaskquit
op
let output_current = -i(VOUT)
let reference_current = -i(VDD)
let gate_voltage = v(gate)
print output_current reference_current gate_voltage
write dc.raw all
'''
    return prefix + body + 'quit\n.endc\n.end\n'


def prompt(taskset, task):
    spec = taskset['specification']
    grids = '; '.join(f'{key}_index 0..{len(values)-1} selects {values}' for key, values in spec['actions'].items())
    return (f"Tune an educational {spec['family'].replace('_', ' ')} using simulate. "
            f"Conditions: {task['conditions']}. Targets: {task['targets']}. "
            f"Hard constraints: {spec['constraints']}. Parameter grids: {grids}. "
            f"At most {spec['max_actions']} calls; each checks {len(spec['robustness']['supply_factors'])} voltage/temperature conditions. "
            "Call simulate with integer indices, read the measurements, then improve your next choice. "
            "Use a tool call first. Finish with a short answer once all constraints pass.")


class AnalogEnvironment:
    def __init__(self, workspace, project_id, job_id, allocate_episode, cancel, record, taskset):
        self.workspace, self.project_id, self.job_id = workspace, project_id, job_id
        self.allocate_episode, self.cancel, self.record = allocate_episode, cancel, record
        self.taskset = copy.deepcopy(taskset)
        self.spec = self.taskset['specification']

    def reset(self, task):
        self.task = copy.deepcopy(task)
        self.calls, self.results, self.terminal = 0, [], False
        self.episode = self.allocate_episode()
        self.directory = confined(self.workspace.projects.path(self.project_id),
            f'.af/episodes/{self.job_id}/{self.episode}', hidden=True)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.started = time.monotonic()
        return prompt(self.taskset, self.task)

    def simulate(self, **action):
        if self.cancel.is_set():
            raise InterruptedError('Training cancelled')
        if self.terminal:
            return {'error': 'Episode has ended', 'remaining_calls': 0}
        try:
            values = parameters(self.taskset, action)
        except ValueError as error:
            self.terminal = True
            # Preserve invalid choices as readable strings if JSON cannot represent them.
            safe = {key: str(value) if isinstance(value, float) and not math.isfinite(value) else value
                    for key, value in action.items()}
            self.results.append({'action': safe, 'score': score(self.taskset, self.task, {},
                calls=self.calls, failure='invalid_action'), 'error': str(error), 'run_ids': []})
            self.record(self.snapshot())
            return {'error': str(error), 'remaining_calls': 0}
        self.calls += 1
        cases = []
        robust = self.spec['robustness']
        count = len(robust['supply_factors'])
        for index, (supply, temperature) in enumerate(zip(robust['supply_factors'], robust['temperature_offsets'])):
            variant = copy.deepcopy(self.task)
            variant['conditions']['vdd'] *= supply
            variant['conditions']['temperature'] += temperature
            relative = f'testbenches/action-{self.calls}-case-{index}.cir'
            path = confined(self.directory, relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(deck(self.taskset, variant, action))
            run = invoke_engine(self.workspace, self.project_id, 'evaluation', 'run.start', {
                'kind': 'simulate', 'options': {'netlist': relative},
                'episode': {'job_id': self.job_id, 'index': self.episode},
                'metadata': {'evaluation_job': self.job_id, 'episode': self.episode,
                             'conditions': {**variant['conditions'], **values}}})
            deadline = time.monotonic()+30
            while True:
                result = invoke_engine(self.workspace, self.project_id, 'evaluation', 'run.get', {'run_id': run['id']})
                if result['status'] not in ('queued', 'running'):
                    break
                if self.cancel.is_set() or time.monotonic() > deadline:
                    invoke_engine(self.workspace, self.project_id, 'evaluation', 'run.cancel', {'run_id': run['id']})
                time.sleep(.025)
            failure = None if result['status'] == 'succeeded' else result['status']
            cases.append({'condition': variant['conditions'], 'run_id': run['id'],
                'score': score(self.taskset, variant, result.get('metrics', {}), calls=count*self.calls,
                               failure=failure, compute_seconds=time.monotonic()-self.started)})
            if self.cancel.is_set():
                raise InterruptedError('Training cancelled')
        worst = min(cases, key=lambda case: case['score']['reward'])['score']
        satisfied = all(case['score']['satisfied'] for case in cases)
        self.results.append({'action': action, 'parameters': values, 'score': worst, 'cases': cases,
                             'satisfied': satisfied, 'run_ids': [case['run_id'] for case in cases]})
        self.terminal = satisfied or self.calls >= self.spec['max_actions'] or bool(worst['failure'])
        self.record(self.snapshot())
        return {'metrics': worst['metrics'], 'constraints': worst['constraints'], 'satisfied': satisfied,
                'parameters': values, 'remaining_calls': 0 if self.terminal else self.spec['max_actions']-self.calls}

    def reward(self):
        return self.results[-1]['score']['reward'] if self.results else -1.

    def snapshot(self):
        final = self.results[-1] if self.results else {}
        failure = final.get('score', {}).get('failure')
        return {'episode': self.episode, 'taskset_id': self.taskset['id'], 'task_id': self.task['id'],
                'task_sha256': digest(self.task), 'calls': self.calls,
                'simulator_calls': sum(len(row.get('run_ids', [])) for row in self.results),
                'results': copy.deepcopy(self.results), 'reward': self.reward(),
                'satisfied': bool(final.get('satisfied')),
                'termination': ('success' if final.get('satisfied') else failure or
                    ('step_limit' if self.calls >= self.spec['max_actions'] else 'policy_finish')),
                'evaluator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
