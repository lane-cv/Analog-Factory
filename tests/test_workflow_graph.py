"""The v2 executor must preserve real evidence and reject unsafe graph control."""
import copy
import importlib
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import replace
import sys

from analog_factory.service import Workspace
from analog_factory.workflows import validate_workflow


def literal(value):
    return {'value': value}


def ref(node, port='value', **extra):
    return {'node': node, 'port': port, **extra}


def graph(nodes, edges=None, **extra):
    return {'schema_version': 2, 'name': 'Graph test', 'nodes': nodes,
            'edges': edges or [], 'limits': {'runtime_seconds': 10, 'max_node_executions': 100}, **extra}


def node(identifier, kind, config=None, inputs=None, **extra):
    return {'id': identifier, 'type': kind, 'config': config or {}, 'inputs': inputs or {}, **extra}


class GraphValidationTests(unittest.TestCase):
    def test_v2_accepts_typed_mappings_without_v1_six_node_restriction(self):
        flow = graph([node('a', 'parameter', {'value': 3}),
                      node('b', 'condition', {'operator': 'gt'}, {'left': ref('a'), 'right': literal(2)})])
        self.assertEqual(validate_workflow(flow)['schema_version'], 2)

    def test_rejects_cycles_missing_ports_and_static_type_mismatches(self):
        flow = graph([node('a', 'parameter', {'value': 3}),
                      node('b', 'netlist', {'parameter': 'RD'}, {'content': ref('a'), 'value': literal(5)})])
        with self.assertRaisesRegex(ValueError, 'type'):
            validate_workflow(flow)
        flow['nodes'][1]['inputs']['content'] = ref('a', 'nonexistent')
        with self.assertRaisesRegex(ValueError, 'port'):
            validate_workflow(flow)
        flow['nodes'][1]['inputs']['content'] = literal('.param RD=1')
        flow['edges'] = [{'source': 'a', 'target': 'b'}, {'source': 'b', 'target': 'a'}]
        with self.assertRaisesRegex(ValueError, 'cycle'):
            validate_workflow(flow)

    def test_rejects_unbounded_loop_invalid_retry_and_nonfinite_budgets(self):
        for flow in (graph([node('loop', 'loop', {'body': graph([node('p', 'parameter', {'value': 1})])})]),
                     graph([node('p', 'parameter', {'value': 1}, retries=10000)]),
                     graph([node('p', 'parameter', {'value': 1})], limits={'runtime_seconds': float('nan')})):
            with self.assertRaises(ValueError):
                validate_workflow(flow)

    def test_output_alias_cannot_overwrite_authoritative_review_decision(self):
        flow = graph([node('review', 'human_review', inputs={'value': literal(True)},
                           outputs={'approved': {'path': 'value', 'type': 'boolean'}})])
        with self.assertRaisesRegex(ValueError, 'reserved'):
            validate_workflow(flow)


class GraphExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Workspace(Path(self.tmp.name))
        self.addCleanup(self.workspace.agents.close)
        self.pid = self.workspace.projects.create('Graph execution')['id']
        self.root = self.workspace.projects.path(self.pid)
        self.events = []

    def execute(self, flow, cancel=None, control=None, update=None):
        engine = importlib.import_module('analog_factory.workflow_graph')
        return engine.execute_graph(self.workspace, self.pid, flow, {'id': 'a' * 32},
                                    cancel or threading.Event(), update or (lambda **kw: self.events.append(kw)), control=control)

    def test_branch_skips_invalid_simulation_and_reports_selected_result(self):
        flow = graph([node('check', 'condition', {'operator': 'eq'}, {'left': literal(1), 'right': literal(1)}),
                      node('bad', 'simulate', {'options': {'netlist': 'missing.cir'}}),
                      node('good', 'report', inputs={'value': literal('selected')})],
                     [{'source': 'check', 'target': 'bad', 'when': False},
                      {'source': 'check', 'target': 'good', 'when': True}])
        result = self.execute(flow)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(result['nodes']['bad']['status'], 'skipped')
        self.assertEqual(result['nodes']['good']['outputs']['report']['value'], 'selected')
        self.assertEqual(self.workspace.runs(self.pid), [])

    @unittest.skipUnless(shutil.which('ngspice'), 'actual ngspice required')
    def test_real_simulation_parameter_transform_measure_and_source_preservation(self):
        original = self.workspace.projects.read(self.pid, 'testbenches/pre.cir')['content']
        flow = graph([node('read', 'project', {'path': 'testbenches/pre.cir'}),
                      node('rd', 'parameter', {'value': 2200}),
                      node('deck', 'netlist', {'parameter': 'RD'}, {'content': ref('read', 'content'), 'value': ref('rd')}),
                      node('sim', 'simulate', inputs={'content': ref('deck', 'content')}),
                      node('metric', 'measure', {'metric': 'vout_mean'}, {'result': ref('sim', 'result')}),
                      node('report', 'report', inputs={'bias': ref('metric'), 'run': ref('sim', 'run_id')})])
        result = self.execute(flow)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertGreater(result['nodes']['metric']['outputs']['value'], 0)
        self.assertLess(result['nodes']['metric']['outputs']['value'], 3.3)
        self.assertEqual(len(self.workspace.runs(self.pid)), 1)
        self.assertEqual(self.workspace.projects.read(self.pid, 'testbenches/pre.cir')['content'], original)
        self.assertTrue(any(e.get('active_run') for e in self.events))

    def test_bounded_loop_carries_state_and_stops_on_real_condition(self):
        body = graph([node('index', 'input', {'name': 'index', 'type': 'number'}),
                      node('done', 'condition', {'operator': 'gte'}, {'left': ref('index'), 'right': literal(2)})],
                     outputs={'state': ref('index'), 'done': ref('done', 'result')})
        flow = graph([node('loop', 'loop', {'body': body, 'max_iterations': 5}, {'state': literal(0)})])
        result = self.execute(flow)
        self.assertEqual(result['status'], 'succeeded', result)
        outputs = result['nodes']['loop']['outputs']
        self.assertEqual(len(outputs['iterations']), 3)
        self.assertEqual(outputs['result']['state'], 2)
        self.assertTrue(outputs['satisfied'])

    def test_loop_cannot_reset_global_node_budget(self):
        body = graph([node('p', 'parameter', {'value': 1})])
        flow = graph([node('loop', 'loop', {'body': body, 'max_iterations': 10})],
                     limits={'runtime_seconds': 5, 'max_node_executions': 3})
        result = self.execute(flow)
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(result['termination'], 'node_execution_limit')
        self.assertEqual(result['node_executions'], 3)

    def test_nested_graph_can_tighten_but_not_reset_parent_budgets(self):
        body = graph([node('a', 'parameter', {'value': 1}), node('b', 'parameter', {'value': 2})],
                     limits={'runtime_seconds': 1, 'max_node_executions': 1})
        result = self.execute(graph([node('loop', 'loop', {'body': body, 'max_iterations': 2})]))
        self.assertEqual(result['termination'], 'node_execution_limit', result)
        self.assertEqual(result['node_executions'], 2)

    def test_nested_required_cost_budget_also_blocks_model_before_inference(self):
        body = graph([node('model', 'model', inputs={'prompt': literal('Review')})],
                     limits={'runtime_seconds': 1, 'cost_usd': 1})
        result = self.execute(graph([node('loop', 'loop', {'body': body, 'max_iterations': 1})]))
        self.assertEqual(result['termination'], 'unknown_model_cost', result)

    def test_required_unknown_model_cost_blocks_before_inference(self):
        flow = graph([node('model', 'model', inputs={'prompt': literal('Review')})],
                     limits={'runtime_seconds': 5, 'cost_usd': 1})
        result = self.execute(flow)
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(result['termination'], 'unknown_model_cost')
        self.assertIsNone(result['cost_usd'])
        self.assertEqual(self.workspace.agents.list(self.pid), [])

    def test_model_uses_configured_provider_and_preserves_unknown_cost(self):
        class Endpoint(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                body = json.dumps({'choices': [{'message': {'role': 'assistant', 'content': 'Measured bias is valid.'},
                                                'finish_reason': 'stop'}],
                                   'usage': {'prompt_tokens': 8, 'completion_tokens': 5, 'total_tokens': 13}}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.workspace.save_settings({'provider': {'base_url': f'http://127.0.0.1:{server.server_port}',
                                                   'model': 'local-fixture'}})
        result = self.execute(graph([node('model', 'model', inputs={'prompt': literal('Review the measurement.')})]))
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(result['nodes']['model']['outputs']['reply'], 'Measured bias is valid.')
        self.assertEqual(result['nodes']['model']['outputs']['usage']['total_tokens'], 13)
        self.assertIsNone(result['cost_usd'])
        self.assertEqual(self.workspace.agents.list(self.pid)[0]['mode'], 'read-only')

    def test_measure_accepts_mapped_specification_target_and_tolerance(self):
        flow = graph([node('measure', 'measure', {'metric': 'bias'},
                           {'metrics': literal({'bias': 1.48}), 'target': literal(1.5), 'tolerance': literal(.05)})])
        result = self.execute(flow)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertTrue(result['nodes']['measure']['outputs']['satisfied'])

    def test_missing_measurement_is_failure_and_failure_edge_handles_it(self):
        flow = graph([node('m', 'measure', {'metric': 'missing'}, {'result': literal({'metrics': {}})}),
                      node('failure', 'report', inputs={'error': ref('m', 'error')})],
                     [{'source': 'm', 'target': 'failure', 'on': 'failure'}])
        result = self.execute(flow)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(result['nodes']['m']['status'], 'failed')
        self.assertIn('missing', result['nodes']['failure']['outputs']['report']['error'])

    def test_inactive_failure_edge_cannot_hide_unhandled_measurement_failure(self):
        flow = graph([node('m', 'measure', {'metric': 'missing'}, {'metrics': literal({})}),
                      node('report', 'report')],
                     [{'source': 'm', 'target': 'report', 'on': 'failure', 'when': False}])
        result = self.execute(flow)
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(result['nodes']['report']['status'], 'skipped')

    def test_retry_bound_and_error_history_are_preserved(self):
        flow = graph([node('m', 'measure', {'metric': 'missing'}, {'result': literal({'metrics': {}})}, retries=2)])
        result = self.execute(flow)
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(result['nodes']['m']['attempts'], 3)
        self.assertEqual(len(result['nodes']['m']['logs']), 3)

    def test_loop_failure_can_route_to_parent_failure_handler(self):
        body = graph([node('missing', 'measure', {'metric': 'missing'}, {'metrics': literal({})})])
        flow = graph([node('loop', 'loop', {'body': body, 'max_iterations': 2}),
                      node('diagnosis', 'report', inputs={'error': ref('loop', 'error')})],
                     [{'source': 'loop', 'target': 'diagnosis', 'on': 'failure'}])
        result = self.execute(flow)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(result['nodes']['loop']['status'], 'failed')
        self.assertIn('missing', result['nodes']['diagnosis']['outputs']['report']['error'])

    def test_declared_project_object_output_rejects_array_json(self):
        self.workspace.projects.write(self.pid, 'invalid-spec.json', '[1, 2, 3]')
        result = self.execute(graph([node('read', 'project', {'path': 'invalid-spec.json', 'parse_json': True})]))
        self.assertEqual(result['status'], 'failed', result)
        self.assertIn('object', result['error'])

    def test_drc_and_lvs_require_authoritative_pass_even_when_process_exits_zero(self):
        # Only the external EDA adapter is replaced: real runner processes create
        # explicit report fixtures, and registry validation/parser remain real.
        registry = self.workspace.operations
        original = registry.operations['run.start']
        for kind, report, content in (
                ('drc', 'drc.txt', 'AF_DRC_COUNT=2\nAF_DRC_COMPLETE=1\n'),
                ('drc', 'drc.txt', 'No recognized completion marker\n'),
                ('lvs', 'lvs.txt', 'Circuits do not match.\n'),
                ('lvs', 'lvs.txt', 'No recognized matching statement\n')):
            with self.subTest(kind=kind, content=content):
                code = f'from pathlib import Path; Path("../output/{report}").write_text({content!r})'
                registry.operations['run.start'] = replace(original, handler=lambda c, p, code=code:
                    self.workspace.runner.start(self.root, p['kind'], [sys.executable, '-c', code]))
                flow = graph([node('check', kind), node('failure', 'report', inputs={'run': ref('check', 'result')})],
                             [{'source': 'check', 'target': 'failure', 'on': 'failure'}])
                result = self.execute(flow)
                self.assertEqual(result['status'], 'succeeded', result)
                check = result['nodes']['check']
                self.assertEqual(check['status'], 'failed', result)
                self.assertFalse(check['outputs']['success'])
                self.assertEqual(check['outputs']['result']['status'], 'succeeded')
                self.assertIn(check['outputs']['result']['verification']['status'], ('fail', 'unknown'))
                self.assertEqual(result['nodes']['failure']['status'], 'succeeded')

    def test_review_waits_for_explicit_decision_and_step_waits_before_execution(self):
        engine = importlib.import_module('analog_factory.workflow_graph')
        control = engine.GraphControl(step=True)
        paused, reviewing, finished = threading.Event(), threading.Event(), threading.Event()
        results = []
        def update(**values):
            if values.get('debug_state') == 'paused':
                paused.set()
            if values.get('pending_review'):
                reviewing.set()
        def work():
            results.append(self.execute(graph([node('review', 'human_review', {'prompt': 'Accept result?'})]),
                                        control=control, update=update))
            finished.set()
        thread = threading.Thread(target=work)
        thread.start()
        try:
            self.assertTrue(paused.wait(3))
            self.assertFalse(reviewing.is_set())
            control.command('step')
            self.assertTrue(reviewing.wait(3))
            self.assertFalse(finished.is_set())
            control.command('review', node_id='review', approved=True)
            self.assertTrue(finished.wait(3))
            self.assertTrue(results[0]['nodes']['review']['outputs']['approved'])
        finally:
            control.command('continue')
            if control.pending_review:
                control.command('review', node_id='review', approved=False)
            thread.join(12)

    def test_runtime_and_node_timeout_stop_real_child_processes(self):
        if not shutil.which('ngspice'):
            self.skipTest('actual ngspice required')
        self.workspace.projects.write(self.pid, 'testbenches/long.cir',
                                      'Long transient\nV1 in 0 sin(0 1 1k)\nR1 in 0 1k\n.tran 1p 1\n.end\n')
        for limits, timeout, termination in (({'runtime_seconds': .15}, 10, 'runtime_limit'),
                                             ({'runtime_seconds': 5}, .15, 'node_timeout')):
            flow = graph([node('sim', 'simulate', {'options': {'netlist': 'testbenches/long.cir'}},
                               timeout_seconds=timeout)], limits=limits)
            result = self.execute(flow)
            self.assertEqual(result['status'], 'timed_out', result)
            self.assertEqual(result['termination'], termination)
            self.assertNotIn(self.workspace.runs(self.pid)[0]['status'], ('queued', 'running'))

    def test_debug_pause_cannot_outlive_loop_node_timeout(self):
        engine = importlib.import_module('analog_factory.workflow_graph')
        control = engine.GraphControl(step=True)
        control.command('step')
        body = graph([node('p', 'parameter', {'value': 1})])
        flow = graph([node('loop', 'loop', {'body': body, 'max_iterations': 2}, timeout_seconds=.1)],
                     limits={'runtime_seconds': .5})
        start = time.monotonic()
        result = self.execute(flow, control=control)
        self.assertEqual(result['termination'], 'node_timeout', result)
        self.assertLess(time.monotonic() - start, .4)

    def test_alias_output_mapping_validates_runtime_json_type(self):
        flow = graph([node('spec', 'specification', {'value': {'target': 'not numeric'}},
                           outputs={'target': {'path': 'value.target', 'type': 'number'}})])
        result = self.execute(flow)
        self.assertEqual(result['status'], 'failed', result)
        self.assertIn('number', result['error'])

    @unittest.skipUnless(shutil.which('ngspice'), 'actual ngspice required')
    def test_shipped_parameter_search_converges_using_actual_run_measurements(self):
        examples = Path(__file__).resolve().parents[1] / 'examples/workflows'
        names = ('simulation-diagnosis', 'parameter-search', 'pre-post-comparison', 'verification-analysis')
        for name in names:
            validate_workflow(json.loads((examples / f'{name}.json').read_text()))
        flow = json.loads((examples / 'parameter-search.json').read_text())
        result = self.execute(flow)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertTrue(result['nodes']['search']['outputs']['satisfied'])
        result_runs = self.workspace.runs(self.pid)
        self.assertGreater(len(result_runs), 1)
        self.assertTrue(all(run['status'] == 'succeeded' for run in result_runs))
        self.assertEqual(len(result_runs), result['loop_iterations'])

    @unittest.skipUnless(shutil.which('ngspice'), 'actual ngspice required')
    def test_shipped_diagnosis_reports_real_failed_simulation_logs(self):
        path = Path(__file__).resolve().parents[1] / 'examples/workflows/simulation-diagnosis.json'
        self.workspace.projects.write(self.pid, 'testbenches/pre.cir',
                                      'Invalid simulation\nX1 a b missing_subcircuit\n.tran 1u 1m\n.end\n')
        result = self.execute(json.loads(path.read_text()))
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(result['nodes']['simulate']['status'], 'failed')
        report = result['nodes']['diagnosis']['outputs']['report']
        self.assertIn('unknown subckt', report['log'].lower())
        self.assertEqual(result['nodes']['passed']['status'], 'skipped')

    def test_source_edit_during_review_aborts_before_next_node(self):
        engine = importlib.import_module('analog_factory.workflow_graph')
        control = engine.GraphControl()
        def update(**values):
            if values.get('pending_review'):
                self.workspace.projects.write(self.pid, 'testbenches/pre.cir', '.param RD=1\n.end\n')
                control.command('review', node_id='review', approved=True)
        result = self.execute(graph([node('review', 'human_review'), node('sim', 'simulate')],
                                    [{'source': 'review', 'target': 'sim'}]), control=control, update=update)
        self.assertEqual(result['termination'], 'source_conflict')
        self.assertEqual(self.workspace.runs(self.pid), [])

    def test_review_branch_uses_approved_port_even_when_an_alias_is_named_result(self):
        engine = importlib.import_module('analog_factory.workflow_graph')
        control = engine.GraphControl()
        def update(**values):
            if values.get('pending_review'):
                control.command('review', node_id='review', approved=False)
        flow = graph([node('review', 'human_review', inputs={'value': literal(True)},
                           outputs={'result': {'path': 'value', 'type': 'boolean'}}),
                      node('accepted', 'report')], [{'source': 'review', 'target': 'accepted', 'when': True}])
        result = self.execute(flow, control=control, update=update)
        self.assertFalse(result['nodes']['review']['outputs']['approved'])
        self.assertEqual(result['nodes']['accepted']['status'], 'skipped')

    def test_cancellation_stops_actual_ngspice_child_run(self):
        if not shutil.which('ngspice'):
            self.skipTest('actual ngspice required')
        # This real transient would need billions of steps without cancellation.
        self.workspace.projects.write(self.pid, 'testbenches/long.cir',
                                      'Long transient\nV1 in 0 sin(0 1 1k)\nR1 in 0 1k\n.tran 1p 1\n.end\n')
        cancel = threading.Event()
        def update(**values):
            if values.get('active_run'):
                cancel.set()
        result = self.execute(graph([node('sim', 'simulate', {'options': {'netlist': 'testbenches/long.cir'}})]),
                              cancel=cancel, update=update)
        self.assertEqual(result['status'], 'cancelled', result)
        runs = self.workspace.runs(self.pid)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]['status'], 'cancelled', runs)


if __name__ == '__main__':
    unittest.main()
