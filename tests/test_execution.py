import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time
import unittest

from analog_factory.execution import RunManager


# Netgen 1.5.133's real SKY130 report has no "Final result:" prefix. The
# topology match precedes pin matching and may still have property errors.
LEGACY_SKY130_MATCH = '''Subcircuit summary:
Circuit 1: sky130_nfet_w1_l1 |Circuit 2: sky130_nfet_w1_l1
sky130_fd_pr__nfet_01v8 (1) |sky130_fd_pr__nfet_01v8 (1)
Netlists match uniquely.
Circuits match correctly.

Subcircuit pins:
Circuit 1: sky130_nfet_w1_l1 |Circuit 2: sky130_nfet_w1_l1
G |G
D |D
S |S
B |B
Cell pin lists are equivalent.
Device classes sky130_nfet_w1_l1 and sky130_nfet_w1_l1 are equivalent.
Circuits match uniquely.
'''
LEGACY_SKY130_COMPLETION = 'Result: Circuits match uniquely.\nLVS Done.\n'


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        (self.project / 'source.txt').write_text('first')
        self.manager = RunManager()

    def run_code(self, code, kind='simulate', **kwargs):
        run = self.manager.start(self.project, kind, [sys.executable, '-c', code], **kwargs)
        return self.manager.wait(self.project, run['id'], timeout=10)

    def test_snapshot_hashes_staleness_and_retry(self):
        result = self.run_code("from pathlib import Path; Path('../output/result.txt').write_text(Path('source.txt').read_text())")
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['verification']['status'], 'unknown')
        self.assertEqual(result['input_hashes']['source.txt'], hashlib.sha256(b'first').hexdigest())
        self.assertIn('result.txt', result['artifact_hashes'])
        self.assertFalse(result['stale'])
        (self.project / 'source.txt').write_text('second')
        self.assertTrue(self.manager.get(self.project, result['id'])['stale'])
        retry = self.manager.retry(self.project, result['id'])
        retry = self.manager.wait(self.project, retry['id'], timeout=10)
        self.assertEqual(retry['parent_id'], result['id'])
        self.assertNotEqual(retry['id'], result['id'])
        self.assertEqual((self.project / '.af/runs' / retry['id'] / 'output/result.txt').read_text(), 'second')
        self.assertEqual((self.project / '.af/runs' / result['id'] / 'output/result.txt').read_text(), 'first')
        self.assertEqual(len(self.manager.list(self.project)), 2)

    def test_timeout_terminates_the_process_group(self):
        result = self.run_code("import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', \"import time; from pathlib import Path; time.sleep(1); Path('../output/escaped').write_text('bad')\"]); time.sleep(5)", timeout=0.15)
        self.assertEqual(result['status'], 'timed_out')
        time.sleep(1.1)
        self.assertFalse((self.project / '.af/runs' / result['id'] / 'output/escaped').exists())

    def test_cancel_is_terminal_and_retry_is_fresh(self):
        run = self.manager.start(self.project, 'simulate', [sys.executable, '-c', 'import time; time.sleep(10)'])
        self.manager.cancel(self.project, run['id'])
        result = self.manager.wait(self.project, run['id'], timeout=5)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(self.manager.cancel(self.project, run['id'])['status'], 'cancelled')

    def test_zero_exit_ngspice_error_is_failure(self):
        result = self.run_code("print('Error: unknown subckt: missing')")
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['status'], 'failed')

    def test_measurements_and_definition_provenance(self):
        (self.project / 'test.cir').write_text('test\n.meas tran gain PARAM=2\n.end\n')
        result = self.run_code("print('gain = -2.0000e+00')")
        self.assertEqual(result['metrics']['gain'], -2.0)
        self.assertEqual(result['metric_provenance']['gain']['definition']['path'], 'test.cir')
        self.assertEqual(result['metric_provenance']['gain']['source'], 'process.log')

    def test_drc_zero_exit_without_report_is_unknown(self):
        result = self.run_code("print('done')", kind='drc')
        self.assertEqual(result['verification']['status'], 'unknown')

    def test_drc_report_zero_and_nonzero(self):
        for count, wanted in [(0, 'pass'), (4, 'fail')]:
            result = self.run_code(f"from pathlib import Path; Path('../output/drc.txt').write_text('AF_DRC_COUNT={count}\\nAF_DRC_COMPLETE=1\\n')", kind='drc')
            self.assertEqual(result['verification']['status'], wanted)
            self.assertEqual(result['verification']['error_count'], count)

    def test_symlinks_hidden_files_and_generated_path_escape(self):
        (self.project / '.secret').write_text('never copy')
        (self.project / 'link').symlink_to('/etc/passwd')
        result = self.run_code("pass")
        snapshot = self.project / '.af/runs' / result['id'] / 'input'
        self.assertFalse((snapshot / '.secret').exists())
        self.assertFalse((snapshot / 'link').exists())
        with self.assertRaises(ValueError):
            self.run_code('pass', metadata={'generated_files': {'../bad': 'x'}})
        with self.assertRaises(ValueError):
            self.manager.get(self.project, '../../source.txt')

    def test_missing_executable_and_interrupted_manifest(self):
        result = self.manager.start(self.project, 'simulate', ['/does/not/exist'])
        result = self.manager.wait(self.project, result['id'], timeout=5)
        self.assertEqual(result['status'], 'unavailable')
        path = self.project / '.af/runs' / result['id'] / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest.update(status='running', owner_pid=-1)
        path.write_text(json.dumps(manifest))
        recovered = RunManager().get(self.project, result['id'])
        self.assertEqual(recovered['status'], 'failed')
        self.assertIn('interrupt', recovered['error'].lower())

    def test_secret_environment_not_recorded(self):
        result = self.run_code('pass', env={'MY_API_KEY': 'sensitive', 'SPICE_ASCIIRAWFILE': '1'})
        self.assertNotIn('MY_API_KEY', result['environment'])
        self.assertEqual(result['environment']['SPICE_ASCIIRAWFILE'], '1')

    def test_lvs_partial_match_is_not_a_pass(self):
        reports = [('Netlists match uniquely.\n', 'unknown'),
                   ('Netlists match uniquely.\nFinal result: Top level cell failed pin matching.\n', 'fail'),
                   ('Final result: Circuits match uniquely.\nProperty errors were found.\n', 'fail'),
                   ('Final result: Circuits match uniquely.\n', 'pass')]
        for report, status in reports:
            with self.subTest(report=report):
                result = self.run_code(f"from pathlib import Path; Path('../output/lvs.txt').write_text({report!r})", kind='lvs')
                self.assertEqual(result['verification']['status'], status)

    def test_lvs_legacy_sky130_completed_match_is_pass(self):
        result = self.run_code(
            f"from pathlib import Path; Path('../output/lvs.txt').write_text({LEGACY_SKY130_MATCH!r}); "
            f"print({LEGACY_SKY130_COMPLETION!r})", kind='lvs')
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['verification']['status'], 'pass')
        self.assertEqual(result['verification']['reports'], ['lvs.txt'])

    def test_lvs_legacy_sky130_incomplete_match_is_not_pass(self):
        cases = [
            ('Netlists match uniquely.\nCircuits match correctly.\n', LEGACY_SKY130_COMPLETION, 0),
            (LEGACY_SKY130_MATCH, 'Result: Circuits match uniquely.\n', 0),
            (LEGACY_SKY130_MATCH, 'LVS Done.\n', 0),
            (LEGACY_SKY130_MATCH, LEGACY_SKY130_COMPLETION, 1),
        ]
        for report, log, exit_code in cases:
            with self.subTest(report=report, log=log, exit_code=exit_code):
                result = self.run_code(
                    f"from pathlib import Path; Path('../output/lvs.txt').write_text({report!r}); "
                    f"print({log!r}); raise SystemExit({exit_code})", kind='lvs')
                self.assertEqual(result['verification']['status'], 'unknown')

    def test_lvs_legacy_sky130_width_errors_override_topology_match(self):
        mismatch = ('There were property errors.\n'
                    'sky130_fd_pr__nfet_01v80 vs. sky130_fd_pr__nfet_01v8ref:\n'
                    ' w circuit1: 1   circuit2: 2   (delta=66.7%, cutoff=1%)\n')
        reports = [LEGACY_SKY130_MATCH.replace('Subcircuit pins:', mismatch + 'Subcircuit pins:'),
                   LEGACY_SKY130_MATCH + 'Property errors were found.\n']
        for report in reports:
            with self.subTest(report=report):
                result = self.run_code(
                    f"from pathlib import Path; Path('../output/lvs.txt').write_text({report!r}); "
                    f"print({LEGACY_SKY130_COMPLETION!r})", kind='lvs')
                self.assertEqual(result['status'], 'succeeded')
                self.assertEqual(result['verification']['status'], 'fail')

    def test_lvs_legacy_hierarchy_requires_top_level_pin_equivalence(self):
        child = LEGACY_SKY130_MATCH.replace('sky130_nfet_w1_l1', 'child_nfet')
        top = '''Subcircuit summary:
Circuit 1: top |Circuit 2: top
child_nfet (1) |child_nfet (1)
Netlists match uniquely.
Circuits match correctly.

Subcircuit pins:
Circuit 1: top |Circuit 2: top
IN |IN
OUT |OUT
'''
        cases = [
            (top + 'Circuits match uniquely.\n', 'unknown'),
            (top.split('Subcircuit pins:')[0] + 'Circuits match uniquely.\n', 'unknown'),
            (top + 'Cell pin lists are equivalent.\nCircuits match uniquely.\n', 'pass'),
        ]
        for last_circuit, wanted in cases:
            with self.subTest(last_circuit=last_circuit):
                report = child + '\n' + last_circuit
                result = self.run_code(
                    f"from pathlib import Path; Path('../output/lvs.txt').write_text({report!r}); "
                    f"print({LEGACY_SKY130_COMPLETION!r})", kind='lvs')
                self.assertEqual(result['verification']['status'], wanted)

    def test_lvs_hierarchical_pin_failures_override_unique_verdict(self):
        child = LEGACY_SKY130_MATCH.replace('sky130_nfet_w1_l1', 'child_nfet')
        for error in ('No matching pin in cell top for pin UNUSED',
                      'No netlist match for cell top'):
            for verdict in ('Circuits match uniquely.', 'Final result: Circuits match uniquely.'):
                with self.subTest(error=error, verdict=verdict):
                    report = (child + '\nSubcircuit summary:\nCircuit 1: top |Circuit 2: top\n'
                              'Netlists match uniquely.\nCircuits match correctly.\n'
                              'Subcircuit pins:\nCircuit 1: top |Circuit 2: top\n' + error + '\n' + verdict + '\n')
                    result = self.run_code(
                        f"from pathlib import Path; Path('../output/lvs.txt').write_text({report!r}); "
                        f"print({LEGACY_SKY130_COMPLETION!r})", kind='lvs')
                    self.assertEqual(result['verification']['status'], 'fail')

    def test_generated_override_keeps_source_and_effective_hashes(self):
        result = self.run_code("from pathlib import Path; print(Path('source.txt').read_text())",
                               metadata={'generated_files': {'source.txt': 'override'}})
        self.assertNotEqual(result['source_hashes']['source.txt'], result['input_hashes']['source.txt'])
        self.assertFalse(result['stale'])
        self.assertEqual((self.project / 'source.txt').read_text(), 'first')

    def test_relative_executable_is_snapshotted_and_escape_rejected(self):
        (self.project / 'run.sh').write_text('#!/bin/sh\necho local\n')
        (self.project / 'run.sh').chmod(0o700)
        run = self.manager.start(self.project, 'test', ['./run.sh'])
        result = self.manager.wait(self.project, run['id'], timeout=5)
        self.assertEqual(result['status'], 'succeeded', result)
        with self.assertRaises(ValueError):
            self.manager.start(self.project, 'test', ['../../outside.sh'])

    def test_missing_measurements_are_not_a_successful_simulation(self):
        result = self.run_code("print('No .measure possible in batch mode (-b) with -r rawfile set!')")
        self.assertEqual(result['status'], 'failed')

    def test_aliases_copy_effective_generated_inputs(self):
        result = self.run_code("from pathlib import Path; print(Path('aliases/deck.cir').read_text())",
                               metadata={'generated_files': {'source.txt': 'tuned'},
                                         'input_aliases': {'aliases/deck.cir': 'source.txt'}})
        path = self.project / '.af/runs' / result['id'] / 'input/aliases/deck.cir'
        self.assertEqual(path.read_text(), 'tuned')
        with self.assertRaises(ValueError):
            self.run_code('pass', metadata={'input_aliases': {'alias': '../outside'}})

    def test_other_process_can_inspect_and_cancel_active_run(self):
        run = self.manager.start(self.project, 'test', [sys.executable, '-c', 'import time; time.sleep(10)'])
        code = ('import sys; from pathlib import Path; from analog_factory.execution import RunManager; '
                'm=RunManager(); p=Path(sys.argv[1]); r=m.get(p,sys.argv[2]); '
                'print(r["status"]); m.cancel(p,sys.argv[2])')
        observer = subprocess.run([sys.executable, '-c', code, str(self.project), run['id']], capture_output=True, text=True, timeout=5)
        try:
            self.assertEqual(observer.returncode, 0, observer.stderr)
            self.assertIn(observer.stdout.strip(), {'running', 'queued'})
            result = self.manager.wait(self.project, run['id'], timeout=5)
            self.assertEqual(result['status'], 'cancelled')
        finally:
            self.manager.cancel(self.project, run['id'])
            self.manager.wait(self.project, run['id'], timeout=5)


if __name__ == '__main__':
    unittest.main()
