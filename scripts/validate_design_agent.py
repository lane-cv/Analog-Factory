#!/usr/bin/env python3
"""Bounded live Codex OTA design acceptance through registered operations.

Uses existing native login without copying credentials or changing global config.
The report and unsuccessful sessions remain in a new isolated runtime directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analog_factory.design_agent_validation import (
    AC_PATH, DC_PATH, DESIGN_PATH, INITIAL_SOURCE, evaluate, validate_limits, validate_netlist)
from analog_factory.operations import InvocationContext
from analog_factory.server import create_server
from analog_factory.service import Workspace

TERMINAL = {'succeeded', 'failed', 'cancelled', 'timed_out', 'interrupted', 'unavailable'}


def validate_workspace(path):
    path = Path(path).resolve()
    user_workspace = (ROOT / '.workspace').resolve()
    if path == user_workspace or user_workspace in path.parents:
        raise ValueError('Use a dedicated validation workspace outside the user .workspace')
    if path.exists():
        raise ValueError('Use a new validation workspace; existing runtime evidence is preserved')
    return path


def approval_stop_reason(session, usage):
    scope = session.get('scope', {})
    pending = session.get('pending_approval') or {}
    arguments = pending.get('arguments') or {}
    is_run = arguments.get('operation_id') == 'run.start' or pending.get('tool') == 'run_simulation'
    if usage.get('actions', 0) >= scope.get('max_actions', 100) or (
            is_run and usage.get('runs', 0) >= scope.get('max_runs', 20)):
        return 'scope_budget_exhausted'
    return 'out_of_scope'


def cancel_active_runs(invoke, pid):
    """Do not leave a submitted EDA child running after its model turn ends."""
    records = invoke('run.list', {'project_id': pid})
    active = [record['id'] for record in records if record['status'] not in TERMINAL]
    for run_id in active:
        invoke('run.cancel', {'project_id': pid, 'run_id': run_id})
    deadline = time.monotonic() + 10
    while active:
        records = invoke('run.list', {'project_id': pid})
        active = [record['id'] for record in records if record['status'] not in TERMINAL]
        if time.monotonic() >= deadline:
            raise RuntimeError('Cancelled EDA child did not drain within 10 seconds')
        if active:
            time.sleep(.1)
    return records


def final_status(report):
    """A late persistence/inspection failure must not retain an earlier pass."""
    return 'fail' if report.get('failures') else report['status']


def _public_probe(executable):
    result = {}
    for arguments, key in ((['--version'], 'version'), (['login', 'status'], 'login')):
        try:
            process = subprocess.run([executable, *arguments], capture_output=True, text=True, timeout=10)
            combined = process.stdout + process.stderr
            # Only these public facts leave the subprocess; arbitrary CLI output
            # is never copied into the report, even on failure.
            value = re.search(r'codex-cli [0-9.]+', combined)
            result[key] = {'exit_code': process.returncode}
            if key == 'version':
                result[key]['value'] = value[0] if value else None
            else:
                result[key]['logged_in'] = process.returncode == 0 and 'Logged in' in combined
        except (OSError, subprocess.SubprocessError):
            result[key] = {'exit_code': None, 'available': False}
    return result


def _decks(model):
    common = (f'* Immutable live design-agent acceptance harness.\n.lib "{model}" tt\n'
              f'.include "{DESIGN_PATH}"\nXOTA inp inn bias vdd 0 out ota5\n'
              'VDD vdd 0 1.8\nVP inp 0 dc 0.9 ac 0.5\nVN inn 0 dc 0.9 ac 0.5 180\n'
              'VB bias 0 0.65\nCL out 0 1p\n.temp 27\n')
    return {AC_PATH: common + '.ac dec 40 1 1g\n.meas ac gain_db find vdb(out) at=1\n'
                           '.meas ac unity_gain_hz when vdb(out)=0 fall=1\n.end\n',
            DC_PATH: common + '.dc VP 0.899 0.901 0.001\n'
                           '.meas dc output_dc_v find v(out) at=0.9\n'
                           '.meas dc supply_current_a find i(VDD) at=0.9\n.end\n'}


def validate_input_closures(project, decks, pdk_root):
    """Use the shared closed-input guard before trusting independent raw data."""
    from analog_factory import characterization
    from analog_factory.eda import input_dependencies

    project = Path(project).resolve()
    result = {'status': 'validated', 'implementation_sha256': hashlib.sha256(
        Path(characterization.__file__).read_bytes()).hexdigest(), 'stages': {}}
    for stage, path in (('ac', AC_PATH), ('dc', DC_PATH)):
        content = (project / path).read_text()
        if content != decks[path]:
            raise ValueError('Agent modified an immutable evaluation testbench')
        closure = input_dependencies(project, path, {'PDK_ROOT': str(pdk_root)})
        unresolved = [reason for reason in closure['provenance']['limitations'] if reason !=
                      'External model files are hashed but are not included in the immutable input snapshot']
        if unresolved:
            raise ValueError('Acceptance requires a closed source/model include closure: ' + unresolved[0])
        characterization.validate_source_closure(
            project, path, content, closure['external_dependencies'], pdk_root)
        dependencies = closure['external_dependencies']
        result['stages'][stage] = {
            'source_sha256': hashlib.sha256(content.encode()).hexdigest(),
            'external_dependency_count': len(dependencies),
            'external_dependencies_sha256': hashlib.sha256(
                json.dumps(dependencies, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
    return result


def validate(workspace_path, config, *, model=None, effort=None, max_actions=12, max_runs=4, runtime=240):
    validate_limits(max_actions, max_runs, runtime)
    workspace_path = validate_workspace(workspace_path)
    workspace_path.mkdir(parents=True)
    report_path = workspace_path / 'design-agent-validation.json'
    report = {'schema_version': 1, 'status': 'fail', 'evidence_kind': 'live-native-model-attempt',
              'backend': 'codex', 'model': model, 'effort': effort, 'workspace': str(workspace_path),
              'limits': {'max_actions': max_actions, 'max_runs': max_runs, 'runtime_s': runtime,
                         'native_turn_timeout_s': 300, 'independent_runs': 2, 'independent_run_timeout_s': 180},
              'coverage': 'Five-MOS SKY130 schematic/netlist design at TT/27 C; no layout or PVT signoff.',
              'operations': [], 'agent_runs': [], 'independent_runs': {}, 'failures': []}
    workspace, server, server_thread, pid, sid = None, None, None, None, None
    session, final_source, stop_reason = {}, INITIAL_SOURCE, None
    started = time.monotonic()
    try:
        workspace = Workspace(workspace_path)
        registry = workspace.operations
        context = InvocationContext(source='cli')

        def invoke(name, parameters):
            version = registry.version(parameters.get('project_id'))
            envelope = registry.invoke(name, parameters, context, expected_version=version)
            report['operations'].append({'operation_id': name, 'trace_id': envelope['trace_id'],
                                         'ok': envelope['ok'], 'task_id': envelope.get('task_id')})
            if not envelope['ok']:
                raise RuntimeError(f'{name}: {envelope.get("error", {}).get("message", "failed")}')
            return envelope['data']

        invoke('settings.update', {'settings': config})
        pid = invoke('project.create', {'name': 'Live native five-MOS OTA design', 'template': 'sky130'})['id']
        report['project_id'] = pid
        backends = invoke('agent.backends', {'project_id': pid})['backends']
        native = next(row for row in backends if row['id'] == 'codex')
        if not native['available']:
            raise RuntimeError('Configured Codex native executable is unavailable')
        report['native_probe'] = _public_probe(native['path'])
        if not report['native_probe']['login'].get('logged_in'):
            raise RuntimeError('Native Codex public login status does not confirm authentication')
        catalog = invoke('agent.models', {'backend': 'codex', 'project_id': pid, 'refresh': True})
        report['model_catalog_source'] = catalog.get('source')
        if model is None:
            default = next((row for row in catalog.get('models', []) if row.get('is_default')), None)
            if default is None:
                raise RuntimeError('Native model catalog did not advertise a default; supply --model explicitly')
            model = default['id']
            if effort is None:
                effort = default.get('default_effort')
        report.update(model=model, effort=effort, model_selection='explicit CLI option or advertised native default')
        model_library = Path(config.get('pdk_root', '')) / config.get('pdk_variant', 'sky130A') / 'libs.tech/combined/sky130.lib.spice'
        if not model_library.is_file():
            raise RuntimeError('Configured scalable SKY130 library is missing')
        decks = _decks(model_library.resolve())
        for path, content in {DESIGN_PATH: INITIAL_SOURCE, **decks}.items():
            invoke('file.write', {'project_id': pid, 'path': path, 'content': content})
        initial_version = registry.version(pid)
        report['source_before'] = {'revision': initial_version, 'sha256': hashlib.sha256(INITIAL_SOURCE.encode()).hexdigest()}
        # Explicitly issued human grant; only design writes and the two fixed
        # testbenches are within automatic authority. No approval is autoanswered.
        scope = {'operations': ['file.write', 'run.start'], 'paths': [DESIGN_PATH, AC_PATH, DC_PATH],
                 'max_actions': max_actions, 'max_runs': max_runs}
        # Deck writes are not intended actions: their hashes are checked after
        # the turn and before independent execution, irrespective of grant paths.
        server = create_server(workspace, 0, desktop_url='')
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        session = invoke('agent.session.create', {'project_id': pid, 'backend': 'codex',
                         'mode': 'automatic', 'scope': scope, 'model': model, 'effort': effort})
        sid = session['id']
        report['session_id'] = sid
        prompt = (f'Design a real five-transistor SKY130 OTA in {DESIGN_PATH}. The current file is an empty interface. '
                  'Use three sky130_fd_pr__nfet_01v8 and two sky130_fd_pr__pfet_01v8 X devices: an NMOS '
                  'differential input pair, a PMOS current mirror load, and a bias-driven NMOS tail current device. '
                  'Use only nodes inp inn bias vdd vss out tail nmirror; explicit numeric l and w in um between '
                  '0.15 and 100. Exactly five unique X records within .subckt ota5 inp inn bias vdd vss out and '
                  '.ends ota5; no other directives/elements. Choose connectivity and dimensions yourself. '
                  'At TT, 27 C, VDD=1.8 V, input common-mode 0.9 V, external bias 0.65 V, CL=1 pF, achieve '
                  'differential gain >30 dB at 1 Hz, a downward unity gain crossing, output bias inside 0.2–1.6 V, '
                  'supply current magnitude below 100 uA. Discover registry schemas then use file.write and '
                  f'run.start simulate with explicit netlist {AC_PATH} and {DC_PATH}. Read actual run results/logs, '
                  'Set timeout=180 on each simulator run: this prepared PDK has observed 30–50 second startup '
                  'and may be slower under load. Run the two simulations sequentially, waiting for the first '
                  'to finish before starting the second. Prefer subscribe_events with wait=25 over frequent '
                  'run.get polling; use run.get view=summary or compact get_operation_status for progress. '
                  'Request full provenance once after completion. The native model turn itself has a '
                  '300 second timeout, so finish within that stricter bound. '
                  'iterate if needed, and report failures honestly. Testbench files are fixed evaluation assets: '
                  f'do not edit them. Automatic budget: {max_actions} mutations including at most {max_runs} '
                  f'simulation runs, and {runtime:g} seconds. Only write {DESIGN_PATH}. Stop when done or when '
                  'budget is insufficient. Native shell/filesystem writes are unavailable; use shared MCP operations.')
        report['prompt'] = prompt
        invoke('agent.session.send', {'project_id': pid, 'session_id': sid, 'message': prompt,
                                     'files': [DESIGN_PATH, AC_PATH, DC_PATH]})
        deadline = time.monotonic() + runtime
        while True:
            session = invoke('agent.session.get', {'project_id': pid, 'session_id': sid})
            if session['status'] in TERMINAL and (pid, sid) not in workspace.agents.active:
                break
            if session['status'] == 'waiting_approval':
                usage = _scope_usage(workspace, pid, sid)
                stop_reason = approval_stop_reason(session, usage)
                invoke('agent.session.cancel', {'project_id': pid, 'session_id': sid})
                break
            if time.monotonic() >= deadline:
                stop_reason = 'runtime_exhausted'
                invoke('agent.session.cancel', {'project_id': pid, 'session_id': sid})
                break
            time.sleep(.25)
        workspace.agents.close()  # Drain the worker before inspecting final files.
        session = invoke('agent.session.get', {'project_id': pid, 'session_id': sid})
        report['session'] = session
        report['scope_usage'] = _scope_usage(workspace, pid, sid)
        report['agent_runs'] = cancel_active_runs(invoke, pid)
        final_source = invoke('file.read', {'project_id': pid, 'path': DESIGN_PATH})['content']
        report['source_after'] = {'revision': registry.version(pid), 'sha256': hashlib.sha256(final_source.encode()).hexdigest(),
                                  'content': final_source}
        if session['status'] != 'succeeded' or stop_reason:
            report['failures'].append(stop_reason or 'Native agent session failed')
        if final_source != INITIAL_SOURCE:
            validate_netlist(final_source)
            for path, content in decks.items():
                if invoke('file.read', {'project_id': pid, 'path': path})['content'] != content:
                    raise RuntimeError('Agent modified an immutable evaluation testbench')
            report['input_closure'] = validate_input_closures(
                workspace.projects.path(pid), decks, config.get('pdk_root'))
            raw = {}
            for stage, path in (('ac', AC_PATH), ('dc', DC_PATH)):
                record = invoke('run.start', {'project_id': pid, 'kind': 'simulate',
                                 'options': {'netlist': path}, 'timeout': 180})
                run_deadline = time.monotonic() + 190
                while record['status'] not in TERMINAL:
                    if time.monotonic() > run_deadline:
                        invoke('run.cancel', {'project_id': pid, 'run_id': record['id']})
                        raise RuntimeError('Independent simulator exceeded its finite deadline')
                    time.sleep(.1)
                    record = invoke('run.get', {'project_id': pid, 'run_id': record['id']})
                report['independent_runs'][stage] = record
                if record['status'] == 'succeeded':
                    raw[stage] = invoke('run.waveforms', {'project_id': pid, 'run_id': record['id']})
            # Re-read records after both runs, so edits/staleness cannot hide.
            for stage, record in report['independent_runs'].copy().items():
                report['independent_runs'][stage] = invoke('run.get', {'project_id': pid, 'run_id': record['id']})
            verdict = evaluate(session=session, runs=report['independent_runs'], waveforms=raw,
                               source=final_source, initial_source=INITIAL_SOURCE, stop_reason=stop_reason)
            report.update(verdict)
            report['waveform_metrics'] = verdict['metrics']
        report['operation_events'] = registry.subscribe_events(after=0, wait=0, context=context)['events']
        report['transport_completed'] = session.get('status') == 'succeeded'
        report['agent_tool_execution'] = any(event.get('type') == 'tool_result'
                                           for turn in session.get('turns', []) for event in turn.get('events', []))
    except Exception as error:
        report['failures'].append(str(error))
    finally:
        if workspace is not None:
            workspace.agents.close()
            if pid:
                try:
                    records = cancel_active_runs(invoke, pid)
                    if not report['agent_runs']:
                        report['agent_runs'] = records
                except (OSError, ValueError, RuntimeError) as error:
                    report['status'] = 'fail'
                    report['failures'].append(f'Child cleanup failed: {error}')
            if pid and sid:
                try:
                    report['session'] = workspace.agents.get(pid, sid)
                    report['scope_usage'] = _scope_usage(workspace, pid, sid)
                except (OSError, ValueError):
                    pass
            if server is not None:
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=2)
            else:
                workspace.operations.close()
        report['elapsed_s'] = time.monotonic() - started
        report['status'] = final_status(report)
        report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    return report


def _scope_usage(workspace, pid, sid):
    # Read only the local grant's counters; its private grant ID never leaves
    # runtime storage and is not a provider credential.
    from analog_factory.agent_scope import current
    record = workspace.agents._load(pid, sid)
    _, approved = current(workspace, pid, record)
    return {'actions': approved['actions'], 'runs': approved['runs']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings', type=Path, default=ROOT / '.validation/settings.json')
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--model')
    parser.add_argument('--effort')
    parser.add_argument('--max-actions', type=int, default=12)
    parser.add_argument('--max-runs', type=int, default=4)
    parser.add_argument('--runtime', type=float, default=240)
    args = parser.parse_args()
    try:
        settings = json.loads(args.settings.read_text())
        # Only public EDA configuration is copied. Native CLI owns its login.
        config = {key: settings[key] for key in ('tools', 'pdk_root', 'pdk_variant', 'pdk_version') if key in settings}
        report = validate(args.workspace, config, model=args.model, effort=args.effort,
                          max_actions=args.max_actions, max_runs=args.max_runs, runtime=args.runtime)
    except (OSError, ValueError) as error:
        print(f'Design-agent acceptance prerequisite failed: {error}', file=sys.stderr)
        return 1
    print(json.dumps({'status': report['status'], 'report': str(args.workspace / 'design-agent-validation.json'),
                      'failures': report['failures'], 'metrics': report.get('metrics', {})}, indent=2))
    return 0 if report['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
