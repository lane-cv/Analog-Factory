"""Independent evaluator, bounded actions, and optional real language-model training."""
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class TrainingContractTests(unittest.TestCase):
    def environment(self):
        self.assertTrue((ROOT / 'analog_factory/training/environment.py').is_file(),
                        'Training needs an independent bounded analog environment')
        from analog_factory.training import environment
        return environment

    def test_tasksets_are_reproducible_and_disjoint(self):
        env = self.environment()
        a, b = env.tasksets(), env.tasksets()
        self.assertEqual(a, b)
        self.assertEqual([len(a[k]) for k in ('train', 'validation', 'test')], [64, 16, 32])
        ids = [t['id'] for tasks in a.values() for t in tasks]
        self.assertEqual(len(ids), len(set(ids)))
        train = {tuple(t['conditions'].values()) + tuple(t['targets'].values()) for t in a['train']}
        test = {tuple(t['conditions'].values()) + tuple(t['targets'].values()) for t in a['test']}
        self.assertFalse(train & test)
        a['train'][0]['targets']['gain_db'] = -999
        self.assertNotEqual(a, env.tasksets())

    def test_actions_are_typed_bounded_and_cannot_override_evaluator(self):
        env = self.environment()
        valid = {'rd_index': 2, 'bias_index': 2, 'width_index': 2}
        self.assertEqual(set(env.parameters(valid)), {'rd', 'bias', 'width'})
        for bad in ({**valid, 'rd_index': -1}, {**valid, 'rd_index': 99},
                    {**valid, 'bias_index': True}, {**valid, 'width_index': math.nan},
                    {**valid, 'width_index': '2'}, {**valid, 'reward': 999}, {}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                env.parameters(bad)

    def test_missing_or_nonfinite_measurement_is_never_success(self):
        env = self.environment()
        task = env.tasksets()['train'][0]
        for metrics in ({}, {'gain_db': math.nan, 'vout_dc': .9, 'supply_current': .0001}):
            score = env.score(task, metrics, calls=1)
            self.assertEqual(score['reward'], -1)
            self.assertFalse(score['satisfied'])
            self.assertTrue(score['failure'])

    def test_constraint_failure_is_separate_from_objective(self):
        env = self.environment()
        task = env.tasksets()['train'][0]
        ideal = {'gain_db': task['targets']['gain_db'] + 2,
                 'vout_dc': task['conditions']['vdd'] * task['targets']['output_fraction'],
                 'supply_current': -task['targets']['current_limit'] / 2}
        good = env.score(task, ideal, calls=1)
        self.assertTrue(good['satisfied'])
        bad = env.score(task, {**ideal, 'vout_dc': -.1}, calls=1)
        self.assertFalse(bad['constraints']['output_range'])
        self.assertFalse(bad['satisfied'])
        self.assertIsNone(bad['failure'])
        self.assertLess(bad['reward'], good['reward'])
        self.assertEqual(good['cost']['simulator_calls'], 1)
        self.assertIsNone(good['cost']['monetary_usd'])

    def test_nonfinite_measurements_can_be_saved_as_failure_evidence(self):
        env = self.environment()
        task = env.tasksets()['train'][0]
        result = env.score(task, {'gain_db': math.nan, 'vout_dc': math.inf,
                                 'supply_current': -.0001}, calls=1)
        encoded = json.dumps(result, allow_nan=False)
        self.assertEqual(json.loads(encoded)['metrics']['gain_db'], None)

    def test_deck_is_fixed_real_ngspice_and_has_no_policy_code(self):
        env = self.environment()
        task = env.tasksets()['train'][0]
        text = env.deck(task, {'rd_index': 2, 'bias_index': 2, 'width_index': 2})
        self.assertIn('M1 out gate 0 0 NTRAIN', text)
        self.assertIn('ac dec', text)
        self.assertIn('gain_db', text)
        self.assertNotIn('reward', text)

    def manager(self):
        self.assertTrue((ROOT / 'analog_factory/training/manager.py').is_file(),
                        'Optional training requires a bounded process manager')
        from analog_factory.training import manager
        return manager

    def test_training_options_do_not_accept_untrusted_program_or_model_paths(self):
        manager = self.manager()
        self.assertEqual(manager.options({})['framework'], 'trl_cpu')
        for bad in ({'python': '/bin/echo'}, {'model': '/tmp/pickle'}, {'steps': True},
                    {'steps': 100000}, {'wall_seconds': math.inf}, {'split': 'test'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                manager.options(bad)

    def test_framework_capabilities_distinguish_cpu_candidate_from_gpu_adapters(self):
        manager = self.manager()
        capabilities = manager.resources()
        self.assertEqual(set(capabilities['frameworks']), {'trl_cpu', 'agent_lightning', 'areal', 'agentfly'})
        for name in ('agent_lightning', 'areal', 'agentfly'):
            self.assertFalse(capabilities['frameworks'][name]['available'])
            self.assertTrue(capabilities['frameworks'][name]['reason'])

    def test_registered_training_contract_and_agent_permission(self):
        self.assertTrue((ROOT / 'analog_factory/operation_training.py').is_file())
        from analog_factory.operation_training import register_operations
        from analog_factory.operations import OperationRegistry, InvocationContext
        from analog_factory.service import Workspace
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(Path(temporary))
            registry = OperationRegistry(workspace)
            register_operations(registry)
            self.assertEqual(set(registry.operations), {'training.resources', 'training.tasksets',
                'training.taskset.validate', 'training.taskset.get', 'training.taskset.list', 'training.taskset.create',
                'training.start', 'training.evaluate', 'training.get', 'training.list',
                'training.cancel', 'training.replay'})
            self.assertTrue(registry.invoke('training.resources', {})['ok'])
            project = workspace.projects.create('Training contract', 'amplifier')
            result = registry.invoke('training.start', {'project_id': project['id']},
                context=InvocationContext(source='external-agent', permissions=frozenset({'read'})))
            self.assertEqual(result['error']['code'], 'PERMISSION_DENIED')

    def test_checkpoint_requires_safetensors_and_exact_recorded_file_set(self):
        manager = self.manager()
        from analog_factory.service import Workspace
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(Path(temporary))
            project = workspace.projects.create('Checkpoint integrity', 'amplifier')['id']
            parent = {'id': 'a'*32, 'owner_identity': 'local', 'status': 'succeeded',
                      'model_weights_updated': True, 'checkpoint': {'hashes': {}}}
            root = manager.directory(workspace, project, parent['id'])/'checkpoint'
            root.mkdir(parents=True)
            with patch.object(manager, 'resources', return_value={'frameworks': {'trl_cpu': {'available': True}}}), \
                 patch.object(manager, 'get', return_value=parent), patch.object(workspace.jobs, 'start') as start:
                with self.assertRaisesRegex(ValueError, 'Checkpoint|checkpoint'):
                    manager.start(workspace, project, checkpoint_job_id=parent['id'])
                start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
