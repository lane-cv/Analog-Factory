"""Real simulator evidence for bounded advanced analyses and model qualification."""
import copy
import importlib.util
import json
import math
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from analog_factory.service import Workspace


RC_SOURCE = '''Analog-factory real RC batch fixture
.param R=1000 VDD=1.8
VIN in 0 DC 0 AC 2 SIN(0 0.1 1k)
VDD vdd 0 {VDD}
RPOWER vdd 0 1000
R1 in out {R}
C1 out 0 1u
.end
'''


def ac_spec():
    return {'source': 'testbenches/pre.cir',
            'analysis': {'type': 'ac', 'parameters': {'points': 60, 'start': 1, 'stop': 100000}},
            'axes': [{'parameter': 'R', 'values': [1000, 2000]}],
            'temperatures_c': [27],
            'measurements': [{'kind': 'gain', 'name': 'gain_db', 'output': 'out', 'input': 'in', 'frequency_hz': 100},
                             {'kind': 'bandwidth', 'name': 'bandwidth_hz', 'output': 'out', 'input': 'in',
                              'reference_hz': 1, 'drop_db': 3.01029995664},
                             {'kind': 'phase', 'name': 'phase_deg', 'output': 'out', 'input': 'in', 'frequency_hz': 100}]}


class BatchTests(unittest.TestCase):
    def statistical_fixture(self):
        pdk = Path(self.tmp.name) / 'controlled-pdk'
        combined = pdk / 'sky130A/libs.tech/combined'
        continuous = combined / 'continuous'
        continuous.mkdir(parents=True)
        (continuous / 'parameters_fet_tt.spice').write_text('.param sw_polycd=MC_PR_SWITCH*agauss(0,1.5e-9,1)\n')
        (continuous / 'models.spice').write_text('''
.subckt sky130_fd_pr__nfet_01v8 d g s b w=1 l=.15
.param local=MC_MM_SWITCH*agauss(0,.01,1)
M1 d g s b n w={w*1u} l={l*1u}
.model n NMOS level=1 kp=100u vto={.4+sw_polycd*1e7+local}
.ends
.subckt sky130_fd_pr__pfet_01v8 d g s b w=1 l=.15
.param local=MC_MM_SWITCH*agauss(0,.01,1)
M1 d g s b p w={w*1u} l={l*1u}
.model p PMOS level=1 kp=50u vto={-.4+sw_polycd*1e7+local}
.ends
''')
        library = combined / 'sky130.lib.spice'
        blocks=[]
        for section, process, mismatch in (('tt',0,0),('mc',1,0),('tt_mm',0,1)):
            blocks.append(f'.lib {section}\n.param MC_PR_SWITCH={process}\n.param MC_MM_SWITCH={mismatch}\n'
                          '.include continuous/parameters_fet_tt.spice\n.include continuous/models.spice\n'
                          f'.endl {section}\n')
        library.write_text(''.join(blocks))
        self.workspace.configure_project(self.pid, {'pdk_root':str(pdk)})
        source=f'* Controlled parser fixture, not foundry evidence\n.lib "{library}" tt\nVD dn 0 .9\nVG gn 0 .8\nX1 dn gn 0 0 sky130_fd_pr__nfet_01v8 w=1 l=.15\n.op\n.end\n'
        self.workspace.projects.write(self.pid,'testbenches/pre.cir',source)
        return library, continuous, source

    @unittest.skipUnless(shutil.which('ngspice'), 'Requires real ngspice')
    def test_explicit_adapted_qualification_and_batches_bind_semantics_and_snapshot_hashes(self):
        from analog_factory import simulation_batches as batch
        library, continuous, original = self.statistical_fixture()
        response=self.registry.invoke('simulation.statistics.qualify',{'project_id':self.pid,
            'library':str(library),'kind':'global_process','section':'mc','model_semantics':'cache_global_fet_draws'},
            expected_version=self.registry.version(self.pid))
        self.assertTrue(response['ok'],response)
        job=self.workspace.jobs.wait(self.workspace.projects.path(self.pid),response['data']['id'],timeout=30)
        self.assertEqual(job['status'],'succeeded',job)
        evidence=job['qualification']
        self.assertEqual(evidence['model_semantics'],'cache_global_fet_draws')
        self.assertTrue(evidence['adapter']['experimental'])
        self.assertTrue(evidence['adapter']['source_dependencies'])
        self.assertTrue(evidence['adapter']['generated_hashes'])
        for item in job['iterations']:
            record=self.workspace.runner.get(self.workspace.projects.path(self.pid),item['run_id'])
            self.assertEqual(record['external_dependencies'],{})
            for name,digest in evidence['adapter']['generated_hashes'].items():
                self.assertEqual(record['input_hashes'][name],digest)
        spec={'analysis':{'type':'op'},'corners':{'library':str(library),'sections':['mc']},
              'statistics':{'kind':'global_process','samples':2,'seed':73,'qualification_job':job['id'],
                            'model_semantics':'cache_global_fet_draws'},
              'measurements':[{'kind':'power','name':'power_w','supply_node':'dn','supply_source':'VD'}]}
        installed=copy.deepcopy(spec); installed['statistics'].pop('model_semantics')
        with self.assertRaisesRegex(ValueError,'semantic'):
            self.prepare(self.workspace,self.pid,installed)
        result=self.start(spec)
        self.assertEqual(result['status'],'succeeded',result)
        self.assertNotEqual(result['iterations'][0]['metrics']['power_w'],result['iterations'][1]['metrics']['power_w'])
        self.assertEqual(self.workspace.projects.read(self.pid,'testbenches/pre.cir')['content'],original)
        for override in ('.param sw_polycd=0', '.para sw_polycd=0', '.param process_mc_factor=0',
                         '.func af_cached_agauss(a,b,c) {0}'):
            self.workspace.projects.write(self.pid,'testbenches/pre.cir',original.replace('.op',override+'\n.op'))
            with self.subTest(override=override), self.assertRaisesRegex(ValueError,'overrid|function'):
                self.prepare(self.workspace,self.pid,spec)
        self.workspace.projects.write(self.pid,'testbenches/pre.cir',original)
        (continuous/'models.spice').write_text((continuous/'models.spice').read_text()+'\n* source changed\n')
        with self.assertRaisesRegex(ValueError,'changed'):
            self.prepare(self.workspace,self.pid,spec)

    @unittest.skipUnless(shutil.which('ngspice'), 'Requires real ngspice')
    def test_global_adapter_cannot_be_selected_for_mismatch_and_original_remains_rejected(self):
        library, continuous, _ = self.statistical_fixture()
        response=self.registry.invoke('simulation.statistics.qualify',{'project_id':self.pid,
            'library':str(library),'kind':'device_mismatch','section':'tt_mm','model_semantics':'cache_global_fet_draws'},
            expected_version=self.registry.version(self.pid))
        self.assertFalse(response['ok'],response)
        self.assertEqual(self.workspace.jobs.list(self.workspace.projects.path(self.pid)),[])
        response=self.registry.invoke('simulation.statistics.qualify',{'project_id':self.pid,
            'library':str(library),'kind':'global_process','section':'mc'},expected_version=self.registry.version(self.pid))
        self.assertTrue(response['ok'],response)
        job=self.workspace.jobs.wait(self.workspace.projects.path(self.pid),response['data']['id'],timeout=30)
        self.assertEqual(job['status'],'failed',job)
        self.assertFalse(job['qualification']['checks']['requested_correlation'])

    def test_qualified_sources_reject_commented_families_local_models_and_redefinitions(self):
        from analog_factory import simulation_batches as batch
        self.assertTrue(hasattr(batch, '_qualified_source_models'))
        for source in ('* sky130_fd_pr__nfet_01v8\n.model local NMOS\nM1 d g 0 0 local\n',
                       '.subckt sky130_fd_pr__nfet_01v8 d g s b\nR1 d s 1k\n.ends\n',
                       '* sky130_fd_pr__nfet_01v8\nX1 d g s b unrelated w=1\n'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                batch._qualified_source_models(source)
        self.assertEqual(batch._qualified_source_models('* ignored sky130_fd_pr__fake\nX1 d g s b sky130_fd_pr__nfet_01v8\n+ w=1 l=.15\n'), {'sky130_fd_pr__nfet_01v8'})

    @unittest.skipUnless(shutil.which('ngspice'), 'Actual ngspice is required')
    def test_final_scenario_configuration_change_is_not_reported_successful(self):
        from analog_factory import simulation_batches as batch
        spec=ac_spec();spec['axes']=[]
        plan=batch.prepare_batch(self.workspace,self.pid,spec)
        def update(**fields):
            if fields.get('active_run'):
                self.workspace.save_settings({'tools':{'ngspice':'/missing-after-snapshot'}})
        result=batch.execute_batch(self.workspace,self.pid,plan,{'id':'c'*32},threading.Event(),update)
        self.assertEqual(result['status'],'failed',result)
        self.assertEqual(result['termination'],'source_conflict')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Workspace(Path(self.tmp.name))
        self.addCleanup(self.workspace.agents.close)
        self.pid = self.workspace.projects.create('Advanced simulation')['id']
        self.workspace.projects.write(self.pid, 'testbenches/pre.cir', RC_SOURCE)
        self.assertIsNotNone(importlib.util.find_spec('analog_factory.simulation_batches'),
                             'Advanced simulation batches must have a real service')
        from analog_factory.simulation_batches import prepare_batch, execute_batch
        self.prepare, self.execute = prepare_batch, execute_batch
        self.registry = self.workspace.operations
        self.addCleanup(self.registry.close)
        from analog_factory.operation_simulation_batches import register_simulation_batches
        if 'simulation.batch.start' not in self.registry.operations:
            register_simulation_batches(self.registry)

    def start(self, spec):
        result = self.registry.invoke('simulation.batch.start', {'project_id': self.pid, 'spec': spec},
                                      expected_version=self.registry.version(self.pid))
        self.assertTrue(result['ok'], result)
        return self.workspace.jobs.wait(self.workspace.projects.path(self.pid), result['data']['id'], timeout=30)

    def test_preview_cartesian_conditions_preserve_source_and_define_measurements(self):
        spec = ac_spec()
        spec['temperatures_c'] = [-40, 125]
        spec['supply'] = {'parameter': 'VDD', 'values': [1.62, 1.98]}
        plan = self.prepare(self.workspace, self.pid, spec)
        self.assertEqual(len(plan['scenarios']), 8)
        conditions = {(s['conditions']['parameters']['R'], s['conditions']['temperature_c'],
                       s['conditions']['parameters']['VDD']) for s in plan['scenarios']}
        self.assertEqual(conditions, {(r,t,v) for r in (1000,2000) for t in (-40,125) for v in (1.62,1.98)})
        scenario = plan['scenarios'][-1]
        self.assertIn('.temp 125', scenario['generated_files']['testbenches/pre.cir'])
        self.assertIn('gain_db', scenario['generated_files']['af_scripts/simulate.cir'])
        self.assertEqual(scenario['conditions']['measurement_definitions']['phase_deg']['units'], 'degrees')
        self.assertEqual(self.workspace.projects.read(self.pid, 'testbenches/pre.cir')['content'], RC_SOURCE)

    def test_invalid_grids_parameters_and_analysis_measurements_reject_before_job(self):
        cases = []
        spec = ac_spec(); spec['limits'] = {'max_runs': 1}; cases.append(spec)
        spec = ac_spec(); spec['axes'][0]['values'] = [float('nan')]; cases.append(spec)
        spec = ac_spec(); spec['axes'][0]['parameter'] = 'NOT_DECLARED'; cases.append(spec)
        spec = ac_spec(); spec['axes'].append({'parameter': 'R', 'values': [1]}); cases.append(spec)
        spec = ac_spec(); spec['temperatures_c'] = [-274]; cases.append(spec)
        spec = ac_spec(); spec['measurements'][0]['output'] = 'out)\nshell touch unsafe'; cases.append(spec)
        spec = ac_spec(); spec['measurements'] = [{'kind':'swing','name':'swing','output':'out','start_s':0,'stop_s':1}]; cases.append(spec)
        for spec in cases:
            with self.subTest(spec=spec):
                with self.assertRaises(ValueError):
                    self.prepare(self.workspace, self.pid, spec)
        self.assertEqual(self.workspace.jobs.list(self.workspace.projects.path(self.pid)), [])

    def test_real_library_sections_are_checked_and_bound_to_snapshot(self):
        library = '.lib tt\n.param FACTOR=1\n.endl tt\n.lib ss\n.param FACTOR=2\n.endl ss\n'
        self.workspace.projects.write(self.pid, 'models/corners.lib', library)
        source = RC_SOURCE.replace('R1 in out {R}', 'R1 in out {R*FACTOR}').replace('.param R=', '.lib "models/corners.lib" tt\n.param R=')
        self.workspace.projects.write(self.pid, 'testbenches/pre.cir', source)
        spec = ac_spec(); spec['axes'] = []; spec['corners'] = {'library':'models/corners.lib','sections':['tt','ss']}
        plan = self.prepare(self.workspace, self.pid, spec)
        self.assertEqual([s['conditions']['corner'] for s in plan['scenarios']], ['tt', 'ss'])
        self.assertIn('.lib "models/corners.lib" ss', plan['scenarios'][1]['generated_files']['testbenches/pre.cir'])
        spec['corners']['sections'] = ['not_a_real_section']
        with self.assertRaises(ValueError): self.prepare(self.workspace, self.pid, spec)
        spec['corners'] = {'library':'/etc/passwd','sections':['tt']}
        with self.assertRaises(ValueError): self.prepare(self.workspace, self.pid, spec)

    def test_seeded_engineering_variation_is_distinct_from_foundry_statistics(self):
        spec = ac_spec(); spec['axes'] = []
        spec['statistics'] = {'kind':'engineering_parameter', 'samples':4, 'seed':7,
                              'variables':[{'parameter':'R','distribution':'uniform','minimum':900,'maximum':1100}]}
        first = self.prepare(self.workspace, self.pid, spec)
        second = self.prepare(self.workspace, self.pid, copy.deepcopy(spec))
        values = [s['conditions']['parameters']['R'] for s in first['scenarios']]
        self.assertEqual(values, [s['conditions']['parameters']['R'] for s in second['scenarios']])
        self.assertTrue(all(900 <= value <= 1100 for value in values))
        self.assertEqual(len(set(values)), 4)
        self.assertTrue(all(s['conditions']['statistical_classification'] == 'engineering_parameter' for s in first['scenarios']))
        for kind in ('global_process','device_mismatch'):
            spec['statistics'] = {'kind':kind,'samples':3,'seed':7}
            with self.assertRaisesRegex(ValueError, 'qualif'):
                self.prepare(self.workspace, self.pid, spec)

    @unittest.skipUnless(shutil.which('ngspice'), 'Requires real ngspice')
    def test_real_rc_sweep_measures_gain_bandwidth_and_phase_with_provenance(self):
        result = self.start(ac_spec())
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(len(result['iterations']), 2)
        for item in result['iterations']:
            resistance = item['conditions']['parameters']['R']
            tau = resistance * 1e-6
            self.assertAlmostEqual(item['metrics']['bandwidth_hz'], 1/(2*math.pi*tau), delta=1)
            self.assertAlmostEqual(item['metrics']['gain_db'], -10*math.log10(1+(2*math.pi*100*tau)**2), delta=.02)
            self.assertAlmostEqual(item['metrics']['phase_deg'], -math.degrees(math.atan(2*math.pi*100*tau)), delta=.05)
            run = self.workspace.runner.get(self.workspace.projects.path(self.pid), item['run_id'])
            self.assertEqual(run['metadata']['conditions']['batch_job_id'], result['id'])
            self.assertIn('bandwidth_hz', run['metadata']['conditions']['measurement_definitions'])
            self.assertIn('bandwidth_hz', self.workspace.log(self.pid, item['run_id'])['content'])
        self.assertEqual(self.workspace.projects.read(self.pid, 'testbenches/pre.cir')['content'], RC_SOURCE)

    @unittest.skipUnless(shutil.which('ngspice'), 'Requires real ngspice')
    def test_differential_gain_uses_both_input_and_output_nodes(self):
        source=RC_SOURCE.replace('VIN in 0 DC 0 AC 2 SIN(0 0.1 1k)',
            'VIN in 0 AC 1\nVNEG inn 0 AC 1 180').replace('C1 out 0 1u','C1 out inn 1u')
        self.workspace.projects.write(self.pid,'testbenches/pre.cir',source)
        spec=ac_spec(); spec['axes']=[]
        for measure in spec['measurements']:
            measure.update(input_negative='inn',output_negative='inn')
        response=self.registry.invoke('simulation.batch.preview',{'project_id':self.pid,'spec':spec})
        self.assertTrue(response['ok'],response.get('error'))
        result=self.start(spec)
        self.assertEqual(result['status'],'succeeded',result)
        self.assertAlmostEqual(result['iterations'][0]['metrics']['gain_db'],-1.44507,delta=.02)

    @unittest.skipUnless(shutil.which('ngspice'), 'Requires real ngspice')
    def test_real_temperature_supply_scenarios_measure_power_and_swing(self):
        spec = {'source':'testbenches/pre.cir','analysis':{'type':'tran','parameters':{'step':1e-6,'stop':.005}},
                'temperatures_c':[0,85], 'supply':{'parameter':'VDD','values':[1.5,2]},
                'measurements':[{'kind':'power','name':'power_w','supply_node':'vdd','supply_source':'VDD','start_s':.001,'stop_s':.005},
                                {'kind':'swing','name':'swing_vpp','output':'out','start_s':.001,'stop_s':.005}]}
        result = self.start(spec)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(len(result['iterations']), 4)
        for item in result['iterations']:
            self.assertAlmostEqual(item['metrics']['power_w'], item['conditions']['parameters']['VDD']**2/1000, places=6)
            self.assertGreater(item['metrics']['swing_vpp'], 0)

    @unittest.skipUnless(shutil.which('ngspice'), 'Requires real ngspice')
    def test_missing_measurement_is_failure_with_run_evidence(self):
        spec = ac_spec(); spec['axes'] = []; spec['measurements'][0]['output'] = 'missing_node'
        result = self.start(spec)
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(len(result['iterations']), 1)
        self.assertIn('run_id', result['iterations'][0])
        self.assertNotIn('gain_db', result['iterations'][0]['metrics'])

    @unittest.skipUnless(shutil.which('ngspice'), 'Requires real ngspice')
    def test_cancel_stops_actual_child_and_does_not_launch_later_scenarios(self):
        plan = self.prepare(self.workspace, self.pid, ac_spec())
        cancel, updates = threading.Event(), []
        def update(**fields):
            updates.append(fields)
            if fields.get('active_run'): cancel.set()
        result = self.execute(self.workspace, self.pid, plan, {'id':'a'*32}, cancel, update)
        self.assertEqual(result['status'], 'cancelled')
        runs = self.workspace.runs(self.pid)
        self.assertLessEqual(len(runs), 1)
        self.assertTrue(all(r['status'] not in ('queued','running') for r in runs))

    @unittest.skipUnless(shutil.which('ngspice'), 'Requires real ngspice')
    def test_source_change_stops_remaining_scenarios(self):
        plan = self.prepare(self.workspace, self.pid, ac_spec())
        def update(**fields):
            if fields.get('completed_runs') == 1:
                self.workspace.projects.write(self.pid, 'notes.txt', 'Concurrent source change')
        result = self.execute(self.workspace, self.pid, plan, {'id':'a'*32}, threading.Event(), update)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['termination'], 'source_conflict')
        self.assertEqual(len(self.workspace.runs(self.pid)), 1)

    @unittest.skipUnless(shutil.which('ngspice'), 'Requires real ngspice')
    def test_total_deadline_cancels_child_before_later_scenarios(self):
        spec={'analysis':{'type':'op'},'axes':[{'parameter':'R','values':list(range(1000,1032))}],
              'temperatures_c':[0,27,85,125],
              'measurements':[{'kind':'power','name':'power_w','supply_node':'vdd','supply_source':'VDD'}],
              'limits':{'max_runs':128,'runtime_seconds':1,'run_timeout':60}}
        result=self.start(spec)
        self.assertEqual(result['status'],'timed_out',result)
        self.assertEqual(result['termination'],'runtime_limit')
        self.assertLess(result['completed_runs'],128)
        self.assertTrue(all(run['status'] not in ('queued','running') for run in self.workspace.runs(self.pid)))

    @unittest.skipUnless(shutil.which('ngspice'), 'Requires real ngspice')
    def test_external_corner_change_before_snapshot_stops_batch(self):
        pdk=Path(self.tmp.name)/'pdk';pdk.mkdir();library=pdk/'corners.lib'
        library.write_text('.lib tt\n.param factor=1\n.endl tt\n')
        self.workspace.configure_project(self.pid,{'pdk_root':str(pdk)})
        source=RC_SOURCE.replace('.param R=',f'.lib "{library}" tt\n.param R=')
        self.workspace.projects.write(self.pid,'testbenches/pre.cir',source)
        spec=ac_spec();spec['corners']={'library':str(library),'sections':['tt']}
        plan=self.prepare(self.workspace,self.pid,spec)
        library.write_text('.lib tt\n.param factor=2\n.endl tt\n')
        result=self.execute(self.workspace,self.pid,plan,{'id':'a'*32},threading.Event(),lambda **fields:None)
        self.assertEqual(result['termination'],'source_conflict')
        self.assertEqual(self.workspace.runs(self.pid),[])

    def test_start_requires_revision_and_catalog_examples_validate(self):
        from analog_factory.operation_schema import validate
        result = self.registry.invoke('simulation.batch.start', {'project_id':self.pid,'spec':ac_spec()})
        self.assertEqual(result['error']['code'], 'VERSION_REQUIRED')
        for name, operation in self.registry.operations.items():
            if name.startswith('simulation.batch.') or name.startswith('simulation.statistics.'):
                for example in operation.examples: validate(example['parameters'], operation.input_schema)

    def test_untrusted_statistical_certificate_never_enables_foundry_samples(self):
        from analog_factory.storage import atomic_json
        fake = 'a'*32
        atomic_json(self.workspace.projects.path(self.pid)/'.af/jobs'/f'{fake}.json',
                    {'id':fake,'kind':'statistical-qualification','status':'succeeded',
                     'qualification':{'qualified':True,'kind':'global_process'}})
        # Durable operation journals can restore task descriptors after restart;
        # a descriptor is not authority to restore statistical qualification.
        self.registry.tasks[f'job:{self.pid}:{fake}']={'operation_id':'simulation.statistics.qualify'}
        spec = ac_spec(); spec['statistics'] = {'kind':'global_process','samples':2,'qualification_job':fake}
        with self.assertRaisesRegex(ValueError,'qualif'):
            self.prepare(self.workspace,self.pid,spec)

    def test_qualification_operation_rejects_wrong_model_sections_before_job_creation(self):
        self.assertTrue('simulation.statistics.qualify' in self.registry.operations,
                        'Statistical qualification must be a registered operation')
        self.workspace.projects.write(self.pid,'models/fake.lib','.lib tt\n.param factor=1\n.endl tt\n')
        result = self.registry.invoke('simulation.statistics.qualify',{'project_id':self.pid,'source':'testbenches/pre.cir',
            'library':'models/fake.lib','kind':'global_process','section':'tt'},expected_version=self.registry.version(self.pid))
        self.assertFalse(result['ok'])
        self.assertEqual(self.workspace.jobs.list(self.workspace.projects.path(self.pid)),[])

    @unittest.skipUnless(shutil.which('ngspice'), 'Requires real ngspice')
    def test_qualification_identity_tracks_launcher_target_binary(self):
        from analog_factory.simulation_batches import _simulator_identity
        executable=Path(self.tmp.name)/'ngspice-copy';shutil.copy2(shutil.which('ngspice'),executable)
        launcher=Path(self.tmp.name)/'launcher';launcher.write_text(f'#!/bin/sh\nexec "{executable}" "$@"\n');launcher.chmod(0o700)
        self.workspace.configure_project(self.pid,{'tools':{'ngspice':str(launcher)}})
        first=_simulator_identity(self.workspace,self.pid)
        with executable.open('ab') as stream:stream.write(b'changed-binary-provenance')
        second=_simulator_identity(self.workspace,self.pid)
        self.assertNotEqual(first,second,'Changing the underlying binary must invalidate a launcher-backed qualification')

    @unittest.skipUnless(shutil.which('ngspice'), 'Requires real ngspice')
    def test_real_library_corners_change_measured_bandwidth(self):
        library='.lib tt\n.param FACTOR=1\n.endl tt\n.lib ss\n.param FACTOR=2\n.endl ss\n'
        self.workspace.projects.write(self.pid,'models/corners.lib',library)
        self.workspace.projects.write(self.pid,'testbenches/pre.cir',RC_SOURCE.replace('R1 in out {R}',
            'R1 in out {R*FACTOR}').replace('.param R=', '.lib "models/corners.lib" tt\n.param R='))
        spec=ac_spec(); spec['axes']=[]; spec['corners']={'library':'models/corners.lib','sections':['tt','ss']}
        result=self.start(spec)
        self.assertEqual(result['status'],'succeeded',result)
        self.assertAlmostEqual(result['iterations'][0]['metrics']['bandwidth_hz'],159.15,delta=1)
        self.assertAlmostEqual(result['iterations'][1]['metrics']['bandwidth_hz'],79.58,delta=1)

    def test_statistical_checks_reject_wrong_seed_and_correlation_claims(self):
        from analog_factory import simulation_batches as batch
        self.assertTrue(hasattr(batch,'_qualification_checks'))
        nominal={'n1':1e-4,'n2':1e-4,'p1':2e-4,'p2':2e-4}
        first={'n1':1.1e-4,'n2':1.1e-4,'p1':2.1e-4,'p2':2.1e-4}
        second={'n1':1.2e-4,'n2':1.2e-4,'p1':2.2e-4,'p2':2.2e-4}
        items=[{'metrics':copy.deepcopy(value)} for value in (nominal,nominal,first,first,second)]
        self.assertTrue(all(batch._qualification_checks(items,'global_process').values()))
        self.assertFalse(batch._qualification_checks(items,'device_mismatch')['requested_correlation'])
        items[3]['metrics']['n1']=3e-4
        self.assertFalse(batch._qualification_checks(items,'global_process')['same_seed_repeatable'])

    @unittest.skipUnless(os.environ.get('AF_TEST_REAL_SKY130_STATISTICS') == '1',
                         'Opt-in actual installed SKY130 statistical compatibility test (several minutes)')
    def test_real_sky130_statistics_measures_correlation_before_enabling_samples(self):
        repo=Path(__file__).resolve().parents[1]
        library=next((repo/'.tools/pdks/sky130').glob('*/sky130A/libs.tech/combined/sky130.lib.spice'))
        self.workspace.configure_project(self.pid,{'pdk_root':str(library.parents[3]),
            'tools':{'ngspice':str(repo/'.tools/eda/bin/ngspice-sky130')}})
        source=f'''* SKY130 statistical current test
.lib "{library}" tt
VD dn 0 .9
VG gn 0 .8
X1 dn gn 0 0 sky130_fd_pr__nfet_01v8 w=1 l=.15
.op
.end
'''
        self.workspace.projects.write(self.pid,'testbenches/pre.cir',source)
        results=[]
        for kind,section in [('global_process','mc'),('device_mismatch','tt_mm')]:
            if os.environ.get('AF_SKY130_VARIATION_KIND') and os.environ['AF_SKY130_VARIATION_KIND'] != kind:
                continue
            response=self.registry.invoke('simulation.statistics.qualify',{'project_id':self.pid,
                'library':str(library),'kind':kind,'section':section},expected_version=self.registry.version(self.pid))
            self.assertTrue(response['ok'],response)
            job=self.workspace.jobs.wait(self.workspace.projects.path(self.pid),response['data']['id'],timeout=1000)
            self.assertEqual(len(job['iterations']),5)
            self.assertTrue(all(item['status']=='succeeded' for item in job['iterations']),job)
            self.assertTrue(job['qualification']['checks']['same_seed_repeatable'],job['qualification'])
            self.assertTrue(job['qualification']['checks']['both_families_seed_sensitive'],job['qualification'])
            spec={'source':'testbenches/pre.cir','analysis':{'type':'op'},
                'corners':{'library':str(library),'sections':[section]},
                'statistics':{'kind':kind,'samples':2,'seed':73,'qualification_job':job['id']},
                'measurements':[{'kind':'power','name':'power_w','supply_node':'dn','supply_source':'VD'}],
                'limits':{'runtime_seconds':400,'run_timeout':180}}
            if kind=='global_process':
                # This installed open PDK emits independent instance draws even
                # with MM disabled. Never advertise that as global correlation.
                self.assertEqual(job['status'],'failed')
                self.assertFalse(job['qualification']['checks']['requested_correlation'])
                self.assertFalse(job['qualification']['qualified'])
                with self.assertRaisesRegex(ValueError,'qualif'):self.prepare(self.workspace,self.pid,spec)
                results.append({'kind':kind,'checks':job['qualification']['checks'],
                                'currents':[item['metrics'] for item in job['iterations']]})
                continue
            self.assertEqual(job['status'],'succeeded',job)
            self.assertTrue(job['qualification']['qualified'])
            plan=self.prepare(self.workspace,self.pid,spec)
            self.assertEqual(len(plan['scenarios']),2)
            run_response=self.registry.invoke('simulation.batch.start',{'project_id':self.pid,'spec':spec},expected_version=self.registry.version(self.pid))
            self.assertTrue(run_response['ok'],run_response)
            batch=self.workspace.jobs.wait(self.workspace.projects.path(self.pid),run_response['data']['id'],timeout=450)
            self.assertEqual(batch['status'],'succeeded',batch)
            self.assertNotEqual(batch['iterations'][0]['metrics']['power_w'],batch['iterations'][1]['metrics']['power_w'])
            results.append({'kind':kind,'checks':job['qualification']['checks'],
                            'currents':[item['metrics'] for item in job['iterations']],
                            'sample_power':[item['metrics'] for item in batch['iterations']]})
            bad=copy.deepcopy(spec); bad['corners']['sections']=['tt']
            with self.assertRaises(ValueError):self.prepare(self.workspace,self.pid,bad)
            self.registry.tasks.pop(f'job:{self.pid}:{job["id"]}')
            with self.assertRaisesRegex(ValueError,'qualif'):self.prepare(self.workspace,self.pid,spec)
        self.assertEqual(self.workspace.projects.read(self.pid,'testbenches/pre.cir')['content'],source)
        print('SKY130_STATISTICAL_EVIDENCE='+json.dumps(results,sort_keys=True),flush=True)


if __name__ == '__main__':
    unittest.main()
