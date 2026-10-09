"""Bounded projections of authoritative task records, with full-read links."""
import hashlib
import json
import math


def _text(value, limit=512):
    return value.encode()[:limit].decode(errors='ignore') if isinstance(value, str) else value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def summarize_record(record, project_id, task_kind='run', progress=None):
    """No state inference: project only the record read by the normal handler."""
    summary = {key: _text(record[key]) for key in ('id', 'kind', 'backend', 'status', 'exit_code',
               'stale', 'created_at', 'started_at', 'ended_at', 'error', 'electrical_status',
               'termination', 'completed_runs', 'scenario_count') if key in record and
               (record[key] is None or isinstance(record[key], (str, int, float, bool)))}
    metrics = record.get('metrics') or {}
    eligible = [(key, value) for key, value in sorted(metrics.items()) if isinstance(key, str)
                and len(key.encode()) <= 80 and type(value) in (int, float) and math.isfinite(value)]
    summary['metrics'] = dict(eligible[:32])
    verification = record.get('verification') or {}
    summary['verification'] = {key: _text(verification[key]) for key in ('status', 'reason')
                               if isinstance(verification.get(key), str)}
    qualification = record.get('qualification') or {}
    if isinstance(qualification, dict):
        summary['qualification'] = {key: _text(qualification[key]) for key in ('qualified', 'status', 'reason')
                                    if key in qualification and isinstance(qualification[key], (bool, str))}
    summary['progress'] = {key: _text(value) for key, value in (progress or {}).items()
                           if isinstance(value, (str, int, float, bool)) or value is None} or None
    summary['record_sha256'] = _digest(record)
    summary['source_manifest_sha256'] = _digest({'sources': record.get('source_hashes', {}),
                                               'dependencies': record.get('external_dependencies', {})})
    operation, identifier = {'run': ('run.get', 'run_id'), 'job': ('job.get', 'job_id'),
                             'agent': ('agent.session.get', 'session_id'),
                             'terminal': ('native.terminal.get', 'terminal_id')}[task_kind]
    parameters = {'project_id': project_id, identifier: record['id']}
    if task_kind == 'run':
        parameters['view'] = 'full'
    summary['full_record'] = {'operation_id': operation, 'parameters': parameters}
    artifacts = sorted((record.get('artifact_hashes') or {}).items())
    eligible_artifacts = [(path, digest) for path, digest in artifacts if len(path.encode()) <= 200]
    summary['artifact_operations'] = [
        {'operation_id': 'run.artifact', 'parameters': {'project_id': project_id, 'run_id': record['id'],
         'path': path}, 'sha256': _text(digest, 64)} for path, digest in eligible_artifacts[:8]] if task_kind == 'run' else []
    summary['summary'] = True
    summary['omitted_metrics'] = max(0, len(metrics) - len(summary['metrics']))
    summary['omitted_artifacts'] = max(0, len(artifacts) - len(summary['artifact_operations']))
    summary['summary_limits'] = {'metrics': 32, 'artifacts': 8, 'text_bytes': 512}
    return summary
