"""Verify completed local held-out evidence before a bounded evaluation resume."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import random

from ..execution import _results
from ..storage import confined, read_json
from . import environment as legacy, configurable_environment as configurable

PHASES = {'heldout_before': 'before', 'random_baseline': 'random', 'heldout_after_reload': 'after'}


def _hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def prepare(workspace, project_id, source_id, checkpoint_id, requested):
    from . import manager, task_catalog
    source = manager.get(workspace, project_id, source_id)
    if source.get('imported_evidence') or not source.get('owner_identity'):
        raise ValueError('Imported evaluation evidence cannot authorize resume')
    if source['kind'] != 'training_evaluation' or source['status'] not in ('failed', 'cancelled', 'interrupted'):
        raise ValueError('Resume requires a stopped incomplete checkpoint evaluation')
    root = manager.directory(workspace, project_id, source_id)
    request = read_json(confined(root, 'request.json'), {})
    if request.get('checkpoint_job_id') != checkpoint_id or source.get('parent_id') != checkpoint_id:
        raise ValueError('Resume checkpoint differs from the original evaluation')
    if request.get('resume'):
        raise ValueError('Only one resume of an original evaluation is currently supported')
    original = manager.options(request.get('options', {}))
    policy_identity = manager.completion_identity(original['max_completion_length'], request.get('completion_budget_semantics'))
    for key in ('taskset_id', 'seed', 'eval_tasks', 'framework', 'max_completion_length'):
        if key in requested and requested[key] != original[key]:
            raise ValueError('Resume must preserve original taskset, seed, task count, framework and completion budget')
    settings = manager.options({**original, **requested})
    catalog = request.get('taskset_record')
    if original['taskset_id'] != legacy.TASKSET_ID:
        current = task_catalog.get(workspace, project_id, original['taskset_id'])
        if catalog != current:
            raise ValueError('Frozen evaluation taskset changed')
    elif catalog is not None or request.get('taskset', {}).get('hashes') != manager.taskset_summary()['hashes']:
        raise ValueError('Original evaluator task splits changed')
    data = catalog['splits'] if catalog else legacy.tasksets()
    tasks = {task['id']: (index, task) for index, task in enumerate(data['test'][:settings['eval_tasks']])}
    evaluator = configurable if catalog else legacy
    evaluator_hash = _hash(Path(evaluator.__file__))
    model = read_json(manager.RUNTIME / 'model-source.json')
    traces = {}
    evidence_hashes = {}
    for path in sorted(confined(root, 'trajectories').glob('*.json')):
        trace = read_json(confined(root, path.relative_to(root).as_posix()))
        if trace.get('phase') not in ('heldout_before', 'heldout_after_reload'):
            continue
        if trace.get('model') != model or trace.get('seed') != settings['seed']:
            raise ValueError('Policy identity or seed changed in completed trajectory')
        trace_limits = trace.get('policy_limits', {})
        if not isinstance(trace_limits, dict):
            raise ValueError('Completed trajectory has invalid completion budget identity')
        trace_identity = manager.completion_identity(trace_limits.get('completion_tokens',96), trace_limits.get('completion_budget_semantics'))
        if trace_identity != policy_identity:
            raise ValueError('Completed trajectory completion budget differs from original evaluation')
        tokens = trace.get('completion_ids', [])
        if not tokens or len(tokens) != len(trace.get('completion_mask', [])) or len(tokens) != len(trace.get('old_policy_logprobs', [])):
            raise ValueError('Completed policy trajectory lacks exact tokens and probabilities')
        if len(tokens) > policy_identity['completion_tokens']:
            raise ValueError('Completed policy trajectory exceeds its verified completion budget')
        key = (trace['phase'], trace['task_id'])
        if key in traces:
            raise ValueError('Ambiguous duplicate completed task trajectory')
        traces[key] = trace
        evidence_hashes[path.relative_to(root).as_posix()] = _hash(path)
    completed = {key: [] for key in PHASES.values()}
    project = workspace.projects.path(project_id)
    for path in sorted(confined(root, 'episodes').glob('*.json')):
        episode = read_json(confined(root, path.relative_to(root).as_posix()))
        phase = episode.get('phase')
        if phase not in PHASES:
            continue
        trace = traces.get((phase, episode.get('task_id')))
        if phase != 'random_baseline' and (trace is None or trace.get('episode') != episode.get('episode')):
            continue  # In-flight tool output has no completed generation receipt.
        if phase == 'random_baseline' and episode.get('termination') not in ('success', 'step_limit'):
            continue
        if episode.get('task_id') not in tasks:
            raise ValueError('Completed episode is outside the original held-out split')
        index, task = tasks[episode['task_id']]
        if episode.get('task_sha256') != legacy.digest(task) or episode.get('evaluator_sha256') != evaluator_hash:
            raise ValueError('Completed episode task or evaluator hash changed')
        if trace and any(trace.get(key) != episode.get(key) for key in
                         ('calls', 'results', 'reward', 'satisfied', 'task_sha256', 'evaluator_sha256')):
            raise ValueError('Episode output differs from its completed policy trajectory')
        maximum = catalog['specification']['max_actions'] if catalog else legacy.MAX_ACTIONS
        if type(episode.get('calls')) is not int or not 0 <= episode['calls'] <= maximum:
            raise ValueError('Completed episode exceeded its bounded action budget')
        for action_index, action in enumerate(episode.get('results', []), 1):
            if action['score'].get('failure') == 'invalid_action':
                if action.get('run_ids') or action['score']['reward'] != -1:
                    raise ValueError('Invalid-action failure evidence changed')
                continue
            if catalog:
                configurable.parameters(catalog, action['action'])
            else:
                legacy.parameters(action['action'])
            count = len(catalog['specification']['robustness']['supply_factors']) if catalog else 2
            if len(action.get('cases', [])) != count or action.get('run_ids') != [case['run_id'] for case in action['cases']]:
                raise ValueError('Completed action lacks all required simulator cases')
            for case_index, case in enumerate(action.get('cases', [])):
                run_id = case['run_id']
                if not manager.IDENTIFIER.fullmatch(str(run_id)):
                    raise ValueError('Invalid simulator evidence identifier')
                run_root = confined(project, f'.af/episodes/{source_id}/{episode["episode"]}/.af/runs/{run_id}', hidden=True)
                manifest = read_json(confined(run_root, 'manifest.json'), {})
                if manifest.get('imported_evidence') or manifest.get('status') in ('queued', 'running', 'interrupted'):
                    raise ValueError('Simulator evidence is imported or incomplete')
                variant = copy.deepcopy(task)
                if catalog:
                    robust = catalog['specification']['robustness']
                    variant['conditions']['vdd'] *= robust['supply_factors'][case_index]
                    variant['conditions']['temperature'] += robust['temperature_offsets'][case_index]
                    expected_deck = configurable.deck(catalog, variant, action['action'])
                else:
                    if case_index:
                        variant['conditions']['vdd'] *= .95
                        variant['conditions']['temperature'] += 10
                    expected_deck = legacy.deck(variant, action['action'])
                deck = confined(run_root, f'input/testbenches/action-{action_index}-case-{case_index}.cir')
                if deck.read_text() != expected_deck or case['condition'] != variant['conditions']:
                    raise ValueError('Simulator input or required conditions changed')
                for name, expected in manifest.get('artifact_hashes', {}).items():
                    if _hash(confined(run_root, 'output/' + name)) != expected:
                        raise ValueError('Simulator output artifact hash changed')
                parsed = copy.deepcopy(manifest)
                parsed.update(metrics={}, metric_provenance={})
                _results(run_root, parsed)
                if parsed['metrics'] != manifest['metrics']:
                    raise ValueError('Recorded measurements differ from actual simulator output')
                failure = None if manifest['status'] == 'succeeded' else manifest['status']
                expected_score = (configurable.score(catalog, variant, parsed['metrics'], calls=count*action_index, failure=failure)
                                  if catalog else legacy.score(variant, parsed['metrics'], calls=count*action_index, failure=failure))
                if any(case['score'].get(key) != expected_score[key] for key in
                       ('reward', 'satisfied', 'failure', 'constraints', 'objectives', 'metrics')):
                    raise ValueError('Completed reward differs from independent electrical scoring')
                evidence_hashes[f'run:{run_id}'] = _hash(run_root / 'manifest.json')
            expected_worst = min(action['cases'], key=lambda case: case['score']['reward'])['score']
            if action['score'] != expected_worst or action['satisfied'] != all(case['score']['satisfied'] for case in action['cases']):
                raise ValueError('Completed action hides a required condition failure')
        last = episode.get('results', [{}])[-1] if episode.get('results') else {}
        if episode['reward'] != last.get('score', {}).get('reward', -1) or episode['satisfied'] != bool(last.get('satisfied')):
            raise ValueError('Completed episode result changed')
        item = copy.deepcopy(episode)
        item['evidence_job_id'] = source_id
        item['original_episode'] = episode['episode']
        item['evaluation_seed'] = settings['seed']+10000+index if phase != 'random_baseline' else settings['seed']
        completed[PHASES[phase]].append(item)
        evidence_hashes[path.relative_to(root).as_posix()] = _hash(path)
    for phase, episodes in completed.items():
        if len({episode['task_id'] for episode in episodes}) != len(episodes):
            raise ValueError('Completed task output is duplicated')
        episodes.sort(key=lambda episode: tasks[episode['task_id']][0])
        if [tasks[episode['task_id']][0] for episode in episodes] != list(range(len(episodes))):
            raise ValueError('Completed evaluation cases do not form a reproducible prefix')
    rng = random.Random(settings['seed'])
    grids = catalog['specification']['actions'] if catalog else legacy.GRIDS
    for episode in completed['random']:
        for row in episode['results']:
            expected = {name + '_index': rng.randrange(len(values)) for name, values in grids.items()}
            if row['action'] != expected:
                raise ValueError('Random baseline actions differ from its fixed seed')
    if not any(completed.values()):
        raise ValueError('No independently verifiable completed evaluation cases to resume')
    return settings, {'source_job_id': source_id, 'source_status': source['status'],
        'source_error': source.get('error'), 'source_compute_seconds': source.get('compute_seconds', 0),
        'source_peak_rss_bytes': source.get('peak_rss_bytes', 0), 'evaluator_sha256': evaluator_hash,
        'checkpoint_job_id': checkpoint_id, 'taskset_hashes': request['taskset']['hashes'],
        'evidence_hashes': evidence_hashes, 'completed': completed}
