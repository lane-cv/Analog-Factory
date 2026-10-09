"""Independent measurements and bounded source-preserving characterization."""
import copy
import importlib.util
import math
import tempfile
import threading
import unittest
from pathlib import Path
from analog_factory.service import Workspace


class CharacterizationTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('analog_factory.characterization'),
                             'OTA characterization must have an implemented service')
        from analog_factory import characterization
        return characterization

    def test_independent_ac_bandwidth_and_gain(self):
        api=self.api()
        frequencies=[1,10,100,1000]
        rows=[[ [f,0],[.5,0],[-.5,0],[100/math.sqrt(1+(f/100)**2),0]] for f in frequencies]
        raw={'variables':[{'name':n} for n in ('frequency','v(inp)','v(inn)','v(out)')],
             'complex_points':rows,'points':[[x[0] for x in r] for r in rows]}
        metrics=api.measure('ac',raw,{'supply_v':1.8})
        self.assertAlmostEqual(metrics['gain_db'],39.9995657,places=5)
        self.assertAlmostEqual(metrics['bandwidth_hz'],100,delta=.03)

    def test_transient_never_claims_settling_for_a_drifting_tail(self):
        api=self.api()
        raw={'variables':[{'name':'time'},{'name':'v(out)'}],
             'points':[[i*1e-6,float(i)] for i in range(101)]}
        with self.assertRaisesRegex(ValueError,'settling'):
            api.measure('tran',raw,{'supply_v':1.8})

    def test_noise_integrates_density_not_sample_mean(self):
        api=self.api()
        raw={'variables':[{'name':'frequency'},{'name':'onoise_spectrum'}],
             'points':[[1,2],[3,2],[10,2]]}
        self.assertAlmostEqual(api.measure('noise',raw,{'supply_v':1.8})['noise_v_rms'],6)

    def test_dc_offset_requires_output_crossing_and_interpolates(self):
        api=self.api()
        raw={'variables':[{'name':'v-sweep'},{'name':'v(out)'}],
             'points':[[-.01,.2],[0,.8],[.01,1.4]]}
        self.assertAlmostEqual(api.measure('dc',raw,{'supply_v':1.8})['offset_v'],1/600)
        raw['points']=[[-.01,.1],[.01,.2]]
        with self.assertRaisesRegex(ValueError,'crossing'):api.measure('dc',raw,{'supply_v':1.8})

    def test_observations_are_not_pass_without_targets(self):
        api=self.api()
        self.assertEqual(api.assess({'gain_db':40},{}),{'status':'observed','checks':{}})
        self.assertEqual(api.assess({'gain_db':40},{'gain_db_min':45})['status'],'fail')
        self.assertEqual(api.assess({'gain_db':40},{'noise_v_rms_max':1e-5})['status'],'unknown')

    def test_missing_sources_invalid_cases_and_duplicates_reject_before_job(self):
        api=self.api()
        with tempfile.TemporaryDirectory() as path:
            ws=Workspace(Path(path));self.addCleanup(ws.agents.close);self.addCleanup(ws.operations.close)
            pid=ws.projects.create('Characterization')['id']
            with self.assertRaises((ValueError,FileNotFoundError)):api.prepare(ws,pid,{})
        for change in ({'cases':[{'corner':'mc','supply_v':1.8,'temperature_c':27}]},
                       {'cases':[{'corner':'tt','supply_v':float('nan'),'temperature_c':27}]},
                       {'analyses':['ac','ac']},{'views':['pre','pre']},
                       {'limits':{'runtime_seconds':1801}}):
            with self.subTest(change=change),self.assertRaises(ValueError):api.normalize(change)

    def test_cancellation_prevents_launch(self):
        api=self.api();event=threading.Event();event.set()
        plan={'spec':api.normalize({}), 'scenarios':[], 'source_version':'unknown','config_digest':'unknown','model_dependencies':{}}
        result=api.execute(None,None,plan,{'id':'a'*32},event,lambda **v:None)
        self.assertEqual(result['status'],'cancelled')

    def test_validator_accepts_existing_nominal_pass_verdict(self):
        from unittest.mock import patch
        from scripts.validate_characterization import validate
        with tempfile.TemporaryDirectory() as path:
            ws=Workspace(Path(path));pid=ws.projects.create('No models')['id']
            ws.operations.close();ws.agents.close()
            with patch('scripts.validate_characterization.validate_ota',return_value={'status':'pass','project_id':pid}):
                with self.assertRaises(RuntimeError) as failure:validate(path,{}, {})
            self.assertNotIn('Original nominal OTA acceptance failed',str(failure.exception))

    def test_source_conflict_fails_before_any_run(self):
        api=self.api()
        from analog_factory.simulation_batches import _config_digest
        with tempfile.TemporaryDirectory() as path:
            ws=Workspace(Path(path));self.addCleanup(ws.agents.close);self.addCleanup(ws.operations.close)
            pid=ws.projects.create('Stale characterization')['id']
            spec=api.normalize({'views':['pre'],'analyses':['op']})
            scenario={'index':0,'source':'testbenches/pre.cir','conditions':{**spec['cases'][0],'view':'pre','analysis':'op','measurement_definitions':{}},'generated_files':{}}
            plan={'spec':spec,'scenarios':[scenario],'source_version':ws.operations.version(pid),'config_digest':_config_digest(ws,pid),'model_dependencies':{}}
            ws.projects.write(pid,'reports/change.txt','source changed')
            result=api.execute(ws,pid,plan,{'id':'a'*32},threading.Event(),lambda **v:None)
            self.assertEqual(result['termination'],'source_conflict')
            self.assertIn('sources',result.get('error','').lower())
            self.assertEqual(result['completed_runs'],0)

    def test_reduced_display_is_rejected_for_independent_measurement(self):
        api=self.api()
        with self.assertRaisesRegex(ValueError,'full original'):
            api.measure('noise',{'display_sampling':{'method':'bucket_extrema'}},{'supply_v':1.8})

    def test_real_ngspice_registry_runs_ac_op_dc_tran_noise_without_source_edits(self):
        import shutil
        if not shutil.which('ngspice'):self.skipTest('Actual ngspice unavailable')
        api=self.api()
        from analog_factory.operation_characterization import register_characterization
        with tempfile.TemporaryDirectory() as path:
            base=Path(path);library=base/'pdk/sky130A/libs.tech/combined/sky130.lib.spice'
            library.parent.mkdir(parents=True);library.write_text('.lib tt\n.param TEST_ONLY=1\n.endl tt\n')
            ws=Workspace(base/'workspace');self.addCleanup(ws.agents.close);self.addCleanup(ws.operations.close)
            ws.save_settings({'pdk_root':str(base/'pdk'),'pdk_variant':'sky130A'})
            pid=ws.projects.create('Linear simulator fixture, not SKY130 acceptance')['id']
            netlist='.subckt ota5 inp inn bias vdd vss out\nVCM mid 0 .9\nEAMP amp mid inp inn 100\nRAMP amp out 100k\nRPOWER vdd 0 1Meg\n.ends ota5\n'
            source=f'Test-only differential RC fixture\n.lib "{library}" tt\n.include "netlists/schematic.spice"\nXOTA inp inn bias vdd 0 out ota5\nVDD vdd 0 1.8\nVP inp 0 DC .9 AC .5\nVN inn 0 DC .9 AC .5 180\nVB bias 0 .65\nCL out 0 1p\n.temp 27\n.end\n'
            ws.projects.write(pid,'testbenches/pre.cir',source);ws.projects.write(pid,'netlists/schematic.spice',netlist)
            if 'reference.characterize' not in ws.operations.operations:register_characterization(ws.operations)
            result=ws.operations.invoke('reference.characterize',{'project_id':pid,'spec':{'views':['pre'],'limits':{'runtime_seconds':30,'run_timeout':10},'targets':{'gain_db_min':30}}},expected_version=ws.operations.version(pid))
            self.assertTrue(result['ok'],result)
            job=ws.jobs.wait(ws.projects.path(pid),result['data']['id'],timeout=35)
            self.assertEqual(job['status'],'succeeded',job)
            self.assertEqual(job['electrical_status'],'pass',job)
            self.assertEqual(job['completed_runs'],5)
            self.assertEqual(set(job['groups'][0]['metrics']),set(api.METRICS))
            self.assertAlmostEqual(job['groups'][0]['metrics']['offset_v'],0,places=8)
            self.assertAlmostEqual(job['groups'][0]['metrics']['power_w'],3.24e-6,delta=1e-9)
            self.assertEqual(ws.projects.read(pid,'testbenches/pre.cir')['content'],source)
            event=threading.Event();plan=api.prepare(ws,pid,{'views':['pre'],'analyses':['ac','op']})
            def stop_active(**values):
                if values.get('active_run'):event.set()
            cancelled=api.execute(ws,pid,plan,{'id':'b'*32},event,stop_active)
            self.assertEqual(cancelled['status'],'cancelled',cancelled)
            self.assertLessEqual(cancelled['completed_runs'],1)
            self.assertIsNone(cancelled['active_run'])
            if cancelled['iterations']:
                run=ws.runner.get(ws.projects.path(pid),cancelled['iterations'][0]['run_id'])
                self.assertNotIn(run['status'],('queued','running'))

    def test_preparation_tracks_external_model_closure(self):
        api=self.api()
        with tempfile.TemporaryDirectory() as path:
            base=Path(path);model=base/'pdk/sky130A/libs.tech/combined/sky130.lib.spice';model.parent.mkdir(parents=True)
            child=model.parent/'device.spice';child.write_text('.param PDK_CHILD=1\n')
            model.write_text('.lib tt\n.include "device.spice"\n.endl tt\n')
            ws=Workspace(base/'workspace');self.addCleanup(ws.agents.close);self.addCleanup(ws.operations.close)
            ws.save_settings({'pdk_root':str(base/'pdk'),'pdk_variant':'sky130A'})
            pid=ws.projects.create('Closure accounting')['id']
            source=f'Closure test\n.lib "{model}" tt\n.include "netlists/schematic.spice"\nVDD vdd 0 1.8\nVP inp 0 DC .9 AC .5\nVN inn 0 DC .9 AC .5 180\n.end\n'
            ws.projects.write(pid,'testbenches/pre.cir',source);ws.projects.write(pid,'netlists/schematic.spice','R1 out 0 1k\n')
            plan=api.prepare(ws,pid,{'views':['pre'],'analyses':['op']})
            self.assertIn(str(child),plan['model_dependencies'])
            self.assertTrue(plan.get('simulator_identity',{}).get('binaries'),'Characterization must preserve real simulator/launcher hashes')

    def test_dc_exact_sample_crossing_is_counted_once(self):
        api=self.api()
        raw={'variables':[{'name':'v-sweep'},{'name':'v(out)'}],
             'points':[[-.01,.5],[0,.9],[.01,1.3]]}
        self.assertEqual(api.measure('dc',raw,{'supply_v':1.8})['offset_v'],0)

    def test_normalized_duplicate_cases_are_rejected(self):
        api=self.api()
        with self.assertRaisesRegex(ValueError,'unique'):
            api.normalize({'cases':[{}, {'corner':'tt','supply_v':1.8,'temperature_c':27}]})

    def controller_probe(self,scenario):
        import shutil
        import subprocess
        if not shutil.which('node'):self.skipTest('Node required for controller contract probe')
        script=r'''
const fs=require('fs'), vm=require('vm'), assert=require('assert');
let version='v1', cancelled=0, detailReads=0;
const state={project:{id:'a'.repeat(32)},dirty:false};
const node=()=>({open:false,innerHTML:'',setAttribute(){},addEventListener(){},querySelector(){return {disabled:false};},showModal(){this.open=true;},close(){this.open=false;}});
global.document={createElement:node,body:{append(){}}};
global.window={AFOperations:{invoke:async (operation,parameters,options)=>{
 if(operation==='project.detail'){detailReads++;return {version};}
 if(operation==='job.list')return {data:[]};
 if(operation==='reference.characterize'){
  if(options.expected_version!==version){const e=new Error('Source changed');e.code='VERSION_CONFLICT';throw e;}
  return {data:{id:'b'.repeat(32),kind:'ota-characterization',status:'queued',spec:parameters.spec}};
 }
 if(operation==='job.cancel'){cancelled++;return {data:{}};}
 if(operation==='job.get')return {data:{id:'b'.repeat(32),status:'cancelled'}};
 throw new Error('Unexpected operation '+operation);
}}};
vm.runInThisContext(fs.readFileSync('analog_factory/static/characterization.js','utf8'));
(async()=>{const c=window.AFCharacterization.create({state});c.mount(node());await c.commands.open();
SCENARIO
 c.commands.close();})().catch(error=>{console.error(error);process.exitCode=1;});
'''.replace('SCENARIO',scenario)
        result=subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_controller_pins_opened_source_version(self):
        self.controller_probe("version='v2';let conflict=false;try{await c.commands.start();}catch(e){conflict=e.code==='VERSION_CONFLICT';}assert(conflict,'A changed source must reject the opened draft');assert.equal(detailReads,1,'Start must not silently advance the source version');")

    def test_controller_cancellation_works_with_unsaved_editor_draft(self):
        self.controller_probe("await c.commands.start();state.dirty=true;await c.commands.cancel();assert.equal(cancelled,1,'Dirty editor must not prevent active job cancellation');")

    def test_missing_raw_never_inherits_logged_or_forged_metrics(self):
        from unittest.mock import patch
        from analog_factory.simulation_batches import _config_digest
        api=self.api()
        with tempfile.TemporaryDirectory() as path:
            ws=Workspace(Path(path));self.addCleanup(ws.agents.close);self.addCleanup(ws.operations.close)
            pid=ws.projects.create('Missing authoritative raw')['id']
            spec=api.normalize({'views':['pre'],'analyses':['ac'],'targets':{'gain_db_min':30}})
            conditions={**spec['cases'][0],'view':'pre','analysis':'ac','measurement_definitions':{}}
            scenario={'index':0,'source':'testbenches/pre.cir','conditions':conditions,'generated_files':{}}
            plan={'spec':spec,'scenarios':[scenario],'source_version':ws.operations.version(pid),'config_digest':_config_digest(ws,pid),'model_dependencies':{}}
            item={'index':0,'run_id':'c'*32,'status':'succeeded','stale':False,'conditions':conditions,'metrics':{'gain_db':999}}
            with patch('analog_factory.characterization.execute_batch',return_value={'status':'succeeded','termination':'completed','iterations':[item]}):
                result=api.execute(ws,pid,plan,{'id':'a'*32},threading.Event(),lambda **v:None)
            self.assertEqual(result['groups'][0]['metrics'],{},'Only independently measured raw data may enter electrical assessment')
            self.assertEqual(result['groups'][0]['assessment']['status'],'unknown')
            self.assertEqual(result['electrical_status'],'unknown')

    def test_discovery_reports_actual_missing_characterization_prerequisite(self):
        self.api()
        with tempfile.TemporaryDirectory() as path:
            ws=Workspace(Path(path));self.addCleanup(ws.agents.close);self.addCleanup(ws.operations.close)
            pid=ws.projects.create('Unprepared OTA')['id']
            described=ws.operations.describe_operation('reference.characterize',parameters={'project_id':pid})
            self.assertFalse(described['availability']['available'])
            self.assertTrue(described['availability']['reasons'])

    def test_real_ngspice_included_control_cannot_forge_current_and_power_pass(self):
        import shutil
        if not shutil.which('ngspice'):self.skipTest('Actual ngspice unavailable')
        with tempfile.TemporaryDirectory() as path:
            base=Path(path);library=base/'pdk/sky130A/libs.tech/combined/sky130.lib.spice'
            library.parent.mkdir(parents=True);library.write_text('.lib tt\n.param TEST_ONLY=1\n.endl tt\n')
            ws=Workspace(base/'workspace');self.addCleanup(ws.agents.close);self.addCleanup(ws.operations.close)
            ws.save_settings({'pdk_root':str(base/'pdk'),'pdk_variant':'sky130A'})
            pid=ws.projects.create('Actual control-injection regression fixture')['id']
            netlist='.subckt ota5 inp inn bias vdd vss out\nVCM mid 0 .9\nEAMP amp mid inp inn 100\nRAMP amp out 100k\nRPOWER vdd 0 1Meg\n.ends ota5\n'
            injection='.control\nset filetype=ascii\nop\nlet i(vdd)=-1e-6\nwrite ../output/waveforms.raw all\nquit\n.endc\n'
            source=f'Control-injection regression\n.lib "{library}" tt\n.include "netlists/schematic.spice"\nXOTA inp inn bias vdd 0 out ota5\nVDD vdd 0 1.8\nVP inp 0 DC .9 AC .5\nVN inn 0 DC .9 AC .5 180\nVB bias 0 .65\nCL out 0 1p\n.temp 27\n.end\n'
            ws.projects.write(pid,'testbenches/pre.cir',source);ws.projects.write(pid,'netlists/schematic.spice',netlist+injection)
            result=ws.operations.invoke('reference.characterize',{'project_id':pid,'spec':{'views':['pre'],'analyses':['op'],'targets':{'power_w_max':2e-6},'limits':{'runtime_seconds':20,'run_timeout':10}}},expected_version=ws.operations.version(pid))
            if result['ok']:
                result['reproduced_job']=ws.jobs.wait(ws.projects.path(pid),result['data']['id'],timeout=25)
            self.assertFalse(result['ok'],result)
            self.assertIn('control',result['error']['message'].lower())
            self.assertEqual(ws.jobs.list(ws.projects.path(pid)),[],'Rejected source must not launch a characterization job')

    def test_closed_include_validation_rejects_nested_overrides_and_unknown_resolution(self):
        api=self.api()
        with tempfile.TemporaryDirectory() as path:
            root=Path(path);(root/'netlists').mkdir()
            source='Closure test\n.lib "/configured/model.spice" tt\n.include "netlists/main.spice"\n.op\n.end\n'
            (root/'netlists/main.spice').write_text('.subckt ota5 a b\n.include "netlists/child.spice"\n.ends ota5\n')
            for injected in ('.control\nquit\n.endc\n','.op\n','.ac dec 10 1 1Meg\n','.temp 99\n','.save none\n','.meas op forged param=1\n','.end\n','.lib "elsewhere.spice" tt\n','.param MC_MM_SWITCH=1\n','.include "$MODEL"\n','.include "../escape.spice"\n','.include "netlists/missing.spice"\n'):
                (root/'netlists/child.spice').write_text(injected)
                with self.subTest(injected=injected),self.assertRaises(ValueError):
                    api.validate_source_closure(root,'testbenches/pre.cir',source,{})
            (root/'netlists/child.spice').write_text('.param W=4\n.model local NMOS\nR1 a b 1k\n')
            api.validate_source_closure(root,'testbenches/pre.cir',source,{})

    def test_model_closure_rejects_controls_and_ambiguous_provenance(self):
        api=self.api()
        with tempfile.TemporaryDirectory() as path:
            root=Path(path);model=root/'model.spice';source='Title\n.lib "model.spice" tt\n.end\n'
            for text in ('.control\nquit\n.endc\n','.op\n','.save none\n'):
                model.write_text(text)
                with self.subTest(text=text),self.assertRaises(ValueError):api.validate_source_closure(root,'testbenches/pre.cir',source,{str(model):'hash'})
            model.write_text('.lib tt\n.param W=4\n.option scale=1u\n.options parser scale=1u\n.model nf NMOS\n.endl tt\n')
            api.validate_source_closure(root,'testbenches/pre.cir',source,{str(model):'hash'})

    def test_model_include_cannot_escape_into_unvalidated_project_cards(self):
        api=self.api()
        with tempfile.TemporaryDirectory() as path:
            root=Path(path);(root/'netlists').mkdir();target=root/'netlists/unvalidated.spice';target.write_text('.temp 999\n')
            model=root/'model.spice';model.write_text(f'.lib tt\n.include "{target}"\n.endl tt\n')
            with self.assertRaisesRegex(ValueError,'model include'):
                api.validate_source_closure(root,'testbenches/pre.cir','Title\n.lib "model.spice" tt\n.end\n',{str(model):'hash'})

    def test_effective_configured_model_is_checked_when_saved_deck_names_other_library(self):
        api=self.api()
        with tempfile.TemporaryDirectory() as path:
            base=Path(path);model=base/'pdk/sky130A/libs.tech/combined/sky130.lib.spice';model.parent.mkdir(parents=True)
            model.write_text('.lib tt\n.control\nop\nquit\n.endc\n.endl tt\n')
            other=model.parent/'old-model.lib.spice';other.write_text('.lib tt\n.param OLD=1\n.endl tt\n')
            ws=Workspace(base/'workspace');self.addCleanup(ws.agents.close);self.addCleanup(ws.operations.close)
            ws.save_settings({'pdk_root':str(base/'pdk'),'pdk_variant':'sky130A'})
            pid=ws.projects.create('Effective model closure regression')['id']
            source=f'Title\n.lib "{other}" tt\n.include "netlists/schematic.spice"\nVDD vdd 0 1.8\nVP inp 0 DC .9 AC .5\nVN inn 0 DC .9 AC .5 180\n.end\n'
            ws.projects.write(pid,'testbenches/pre.cir',source);ws.projects.write(pid,'netlists/schematic.spice','R1 out 0 1k\n')
            with self.assertRaisesRegex(ValueError,'control'):api.prepare(ws,pid,{'views':['pre'],'analyses':['op']})
