"""Core-side supervision of the optional local language-model trainer."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import time

from ..storage import atomic_json, confined, read_json
from ..workflows import invoke_engine
from .environment import TASKSET_ID, GRIDS, MAX_ACTIONS, digest, tasksets

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / '.tools/training'
IDENTIFIER = re.compile(r'[a-f0-9]{32}')
COMPLETION_BUDGET_SEMANTICS = 'policy_and_tool_feedback_v1'
DEFAULTS = {'framework': 'trl_cpu', 'steps': 2, 'train_tasks': 4, 'eval_tasks': 4,
            'seed': 42, 'wall_seconds': 1800, 'taskset_id': TASKSET_ID, 'max_completion_length': 96}


def completion_identity(budget=96, semantics=None):
    """Normalize historical 96-token receipts, rejecting ambiguous new budgets."""
    if type(budget) is not int or not 96 <= budget <= 2048:
        raise ValueError('Completion budget must be an integer from 96 to 2048')
    if semantics is None:
        if budget != 96:
            raise ValueError('Expanded completion budget requires explicit feedback-inclusive semantics')
        semantics = COMPLETION_BUDGET_SEMANTICS
    if semantics != COMPLETION_BUDGET_SEMANTICS:
        raise ValueError('Incompatible completion budget semantics')
    return {'completion_tokens': budget, 'completion_budget_semantics': semantics}


def options(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError('Unknown training options; executable/model/scoring paths are not policy inputs')
    result = {**DEFAULTS, **value}
    if result['framework'] != 'trl_cpu':
        raise ValueError('Only the optional TRL CPU adapter is implemented')
    from .task_catalog import IDENTIFIER as TASK_IDENTIFIER
    if result['taskset_id'] != TASKSET_ID and not TASK_IDENTIFIER.fullmatch(str(result['taskset_id'])):
        raise ValueError('Choose a published taskset identifier')
    for key, low, high in (('steps', 1, 128), ('train_tasks', 2, 64), ('eval_tasks', 1, 32),
                           ('seed', 0, 2**31-1), ('wall_seconds', 60, 14400), ('max_completion_length', 96, 2048)):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f'{key} must be an integer from {low} to {high}')
    return result


def resources():
    ready = all((RUNTIME / path).is_file() for path in
                ('venv/bin/python', 'model/model.safetensors', 'model-source.json', 'runtime.json'))
    qualification = read_json(RUNTIME / 'qualification.json', {'status': 'unqualified'})
    cpu = {'available': ready, 'implemented': True, 'version': '1.14.2',
           'reason': '' if ready else 'Run the optional project-local training setup and download the pinned model',
           'device': 'cpu', 'qualification': qualification}
    frameworks = {'trl_cpu': cpu}
    for name, version in (('agent_lightning', '1.0.2'), ('areal', '2.1.0'), ('agentfly', '0.1.0')):
        frameworks[name] = {'available': False, 'implemented': False, 'version': version,
            'reason': 'Adapter not integrated; documented training requires compatible GPU resources and qualification'}
    mem = {}
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            key, value = line.split(':', 1)
            if key in ('MemTotal', 'MemAvailable'):
                mem[key] = int(value.split()[0]) * 1024
    except OSError:
        pass
    return {'frameworks': frameworks, 'model': read_json(RUNTIME / 'model-source.json', {}),
            'host': {'cpu_threads': os.cpu_count(), 'memory_bytes': mem,
                     'nvidia_detected': Path('/dev/nvidia0').exists()},
            'qualification': qualification, 'runtime': read_json(RUNTIME / 'runtime.json', {})}


def taskset_summary():
    data = tasksets()
    return {'taskset_id': TASKSET_ID, 'counts': {k: len(v) for k, v in data.items()},
            'hashes': {k: digest(v) for k, v in data.items()}, 'bounds': GRIDS,
            'max_actions': MAX_ACTIONS, 'conditions_per_action': 2,
            'coverage': 'Educational level-1 MOS, voltage/temperature probes; not foundry PVT qualification',
            'evaluation_specification': 'Fixed trusted code; sealed test split is never used for weight updates'}


def directory(workspace, project_id, job_id):
    if not IDENTIFIER.fullmatch(str(job_id)):
        raise ValueError('Invalid training job identifier')
    return confined(workspace.projects.path(project_id), f'.af/training/{job_id}', hidden=True)


def get(workspace, project_id, job_id):
    record = workspace.jobs.get(workspace.projects.path(project_id), job_id)
    if record.get('kind') not in ('training', 'training_evaluation', 'training_replay'):
        raise ValueError('This is not a training job')
    root = directory(workspace, project_id, job_id)
    log = confined(root, 'worker.log')
    if log.is_file():
        with log.open('rb') as stream:
            stream.seek(max(0, log.stat().st_size - 12000))
            record['log_tail'] = stream.read(12000).decode(errors='replace')
    return record


def _rss(pid):
    try:
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def _cancel_simulators(workspace, project_id, job_id):
    for run in workspace.runs(project_id):
        if run.get('metadata', {}).get('evaluation_job') == job_id and run['status'] in ('queued', 'running'):
            invoke_engine(workspace, project_id, 'evaluation', 'run.cancel', {'run_id': run['id']})


def start(workspace, project_id, values=None, *, checkpoint_job_id=None, resume_job_id=None):
    values = values or {}
    settings = options(values)
    resume = None
    if resume_job_id:
        if not checkpoint_job_id:
            raise ValueError('Evaluation resume requires the original checkpoint')
        from .resume import prepare
        settings, resume = prepare(workspace, project_id, resume_job_id, checkpoint_job_id, values)
    info = resources()['frameworks']['trl_cpu']
    if not info['available']:
        raise RuntimeError(info['reason'])
    parent = None
    if checkpoint_job_id:
        parent = get(workspace, project_id, checkpoint_job_id)
        if parent.get('imported_evidence') or not parent.get('owner_identity') or not parent.get('model_weights_updated') or parent['status'] != 'succeeded':
            raise ValueError('Evaluation requires a successful local training checkpoint')
        parent_taskset = parent.get('options', {}).get('taskset_id', TASKSET_ID)
        if 'taskset_id' in values and settings['taskset_id'] != parent_taskset:
            raise ValueError('Checkpoint comparison must use its original taskset')
        settings['taskset_id'] = parent_taskset
        checkpoint = directory(workspace, project_id, checkpoint_job_id) / 'checkpoint'
        hashes = parent.get('checkpoint', {}).get('hashes', {})
        required = {'adapter_model.safetensors', 'adapter_config.json'}
        actual = {path.relative_to(checkpoint).as_posix() for path in checkpoint.rglob('*') if path.is_file()}
        if (not isinstance(hashes, dict) or not required <= hashes.keys() or actual != set(hashes)
                or sum(confined(checkpoint, name).stat().st_size for name in actual) > 64*1024**2):
            raise ValueError('Checkpoint must contain the exact recorded safetensors adapter and configuration files')
        for relative, expected in hashes.items():
            path = confined(checkpoint, relative)
            if not isinstance(expected, str) or not re.fullmatch(r'[a-f0-9]{64}', expected) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('Checkpoint content changed after qualification')
    from . import task_catalog
    catalog = None if settings['taskset_id'] == TASKSET_ID else task_catalog.get(workspace, project_id, settings['taskset_id'])
    summary = taskset_summary() if catalog is None else task_catalog.describe(catalog)
    if settings['train_tasks'] > summary['counts']['train'] or settings['eval_tasks'] > summary['counts']['test']:
        raise ValueError('Requested task counts exceed this taskset split')
    root = workspace.projects.path(project_id)
    def run(job, cancel, update):
        target = directory(workspace, project_id, job['id'])
        target.mkdir(parents=True, exist_ok=False)
        request = {'workspace': str(workspace.root), 'project_id': project_id, 'job_id': job['id'],
                   'options': settings, 'output': str(target), 'runtime': str(RUNTIME),
                   'checkpoint_job_id': checkpoint_job_id, 'taskset': summary, 'taskset_record': catalog,
                   'resume': resume, 'completion_budget_semantics': COMPLETION_BUDGET_SEMANTICS,
                   'source_hashes': {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                                     for name in ('manager.py', 'worker.py', 'environment.py', 'task_catalog.py', 'configurable_environment.py', 'resume.py')}}
        sources = target / 'sources'
        sources.mkdir()
        for name, expected in request['source_hashes'].items():
            content = (Path(__file__).parent / name).read_bytes()
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError('Training source changed during request preparation')
            (sources / name).write_bytes(content)
        atomic_json(target / 'request.json', request)
        env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL', 'TMPDIR') if k in os.environ}
        env.update(PYTHONPATH=str(ROOT), HF_HOME=str(RUNTIME / 'cache/huggingface'),
                   HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                   HF_HUB_DISABLE_IMPLICIT_TOKEN='1', WANDB_MODE='disabled',
                   TOKENIZERS_PARALLELISM='false', OMP_NUM_THREADS='4', MKL_NUM_THREADS='4')
        before = time.monotonic()
        peak = 0
        reason = None
        stopped = None
        with (target / 'worker.log').open('wb') as output:
            for phase in ([], ['--reload']):
                if any(hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() != expected
                       for name, expected in request['source_hashes'].items()):
                    raise ValueError('Training source changed before worker launch; start a new qualified job')
                process = subprocess.Popen([str(RUNTIME / 'venv/bin/python'), '-m', 'analog_factory.training.worker',
                    '--request', str(target / 'request.json'), *phase], cwd=ROOT, env=env, stdout=output,
                    stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    while process.poll() is None:
                        peak = max(peak, _rss(process.pid))
                        progress = read_json(confined(target, 'progress.json'), {})
                        update(**{key: value for key, value in progress.items()
                                  if key in ('phase', 'curves', 'completed_episodes', 'iterations', 'checkpoint', 'evaluation')},
                               peak_rss_bytes=peak, compute_seconds=time.monotonic()-before)
                        if reason is None:
                            reason = ('cancelled' if cancel.is_set() else 'memory_limit' if peak > 8 * 1024**3
                                      else 'wall_limit' if time.monotonic()-before > settings['wall_seconds'] else None)
                        if reason:
                            if stopped is None:
                                (target / 'cancel').write_text(reason)
                                stopped = time.monotonic()
                                _cancel_simulators(workspace, project_id, job['id'])
                            if time.monotonic() - stopped > 10:
                                os.killpg(process.pid, signal.SIGKILL)
                        time.sleep(.25)
                    process.wait()
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    _cancel_simulators(workspace, project_id, job['id'])
                current = read_json(confined(target, 'result.json'), {})
                if any(hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() != expected
                       for name, expected in request['source_hashes'].items()):
                    reason = reason or 'source_changed'
                if reason or process.returncode or current.get('status') != 'checkpoint_saved':
                    break
        result = read_json(confined(target, 'result.json'), {})
        result.update(peak_rss_bytes=peak, compute_seconds=time.monotonic()-before,
                      worker_exit_code=process.returncode)
        if reason or process.returncode or not result.get('status'):
            result.update(status='cancelled' if reason == 'cancelled' else 'failed',
                          error=reason or result.get('error') or 'Worker failed; inspect training log')
        result['source_hashes'] = request['source_hashes']
        if resume:
            result.update(resume={key: value for key, value in resume.items() if key != 'completed'},
                resumed_completed_counts={key: len(value) for key, value in resume['completed'].items()},
                aggregate_compute_seconds=resume['source_compute_seconds'] + result['compute_seconds'])
        if result.get('status') == 'succeeded' and result.get('model_weights_updated') and result.get('evaluation', {}).get('checkpoint_reloaded'):
            atomic_json(RUNTIME / 'qualification.json', {'status': 'verified_weight_update_and_reload',
                'job_id': job['id'], 'model': result.get('model'), 'checkpoint': result.get('checkpoint'),
                'parameter_update_l2': result.get('parameter_update_l2'), 'peak_rss_bytes': peak,
                'compute_seconds': result['compute_seconds'],
                'success_rate_change': result['evaluation'].get('success_rate_change'),
                'performance_improvement_claimed': result['evaluation'].get('success_rate_change', 0) > 0})
        return result
    with workspace.lock:
        if any(job['status'] in ('queued', 'running') for job in workspace.jobs.list(root)):
            raise ValueError('A job is already active for this project')
        return workspace.jobs.start(root, 'training_evaluation' if parent else 'training', run,
            {'options': settings, 'taskset': summary, 'framework': 'trl_cpu',
             'classification': 'language_model_reinforcement_learning', 'model_weights_updated': False,
             'parent_id': checkpoint_job_id, 'resume_source_job_id': resume_job_id,
             'completion_budget_semantics': COMPLETION_BUDGET_SEMANTICS})


def replay(workspace, project_id, job_id, episode):
    from .environment import AnalogEnvironment, parameters
    parent = get(workspace, project_id, job_id)
    if type(episode) is not int or not 0 <= episode <= 100000:
        raise ValueError('Invalid episode index')
    source = read_json(confined(directory(workspace, project_id, job_id), f'episodes/{episode}.json'))
    if not source:
        raise FileNotFoundError('Training episode does not exist')
    from . import task_catalog, configurable_environment
    taskset_id = parent.get('options', {}).get('taskset_id', TASKSET_ID)
    catalog = None if taskset_id == TASKSET_ID else task_catalog.get(workspace, project_id, taskset_id)
    data = tasksets() if catalog is None else catalog['splits']
    task = next((t for group in data.values() for t in group if t['id'] == source['task_id']), None)
    if task is None or digest(task) != source['task_sha256']:
        raise ValueError('Episode task does not match the fixed evaluator taskset')
    actions = [result['action'] for result in source.get('results', [])]
    maximum = MAX_ACTIONS if catalog is None else catalog['specification']['max_actions']
    if not actions or len(actions) > maximum:
        raise ValueError('Episode has no replayable bounded simulator actions')
    for action in actions:
        if catalog is None:
            parameters(action)
        else:
            configurable_environment.parameters(catalog, action)
    root = workspace.projects.path(project_id)
    def execute(job, cancel, update):
        target = directory(workspace, project_id, job['id'])
        target.mkdir(parents=True)
        def record(value):
            atomic_json(target / 'episodes/0.json', value)
            update(iterations=value['results'])
        env = (AnalogEnvironment(workspace, project_id, job['id'], lambda: 0, cancel, record) if catalog is None
               else configurable_environment.AnalogEnvironment(workspace, project_id, job['id'], lambda: 0, cancel, record, catalog))
        env.reset(task)
        for action in actions:
            env.simulate(**action)
            if env.terminal:
                break
        return {'classification': 'trajectory_replay', 'model_weights_updated': False,
                'source_job_id': job_id, 'source_episode': episode,
                'original_reward': source['reward'], 'replayed': env.snapshot()}
    with workspace.lock:
        if any(job['status'] in ('queued', 'running') for job in workspace.jobs.list(root)):
            raise ValueError('A job is already active for this project')
        return workspace.jobs.start(root, 'training_replay', execute, {'options': {'taskset_id': taskset_id}})
