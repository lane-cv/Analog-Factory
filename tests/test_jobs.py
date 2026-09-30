import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from analog_factory.jobs import Jobs


class JobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        self.jobs = Jobs()

    def test_another_process_can_inspect_and_cancel_a_live_job(self):
        finished = threading.Event()

        def work(record, cancel, update):
            try:
                while not cancel.is_set():
                    time.sleep(.01)
                update(observed_cancel=True)
            finally:
                finished.set()

        job = self.jobs.start(self.project, 'test', work)
        try:
            code = ('import sys; from pathlib import Path; from analog_factory.jobs import Jobs; '
                    'm=Jobs(); p=Path(sys.argv[1]); print(m.get(p,sys.argv[2])["status"]); m.cancel(p,sys.argv[2])')
            observer = subprocess.run([sys.executable, '-c', code, str(self.project), job['id']],
                                      capture_output=True, text=True, timeout=5)
            self.assertEqual(observer.returncode, 0, observer.stderr)
            self.assertIn(observer.stdout.strip(), {'queued', 'running'})
            self.assertTrue(finished.wait(5), 'Cancellation did not reach the callback')
            result = self.jobs.wait(self.project, job['id'], timeout=5)
            self.assertEqual(result['status'], 'cancelled')
            self.assertTrue(result['observed_cancel'])
        finally:
            self.jobs.cancel(self.project, job['id'])
            finished.wait(5)
            self.jobs.wait(self.project, job['id'], timeout=5)

    def test_abandoned_job_is_interrupted_after_owner_exit(self):
        code = ('import sys,time,os; from pathlib import Path; from analog_factory.jobs import Jobs; '
                'r=Jobs().start(Path(sys.argv[1]),"test",lambda record,cancel,update: time.sleep(30)); '
                'print(r["id"],flush=True); os._exit(0)')
        process = subprocess.run([sys.executable, '-c', code, str(self.project)],
                                 capture_output=True, text=True, timeout=5)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = self.jobs.get(self.project, process.stdout.strip())
        self.assertEqual(result['status'], 'interrupted')
        self.assertIsNotNone(result.get('ended_at'))

    def test_another_manager_does_not_interrupt_live_job(self):
        release = threading.Event()
        job = self.jobs.start(self.project, 'test', lambda record, cancel, update: release.wait(5) and {})
        try:
            self.assertIn(Jobs().get(self.project, job['id'])['status'], {'queued', 'running'})
            self.assertIn(Jobs().list(self.project)[0]['status'], {'queued', 'running'})
        finally:
            release.set()
            self.jobs.wait(self.project, job['id'], timeout=5)

    def test_recycled_owner_pid_is_not_treated_as_a_live_worker(self):
        job_id = 'a' * 32
        path = self.project / '.af/jobs' / f'{job_id}.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'id': job_id, 'status': 'running', 'owner_pid': os.getpid(),
                                    'owner_identity': 'different process birth'}))
        self.assertEqual(self.jobs.get(self.project, job_id)['status'], 'interrupted')

    def test_job_storage_rejects_symlinks_and_path_escape(self):
        with tempfile.TemporaryDirectory() as outside:
            (self.project / '.af').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                self.jobs.start(self.project, 'test', lambda *args: {})
            with self.assertRaises(ValueError):
                self.jobs.list(self.project)
            self.assertFalse(list(Path(outside).iterdir()))
        with self.assertRaises(ValueError):
            self.jobs.get(self.project, '../outside')

    def test_job_manifest_symlinks_cannot_be_read_or_cancelled(self):
        job_id = 'b' * 32
        root = self.project / '.af/jobs'
        root.mkdir(parents=True)
        target = self.project / 'unrelated.json'
        target.write_text('{"status":"running"}')
        (root / f'{job_id}.json').symlink_to(target)
        with self.assertRaises(ValueError):
            self.jobs.get(self.project, job_id)
        with self.assertRaises(ValueError):
            self.jobs.cancel(self.project, job_id)

    def test_initial_data_cannot_override_job_identity_or_owner(self):
        job = self.jobs.start(self.project, 'test', lambda *args: {},
                              initial={'id': 'forged', 'owner_pid': -1, 'status': 'invented'})
        self.assertNotEqual(job['id'], 'forged')
        self.assertEqual(job['owner_pid'], os.getpid())
        self.assertEqual(self.jobs.wait(self.project, job['id'])['status'], 'succeeded')

    def test_cancellation_remains_cancelled_when_callback_raises(self):
        def work(record, cancel, update):
            while not cancel.is_set():
                time.sleep(.01)
            raise RuntimeError('Work interrupted during cancellation')

        job = self.jobs.start(self.project, 'test', work)
        Jobs().cancel(self.project, job['id'])
        result = self.jobs.wait(self.project, job['id'], timeout=5)
        self.assertEqual(result['status'], 'cancelled')
        self.assertIsNotNone(result['ended_at'])


if __name__ == '__main__':
    unittest.main()
