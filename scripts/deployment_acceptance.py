#!/usr/bin/env python3
"""Exercise a fresh, explicitly authorized deployment with real EDA tools.

This creates acceptance projects and retains their evidence. It requires an
installed desktop profile, an empty projects directory, and an unused output
directory. It never defaults to the current user's workspace.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from run_deployment import assert_port_free, child_environment, load_config, workspace_lock

TERMINAL = {'succeeded', 'failed', 'cancelled', 'timed_out', 'interrupted', 'unavailable'}


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def empty_projects(workspace):
    projects = workspace / 'projects'
    if projects.is_symlink() or (projects.exists() and
            (not projects.is_dir() or any(projects.iterdir()))):
        raise ValueError('Acceptance requires an empty projects directory; existing projects are never adopted')


def public_settings(workspace):
    # Settings are public configuration. Do not open credentials.json, CLI login
    # files, or copy provider settings into the evidence bundle.
    data = json.loads((workspace / 'settings.json').read_text())
    return {key: data[key] for key in ('tools', 'pdk_root', 'pdk_variant', 'pdk_version') if key in data}


class HTTP:
    def __init__(self, base, report, launcher):
        self.base = base
        self.report = report
        self.launcher = launcher
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, body=None, timeout=15):
        if self.launcher.poll() is not None:
            raise RuntimeError(f'Deployment launcher exited with status {self.launcher.returncode}')
        request = urllib.request.Request(self.base + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Content-Type': 'application/json', 'X-AF-Client': 'deployment-acceptance'})
        try:
            with self.opener.open(request, timeout=timeout) as response:
                payload = response.read(8 * 1024 * 1024 + 1)
                if response.status != 200 or len(payload) > 8 * 1024 * 1024:
                    raise RuntimeError(f'{path}: invalid HTTP response {response.status}')
                return payload
        except urllib.error.HTTPError as error:
            detail = error.read(4096).decode(errors='replace')
            raise RuntimeError(f'{path}: HTTP {error.code}: {detail}') from error

    def ready(self, path, timeout=45):
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            try:
                payload = self.request(path, timeout=2)
                self.report['checks']['http'][path] = {'status': 200, 'bytes': len(payload)}
                return payload
            except (OSError, RuntimeError) as error:
                last_error = error
                if self.launcher.poll() is not None:
                    raise
                time.sleep(.2)
        raise TimeoutError(f'Timed out waiting for {path}: {last_error}')

    def invoke(self, operation, parameters=None):
        envelope = json.loads(self.request('/api/operations/invoke', {
            'operation_id': operation, 'parameters': parameters or {}}))
        self.report['operations'].append({'operation_id': operation,
            'ok': envelope.get('ok'), 'trace_id': envelope.get('trace_id'),
            'task_id': envelope.get('task_id'), 'status': envelope.get('status')})
        if not envelope.get('ok'):
            raise RuntimeError(f'{operation}: {envelope.get("error")}')
        return envelope['data']


def run_summary(record):
    return {key: record.get(key) for key in ('id', 'kind', 'status', 'exit_code',
            'command', 'tool_versions', 'metrics', 'verification', 'error')}


def poll_run(http, project_id, record, timeout=190):
    deadline = time.monotonic() + timeout
    while record['status'] not in TERMINAL:
        if time.monotonic() >= deadline:
            http.invoke('run.cancel', {'project_id': project_id, 'run_id': record['id']})
            raise TimeoutError(f'{record["kind"]} run exceeded acceptance deadline')
        time.sleep(.2)
        record = http.invoke('run.get', {'project_id': project_id, 'run_id': record['id']})
    return record


def save_run(http, output, project_id, record, name):
    logs = http.invoke('run.log', {'project_id': project_id, 'run_id': record['id']})
    (output / f'{name}.log').write_text(logs['content'])
    summary = run_summary(record)
    summary['log'] = f'{name}.log'
    return summary


def exercise_http(http, output, report, active_runs):
    homepage = http.ready('/')
    if b'<html' not in homepage.lower():
        raise ValueError('The root route did not return the application HTML')
    catalog = json.loads(http.ready('/api/operations'))
    desktop = http.ready('/desktop/vnc.html')
    if b'novnc' not in desktop.lower():
        raise ValueError('The desktop route did not return real noVNC HTML')
    operations = {entry['operation_id']: entry for entry in catalog['operations']}
    templates = operations['project.create']['input_schema']['properties']['template']['enum']
    if 'ota5' not in templates:
        raise ValueError('The live operation catalog does not offer the ota5 template')
    report['checks']['catalog'] = {'operation_count': len(operations), 'templates': templates}
    if http.invoke('project.list'):
        raise ValueError('The running workspace contains projects; refusing acceptance mutations')
    status = http.invoke('workspace.status')
    report['toolchain'] = status['toolchain']
    project = http.invoke('project.create', {'name': 'Deployment HTTP OTA acceptance', 'template': 'ota5'})
    project_id = project['id']
    report['http_project_id'] = project_id
    record = http.invoke('run.start', {'project_id': project_id, 'kind': 'simulate', 'timeout': 180})
    active_runs.append((project_id, record['id']))
    record = poll_run(http, project_id, record)
    report['checks']['http_simulation'] = save_run(http, output, project_id, record, 'http-simulation')
    if record['status'] != 'succeeded':
        raise RuntimeError(f'HTTP simulation failed: {record.get("error") or record["status"]}')
    gain = record['metrics'].get('gain_db')
    if not isinstance(gain, (int, float)) or not math.isfinite(gain) or gain <= 30:
        raise AssertionError(f'HTTP OTA simulation did not produce real gain above 30 dB: {gain}')
    active_runs.remove((project_id, record['id']))
    report['checks']['native_gui'] = []
    for kind, field, source in (('xschem', 'schematic', 'schematics/ota5.sch'),
                                ('magic', 'layout', 'layout/ota5.mag')):
        record = http.invoke('run.start', {'project_id': project_id, 'kind': kind,
                            'options': {field: source}, 'timeout': 30})
        active_runs.append((project_id, record['id']))
        parameters = {'project_id': project_id, 'run_id': record['id']}
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            time.sleep(.2)
            record = http.invoke('run.get', parameters)
            if record['status'] in TERMINAL:
                report['checks']['native_gui'].append(save_run(http, output, project_id, record, kind))
                raise RuntimeError(f'{kind} GUI exited before cancellation: {record["status"]}')
        if record['status'] != 'running' or not record.get('pid'):
            raise RuntimeError(f'{kind} GUI did not start a real process')
        http.invoke('run.cancel', parameters)
        record = poll_run(http, project_id, record, timeout=12)
        summary = save_run(http, output, project_id, record, kind)
        summary['observed_running_seconds'] = 3
        report['checks']['native_gui'].append(summary)
        if record['status'] != 'cancelled':
            raise RuntimeError(f'{kind} did not acknowledge cancellation: {record["status"]}')
        active_runs.remove((project_id, record['id']))


def accept(config_path, output):
    config = load_config(config_path)
    workspace = Path(config['workspace']).resolve()
    if not config['desktop']:
        raise ValueError('This acceptance requires a deployment installed with the desktop profile')
    empty_projects(workspace)
    settings = public_settings(workspace)
    if not settings.get('pdk_root'):
        raise ValueError('Acceptance requires configured tools and a prepared SKY130 PDK')
    if output == workspace or output in workspace.parents or workspace in output.parents:
        raise ValueError('Evidence output must be separate from the configured workspace')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('Use a new empty evidence output directory')
    with workspace_lock(workspace):
        assert_port_free(config['host'], config['port'])
        assert_port_free('127.0.0.1', config['desktop_port'])
        empty_projects(workspace)
    output.mkdir(parents=True, exist_ok=True)
    report = {'schema_version': 1, 'status': 'running',
        'started_at': datetime.now(timezone.utc).isoformat(), 'platform': platform.platform(),
        'python': sys.version, 'workspace': str(workspace), 'checks': {'http': {}},
        'operations': [], 'commands': [],
        'limits': 'Validates real local HTTP/noVNC delivery, native GUI process lifecycle, simulation, '
                  'Magic local DRC, Netgen LVS and capacitance-inclusive extraction. '
                  'Browser interaction, agent login/inference, distributed resistance, GPU training '
                  'and fabrication signoff are outside this acceptance.'}
    record_path = output / 'deployment-acceptance.json'
    write_json(record_path, report)
    write_json(output / 'settings-public.json', settings)
    command = [config['python'], str(ROOT / 'scripts/run_deployment.py'), '--config', str(config_path)]
    report['commands'].append(command)
    launcher = None
    http = None
    active_runs = []
    try:
        with (output / 'deployment.log').open('w') as log:
            launcher = subprocess.Popen(command, cwd=ROOT, env=child_environment(config),
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            http = HTTP(f'http://{config["host"]}:{config["port"]}', report, launcher)
            exercise_http(http, output, report, active_runs)
            print('HTTP, noVNC and native GUI checks passed; validating actual OTA DRC/LVS/PEX', flush=True)
            from validate_ota5 import validate
            reference = validate(output / 'ota', settings, template=True)
            report['checks']['ota'] = {'status': reference['status'],
                'report': 'ota/ota5-validation.json', 'parasitic_capacitors': reference['parasitic_capacitors'],
                'gain_delta_db': reference['gain_delta_db'],
                'runs': {key: run_summary(reference[key]) for key in
                         ('xschem', 'drc', 'extract', 'lvs', 'independent_lvs', 'negative_lvs', 'negative_drc')},
                'pre': {key: reference['pre'][key] for key in
                        ('gain_db', 'waveform_gain_db', 'unity_gain_hz', 'output_dc_v', 'supply_current_a')},
                'post': {key: reference['post'][key] for key in
                         ('gain_db', 'waveform_gain_db', 'unity_gain_hz', 'output_dc_v', 'supply_current_a')}}
            if reference['status'] != 'pass':
                raise RuntimeError('The independent OTA reference failed')
            report['status'] = 'pass'
    except (Exception, KeyboardInterrupt) as error:
        report['status'] = 'fail'
        report['error'] = str(error) or type(error).__name__
    finally:
        for project_id, run_id in active_runs:
            try:
                current = http.invoke('run.cancel', {'project_id': project_id, 'run_id': run_id})
                poll_run(http, project_id, current, timeout=12)
            except Exception as error:
                report.setdefault('cleanup_errors', []).append(str(error))
        if launcher is not None:
            if launcher.poll() is None:
                launcher.send_signal(signal.SIGINT)
            try:
                launcher.wait(timeout=20)
            except subprocess.TimeoutExpired:
                # This PID belongs to this invocation; never search for or kill
                # listeners, other Python processes, or another user's desktop.
                launcher.kill()
                launcher.wait(timeout=5)
                report.setdefault('cleanup_errors', []).append('Owned launcher required SIGKILL')
            report['checks']['shutdown'] = {'launcher_exit_code': launcher.returncode}
            if launcher.returncode != 0:
                report['status'] = 'fail'
        try:
            assert_port_free(config['host'], config['port'])
            assert_port_free('127.0.0.1', config['desktop_port'])
            report['checks'].setdefault('shutdown', {})['ports_released'] = True
        except (OSError, ValueError) as error:
            report.setdefault('cleanup_errors', []).append(str(error))
            report['checks'].setdefault('shutdown', {})['ports_released'] = False
        if report.get('cleanup_errors'):
            report['status'] = 'fail'
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        write_json(record_path, report)
    print(json.dumps({'status': report['status'], 'report': str(record_path),
                      'error': report.get('error')}), flush=True)
    return 0 if report['status'] == 'pass' else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--allow-test-workspace', action='store_true',
        help='Explicitly authorize creating acceptance projects in this otherwise empty workspace')
    args = parser.parse_args(argv)
    if not args.allow_test_workspace:
        parser.error('--allow-test-workspace is required; this command creates real acceptance projects')
    def interrupted(signum, frame):
        raise KeyboardInterrupt()

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        return accept(args.config.resolve(), args.output.resolve())
    except (OSError, ValueError, RuntimeError) as error:
        print(f'Deployment acceptance refused: {error}', file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    raise SystemExit(main())
