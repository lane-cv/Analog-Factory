from pathlib import Path
import shutil
import tempfile
import unittest

from analog_factory import eda, toolchain
from analog_factory.execution import RunManager
from analog_factory.waveforms import parse_raw


class EDATests(unittest.TestCase):
    def test_missing_tool_and_pdk_are_explained(self):
        result = toolchain.detect({'tools': {'ngspice': '/missing/ngspice'}, 'pdk_root': '/missing/pdk'})
        self.assertFalse(result['tools']['ngspice']['available'])
        self.assertTrue(result['tools']['ngspice']['diagnostic'])
        self.assertFalse(result['pdk']['available'])
        self.assertTrue(result['pdk']['diagnostic'])

    def test_mesh_generator_is_not_recognized_as_lvs_netgen(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'netgen'
            exe.write_text('#!/bin/sh\necho "NETGEN mesh generator 6.2"\n')
            exe.chmod(0o755)
            result = toolchain.detect({'tools': {'netgen': str(exe)}})
            self.assertFalse(result['tools']['netgen']['available'])
            self.assertIn('LVS', result['tools']['netgen']['diagnostic'])

    def test_invalid_and_escaped_netlist_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            for path in ['../outside.cir', '/etc/passwd']:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    eda.prepare(project, 'simulate', {'netlist': path}, {})

    @unittest.skipUnless(shutil.which('ngspice'), 'ngspice is not installed')
    def test_real_ngspice_simulation_measurements_and_waveforms(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / 'testbenches').mkdir()
            (project / 'testbenches/pre.cir').write_text('RC integration\nV1 in 0 1\nR1 in out 1k\nC1 out 0 1u\n.tran 10u 2m\n.meas tran voltage AVG v(out) FROM=1m TO=2m\n.end\n')
            prepared = eda.prepare(project, 'simulate', {}, {})
            manager = RunManager()
            started = manager.start(project, 'simulate', **prepared)
            run = manager.wait(project, started['id'], timeout=15)
            self.assertEqual(run['status'], 'succeeded', run)
            self.assertAlmostEqual(run['metrics']['voltage'], 1.0, places=4)
            raw = parse_raw(project / '.af/runs' / run['id'] / 'output/waveforms.raw')
            self.assertGreater(len(raw['points']), 20)
            self.assertIn('v(out)', [v['name'] for v in raw['variables']])

    @unittest.skipUnless(shutil.which('ngspice'), 'ngspice is not installed')
    def test_netlist_names_with_spaces_and_tuning_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / 'deck with spaces.cir').write_text('Voltage test\n.param voltage=1\nV1 out 0 {voltage}\nR1 out 0 1k\n.tran 1u 10u\n.meas tran result AVG v(out)\n.end\n')
            prepared = eda.prepare(project, 'simulate', {'netlist': 'deck with spaces.cir'}, {})
            prepared['metadata']['generated_files']['deck with spaces.cir'] = (project / 'deck with spaces.cir').read_text().replace('voltage=1', 'voltage=2')
            manager = RunManager()
            run = manager.start(project, 'simulate', **prepared)
            result = manager.wait(project, run['id'], timeout=10)
            self.assertEqual(result['status'], 'succeeded', result)
            self.assertAlmostEqual(result['metrics']['result'], 2.0)

    @unittest.skipUnless(shutil.which('ngspice'), 'ngspice is not installed')
    def test_external_model_hash_staleness_and_retry_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'project'
            project.mkdir()
            model = root / 'resistor.inc'
            model.write_text('R1 out 0 1k\n')
            (project / 'deck.cir').write_text(f'External dependency\n.include "{model}"\nV1 out 0 1\n.tran 1u 10u\n.meas tran result AVG i(V1)\n.end\n')
            manager = RunManager()
            run = manager.start(project, 'simulate', **eda.prepare(project, 'simulate', {'netlist': 'deck.cir'}, {}))
            result = manager.wait(project, run['id'], timeout=10)
            self.assertEqual(result['status'], 'succeeded', result)
            self.assertIn(str(model), result['external_dependencies'])
            self.assertFalse(result['provenance']['complete'])
            self.assertFalse(result['stale'])
            model.write_text('R1 out 0 2k\n')
            self.assertTrue(manager.get(project, run['id'])['stale'])
            retry = manager.retry(project, run['id'])
            fresh = manager.wait(project, retry['id'], timeout=10)
            self.assertFalse(fresh['stale'])
            self.assertNotEqual(result['external_dependencies'][str(model)], fresh['external_dependencies'][str(model)])
            self.assertAlmostEqual(fresh['metrics']['result'], -0.0005)

    def test_dependency_closure_distinguishes_lib_sections_and_dynamic_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'project'
            project.mkdir()
            (root / 'model.lib').write_text('.lib tt\n.include "resistor.inc"\n.endl tt\n')
            (root / 'resistor.inc').write_text('R1 out 0 1k\n')
            (project / 'deck.cir').write_text(f'Test\n.lib "{root / "model.lib"}" tt\n.include "$UNKNOWN/file"\n.end\n')
            dependencies = eda.input_dependencies(project, 'deck.cir', {})
            self.assertEqual(set(dependencies['external_dependencies']), {str(root / 'model.lib'), str(root / 'resistor.inc')})
            self.assertFalse(dependencies['provenance']['complete'])
            self.assertTrue(any('dynamic' in item.lower() for item in dependencies['provenance']['limitations']))


if __name__ == '__main__':
    unittest.main()
