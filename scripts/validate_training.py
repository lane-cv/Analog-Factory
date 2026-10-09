#!/usr/bin/env python3
"""Opt-in actual CPU language-model update, checkpoint reload and simulator replay.

Uses an isolated new project. Requires scripts/setup_training.py and ngspice.
Failed/no-update and no-improvement results remain visible, never synthesized.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from analog_factory.service import Workspace
from analog_factory.storage import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=ROOT / '.validation/training-reference')
    parser.add_argument('--family', choices=['reference', 'amplifier', 'current_mirror'], default='reference')
    parser.add_argument('--steps', type=int, default=2)
    parser.add_argument('--eval-tasks', type=int, default=2)
    parser.add_argument('--wall-seconds', type=int, default=1800)
    parser.add_argument('--ngspice', type=Path)
    args = parser.parse_args()
    workspace = Workspace(args.workspace.resolve())
    executable = args.ngspice or ROOT / '.tools/eda/bin/ngspice-sky130'
    if not executable.is_file():
        resolved = shutil.which('ngspice')
        if not resolved:
            raise SystemExit('Configure --ngspice with a real installed simulator')
        executable = Path(resolved)
    workspace.save_settings({'tools': {'ngspice': str(executable.resolve())}})
    def invoke(operation, parameters):
        reply = workspace.operations.invoke(operation, parameters)
        if not reply['ok']:
            raise RuntimeError(reply['error'])
        return reply['data']
    project = invoke('project.create', {'name': 'Real language-model training qualification', 'template': 'amplifier'})
    project_id = project['id']
    options = {'steps': args.steps, 'eval_tasks': args.eval_tasks, 'wall_seconds': args.wall_seconds}
    if args.family != 'reference':
        defaults = invoke('training.taskset.list', {'project_id': project_id})['defaults']
        record = invoke('training.taskset.create', {'project_id': project_id, 'specification': defaults[args.family]})
        options['taskset_id'] = record['id']
    job = invoke('training.start', {'project_id': project_id, 'options': options})
    print(json.dumps({'workspace': str(workspace.root), 'project_id': project_id, 'job_id': job['id']}), flush=True)
    def wait(job):
        previous = None
        while job['status'] in ('queued', 'running'):
            job = invoke('training.get', {'project_id': project_id, 'job_id': job['id']})
            state = (job['status'], job.get('phase'), job.get('completed_episodes'))
            if state != previous:
                print(json.dumps({'state': state, 'seconds': job.get('compute_seconds', 0)}), flush=True)
                previous = state
            time.sleep(.5)
        return job
    job = wait(job)
    replay = None
    if job['status'] == 'succeeded':
        episodes = job['evaluation']['random']['episodes']
        episode = next(item for item in episodes if item['calls'])
        replay = wait(invoke('training.replay', {'project_id': project_id, 'job_id': job['id'], 'episode': episode['episode']}))
        if replay['status'] != 'succeeded' or abs(replay['original_reward']-replay['replayed']['reward']) > 1e-9:
            raise RuntimeError('Actual simulator replay did not reproduce its recorded reward')
    evidence = {'project_id': project_id, 'job': job, 'replay': replay}
    atomic_json(workspace.root / 'training-evidence.json', evidence)
    print(json.dumps({key: job.get(key) for key in ('status', 'error', 'model_weights_updated', 'parameter_update_l2',
        'optimizer_steps', 'compute_seconds', 'peak_rss_bytes')}, indent=2))
    if job['status'] != 'succeeded' or not job.get('model_weights_updated'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
