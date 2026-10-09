#!/usr/bin/env python3
"""Retain real registered patch/review/ngspice evidence in an isolated workspace.

This controlled fixture submits the known resistor change as the local test
owner. It does not run a model or establish live-model acceptance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analog_factory.operations import InvocationContext
from analog_factory.service import Workspace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ngspice', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    workspace = Workspace(args.output / 'runtime')
    evidence = {'schema_version': 1, 'status': 'failed', 'fixture_review': 'scripted local test owner; no model call'}
    job = None
    try:
        workspace.save_settings({'tools': {'ngspice': str(args.ngspice.resolve())}})
        pid = workspace.projects.create('Reviewed resistor fixture')['id']
        path = 'testbenches/pre.cir'
        original = workspace.projects.read(pid, path)['content']
        proposed = original.replace('RD=3300', 'RD=2200')
        assert original != proposed
        context = InvocationContext(source='cli', project_id=pid)
        def invoke(name, parameters):
            result = workspace.operations.invoke(name, {'project_id': pid, **parameters}, context=context)
            evidence.setdefault('operations', []).append({'operation_id': name, 'trace_id': result['trace_id'],
                'ok': result['ok'], 'version': result['version']})
            if not result['ok']: raise RuntimeError(result['error'])
            return result['data']
        preview = invoke('file.change.preview', {'path': path, 'content': proposed,
                          'expected_sha256': hashlib.sha256(original.encode()).hexdigest()})
        flow = {'schema_version': 2, 'name': 'Reviewed resistor fixture', 'nodes': [
            {'id': 'before', 'type': 'simulate'},
            {'id': 'patch', 'type': 'source_patch', 'config': {'path': path}, 'inputs': {
                'content': {'value': proposed}, 'expected_sha256': {'value': preview['current_sha256']}}},
            {'id': 'after', 'type': 'simulate'}],
            'edges': [{'source': 'before', 'target': 'patch'}, {'source': 'patch', 'target': 'after'}],
            'limits': {'runtime_seconds': 30, 'max_node_executions': 20}}
        job = invoke('workflow.run', {'workflow': flow})
        deadline = time.monotonic() + 35
        reviewed = False
        while time.monotonic() < deadline:
            current = invoke('job.get', {'job_id': job['id']})
            if current.get('pending_review') and not reviewed:
                review = current['pending_review']
                assert review['value']['current_content'] == original
                assert review['value']['candidate_content'] == proposed
                evidence['review'] = review
                invoke('job.control', {'job_id': job['id'], 'action': 'review', 'node_id': review['node_id'],
                                      'approved': True, 'review_token': review['review_token']})
                reviewed = True
            if current['status'] not in ('queued', 'running'):
                evidence['job'] = current
                break
            time.sleep(.05)
        else:
            raise TimeoutError('Fixture did not complete')
        assert current['status'] == 'succeeded', current.get('error')
        before = current['nodes']['before']['outputs']['result']
        after = current['nodes']['after']['outputs']['result']
        evidence['before_run'] = invoke('run.get', {'run_id': before['id']})
        evidence['after_run'] = invoke('run.get', {'run_id': after['id']})
        assert before['source_hashes'][path] == preview['current_sha256']
        assert after['source_hashes'][path] == preview['candidate_sha256']
        assert abs(before['metrics']['vout_mean'] - after['metrics']['vout_mean']) > .001
        evidence['history'] = invoke('file.history', {'path': path})
        assert evidence['history'][0]['sha256'] == preview['current_sha256']
        assert evidence['before_run']['stale'] and not evidence['after_run']['stale']
        evidence['status'] = 'succeeded'
    except Exception as error:
        evidence['error'] = str(error)
        raise
    finally:
        evidence['implementation_sha256'] = {name: hashlib.sha256((Path(__file__).resolve().parents[1] / name).read_bytes()).hexdigest()
            for name in ('analog_factory/file_changes.py', 'analog_factory/workflow_graph.py',
                         'analog_factory/operation_graph.py', 'analog_factory/projects.py',
                         'analog_factory/operation_catalog.py', 'scripts/validate_source_patch.py')}
        (args.output / 'evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
        if job:
            workspace.jobs.cancel(workspace.projects.path(pid), job['id'])
            workspace.jobs.wait(workspace.projects.path(pid), job['id'], 5)
        workspace.agents.close()
        workspace.operations.close()
    print(json.dumps({'status': evidence['status'], 'evidence': str(args.output / 'evidence.json'),
                      'before_vout_mean': before['metrics']['vout_mean'], 'after_vout_mean': after['metrics']['vout_mean']}))


if __name__ == '__main__':
    main()
