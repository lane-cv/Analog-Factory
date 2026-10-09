import shutil
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from analog_factory.service import Workspace
from analog_factory.templates import tuning_workflow


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Workspace(Path(self.tmp.name))
        self.project = self.workspace.projects.create('Integration')
        self.pid = self.project['id']

    def test_settings_exclude_secrets_on_disk(self):
        self.workspace.save_settings({'provider': {'protocol': 'openai-compatible', 'base_url': 'http://localhost:1/v1', 'model': 'test', 'api_key': 'do-not-persist'}})
        self.assertNotIn('do-not-persist', (Path(self.tmp.name) / 'settings.json').read_text())
        self.assertNotIn('api_key', self.workspace.settings()['provider'])

    def test_status_uses_actual_training_resources_and_project_provider(self):
        self.workspace.projects.configure(self.pid, {'provider': {
            'protocol': 'openai-compatible', 'base_url': 'http://localhost:1/v1', 'model': 'local'}})
        for available in (False, True):
            with self.subTest(available=available), patch('analog_factory.training.manager.resources',
                    return_value={'frameworks': {'trl_cpu': {'available': available,
                                  'reason': '' if available else 'Pinned model is missing'}}}):
                capabilities = self.workspace.status(self.pid)['capabilities']
                self.assertEqual(capabilities['training']['available'], available)
                self.assertEqual(capabilities['training']['reason'], '' if available else 'Pinned model is missing')
                self.assertNotEqual(capabilities['training']['status'], 'planned')
                self.assertTrue(capabilities['assistant']['available'])
                self.assertIn('typed graph', capabilities['workflows']['status'])

    def test_credentials_are_scoped_to_configured_provider(self):
        self.workspace.save_settings({'provider': {'protocol': 'openai-compatible', 'base_url': 'https://trusted.invalid/v1', 'model': 'a', 'api_key': 'secret', 'api_key_env': 'TRUSTED_KEY'}})
        self.workspace.projects.configure(self.pid, {'provider': {'base_url': 'https://different.invalid/v1'}})
        self.assertIsNone(self.workspace.provider(self.pid).secret)
        self.assertNotIn('api_key_env', self.workspace.config(self.pid)['provider'])
        self.workspace.save_settings({'provider': {'protocol': 'openai-compatible', 'base_url': 'https://new.invalid/v1', 'model': 'b'}})
        self.assertIsNone(self.workspace.provider().secret)

    @unittest.skipUnless(shutil.which('ngspice'), 'ngspice is required for actual simulation')
    def test_real_simulation_and_metrics(self):
        run = self.workspace.run(self.pid, 'simulate', {'netlist': 'testbenches/pre.cir'})
        result = self.workspace.runner.wait(self.workspace.projects.path(self.pid), run['id'])
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertGreater(result['metrics']['vout_mean'], 0)
        self.assertLess(result['metrics']['vout_mean'], 3.3)
        wave = self.workspace.waveforms(self.pid, run['id'])
        self.assertGreater(len(wave['points']), 100)
        self.assertFalse(result['stale'])

    @unittest.skipUnless(shutil.which('ngspice'), 'ngspice is required for closed-loop tuning')
    def test_tuning_uses_real_runs_and_preserves_source(self):
        original = self.workspace.projects.read(self.pid, 'testbenches/pre.cir')['content']
        job = self.workspace.start_workflow(self.pid, tuning_workflow())
        result = self.workspace.jobs.wait(self.workspace.projects.path(self.pid), job['id'], timeout=60)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertTrue(result['satisfied'], result)
        self.assertGreaterEqual(len(result['iterations']), 3)
        self.assertLessEqual(len(result['iterations']), 8)
        self.assertTrue(all(i['run_id'] for i in result['iterations']))
        self.assertEqual(self.workspace.projects.read(self.pid, 'testbenches/pre.cir')['content'], original)

    def test_simulation_directives_validation(self):
        result = self.workspace.configure_simulation(self.pid, {'analysis': 'ac', 'parameters': {'points': 40, 'start': 1, 'stop': '1Meg'}})
        deck = self.workspace.projects.read(self.pid, result['path'])['content']
        self.assertIn('.ac dec 40 1 1Meg', deck)
        with self.assertRaises(ValueError):
            self.workspace.configure_simulation(self.pid, {'analysis': 'invented'})

    @unittest.skipUnless(shutil.which('ngspice'), 'ngspice required')
    def test_all_graphical_analysis_types_execute(self):
        for analysis in ('op', 'dc', 'ac', 'tran', 'noise'):
            with self.subTest(analysis=analysis):
                deck = self.workspace.configure_simulation(self.pid, {'analysis': analysis})
                run = self.workspace.run(self.pid, 'simulate', {'netlist': deck['path']})
                result = self.workspace.runner.wait(self.workspace.projects.path(self.pid), run['id'])
                self.assertEqual(result['status'], 'succeeded', self.workspace.log(self.pid, run['id']))
                self.assertGreater(len(self.workspace.waveforms(self.pid, run['id'])['points']), 0)

    def test_invalid_settings_do_not_replace_valid_configuration(self):
        self.workspace.save_settings({'pdk_variant': 'sky130A'})
        with self.assertRaises(ValueError):
            self.workspace.save_settings({'pdk_variant': '../bad', 'tools': {'ngspice': []}})
        self.assertEqual(self.workspace.settings()['pdk_variant'], 'sky130A')

    @unittest.skipUnless(shutil.which('ngspice'), 'ngspice required')
    def test_episode_evidence_is_accessible_and_exported(self):
        import io
        import zipfile
        job = self.workspace.evaluate(self.pid, {'episodes': 1, 'seed': 42})
        result = self.workspace.jobs.wait(self.workspace.projects.path(self.pid), job['id'])
        self.assertEqual(result['status'], 'succeeded', result)
        rid = result['iterations'][0]['run_id']
        self.assertIn('ngspice', self.workspace.log(self.pid, rid)['content'])
        self.assertGreater(len(self.workspace.waveforms(self.pid, rid)['points']), 0)
        with zipfile.ZipFile(io.BytesIO(self.workspace.projects.export(self.pid))) as archive:
            names = archive.namelist()
            self.assertTrue(any(rid in path for path in names))
            self.assertTrue(any(job['id'] in path and path.endswith('.json') for path in names))

    def test_verification_log_includes_actual_report_filename(self):
        import sys
        root = self.workspace.projects.path(self.pid)
        run = self.workspace.runner.start(root, 'drc', [sys.executable, '-c', "from pathlib import Path; Path('../output/drc.txt').write_text('Detailed rule errors')"])
        self.workspace.runner.wait(root, run['id'])
        self.assertIn('Detailed rule errors', self.workspace.log(self.pid, run['id'])['content'])
