"""Owned browser harness boundaries, without launching the browser suite."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

try:
    from scripts import validate_browser as driver
except ImportError:
    driver = None

ROOT = Path(__file__).resolve().parents[1]


class BrowserValidationTests(unittest.TestCase):
    def require_driver(self):
        self.assertIsNotNone(driver, 'Missing owned browser validation driver')

    def test_exited_child_never_accepts_old_ready_file(self):
        self.require_driver()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            process = subprocess.Popen([sys.executable, '-c', 'pass'])
            process.wait(timeout=5)
            ready = path/'ready.json'
            ready.write_text(json.dumps({'pid':process.pid,'port':8920,
                'source':str(ROOT),'workspace':str(path/'workspace')}))
            with self.assertRaisesRegex(RuntimeError, 'exited'):
                driver.wait_ready(process,ready,ROOT,path/'workspace',timeout=.1)

    def test_ready_file_with_another_pid_is_rejected(self):
        self.require_driver()
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)
            process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
            try:
                ready=path/'ready.json';ready.write_text(json.dumps({'pid':process.pid+1,'port':8920,
                    'source':str(ROOT),'workspace':str(path/'workspace')}))
                with self.assertRaisesRegex(RuntimeError,'identity'):
                    driver.wait_ready(process,ready,ROOT,path/'workspace',timeout=.1)
            finally:
                driver.stop_owned(process)

    def test_port_zero_server_publishes_owned_identity_and_exclusive_ready_file(self):
        self.require_driver()
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp);ready=path/'ready.json';workspace=path/'workspace'
            process=subprocess.Popen([sys.executable,str(ROOT/'tests/operation_test_server.py'),
                '--workspace',str(workspace),'--port','0','--ready-file',str(ready)],
                cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            try:
                data=driver.wait_ready(process,ready,ROOT,workspace,timeout=15)
                self.assertEqual(data['pid'],process.pid)
                self.assertGreater(data['port'],0)
                before=ready.read_bytes()
                duplicate=subprocess.run([sys.executable,str(ROOT/'tests/operation_test_server.py'),
                    '--workspace',str(path/'other'),'--port','0','--ready-file',str(ready)],
                    cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
                self.assertNotEqual(duplicate.returncode,0)
                self.assertEqual(ready.read_bytes(),before,'another launch cannot replace the ownership receipt')
            finally:
                driver.stop_owned(process)
            self.assertIsNotNone(process.poll())

    def test_failed_cli_run_keeps_evidence_and_does_not_stop_unrelated_process(self):
        self.require_driver()
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp);output=path/'fresh'
            unrelated=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
            try:
                # Python deliberately cannot execute this JavaScript file; the
                # lightweight test verifies a real failing child, not a browser PASS.
                result=driver.main(['--source',str(ROOT),'--output',str(output),
                    '--test','operations','--node',sys.executable,'--timeout','5'])
                evidence=json.loads((output/'evidence.json').read_text())
                self.assertEqual(result,1)
                self.assertEqual(evidence['status'],'failed')
                self.assertFalse(evidence['full_default_suite'])
                self.assertEqual(evidence['results'][0]['status'],'failed')
                self.assertIsInstance(evidence['server']['cleanup_exit_code'],int)
                self.assertEqual(evidence['server']['pid'],evidence['server']['ready']['pid'])
                self.assertTrue((output/'operations.log').read_text())
                self.assertIsNone(unrelated.poll(),'cleanup only touches owned process groups')
                with self.assertRaises(SystemExit):driver.main(['--output',str(output)])
            finally:
                driver.stop_owned(unrelated)

    def test_snapshot_omits_private_state_and_is_independent_of_later_edits(self):
        self.require_driver()
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp);source=path/'checkout';source.mkdir()
            for name in ['analog_factory','tests','scripts','.workspace','.validation']:(source/name).mkdir()
            (source/'analog_factory/public.py').write_text('public=1\n')
            (source/'.workspace/private.txt').write_text('never copy')
            (source/'tests/.env').write_text('never copy')
            manifest=driver.copy_source(source,path/'snapshot')
            (source/'analog_factory/public.py').write_text('public=2\n')
            self.assertEqual((path/'snapshot/analog_factory/public.py').read_text(),'public=1\n')
            self.assertFalse((path/'snapshot/.workspace').exists())
            self.assertFalse((path/'snapshot/tests/.env').exists())
            self.assertEqual(set(manifest),{'analog_factory/public.py'})

    def test_failed_and_timed_out_steps_keep_logs_and_terminal_results(self):
        self.require_driver()
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)
            failed=driver.run_step('failure',[sys.executable,'-c','print("retained output"); raise SystemExit(7)'],
                path,dict(),path/'failure.log',timeout=5)
            self.assertEqual((failed['status'],failed['exit_code']),('failed',7))
            self.assertIn('retained output',(path/'failure.log').read_text())
            timed=driver.run_step('timeout',[sys.executable,'-c','import time; time.sleep(30)'],
                path,dict(),path/'timeout.log',timeout=.05)
            self.assertEqual((timed['status'],timed['exit_code']),('timeout',124))


if __name__=='__main__':unittest.main()
