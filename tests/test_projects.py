import tempfile
import unittest
from pathlib import Path

from analog_factory.projects import ProjectStore


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProjectStore(Path(self.tmp.name))
        self.project = self.store.create('Bias amplifier', 'amplifier')
        self.pid = self.project['id']

    def test_template_is_real_editable_circuit(self):
        self.assertIn('.meas tran vout_mean', self.store.read(self.pid, 'testbenches/pre.cir')['content'])
        self.assertIn('netlists/amplifier.cir', [f['path'] for f in self.store.files(self.pid)])

    def test_paths_and_symlinks_cannot_escape_project(self):
        for path in ('../escape', '/etc/passwd', '.af/runs/fake/manifest.json', 'a/../../escape'):
            with self.assertRaises(ValueError):
                self.store.write(self.pid, path, 'bad')
        root = self.store.path(self.pid)
        (root / 'outside').symlink_to('/tmp', target_is_directory=True)
        with self.assertRaises(ValueError):
            self.store.write(self.pid, 'outside/escape', 'bad')

    def test_revision_restore_preserves_old_contents(self):
        original = self.store.read(self.pid, 'testbenches/pre.cir')['content']
        self.store.write(self.pid, 'testbenches/pre.cir', '* changed\n.end\n')
        revision = self.store.history(self.pid, 'testbenches/pre.cir')[0]['id']
        self.store.restore(self.pid, 'testbenches/pre.cir', revision)
        self.assertEqual(self.store.read(self.pid, 'testbenches/pre.cir')['content'], original)

    def test_export_excludes_internal_history_and_symlinks(self):
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(self.store.export(self.pid))) as archive:
            self.assertIn('project.json', archive.namelist())
            self.assertFalse(any('.af/history' in f for f in archive.namelist()))

    def test_unknown_template_and_invalid_names_rejected(self):
        for name, template in [('', 'amplifier'), ('Test', 'imaginary')]:
            with self.assertRaises(ValueError):
                self.store.create(name, template)

    def test_project_metadata_alias_is_protected(self):
        with self.assertRaises(ValueError):
            self.store.write(self.pid, './project.json', '{}')


if __name__ == '__main__':
    unittest.main()
