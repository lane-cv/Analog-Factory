"""Resume never promotes imported/incomplete/mismatched evidence to authority."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch


class ResumeTests(unittest.TestCase):
    def test_imported_and_active_jobs_are_rejected_before_files_are_read(self):
        from analog_factory.training.resume import prepare
        source = {'kind': 'training_evaluation', 'status': 'failed', 'owner_identity': 'local'}
        for fields in ({'imported_evidence': True}, {'owner_identity': None}, {'status': 'running'}, {'kind': 'training'}):
            with self.subTest(fields=fields), patch('analog_factory.training.manager.get', return_value={**source, **fields}):
                with self.assertRaises(ValueError):
                    prepare(None, 'a'*32, 'b'*32, 'c'*32, {})

    def test_seed_taskset_and_checkpoint_cannot_change_during_resume(self):
        from analog_factory.training.resume import prepare
        from analog_factory.training.manager import DEFAULTS
        from analog_factory.storage import atomic_json
        source = {'kind': 'training_evaluation', 'status': 'failed', 'owner_identity': 'local', 'parent_id': 'c'*32}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            atomic_json(root/'request.json', {'checkpoint_job_id': 'c'*32, 'options': DEFAULTS})
            with patch('analog_factory.training.manager.get', return_value=source), patch('analog_factory.training.manager.directory', return_value=root):
                for checkpoint, settings in (('d'*32, {}), ('c'*32, {'seed': 123}),
                    ('c'*32, {'eval_tasks': 32}), ('c'*32, {'taskset_id': 'taskset-'+'1'*24})):
                    with self.subTest(settings=settings), self.assertRaises(ValueError):
                        prepare(None, 'a'*32, 'b'*32, checkpoint, settings)


if __name__ == '__main__':
    unittest.main()
