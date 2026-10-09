"""Immutable user-configured tasks for two trusted educational circuit families.

Configuration is data: it cannot choose executable models, score code, or paths.
Only owner/configure operations can publish records; policy workers receive a
frozen copy and expose only bounded parameter-index actions.
"""
from __future__ import annotations

import copy
import itertools
import math
import random
import re

from ..storage import atomic_json, confined, read_json
from .environment import TASKSET_ID, digest

IDENTIFIER = re.compile(r'taskset-[a-f0-9]{24}')
MODEL_ID = 'educational-level1-nmos-v1'


def defaults(family='amplifier'):
    if family not in ('amplifier', 'current_mirror'):
        raise ValueError('Unknown trusted circuit family')
    common = {'name': 'Educational ' + family.replace('_', ' '), 'family': family,
              'model': MODEL_ID, 'seed': 7082026,
              'counts': {'train': 64, 'validation': 16, 'test': 32},
              'robustness': {'supply_factors': [1., .95], 'temperature_offsets': [0, 10]},
              'cost': {'simulator_call_penalty': .02}, 'max_actions': 3}
    if family == 'amplifier':
        common.update(conditions={'vdd': [1.6, 1.8, 2.], 'load_pf': [1., 2.], 'temperature': [0., 27., 65.]},
            targets={'gain_db': [8., 10., 12.], 'output_fraction': [.4, .5, .6]},
            constraints={'current_limit': .0002, 'output_tolerance': .25, 'output_min': .1, 'output_max': .9},
            actions={'rd': [2000., 4000., 6000., 8000., 10000., 12000.],
                     'bias': [.65, .70, .75, .80, .85, .90], 'width': [2., 4., 6., 8., 10., 12.]},
            scoring={'gain_weight': .5, 'bias_weight': .25, 'power_weight': .25})
    else:
        common.update(conditions={'vdd': [1.6, 1.8, 2.], 'vout': [.4, .8, 1.2],
                                  'reference_ua': [20., 40., 60.], 'temperature': [0., 27., 65.]},
            targets={'ratio': [.5, 1., 2.]},
            constraints={'current_limit': .0002, 'ratio_tolerance': .1, 'gate_min': .2, 'gate_max_fraction': .95},
            actions={'reference_width': [2., 4., 6., 8., 10., 12.],
                     'output_width': [2., 4., 6., 8., 10., 12.], 'length': [1., 2., 3., 4., 5., 6.]},
            scoring={'ratio_weight': .75, 'power_weight': .25})
    return common


def _number(value, low, high, label):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{label} must be finite and between {low} and {high}')
    return float(value)


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f'{label} must contain exactly: ' + ', '.join(expected))


def validate(specification):
    if not isinstance(specification, dict):
        raise ValueError('Taskset specification must be an object')
    family = specification.get('family')
    prototype = defaults(family)
    _keys(specification, prototype, 'Taskset specification')
    result = copy.deepcopy(specification)
    if not isinstance(result['name'], str) or not 1 <= len(result['name'].strip()) <= 80:
        raise ValueError('Taskset name must have 1 to 80 characters')
    result['name'] = result['name'].strip()
    if result['model'] != MODEL_ID:
        raise ValueError('Only the trusted educational MOS model is supported')
    if type(result['seed']) is not int or not 0 <= result['seed'] < 2**31:
        raise ValueError('Seed must be an integer from 0 to 2147483647')
    if type(result['max_actions']) is not int or not 1 <= result['max_actions'] <= 6:
        raise ValueError('max_actions must be an integer from 1 to 6')
    _keys(result['counts'], prototype['counts'], 'counts')
    for key, low, high in (('train', 2, 128), ('validation', 1, 64), ('test', 1, 64)):
        if type(result['counts'][key]) is not int or not low <= result['counts'][key] <= high:
            raise ValueError(f'{key} task count must be an integer from {low} to {high}')
    bounds = {'vdd': (1., 3.), 'temperature': (-40., 125.), 'load_pf': (.1, 20.),
              'vout': (.1, 2.5), 'reference_ua': (1., 100.), 'gain_db': (1., 40.),
              'output_fraction': (.1, .9), 'ratio': (.1, 5.), 'rd': (500., 50000.),
              'bias': (.5, 1.2), 'width': (.5, 50.), 'reference_width': (.5, 50.),
              'output_width': (.5, 50.), 'length': (.5, 10.)}
    for group in ('conditions', 'targets', 'actions'):
        _keys(result[group], prototype[group], group)
        result[group] = {key: result[group][key] for key in prototype[group]}
        for key, values in result[group].items():
            if not isinstance(values, list) or not 1 <= len(values) <= (12 if group == 'actions' else 6):
                raise ValueError(f'{group}.{key} needs a bounded nonempty list')
            values = [_number(v, *bounds[key], f'{group}.{key}') for v in values]
            if len(values) != len(set(values)):
                raise ValueError(f'{group}.{key} cannot contain duplicate choices')
            result[group][key] = values
    for group in ('constraints', 'scoring', 'cost', 'robustness'):
        _keys(result[group], prototype[group], group)
    constraint_bounds = {'current_limit': (1e-6, .001), 'output_tolerance': (.01, .5),
                         'output_min': (0., .5), 'output_max': (.5, 1.),
                         'ratio_tolerance': (.001, .5), 'gate_min': (0., 1.),
                         'gate_max_fraction': (.5, 1.)}
    for key, value in result['constraints'].items():
        result['constraints'][key] = _number(value, *constraint_bounds[key], key)
    for key, value in result['scoring'].items():
        result['scoring'][key] = _number(value, 0., 5., key)
    if not any(result['scoring'].values()):
        raise ValueError('At least one objective must have nonzero weight')
    result['cost']['simulator_call_penalty'] = _number(result['cost']['simulator_call_penalty'], 0., 1., 'simulator_call_penalty')
    for key, limits in (('supply_factors', (.8, 1.2)), ('temperature_offsets', (-40., 40.))):
        values = result['robustness'][key]
        if not isinstance(values, list) or not 1 <= len(values) <= 4:
            raise ValueError('Robustness requires 1 to 4 paired conditions')
        result['robustness'][key] = [_number(v, *limits, key) for v in values]
    if len(result['robustness']['supply_factors']) != len(result['robustness']['temperature_offsets']):
        raise ValueError('Robustness supply factors and temperature offsets must be paired')
    if family == 'current_mirror' and max(result['conditions']['vout']) > min(result['conditions']['vdd']):
        raise ValueError('Current mirror output voltage must not exceed minimum supply')
    return result


def build(specification):
    spec = validate(specification)
    choices = [(group, key, values) for group in ('conditions', 'targets')
               for key, values in spec[group].items()]
    tasks = []
    for values in itertools.product(*(item[2] for item in choices)):
        body = {'family': spec['family'], 'conditions': {}, 'targets': {}}
        for (group, key, _), value in zip(choices, values):
            body[group][key] = value
        tasks.append({'id': digest(body)[:20], **body})
    count = sum(spec['counts'].values())
    if len(tasks) < count:
        raise ValueError(f'Configuration has {len(tasks)} distinct tasks, but split counts need {count}')
    random.Random(spec['seed']).shuffle(tasks)
    splits, index = {}, 0
    for name in ('train', 'validation', 'test'):
        splits[name] = tasks[index:index + spec['counts'][name]]
        index += spec['counts'][name]
    body = {'schema_version': 1, 'evaluator_version': 'trusted-educational-v2',
            'specification': spec, 'splits': splits,
            'hashes': {name: digest(group) for name, group in splits.items()}}
    return {'id': 'taskset-' + digest(body)[:24], **body}


def _path(workspace, project_id, taskset_id):
    if not IDENTIFIER.fullmatch(str(taskset_id)):
        raise ValueError('Invalid taskset identifier')
    return confined(workspace.projects.path(project_id), f'.af/training-tasksets/{taskset_id}.json', hidden=True)


def create(workspace, project_id, specification):
    record = build(specification)
    path = _path(workspace, project_id, record['id'])
    with workspace.lock:
        if path.exists():
            return get(workspace, project_id, record['id'])
        atomic_json(path, record)
    return record


def get(workspace, project_id, taskset_id):
    record = read_json(_path(workspace, project_id, taskset_id))
    if not isinstance(record, dict):
        raise FileNotFoundError('Training taskset does not exist')
    expected = build(record.get('specification'))
    if record != expected or record['id'] != taskset_id:
        raise ValueError('Training taskset integrity changed after publication')
    return record


def describe(record):
    spec = record['specification']
    return {'id': record['id'], 'name': spec['name'], 'family': spec['family'],
            'counts': spec['counts'], 'hashes': record['hashes'], 'bounds': spec['actions'],
            'max_actions': spec['max_actions'], 'conditions_per_action': len(spec['robustness']['supply_factors']),
            'coverage': 'Educational level-1 MOS; voltage/temperature probes, not foundry process qualification',
            'evaluator_version': record['evaluator_version']}


def list_tasksets(workspace, project_id):
    from .manager import taskset_summary
    root = confined(workspace.projects.path(project_id), '.af/training-tasksets', hidden=True)
    return [{'id': TASKSET_ID, 'name': 'Original amplifier qualification', 'family': 'amplifier',
             **taskset_summary()}, *(describe(get(workspace, project_id, p.stem)) for p in sorted(root.glob('*.json')))]
