#!/usr/bin/env python3
"""Produce actual example evidence without installing dependencies or calling a model."""
import argparse
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analog_factory.service import Workspace
from analog_factory.storage import atomic_json
from analog_factory.templates import tuning_workflow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path('.validation'))
    parser.add_argument('--output', type=Path, default=Path('docs/validation.json'))
    args = parser.parse_args()
    workspace = Workspace(args.workspace)
    project = workspace.projects.create('Reproducible amplifier validation')
    root = workspace.projects.path(project['id'])
    status = workspace.status()
    run = workspace.run(project['id'], 'simulate', {})
    baseline = workspace.runner.wait(root, run['id'])
    if baseline['status'] != 'succeeded':
        print(json.dumps(baseline, indent=2))
        return 1
    flow = workspace.start_workflow(project['id'], tuning_workflow())
    tuning = workspace.jobs.wait(root, flow['id'])
    job = workspace.evaluate(project['id'], {'episodes': 3, 'seed': 42})
    evaluation = workspace.jobs.wait(root, job['id'])
    archive = workspace.root / (project['id'] + '.zip')
    archive.write_bytes(workspace.projects.export(project['id']))
    hashes = {name: hashlib.sha256(Path(tool['path']).read_bytes()).hexdigest()
              for name, tool in status['toolchain']['tools'].items() if tool['available']}
    result = {'generated_at': datetime.now(timezone.utc).isoformat(),
              'host': {'system': platform.platform(), 'python': platform.python_version()},
              'toolchain': status['toolchain'], 'executable_sha256': hashes,
              'full_toolchain_compatibility_validated': False,
              'project_id': project['id'], 'project_path': str(root),
              'archive': str(archive), 'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
              'baseline': baseline, 'tuning': tuning, 'evaluation': evaluation,
              'limitations': ['No SKY130/toolchain GUI validation', 'No real model-provider inference',
                              'No DRC/LVS/extraction/post-layout evidence', 'No model-weight training']}
    atomic_json(args.output, result)
    print(json.dumps({'summary': str(args.output), 'project_id': project['id'],
                      'baseline_metrics': baseline['metrics'], 'tuning_satisfied': tuning.get('satisfied'),
                      'iterations': len(tuning['iterations']), 'evaluation_episodes': evaluation.get('episodes'),
                      'archive': str(archive)}, indent=2))
    return 0 if tuning.get('satisfied') and evaluation['status'] == 'succeeded' else 1


if __name__ == '__main__':
    sys.exit(main())
