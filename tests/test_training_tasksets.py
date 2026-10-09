"""Configurable tasksets keep executable scoring and split membership trusted."""
import copy
import json
from pathlib import Path
import tempfile
import unittest


class TasksetTests(unittest.TestCase):
    def catalog(self):
        from analog_factory.training import task_catalog
        return task_catalog

    def test_two_families_have_disjoint_deterministic_splits(self):
        catalog = self.catalog()
        for family in ('amplifier', 'current_mirror'):
            with self.subTest(family=family):
                spec = catalog.defaults(family)
                first = catalog.build(spec)
                self.assertEqual(first, catalog.build(spec))
                reordered = copy.deepcopy(spec)
                reordered['conditions'] = dict(reversed(list(reordered['conditions'].items())))
                self.assertEqual(first, catalog.build(reordered))
                self.assertEqual([len(first['splits'][key]) for key in ('train', 'validation', 'test')], [64, 16, 32])
                ids = [task['id'] for tasks in first['splits'].values() for task in tasks]
                self.assertEqual(len(ids), len(set(ids)))
                changed = copy.deepcopy(spec)
                changed['seed'] += 1
                self.assertNotEqual(first['splits'], catalog.build(changed)['splits'])

    def test_rejects_policy_scoring_source_and_unsafe_domains(self):
        catalog = self.catalog()
        for update in ({'reward_code': 'return 100'}, {'model_path': '/tmp/model'},
                       {'family': 'arbitrary_spice'}, {'seed': True},
                       {'counts': {'train': 10000, 'validation': 16, 'test': 32}},
                       {'conditions': {'vdd': [float('nan')]}},
                       {'scoring': {'simulator_call_penalty': -1}}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                catalog.build({**catalog.defaults('amplifier'), **update})

    def test_project_catalog_is_immutable_content_addressed(self):
        catalog = self.catalog()
        from analog_factory.service import Workspace
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(Path(temporary))
            project = workspace.projects.create('Tasksets', 'amplifier')['id']
            created = catalog.create(workspace, project, catalog.defaults('current_mirror'))
            self.assertEqual(created, catalog.create(workspace, project, catalog.defaults('current_mirror')))
            self.assertEqual(catalog.get(workspace, project, created['id']), created)
            self.assertIn(created['id'], [record['id'] for record in catalog.list_tasksets(workspace, project)])
            path = workspace.projects.path(project) / '.af/training-tasksets' / (created['id'] + '.json')
            tampered = copy.deepcopy(created)
            tampered['splits']['test'][0]['targets']['ratio'] = 999
            path.write_text(json.dumps(tampered))
            with self.assertRaisesRegex(ValueError, 'changed|integrity|hash'):
                catalog.get(workspace, project, created['id'])

    def test_current_mirror_has_independent_electrical_metrics(self):
        catalog = self.catalog()
        from analog_factory.training import configurable_environment as environment
        taskset = catalog.build(catalog.defaults('current_mirror'))
        task = taskset['splits']['train'][0]
        result = environment.score(taskset, task, {
            'output_current': task['conditions']['reference_ua'] * 1e-6 * task['targets']['ratio'],
            'reference_current': task['conditions']['reference_ua'] * 1e-6,
            'gate_voltage': .8}, calls=2)
        self.assertTrue(result['satisfied'])
        wrong = environment.score(taskset, task, {'output_current': 1e-12,
            'reference_current': task['conditions']['reference_ua']*1e-6, 'gate_voltage': .8}, calls=2)
        self.assertFalse(wrong['satisfied'])
        self.assertFalse(wrong['constraints']['ratio_target'])
        self.assertLess(wrong['reward'], result['reward'])
        self.assertIn('IREF', environment.deck(taskset, task,
            {'reference_width_index': 0, 'output_width_index': 0, 'length_index': 0}))

    def test_policy_with_execution_scope_cannot_rewrite_evaluator(self):
        catalog = self.catalog()
        from analog_factory.service import Workspace
        from analog_factory.operations import InvocationContext
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(Path(temporary))
            project = workspace.projects.create('Taskset authorization', 'amplifier')['id']
            reply = workspace.operations.invoke('training.taskset.create',
                {'project_id': project, 'specification': catalog.defaults('amplifier')},
                context=InvocationContext(source='evaluation', permissions=frozenset({'read', 'execute'})))
            self.assertEqual(reply['error']['code'], 'PERMISSION_DENIED')
            self.assertEqual(len(catalog.list_tasksets(workspace, project)), 1)

    def test_nonfinite_measurement_is_json_safe_failure(self):
        catalog = self.catalog()
        from analog_factory.training import configurable_environment as env
        for family in ('amplifier', 'current_mirror'):
            taskset = catalog.build(catalog.defaults(family))
            result = env.score(taskset, taskset['splits']['train'][0], {'gain_db': float('nan')}, calls=1)
            self.assertFalse(result['satisfied'])
            self.assertEqual(json.loads(json.dumps(result, allow_nan=False))['metrics']['gain_db'], None)


if __name__ == '__main__':
    unittest.main()
