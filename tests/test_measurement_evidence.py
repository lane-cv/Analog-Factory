"""Evidence must follow executed decks, never names or caller condition flags."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from analog_factory import eda, templates
from analog_factory.execution import RunManager
from analog_factory.service import Workspace


COMPARATOR = Path(__file__).resolve().parents[1] / 'analog_factory/static/result-comparison.js'
_LOCAL_NODE = COMPARATOR.parents[2] / '.tools/browser-tests/node'
NODE = shutil.which('node') or (str(_LOCAL_NODE) if _LOCAL_NODE.is_file() else None)


def compare(a, b):
    program = 'const fs=require("fs"),c=require(process.argv[1]);const [a,b]=JSON.parse(fs.readFileSync(0,"utf8"));console.log(JSON.stringify(c.compare(a,b)));'
    return json.loads(subprocess.check_output([NODE, '-e', program, str(COMPARATOR)], input=json.dumps([a, b]).encode()))


@unittest.skipUnless(shutil.which('ngspice'), 'ngspice is not installed')
@unittest.skipUnless(NODE, 'Node.js is required to compare the actual execution records')
class OrdinaryEvidenceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.addCleanup(self.workspace.operations.close)
        self.addCleanup(self.workspace.agents.close)
        self.project = self.workspace.projects.create('Evidence', 'amplifier')['id']
        self.root = self.workspace.projects.path(self.project)

    def run_deck(self, *, deck=None, amplifier=None, metadata=None):
        if deck is not None:
            (self.root / 'testbenches/pre.cir').write_text(deck)
        if amplifier is not None:
            (self.root / 'netlists/amplifier.cir').write_text(amplifier)
        response = self.workspace.operations.invoke('run.start', {'project_id': self.project, 'kind': 'simulate', 'metadata': metadata or {}})
        self.assertTrue(response['ok'], response)
        result = self.workspace.runner.wait(self.root, response['data']['id'], timeout=20)
        self.assertEqual(result['status'], 'succeeded', result.get('error'))
        return result

    def test_real_default_rd_revision_has_six_comparable_metrics_with_snapshot_evidence(self):
        baseline = self.run_deck()
        revision = self.run_deck(deck=templates.PRE.replace('RD=3300', 'RD=4700'))
        rows = compare(baseline, revision)['rows']
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(row['comparable'] for row in rows), rows)
        self.assertEqual({row['name']: row['unit'] for row in rows}, {
            'vout_mean':'V', 'vout_pp':'V', 'vin_pp':'V', 'supply_current':'A', 'gain':'1', 'power':'W'})
        self.assertNotEqual(baseline['metrics']['vout_mean'], revision['metrics']['vout_mean'])
        evidence = baseline['metadata']['measurement_evidence']
        self.assertTrue(evidence['complete'], evidence)
        self.assertEqual(evidence['conditions']['temperature_c'], 27)
        self.assertIsNone(evidence['conditions']['corner'])
        self.assertIn('netlists/amplifier.cir', evidence['source_hashes'])
        self.assertIn('runtime', evidence['temperature_provenance']['source'])

    def test_supply_temperature_analysis_and_inline_model_changes_block_actual_records(self):
        baseline = self.run_deck()
        for deck, amplifier in [
            (templates.PRE, templates.AMPLIFIER.replace('vdd 0 3.3', 'vdd 0 3.0')),
            (templates.PRE.replace('.tran', '.temp 55\n.tran'), templates.AMPLIFIER),
            (templates.PRE.replace('.tran 2u', '.tran 4u'), templates.AMPLIFIER),
            (templates.PRE, templates.AMPLIFIER.replace('KP=200u', 'KP=250u')),
        ]:
            with self.subTest(deck=deck, amplifier=amplifier):
                changed = self.run_deck(deck=deck, amplifier=amplifier)
                self.assertTrue(all(not row['comparable'] and row['delta'] is None for row in compare(baseline, changed)['rows']))

    def test_post_prepare_override_and_caller_flags_cannot_replace_actual_source_conditions(self):
        result = self.run_deck(metadata={
            'generated_files': {'netlists/amplifier.cir': templates.AMPLIFIER.replace('vdd 0 3.3', 'vdd 0 2.9')},
            'conditions': {'supply_v':3.3, 'temperature_c':99, 'corner':'tt'}})
        evidence = result['metadata'].get('measurement_evidence', {})
        self.assertEqual(evidence.get('conditions', {}).get('supply_v'), 2.9)
        self.assertEqual(evidence.get('conditions', {}).get('temperature_c'), 27)
        self.assertFalse(evidence.get('complete', True))
        self.assertTrue(all(not row['comparable'] for row in compare(result, result)['rows']))

    def test_control_measurements_cannot_qualify_using_caller_conditions(self):
        deck = ('Control measures\nVDD out 0 1\nR1 out 0 1k\n.tran 1u 1m\n'
                '.control\nrun\nmeas tran measured AVG v(out)\n.endc\n.end\n')
        metadata = {'conditions': {'temperature_c':27, 'corner':None, 'supply_v':1,
                    'analysis':{'type':'tran', 'parameters':{'step':'1u', 'stop':'1m'}},
                    'measurement_definitions':{'measured':{'units':'V', 'analysis':'tran', 'expression':'avg(v(out))'}}}}
        first = self.run_deck(deck=deck, metadata=metadata)
        changed = self.run_deck(deck=deck.replace('out 0 1\n', 'out 0 2\n'), metadata=metadata)
        self.assertEqual(first['metrics']['measured'], 1)
        self.assertEqual(changed['metrics']['measured'], 2)
        rows = compare(first, changed)['rows']
        self.assertTrue(all(not row['comparable'] and row['delta'] is None for row in rows), rows)


class SnapshotEvidenceTests(unittest.TestCase):
    def evidence(self, deck=templates.PRE, amplifier=templates.AMPLIFIER, files=None):
        from analog_factory.measurement_evidence import collect
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = {'testbenches/pre.cir':deck, 'netlists/amplifier.cir':amplifier, **(files or {})}
            for name, content in inputs.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(content)
            dependencies = eda.input_dependencies(root, 'testbenches/pre.cir', {})
            return collect(root, 'testbenches/pre.cir', dependencies)

    def test_incomplete_closure_and_conflicting_directives_cannot_establish_evidence(self):
        for extra in ['.include "missing.inc"', '.include "$UNRESOLVED/models.inc"', '.temp 27\n.temp 55',
                      '.tran 1u 5m', '.meas tran vout_mean AVG i(VDD)',
                      '.control\nalter VDD=2\n.endc', '.option scale=2', '.param RD=4700']:
            with self.subTest(extra=extra):
                evidence = self.evidence(templates.PRE.replace('.end', extra + '\n.end'))
                self.assertFalse(evidence['complete'], evidence)
                self.assertTrue(evidence['limitations'])

    def test_unknown_units_and_dynamic_sources_remain_unqualified(self):
        evidence = self.evidence(templates.PRE.replace("'vout_pp/vin_pp'", "'3*supply_current'"))
        self.assertNotIn('units', evidence['definitions']['gain'])
        self.assertTrue(evidence['definitions']['gain']['limitations'])
        for value in ['{unknown_supply}', 'PWL(0 3.3 1m 3)', '3.3 SIN(3.3 .1 1k)']:
            with self.subTest(value=value):
                dynamic = self.evidence(amplifier=templates.AMPLIFIER.replace('vdd 0 3.3', 'vdd 0 ' + value))
                self.assertFalse(dynamic['complete'], dynamic)

    def test_units_follow_bounded_expression_not_metric_name(self):
        deck = templates.PRE.replace('.meas tran vout_mean AVG v(out)', '.meas tran vout_mean AVG i(VDD)')
        evidence = self.evidence(deck)
        self.assertEqual(evidence['definitions']['vout_mean']['units'], 'A')
        renamed = self.evidence(templates.PRE.replace('vout_mean', 'custom_current_name'))
        self.assertEqual(renamed['definitions']['custom_current_name']['units'], 'V')

    def test_design_parameters_do_not_change_model_or_condition_identity(self):
        baseline = self.evidence()
        changed = self.evidence(templates.PRE.replace('RD=3300','RD=4700'), templates.AMPLIFIER.replace('W=10u', 'W=15u'))
        self.assertEqual(baseline['conditions'], changed['conditions'])
        self.assertNotEqual(baseline['source_hashes'], changed['source_hashes'])

    def test_resolved_measurement_bounds_are_part_of_definition(self):
        deck = templates.PRE.replace('.param RD=3300', '.param RD=3300 START=1m').replace('FROM=1m', 'FROM={START}')
        a = self.evidence(deck)
        b = self.evidence(deck.replace('START=1m','START=2m'))
        self.assertNotEqual(a['definitions']['vout_mean'], b['definitions']['vout_mean'])

    def test_subcircuits_model_expressions_and_scoped_overrides_are_not_qualified(self):
        for extra in ['.subckt child a b\nRchild a b 1k\n.ends\nX1 out 0 child',
                      '.func foo(x) {x*2}', '.alter', '.lib "models.lib" tt', '.option temp=27']:
            with self.subTest(extra=extra):
                deck = templates.PRE.replace('.tran', '.temp 27\n.tran').replace('.end', extra+'\n.end')
                self.assertFalse(self.evidence(deck)['complete'])
        self.assertFalse(self.evidence(amplifier=templates.AMPLIFIER.replace('KP=200u', 'KP={RD}'))['complete'])

    def test_runtime_report_must_be_finite_and_consistent_with_deck(self):
        from analog_factory.measurement_evidence import finalize
        for log in ['', 'Doing analysis at TEMP = 27 and TNOM = 27\nDoing analysis at TEMP = 55 and TNOM = 27\n',
                    'Doing analysis at TEMP = 27 and TNOM = 27\nDoing analysis at TEMP = NaN and TNOM = 27\n']:
            with self.subTest(log=log):
                evidence = self.evidence()
                evidence['source_hashes'] = {}
                finalize(evidence, Path('/nonexistent'), log, {})
                self.assertFalse(evidence['complete'])
        evidence = self.evidence(templates.PRE.replace('.tran', '.temp 55\n.tran'))
        evidence['source_hashes'] = {}
        finalize(evidence, Path('/nonexistent'), 'Doing analysis at TEMP = 27 and TNOM = 27\n', {})
        self.assertFalse(evidence['complete'])

    def test_snapshot_mutation_after_capture_invalidates_evidence(self):
        from analog_factory.measurement_evidence import collect, finalize
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'deck.cir').write_text('Test\nV1 out 0 1\nR1 out 0 1k\n.tran 1u 1m\n.meas tran voltage AVG v(out)\n.end\n')
            evidence = collect(root,'deck.cir',eda.input_dependencies(root,'deck.cir',{}))
            (root/'deck.cir').write_text((root/'deck.cir').read_text().replace('out 0 1\n','out 0 2\n'))
            finalize(evidence, root, 'Doing analysis at TEMP = 27 and TNOM = 27\n', {})
            self.assertFalse(evidence['complete'])

    def test_complete_local_include_closure_excludes_unexecuted_project_decks(self):
        evidence = self.evidence(files={'unused.cir':'.model AFNMOS NMOS (LEVEL=1 KP=99)\n.meas tran gain AVG i(VDD)\n'})
        self.assertTrue(evidence['complete'], evidence)
        self.assertNotIn('unused.cir', evidence['source_hashes'])
        self.assertEqual(evidence['definitions']['gain']['units'], '1')

    def test_end_boundaries_repeated_includes_and_parameter_measure_collisions_fail_closed(self):
        cases = [
            (templates.PRE + '.param EXTRA=4\n', templates.AMPLIFIER),
            (templates.PRE, templates.AMPLIFIER + '.end\n'),
            (templates.PRE.replace('.tran', '.include "netlists/amplifier.cir"\n.tran'), templates.AMPLIFIER),
            (templates.PRE.replace('.param RD=3300', '.param RD=3300 vout_pp=5'), templates.AMPLIFIER),
        ]
        for deck, amplifier in cases:
            with self.subTest(deck=deck,amplifier=amplifier):
                self.assertFalse(self.evidence(deck,amplifier)['complete'])

    def test_duplicate_self_referencing_measurements_do_not_create_cyclic_manifest(self):
        extra = ".meas tran foo PARAM='vout_pp/vin_pp'\n.meas tran foo PARAM='foo'\n"
        evidence = self.evidence(templates.PRE.replace('.end',extra+'.end'))
        self.assertFalse(evidence['complete'])
        json.dumps(evidence,allow_nan=False)

    def test_derived_measurement_dag_is_bounded_and_keeps_transitive_semantics(self):
        measures = ['.meas tran f0 AVG v(out)', '.meas tran f1 AVG v(out)']
        measures += [f".meas tran f{i} PARAM='f{i-1}+f{i-2}'" for i in range(2, 40)]
        deck = templates.PRE.replace('.end', '\n'.join(measures) + '\n.end')
        evidence = self.evidence(deck)
        self.assertTrue(evidence['complete'])
        dependencies = evidence['definitions']['f39']['dependencies']
        for signature in dependencies.values():
            self.assertTrue(isinstance(signature, str), 'Dependency evidence must not recursively duplicate a measurement DAG')
            self.assertRegex(signature, r'^[a-f0-9]{64}$')
        self.assertLess(len(json.dumps(evidence)), 50000)
        moved = self.evidence(deck.replace('.tran', '* Added source comment\n.tran'))
        self.assertEqual(dependencies, moved['definitions']['f39']['dependencies'])
        changed = self.evidence(deck.replace('f0 AVG', 'f0 MAX'))
        self.assertNotEqual(dependencies, changed['definitions']['f39']['dependencies'])

    def test_simulator_identity_requires_actual_binary_and_detects_launcher_target_change(self):
        from analog_factory.measurement_evidence import simulator_identity
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root/'ngspice.bin'
            binary.write_bytes(b'\x7fELF simulator version one')
            wrapper = root/'ngspice'
            wrapper.write_text(f'#!/bin/sh\nexec {binary} "$@"\n')
            first = simulator_identity(wrapper)
            binary.write_bytes(b'\x7fELF simulator version two')
            self.assertNotEqual(first, simulator_identity(wrapper))
            wrapper.write_text('#!/bin/sh\nexec "$NGSPICE" "$@"\n')
            with self.assertRaises(ValueError):
                simulator_identity(wrapper)
