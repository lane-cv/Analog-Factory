"""Acceptance scoring contracts; synthetic records are never live evidence."""
import copy
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import time
import sys
import shutil
import subprocess
import unittest
from unittest.mock import patch

from analog_factory.design_agent_validation import evaluate, validate_limits, validate_netlist
from analog_factory.service import Workspace
from analog_factory.operations import InvocationContext
from analog_factory.agent_cli import _command
from scripts.validate_design_agent import (
    approval_stop_reason, cancel_active_runs, final_status, validate, validate_input_closures, validate_workspace)


NETLIST = '''.subckt ota5 inp inn bias vdd vss out
X1 nmirror inp tail vss sky130_fd_pr__nfet_01v8 l=2 w=4
X2 out inn tail vss sky130_fd_pr__nfet_01v8 l=2 w=4
X3 nmirror nmirror vdd vdd sky130_fd_pr__pfet_01v8 l=2 w=8
X4 out nmirror vdd vdd sky130_fd_pr__pfet_01v8 l=2 w=8
X5 tail bias vss vss sky130_fd_pr__nfet_01v8 l=2 w=4
.ends ota5
'''


def waveform(complex_mode=True):
    names = ['frequency', 'v(inp)', 'v(inn)', 'v(out)'] if complex_mode else ['v(v-sweep)', 'v(out)', 'i(vdd)']
    data = {'variables': [{'name': name} for name in names]}
    if complex_mode:
        data['complex_points'] = [[[1, 0], [.5, 0], [-.5, 0], [100, 0]],
                                  [[1e9, 0], [.5, 0], [-.5, 0], [.001, 0]]]
    else:
        data['points'] = [[.9, .9, -20e-6]]
    return data


class DesignAgentScoringTests(unittest.TestCase):
    def setUp(self):
        digest = hashlib.sha256(NETLIST.encode()).hexdigest()
        self.session = {'status': 'succeeded', 'turns': [{'status': 'succeeded', 'reply': 'All targets passed.'}]}
        self.runs = {name: {'id': name, 'kind': 'simulate', 'status': 'succeeded', 'exit_code': 0,
                           'stale': False, 'source_hashes': {'netlists/agent.spice': digest},
                           'artifact_hashes': {'waveforms.raw': 'a' * 64}}
                     for name in ('ac', 'dc')}

    def score(self, **options):
        return evaluate(session=options.get('session', self.session),
                        runs=options.get('runs', self.runs),
                        waveforms=options.get('waveforms', {'ac': waveform(), 'dc': waveform(False)}),
                        source=options.get('source', NETLIST), initial_source='* empty design\n',
                        stop_reason=options.get('stop_reason'))

    def test_scores_complex_output_over_actual_differential_input(self):
        result = self.score()
        self.assertEqual(result['status'], 'pass')
        self.assertAlmostEqual(result['metrics']['gain_db'], 40)
        self.assertAlmostEqual(result['metrics']['supply_power_w'], 36e-6)

    def test_fabricated_success_text_without_simulator_evidence_fails(self):
        self.assertEqual(self.score(runs={})['status'], 'fail')

    def test_failed_simulator_cannot_pass_even_with_good_waveforms(self):
        self.runs['ac']['status'] = 'failed'
        self.assertIn('simulator', ' '.join(self.score()['failures']))

    def test_cancelled_session_cannot_pass_even_with_good_electrical_evidence(self):
        self.session['status'] = 'cancelled'
        self.assertEqual(self.score()['status'], 'fail')

    def test_exhausted_budget_and_runtime_cannot_pass(self):
        for reason in ('scope_budget_exhausted', 'runtime_exhausted', 'out_of_scope'):
            with self.subTest(reason=reason):
                self.assertEqual(self.score(stop_reason=reason)['status'], 'fail')

    def test_stale_or_replaced_source_cannot_pass(self):
        for change in ('stale', 'source'):
            runs = copy.deepcopy(self.runs)
            if change == 'stale':
                runs['ac']['stale'] = True
            else:
                runs['ac']['source_hashes']['netlists/agent.spice'] = 'b' * 64
            self.assertEqual(self.score(runs=runs)['status'], 'fail')

    def test_missing_waveforms_and_changed_ac_input_fail(self):
        self.assertEqual(self.score(waveforms={})['status'], 'fail')
        ac = waveform()
        ac['complex_points'][0][1] = [1, 0]
        self.assertEqual(self.score(waveforms={'ac': ac, 'dc': waveform(False)})['status'], 'fail')

    def test_out_of_spec_gain_is_independently_rejected(self):
        ac = waveform()
        ac['complex_points'][0][-1] = [10, 0]
        self.assertEqual(self.score(waveforms={'ac': ac, 'dc': waveform(False)})['status'], 'fail')

    def test_netlist_rejects_measurement_injection_or_sixth_device(self):
        for suffix in ('.meas ac gain_db param=100\n', NETLIST.splitlines()[1] + '\n'):
            with self.assertRaises(ValueError):
                validate_netlist(NETLIST + suffix)

    def test_rejects_unbounded_or_invalid_limits(self):
        for arguments in ((0, 4, 240), (12, 21, 240), (12, 4, float('inf')), (True, 4, 240)):
            with self.assertRaises(ValueError):
                validate_limits(*arguments)

    def test_driver_rejects_user_workspace_or_existing_runtime(self):
        root = Path(__file__).resolve().parents[1]
        for path in (root / '.workspace', root / '.workspace' / 'nested'):
            with self.assertRaises(ValueError):
                validate_workspace(path)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                validate_workspace(Path(temp))

    def test_driver_distinguishes_exhausted_budget_from_outside_scope(self):
        session = {'scope': {'max_actions': 2, 'max_runs': 1}, 'pending_approval': {
            'tool': 'invoke_operation', 'arguments': {'operation_id': 'run.start', 'parameters': {}}}}
        self.assertEqual(approval_stop_reason(session, {'actions': 1, 'runs': 1}), 'scope_budget_exhausted')
        self.assertEqual(approval_stop_reason(session, {'actions': 0, 'runs': 0}), 'out_of_scope')

    def test_driver_retains_failed_backend_prerequisite_without_electrical_success(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'acceptance'
            report = validate(path, {'tools': {'codex': '/missing/native-codex'}})
            saved = json.loads((path / 'design-agent-validation.json').read_text())
            self.assertEqual(report['status'], 'fail')
            self.assertEqual(saved['independent_runs'], {})
            self.assertIn('unavailable', ' '.join(saved['failures']))
            self.assertTrue(saved['project_id'])
            self.assertNotIn('metrics', saved)

    def test_completed_text_without_changed_design_fails(self):
        result = evaluate(session=self.session, runs=self.runs,
                          waveforms={'ac': waveform(), 'dc': waveform(False)},
                          source=NETLIST, initial_source=NETLIST)
        self.assertIn('Agent did not change the design source', result['failures'])

    def test_late_recording_failure_cannot_leave_a_passing_report(self):
        report = {'status': 'pass', 'failures': ['Trace recording failed after electrical scoring']}
        self.assertEqual(final_status(report), 'fail')

    def test_driver_rejects_transitive_model_control_and_unresolved_closure(self):
        from scripts.validate_design_agent import _decks
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'project'
            root.mkdir()
            (root / 'netlists').mkdir()
            (root / 'testbenches').mkdir()
            pdk = Path(temp) / 'pdk'
            pdk.mkdir()
            (root / 'netlists/agent.spice').write_text(NETLIST)
            library = pdk / 'library.spice'
            child = pdk / 'device.spice'
            library.write_text('.lib tt\n.include "device.spice"\n.endl tt\n')
            decks = _decks(library)
            for path, content in decks.items():
                (root / path).write_text(content)
            child.write_text('.control\nquit\n.endc\n')
            with self.assertRaisesRegex(ValueError, 'control'):
                validate_input_closures(root, decks, pdk)
            child.write_text('.include "$UNRESOLVED"\n')
            with self.assertRaisesRegex(ValueError, 'closure'):
                validate_input_closures(root, decks, pdk)
            child.write_text('.param W=4\n.option scale=1u\n.model nf NMOS\n')
            report = validate_input_closures(root, decks, pdk)
            self.assertEqual(report['status'], 'validated')
            self.assertEqual(report['stages']['ac']['external_dependency_count'], 2)
            self.assertEqual(len(report['implementation_sha256']), 64)


class NativeFiniteScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Workspace(Path(self.temp.name))
        self.workspace.operation_url = 'http://127.0.0.1:8765'
        self.addCleanup(self.workspace.agents.close)
        self.addCleanup(self.workspace.operations.close)
        self.pid = self.workspace.projects.create('Isolated native scope test')['id']
        self.context = InvocationContext(source='cli')
        self.scope = {'operations': ['file.write'], 'paths': ['netlists/agent.spice'], 'max_actions': 1, 'max_runs': 0}

    def create(self):
        result = self.workspace.operations.invoke('agent.session.create', {'project_id': self.pid,
            'backend': 'codex', 'mode': 'automatic', 'scope': self.scope}, self.context)
        self.assertTrue(result['ok'], result.get('error'))
        return result['data']

    def wait(self, sid, wanted):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = self.workspace.agents.get(self.pid, sid)
            if result['status'] in wanted:
                return result
            time.sleep(.01)
        self.fail('Native turn did not settle')

    def test_native_finite_grant_is_consumed_and_exhaustion_waits_without_extra_write(self):
        session = self.create()
        def driver(*args, operation_bridge=None, **kwargs):
            for content in ('first', 'second'):
                self.workspace.operations.invoke_agent_tool(operation_bridge['token'], 'invoke_operation',
                    {'operation_id': 'file.write', 'parameters': {'path': 'netlists/agent.spice', 'content': content}})
            return {'reply': 'Done'}
        with patch('analog_factory.agents.shutil.which', return_value='/fake/codex'), patch('analog_factory.agents.run_cli_turn', driver):
            self.workspace.agents.send(self.pid, session['id'], {'message': 'Write twice'})
            record = self.wait(session['id'], {'waiting_approval', 'failed'})
            self.assertEqual(record['status'], 'waiting_approval', record)
            self.assertEqual(self.workspace.projects.read(self.pid, 'netlists/agent.spice')['content'], 'first')
            self.assertEqual(record['pending_approval']['arguments']['parameters']['content'], 'second')
            self.workspace.agents.cancel(self.pid, session['id'])
            self.wait(session['id'], {'cancelled'})
        saved = json.loads(next((self.workspace.root / '.agent-scopes').glob('*.json')).read_text())
        self.assertEqual(saved['actions'], 1)

    def test_native_tool_evidence_is_bounded_without_losing_completion_or_artifact_reference(self):
        session = self.create()
        def driver(*args, operation_bridge=None, **kwargs):
            emit = args[7]
            for index in range(20):
                emit({'type': 'tool_result', 'tool': 'analog_factory/invoke_operation',
                      'tool_call_id': str(index), 'text': json.dumps({'run_id': 'a' * 32, 'details': '电' * 24000}, ensure_ascii=False)})
            return {'reply': 'Actual record remains accessible by run ID'}
        with patch('analog_factory.agents.shutil.which', return_value='/fake/codex'), patch('analog_factory.agents.run_cli_turn', driver):
            self.workspace.agents.send(self.pid, session['id'], {'message': 'Poll real records'})
            record = self.wait(session['id'], {'succeeded', 'failed'})
        self.assertEqual(record['status'], 'succeeded', record['turns'][-1].get('error'))
        for event in record['turns'][-1]['events']:
            self.assertLessEqual(len(event['text'].encode()), 16400)
            self.assertTrue(event['truncated'])
            self.assertGreater(event['original_bytes'], 72000)
            self.assertIn('a' * 32, event['text'])
            self.assertNotIn('\ufffd', event['text'])
        if shutil.which('node'):
            script = '''const fs=require('fs'),vm=require('vm'),assert=require('assert');
const c={window:{},URL};vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),c);
const event=JSON.parse(fs.readFileSync(0,'utf8'));
const html=c.window.AFAgentContent.renderTurn({id:'preview',message:'Inspect',status:'succeeded',events:[event]});
assert(html.includes('display truncated'));assert(html.includes(String(event.original_bytes)+' original bytes'));
'''
            result = subprocess.run(['node', '-e', script, str(Path(__file__).resolve().parents[1] / 'analog_factory/static/agent-content.js')],
                                    input=json.dumps(record['turns'][-1]['events'][0]), text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_assistant_output_budget_still_rejects_oversized_prose(self):
        session = self.create()
        def driver(*args, **kwargs):
            args[7]({'type': 'message', 'text': 'x' * (1024 * 1024 + 1)})
            return {'reply': 'Should not complete'}
        with patch('analog_factory.agents.shutil.which', return_value='/fake/codex'), patch('analog_factory.agents.run_cli_turn', driver):
            self.workspace.agents.send(self.pid, session['id'], {'message': 'Huge prose'})
            record = self.wait(session['id'], {'succeeded', 'failed'})
        self.assertEqual(record['status'], 'failed')
        self.assertIn('output limit', record['turns'][-1]['error'])

    def test_clipped_tool_display_preserves_full_real_run_artifact(self):
        target = self.workspace.projects.path(self.pid)
        run = self.workspace.runner.start(target, 'simulate',
            [sys.executable, '-c', 'print("x" * 72000)'], timeout=5)
        run = self.workspace.runner.wait(target, run['id'], timeout=6)
        session = self.create()
        observed = {}
        def driver(*args, operation_bridge=None, **kwargs):
            result = self.workspace.operations.invoke_agent_tool(operation_bridge['token'], 'invoke_operation',
                {'operation_id': 'run.artifact', 'parameters': {'run_id': run['id'], 'path': 'process.log'}})
            observed.update(result)
            args[7]({'type': 'tool_result', 'tool': 'analog_factory/invoke_operation', 'text': json.dumps(result)})
            return {'reply': 'Artifact is available'}
        with patch('analog_factory.agents.shutil.which', return_value='/fake/codex'), patch('analog_factory.agents.run_cli_turn', driver):
            self.workspace.agents.send(self.pid, session['id'], {'message': 'Read artifact'})
            record = self.wait(session['id'], {'succeeded', 'failed'})
        self.assertEqual(record['status'], 'succeeded', record['turns'][-1].get('error'))
        self.assertTrue(record['turns'][-1]['events'][0]['truncated'])
        self.assertEqual(base64.b64decode(observed['data']['content_base64']), b'x' * 72000 + b'\n')
        self.assertEqual((target / '.af/runs' / run['id'] / 'output/process.log').stat().st_size, 72001)

    def test_missing_or_forged_native_grants_cannot_start(self):
        session = self.create()
        path = self.workspace.projects.path(self.pid) / '.af/agent-sessions' / (session['id'] + '.json')
        original = json.loads(path.read_text())
        for field, value in (('scope_grant', None), ('scope', {**self.scope, 'max_actions': 100})):
            record = copy.deepcopy(original)
            record[field] = value
            path.write_text(json.dumps(record))
            with self.assertRaises(PermissionError):
                self.workspace.agents.send(self.pid, session['id'], {'message': 'Escalate'})

    def test_native_automatic_has_no_direct_write_tools_or_unbridged_execution(self):
        argv = _command('codex', '/fake/codex', '', 'automatic', None,
                        operation_bridge={'url': 'http://localhost:1', 'token': 'not-in-argv'})
        self.assertIn('sandbox_mode="read-only"', argv)
        self.assertIn('features.shell_tool=false', argv)
        self.assertNotIn('not-in-argv', argv)
        with self.assertRaises(ValueError):
            _command('codex', '/fake/codex', '', 'automatic', None)

    def test_driver_cancels_active_child_process_through_run_operation(self):
        record = self.workspace.runner.start(self.workspace.projects.path(self.pid), 'simulate',
            [sys.executable, '-c', 'import time; time.sleep(60)'], timeout=60)
        def invoke(name, parameters):
            result = self.workspace.operations.invoke(name, parameters, self.context)
            self.assertTrue(result['ok'], result)
            return result['data']
        records = cancel_active_runs(invoke, self.pid)
        self.assertEqual(next(r for r in records if r['id'] == record['id'])['status'], 'cancelled')


if __name__ == '__main__':
    unittest.main()
