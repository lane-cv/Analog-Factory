"""Bounded immutable SKY130 adaptation and its explicit statistical contract."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest


class StatisticalModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.combined = self.root / 'sky130A/libs.tech/combined'
        self.combined.mkdir(parents=True)
        self.parameters = self.combined / 'continuous/parameters_fet_tt.spice'
        self.parameters.parent.mkdir()
        self.parameters.write_text('.param fixed = 7\n.param sw_polycd = {0+corner_factor*0} + process_mc_factor*MC_PR_SWITCH*AGAUSS(0,1.5e-9,1)\n')
        self.models = self.combined / 'continuous/models_fet.spice'
        self.models.write_text('.subckt n d g s b\n.param local = MC_MM_SWITCH*AGAUSS(0,1,1)\n.ends\n')
        self.library = self.combined / 'sky130.lib.spice'
        self.library.write_text('.lib tt\n.param MC_MM_SWITCH=0 MC_PR_SWITCH=0\n.include continuous/parameters_fet_tt.spice\n.include continuous/models_fet.spice\n.endl tt\n.lib mc\n.param MC_MM_SWITCH=0 MC_PR_SWITCH=1\n.include continuous/parameters_fet_tt.spice\n.include continuous/models_fet.spice\n.endl mc\n')

    def helper(self):
        self.assertIsNotNone(importlib.util.find_spec('analog_factory.statistical_models'),
                             'Explicit experimental model snapshot helper is required')
        from analog_factory.statistical_models import prepare_global_models
        return prepare_global_models

    def test_copy_is_deterministic_preserves_switches_and_local_mismatch_and_originals(self):
        prepare = self.helper()
        original = {str(path): path.read_bytes() for path in self.combined.rglob('*.spice')}
        bundle = prepare(self.library, self.root)
        self.assertEqual(bundle, prepare(self.library, self.root))
        self.assertEqual({str(path): path.read_bytes() for path in self.combined.rglob('*.spice')}, original)
        files = bundle['generated_files']
        model_text = next(text for name, text in files.items() if '.subckt n' in text)
        self.assertEqual(model_text, self.models.read_text())
        parameter_text = next(text for text in files.values() if 'sw_polycd' in text)
        self.assertIn('af_cached_agauss(0,1.5e-9,1)', parameter_text)
        self.assertIn('.param fixed = 7', parameter_text)
        self.assertIn('MC_PR_SWITCH', parameter_text)
        self.assertIn('MC_MM_SWITCH=0 MC_PR_SWITCH=1', files[bundle['library']])
        self.assertEqual(bundle['identity']['source_dependencies'], {
            path: hashlib.sha256(data).hexdigest() for path, data in original.items()})
        self.assertEqual(bundle['identity']['generated_hashes'], {
            name: hashlib.sha256(text.encode()).hexdigest() for name, text in files.items()})

    def test_unsafe_dependencies_dynamic_paths_and_subcircuit_parameters_are_rejected(self):
        prepare = self.helper()
        for include in ('/etc/passwd', '$PDK/foo.spice', '`bad`', '../missing.spice'):
            with self.subTest(include=include):
                self.library.write_text(f'.lib mc\n.include "{include}"\n.endl mc\n')
                with self.assertRaises((ValueError, OSError)):
                    prepare(self.library, self.root)
        self.library.write_text('.lib mc\n.include continuous/parameters_fet_tt.spice\n.endl mc\n')
        self.parameters.write_text('.subckt n a b\n.param x=agauss(0,1,1)\n.ends\n')
        with self.assertRaises(ValueError):
            prepare(self.library, self.root)

    def test_grouping_and_closure_changes_bind_the_adapter_identity(self):
        prepare = self.helper()
        first = prepare(self.library, self.root)
        self.parameters.write_text('.param x={1+2}*3 + agauss(0,1,1)\n')
        second = prepare(self.library, self.root)
        transformed = next(text for text in second['generated_files'].values() if 'af_cached_agauss(0,1,1)' in text)
        self.assertIn('(1+2)*3', transformed)
        self.assertNotEqual(first['identity'], second['identity'])
        with self.assertRaises(ValueError):
            prepare(self.library, self.root, maximum_bytes=10)

    def test_symlinks_control_blocks_and_nontext_models_are_rejected(self):
        prepare = self.helper()
        original = self.models.read_bytes()
        for unsafe in (b'.control\nshell bad\n.endc\n', b'\xff\x00\x80'):
            self.models.write_bytes(unsafe)
            with self.assertRaises(ValueError):
                prepare(self.library, self.root)
        self.models.write_bytes(original)
        target = self.models.with_name('original-models.spice')
        self.models.rename(target)
        self.models.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'symbolic'):
            prepare(self.library, self.root)

    def test_mismatch_draws_in_the_global_parameter_file_are_never_rewritten(self):
        prepare = self.helper()
        for expression in ('MC_MM_SWITCH*agauss(0,1,1)', 'mismatch_factor*agauss(0,1,1)'):
            self.parameters.write_text('.param sw_polycd='+expression+'\n')
            with self.assertRaisesRegex(ValueError,'mismatch'):
                prepare(self.library,self.root)

    def test_cyclic_include_closure_stays_bounded_and_canonical(self):
        prepare = self.helper()
        self.models.write_text('.include ../sky130.lib.spice\n')
        bundle=prepare(self.library,self.root)
        self.assertEqual(len(bundle['generated_files']),3)
        self.assertEqual(len(bundle['identity']['source_dependencies']),3)
        self.assertTrue(all(Path(path).is_relative_to(self.root) for path in bundle['identity']['source_dependencies']))
        # A literal parent traversal inside the PDK remains a declared bounded
        # dependency, as required by SKY130's legacy libs.ref includes.
        extra=self.combined/'extra.spice';extra.write_text('* public legacy fixture\n')
        self.models.write_text('.include ../extra.spice\n')
        self.assertIn(str(extra),prepare(self.library,self.root)['identity']['source_dependencies'])

    def test_explicit_schema_semantics_defaults_to_installed(self):
        from analog_factory.operation_schema import validate
        from analog_factory.simulation_batches import QUALIFICATION_SCHEMA, STATISTICS
        explicit = {'library': str(self.library), 'kind': 'global_process', 'section': 'mc',
                    'model_semantics': 'cache_global_fet_draws'}
        normalized = validate(explicit, QUALIFICATION_SCHEMA)
        self.assertEqual(normalized['model_semantics'], explicit['model_semantics'])
        self.assertEqual(validate({}, STATISTICS)['model_semantics'], 'installed')
        explicit.pop('model_semantics')
        self.assertEqual(validate(explicit, QUALIFICATION_SCHEMA)['model_semantics'], 'installed')


if __name__ == '__main__':
    unittest.main()
