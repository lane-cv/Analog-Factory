"""Fixed educational amplifier tasks and evaluator, independent of policy weights."""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import time

from ..storage import atomic_json, confined
from ..workflows import invoke_engine

GRIDS = {'rd': [2000, 4000, 6000, 8000, 10000, 12000],
         'bias': [.65, .70, .75, .80, .85, .90], 'width': [2, 4, 6, 8, 10, 12]}
MAX_ACTIONS = 3
TASKSET_ID = 'educational-amplifier-v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def tasksets():
    tasks = []
    for vdd, load_pf, temp, gain, fraction in itertools.product(
            (1.6, 1.8, 2.0), (1, 2), (0, 27, 65), (8, 10, 12), (.4, .5, .6)):
        body = {'conditions': {'vdd': vdd, 'load_pf': load_pf, 'temperature': temp},
                'targets': {'gain_db': gain, 'output_fraction': fraction,
                            'output_tolerance': .25, 'current_limit': .0002},
                'initial': {'rd_index': 2, 'bias_index': 2, 'width_index': 2}}
        tasks.append({'id': digest(body)[:20], **body})
    random.Random(7082026).shuffle(tasks)
    return {'train': copy.deepcopy(tasks[:64]), 'validation': copy.deepcopy(tasks[64:80]),
            'test': copy.deepcopy(tasks[80:112])}


def parameters(action):
    if not isinstance(action, dict) or set(action) != {k + '_index' for k in GRIDS}:
        raise ValueError('Action must contain exactly rd_index, bias_index and width_index')
    result = {}
    for name, values in GRIDS.items():
        index = action[name + '_index']
        if type(index) is not int or not 0 <= index < len(values):
            raise ValueError(name + '_index must be an integer from 0 to 5')
        result[name] = values[index]
    return result


def score(task, metrics, *, calls, failure=None, compute_seconds=0):
    targets, conditions = task['targets'], task['conditions']
    result = {'reward': -1., 'satisfied': False, 'failure': failure, 'constraints': {},
              'objectives': {}, 'metrics': {key: value if type(value) in (int, float) and math.isfinite(value) else None
                                            for key, value in metrics.items()},
              'cost': {'simulator_calls': calls, 'compute_seconds': compute_seconds, 'monetary_usd': None}}
    required = ('gain_db', 'vout_dc', 'supply_current')
    if failure or any(type(metrics.get(k)) not in (int, float) or not math.isfinite(metrics[k]) for k in required):
        result['failure'] = failure or 'missing_or_nonfinite_measurement'
        return result
    gain, output, current = (metrics[k] for k in required)
    fraction = output / conditions['vdd']
    result['constraints'] = {
        'output_range': .1 <= fraction <= .9,
        'output_target': abs(fraction - targets['output_fraction']) <= targets['output_tolerance'],
        'current_limit': abs(current) <= targets['current_limit'],
        'gain_target': gain >= targets['gain_db'],
    }
    result['objectives'] = {'gain_deficit': min(1., max(0., targets['gain_db'] - gain) / targets['gain_db']),
        'bias_error': min(1., abs(fraction - targets['output_fraction']) / .5),
        'power_excess': min(1., max(0., abs(current) / targets['current_limit'] - 1.))}
    result['satisfied'] = all(result['constraints'].values())
    obj = result['objectives']
    result['reward'] = (float(result['satisfied']) - .5 * obj['gain_deficit']
                        - .25 * obj['bias_error'] - .25 * obj['power_excess'] - .02 * calls)
    return result


def deck(task, action):
    p, c = parameters(action), task['conditions']
    return f'''Educational common-source amplifier; fixed evaluator-generated deck
.param RD={p['rd']:.12g} VB={p['bias']:.12g} WN={p['width']:.12g}u
VDD vdd 0 {c['vdd']:.12g}
VIN gate 0 dc {{VB}} ac 1
RD vdd out {{RD}}
M1 out gate 0 0 NTRAIN W={{WN}} L=1u
CL out 0 {c['load_pf']:.12g}p
.model NTRAIN NMOS level=1 VTO=0.5 KP=200u LAMBDA=0.05
.temp {c['temperature']:.12g}
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
quit
.endc
.end
'''


def prompt(task):
    c, t = task['conditions'], task['targets']
    return (f"Tune an educational NMOS amplifier using the simulate tool. Supply {c['vdd']} V, "
            f"load {c['load_pf']} pF, temperature {c['temperature']} C. "
            f"Need gain >= {t['gain_db']} dB, output near {t['output_fraction']*c['vdd']:.2f} V "
            f"(tolerance {t['output_tolerance']*c['vdd']:.2f} V), current <= 200 uA. "
            f"Index grids: RD ohm={GRIDS['rd']}; gate bias V={GRIDS['bias']}; width um={GRIDS['width']}. "
            f"Call simulate(rd_index, bias_index, width_index) with integer indices 0..5. "
            f"You have {MAX_ACTIONS} calls. Read measurements and improve your next choice. "
            "Increasing RD or width usually raises gain but lowers output voltage; increasing bias lowers output. "
            "Use a tool call first. When satisfied, finish with a short answer.")


class AnalogEnvironment:
    """Trusted wrapper; policy sees only simulate() and a serialized observation."""
    def __init__(self, workspace, project_id, job_id, allocate_episode, cancel, record):
        self.workspace, self.project_id, self.job_id = workspace, project_id, job_id
        self.allocate_episode, self.cancel, self.record = allocate_episode, cancel, record
        self.task = None
        self.calls, self.results = 0, []
        self.terminal = False

    def reset(self, task):
        self.task = copy.deepcopy(task)
        self.calls, self.results, self.terminal = 0, [], False
        self.episode = self.allocate_episode()
        root = self.workspace.projects.path(self.project_id)
        self.directory = confined(root, f'.af/episodes/{self.job_id}/{self.episode}', hidden=True)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.started = time.monotonic()
        return prompt(self.task)

    def simulate(self, rd_index: int, bias_index: int, width_index: int) -> dict:
        """Simulate the amplifier and return gain, output voltage, current and constraints.

        Args:
            rd_index: Index 0 to 5 into the drain-resistance grid.
            bias_index: Index 0 to 5 into the gate-bias grid.
            width_index: Index 0 to 5 into the transistor-width grid.
        """
        action = {'rd_index': rd_index, 'bias_index': bias_index, 'width_index': width_index}
        if self.cancel.is_set():
            raise InterruptedError('Training cancelled')
        if self.terminal or self.calls >= MAX_ACTIONS:
            return {'error': 'Episode has ended; finish your answer', 'remaining_calls': 0}
        try:
            parameters(action)
        except ValueError as error:
            self.terminal = True
            item = {'action': action, 'score': score(self.task, {}, calls=self.calls, failure='invalid_action'),
                    'error': str(error), 'run_ids': []}
            self.results.append(item)
            self.record(self.snapshot())
            return {'error': str(error), 'remaining_calls': 0}
        self.calls += 1
        cases = []
        for index in range(2):
            variant = copy.deepcopy(self.task)
            if index:
                variant['conditions']['vdd'] *= .95
                variant['conditions']['temperature'] += 10
            relative = f'testbenches/action-{self.calls}-case-{index}.cir'
            path = confined(self.directory, relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(deck(variant, action))
            run = invoke_engine(self.workspace, self.project_id, 'evaluation', 'run.start', {
                'kind': 'simulate', 'options': {'netlist': relative},
                'episode': {'job_id': self.job_id, 'index': self.episode},
                'metadata': {'evaluation_job': self.job_id, 'episode': self.episode,
                             'conditions': {**variant['conditions'], **parameters(action)}}})
            deadline = time.monotonic() + 30
            while True:
                result = invoke_engine(self.workspace, self.project_id, 'evaluation', 'run.get', {'run_id': run['id']})
                if result['status'] not in ('queued', 'running'):
                    break
                if self.cancel.is_set() or time.monotonic() > deadline:
                    invoke_engine(self.workspace, self.project_id, 'evaluation', 'run.cancel', {'run_id': run['id']})
                time.sleep(.025)
            failure = None if result['status'] == 'succeeded' else result['status']
            cases.append({'condition': variant['conditions'], 'run_id': run['id'],
                          'score': score(variant, result.get('metrics', {}), calls=2*self.calls,
                                         failure=failure, compute_seconds=time.monotonic()-self.started)})
            if self.cancel.is_set():
                raise InterruptedError('Training cancelled')
        worst = min(cases, key=lambda case: case['score']['reward'])['score']
        satisfied = all(case['score']['satisfied'] for case in cases)
        item = {'action': action, 'parameters': parameters(action), 'score': worst, 'cases': cases,
                'satisfied': satisfied, 'run_ids': [case['run_id'] for case in cases]}
        self.results.append(item)
        self.terminal = satisfied or self.calls >= MAX_ACTIONS or bool(worst['failure'])
        self.record(self.snapshot())
        return {'metrics': worst['metrics'], 'constraints': worst['constraints'], 'satisfied': satisfied,
                'parameters': parameters(action), 'remaining_calls': 0 if self.terminal else MAX_ACTIONS-self.calls}

    def reward(self):
        return self.results[-1]['score']['reward'] if self.results else -1.

    def snapshot(self):
        return {'episode': self.episode, 'task_id': self.task['id'], 'task_sha256': digest(self.task),
                'calls': self.calls, 'results': copy.deepcopy(self.results), 'reward': self.reward(),
                'satisfied': bool(self.results and self.results[-1].get('satisfied')),
                'termination': ('success' if self.results and self.results[-1].get('satisfied') else
                                'step_limit' if self.calls >= MAX_ACTIONS else 'policy_finish'),
                'evaluator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
