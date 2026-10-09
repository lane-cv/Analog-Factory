"""Reference assets and strict acceptance gates, plus opt-in real-tool replay."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Ota5ReferenceTests(unittest.TestCase):
    def test_replay_script_and_native_sources_exist(self):
        for relative in ('scripts/validate_ota5.py', 'examples/ota5/schematics/ota5.sch',
                         'examples/ota5/layout/ota5.mag', 'examples/ota5/netlists/ota5.spice',
                         'examples/ota5/testbenches/pre.cir', 'examples/ota5/testbenches/post.cir'):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_exactly_five_mos_and_no_embedded_supply_or_bias_device(self):
        path = ROOT / 'examples/ota5/netlists/ota5.spice'
        self.assertTrue(path.is_file(), 'Independent five-device circuit is required')
        lines = [line for line in path.read_text().splitlines() if line and not line.startswith(('*', '.', '+'))]
        self.assertEqual(len(lines), 5)
        self.assertTrue(all(line.startswith('X') and 'sky130_fd_pr__' in line for line in lines))
        self.assertFalse(any('/home/' in p.read_text() for p in (ROOT / 'examples/ota5').rglob('*') if p.is_file() and '__pycache__' not in p.parts))

    def module(self):
        spec = importlib.util.spec_from_file_location('validate_ota5', ROOT / 'scripts/validate_ota5.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_subcircuit_conversion_preserves_devices_and_rejects_extra_device(self):
        module = self.module()
        source = (ROOT / 'examples/ota5/netlists/ota5.spice').read_text()
        generated = source.replace('.subckt', '**.subckt').replace('.ends ota5', '**.ends') + '.end\n'
        converted = module.canonical_subcircuit(generated)
        self.assertEqual([line for line in converted.splitlines() if line.startswith('X')],
                         [line for line in source.splitlines() if line.startswith('X')])
        with self.assertRaises(ValueError):
            module.canonical_subcircuit(source.replace('.ends ota5', 'Xhidden a b c d other_model\n.ends ota5'))
        with self.assertRaises(ValueError):
            module.canonical_subcircuit(source.replace('inp inn bias vdd vss out', 'inp inn bias vdd vss'))

    def test_capacitance_filter_keeps_original_mos_and_requires_extraction(self):
        module = self.module()
        source = (ROOT / 'examples/ota5/netlists/ota5.spice').read_text()
        extracted = source.replace('.ends ota5', 'C0 out vss 2f\n+ m=1\nC1 inp out 0.1f\n.ends ota5')
        filtered, count = module.without_capacitors(extracted)
        self.assertEqual(filtered, source)
        self.assertEqual(count, 2)
        with self.assertRaises(ValueError):
            module.without_capacitors(source)

    def test_gain_is_measured_against_complex_differential_input(self):
        module = self.module()
        self.assertTrue(hasattr(module, 'differential_gain_db'), 'Gain must use the complex differential input')
        raw = {'variables': [{'name': 'frequency'}, {'name': 'v(inp)'},
                             {'name': 'v(inn)'}, {'name': 'v(out)'}],
               'complex_points': [[[1, 0], [0.5, 0], [-0.5, 0], [40, -30]]]}
        self.assertAlmostEqual(module.differential_gain_db(raw), 33.979400086720375)
        raw['complex_points'][0][2] = [0.5, 0]
        with self.assertRaises(ValueError):
            module.differential_gain_db(raw)

    @unittest.skipUnless(os.environ.get('AF_OTA5_REAL_TOOLS') == '1', 'Set AF_OTA5_REAL_TOOLS=1 for the full EDA reference')
    def test_actual_full_reference_and_negative_controls(self):
        spec = importlib.util.spec_from_file_location('validate_ota5', ROOT / 'scripts/validate_ota5.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix='af-ota5-test-') as temporary:
            report = module.validate(Path(temporary), module.configuration(ROOT / '.validation/settings.json'))
            self.assertEqual(report['status'], 'pass')
            self.assertGreater(report['pre']['gain_db'], 30)
            self.assertGreater(report['post']['gain_db'], 30)
            self.assertGreater(report['parasitic_capacitors'], 0)
            self.assertEqual(report['drc']['verification']['status'], 'pass')
            self.assertEqual(report['lvs']['verification']['status'], 'pass')
            self.assertEqual(report['negative_lvs']['verification']['status'], 'fail')
            self.assertEqual(report['negative_drc']['verification']['status'], 'fail')


if __name__ == '__main__':
    unittest.main()
