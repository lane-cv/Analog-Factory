import copy
import math
import unittest

from analog_factory.templates import tuning_workflow
from analog_factory.workflows import validate_workflow, replace_parameter
from analog_factory.evaluation import evaluate_metric


class WorkflowTests(unittest.TestCase):
    def test_real_supported_graph_and_limits(self):
        flow = tuning_workflow()
        self.assertEqual(validate_workflow(flow)['tuning']['parameter'], 'RD')
        flow['limits']['iterations'] = 0
        with self.assertRaises(ValueError):
            validate_workflow(flow)

    def test_disconnected_or_unsupported_graph_rejected(self):
        for mutate in (lambda f: f['edges'].clear(),
                       lambda f: f['nodes'][1].update(type='model'),
                       lambda f: f['edges'].append({'source': 'n5', 'target': 'n0'})):
            flow = tuning_workflow()
            mutate(flow)
            with self.assertRaises(ValueError):
                validate_workflow(flow)

    def test_nonfinite_bounds_and_reversed_bounds_rejected(self):
        for low, high in [(100, 1), (float('nan'), 10000), (0, float('inf'))]:
            flow = tuning_workflow()
            flow['tuning'].update(minimum=low, maximum=high)
            with self.assertRaises(ValueError):
                validate_workflow(flow)

    def test_parameter_edit_does_not_replace_unrelated_directives(self):
        original = '.param RD=3300 OTHER=1\nR1 x 0 {RD}\n.end\n'
        updated = replace_parameter(original, 'RD', 2000)
        self.assertIn('RD=2000', updated)
        self.assertIn('OTHER=1', updated)
        self.assertIn('R1 x 0 {RD}', updated)
        with self.assertRaises(ValueError):
            replace_parameter(original, 'not_found', 1)

    def test_evaluator_missing_measurement_is_failure_not_zero(self):
        score = evaluate_metric(None, {'target': 1.5, 'tolerance': .05})
        self.assertFalse(score['satisfied'])
        self.assertEqual(score['failure'], 'missing_or_nonfinite_measurement')
        self.assertLess(score['reward'], 0)
        score = evaluate_metric(1.51, {'target': 1.5, 'tolerance': .05})
        self.assertTrue(score['satisfied'])
        self.assertAlmostEqual(score['absolute_error'], .01)
