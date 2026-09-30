"""Independent scoring and isolated random-search baseline episodes.

This module evaluates circuit parameter choices. It does not train language models.
The evaluator owns its frozen specification; EDA subprocesses only see design inputs.
"""
import hashlib
import json
import math
import random
import shutil
import time
from pathlib import Path

from .storage import atomic_json


def evaluate_metric(value, specification):
    target = float(specification['target'])
    tolerance = float(specification['tolerance'])
    if not math.isfinite(target) or not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError('Evaluator requires a finite target and positive tolerance')
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return {'reward': -1.0, 'satisfied': False, 'absolute_error': None,
                'failure': 'missing_or_nonfinite_measurement', 'hard_constraints_met': False}
    error = abs(value - target)
    normalized = error / max(abs(target), tolerance)
    return {'reward': 1.0 - min(2.0, normalized), 'satisfied': error <= tolerance,
            'absolute_error': error, 'normalized_error': normalized, 'failure': None,
            'hard_constraints_met': True}


def run_evaluation(workspace, project_id, options, job, cancel, update):
    from . import eda
    from .workflows import replace_parameter
    episodes = options.get('episodes', 3)
    seed = options.get('seed', 42)
    if isinstance(episodes, bool) or not isinstance(episodes, int) or not 1 <= episodes <= 20:
        raise ValueError('Episode count must be an integer from 1 to 20')
    if not isinstance(seed, int):
        raise ValueError('Seed must be an integer')
    root = workspace.projects.path(project_id)
    specification = json.loads(workspace.projects.read(project_id, 'specifications.json')['content'])
    # Validate independently before starting any subprocess.
    evaluate_metric(0, specification)
    serialized = json.dumps(specification, sort_keys=True)
    evaluator_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    rng = random.Random(seed)
    results = []
    directory = root / '.af/episodes' / job['id']
    directory.mkdir(parents=True)
    atomic_json(directory / 'evaluator.json', {'specification': specification,
        'specification_sha256': hashlib.sha256(serialized.encode()).hexdigest(),
        'evaluator_sha256': evaluator_hash, 'seed': seed})
    start = time.monotonic()
    for episode in range(episodes):
        if cancel.is_set():
            break
        episode_root = directory / str(episode)
        episode_root.mkdir()
        for item in workspace.projects.files(project_id):
            # Scoring code/specification are never part of the action directory.
            if item['path'] == 'specifications.json':
                continue
            source = root / item['path']
            target = episode_root / item['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        value = rng.uniform(1000, 10000)
        deck = episode_root / 'testbenches/pre.cir'
        deck.write_text(replace_parameter(deck.read_text(), 'RD', value))
        config = workspace.config(project_id)
        spec = eda.prepare(episode_root, 'simulate', {'netlist': 'testbenches/pre.cir'}, config)
        spec['metadata'].update({'evaluation_job': job['id'], 'episode': episode,
                                 'conditions': {'RD': value}})
        run = workspace.runner.start(episode_root, 'simulate', **spec)
        while True:
            result = workspace.runner.get(episode_root, run['id'])
            if result['status'] not in ('queued', 'running'):
                break
            if cancel.is_set():
                workspace.runner.cancel(episode_root, run['id'])
            time.sleep(.025)
        value_out = result.get('metrics', {}).get(specification.get('metric', 'vout_mean')) if result['status'] == 'succeeded' else None
        score = evaluate_metric(value_out, specification)
        item = {'episode': episode, 'run_id': run['id'], 'parameter': 'RD', 'value': value,
                'metric': value_out, 'score': score, 'status': result['status'],
                'run_path': str((episode_root / '.af/runs' / run['id']).relative_to(root)),
                'observation': {'specification': specification}, 'action': {'RD': value}}
        results.append(item)
        update(iterations=results, completed_episodes=len(results))
    successes = sum(x['score']['satisfied'] for x in results)
    return {'iterations': results, 'seed': seed, 'baseline': 'uniform_random_search',
            'classification': 'circuit_parameter_evaluation', 'model_weights_updated': False,
            'successes': successes, 'episodes': len(results),
            'success_rate': successes / len(results) if results else 0,
            'mean_reward': sum(x['score']['reward'] for x in results) / len(results) if results else None,
            'compute_seconds': time.monotonic() - start, 'evaluator_sha256': evaluator_hash,
            'training_status': 'unavailable: no trainable model/framework configured',
            'coverage': 'single educational circuit, nominal conditions; no PVT robustness or held-out model training comparison'}
