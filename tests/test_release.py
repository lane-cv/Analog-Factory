"""Release gates verify shipped bytes and reject unsafe archive contents."""
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
import warnings
import zipfile

from scripts.validate_release import inspect_archive, stage_source


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.expected = {'analog_factory/__init__.py': hashlib.sha256(b'core').hexdigest(),
                         'analog_factory/static/app.js': hashlib.sha256(b'ui').hexdigest()}

    def wheel(self, extras=(), *, omit=None, change=False):
        path = self.root/'test.whl'
        with zipfile.ZipFile(path, 'w') as archive:
            for name, content in [('analog_factory/__init__.py', b'core'), ('analog_factory/static/app.js', b'ui')]:
                if name != omit:
                    archive.writestr(name, b'changed' if change and name.endswith('.js') else content)
            for name, content in extras:
                with warnings.catch_warnings():
                    warnings.filterwarnings('ignore', message='Duplicate name:', category=UserWarning)
                    archive.writestr(name, content)
        return path

    def test_complete_wheel_preserves_runtime_bytes(self):
        result = inspect_archive(self.wheel(), self.expected)
        self.assertEqual(result['verified_runtime_files'], 2)
        self.assertEqual(result['sha256'], hashlib.sha256((self.root/'test.whl').read_bytes()).hexdigest())

    def test_missing_or_changed_runtime_asset_fails(self):
        with self.assertRaisesRegex(ValueError, 'Missing'):
            inspect_archive(self.wheel(omit='analog_factory/static/app.js'), self.expected)
        with self.assertRaisesRegex(ValueError, 'changed'):
            inspect_archive(self.wheel(change=True), self.expected)

    def test_secret_traversal_and_duplicate_archive_names_fail(self):
        for name in ['.workspace/settings.json', 'analog_factory/.env', '../outside',
                     'analog_factory/.AWS/config', 'analog_factory/.ENV.local',
                     'analog_factory/PRIVATE.KEY', 'analog_factory/Settings.JSON',
                     '/absolute', 'analog_factory/../outside', 'analog_factory\\outside',
                     'analog_factory/__init__.py']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                inspect_archive(self.wheel([(name, b'bad')]), self.expected)

    def test_zip_symlink_is_not_a_shipped_asset(self):
        path = self.wheel()
        link = zipfile.ZipInfo('analog_factory/link')
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        with zipfile.ZipFile(path, 'a') as archive:
            archive.writestr(link, '/outside')
        with self.assertRaisesRegex(ValueError, 'regular'):
            inspect_archive(path, self.expected)

    def test_sdist_has_one_prefix_and_no_links(self):
        path = self.root/'test.tar.gz'
        with tarfile.open(path, 'w:gz') as archive:
            for name, value in [('analog_factory/__init__.py', b'core'), ('analog_factory/static/app.js', b'ui')]:
                entry = tarfile.TarInfo('analog_factory-0.1.0/'+name)
                entry.size = len(value)
                archive.addfile(entry, io.BytesIO(value))
        self.assertEqual(inspect_archive(path, self.expected)['verified_runtime_files'], 2)
        with tarfile.open(path, 'w:gz') as archive:
            entry = tarfile.TarInfo('analog_factory-0.1.0/link')
            entry.type = tarfile.SYMTYPE
            entry.linkname = '/outside'
            archive.addfile(entry)
        with self.assertRaisesRegex(ValueError, 'regular'):
            inspect_archive(path, self.expected)

    def test_staging_uses_current_sources_and_excludes_private_root_state(self):
        source = self.root/'source'
        (source/'analog_factory/static').mkdir(parents=True)
        for name, content in [('pyproject.toml', '[project]\nname="analog-factory"\n'),
                              ('README.md', 'readme'), ('LICENSE', 'license'),
                              ('analog_factory/__init__.py', 'core'), ('analog_factory/static/app.js', 'ui'),
                              ('.env', 'secret')]:
            (source/name).write_text(content)
        manifest = stage_source(source, self.root/'staged')
        self.assertEqual(manifest, self.expected)
        self.assertFalse((self.root/'staged/.env').exists())
        (source/'analog_factory/link.py').symlink_to(source/'.env')
        with self.assertRaisesRegex(ValueError, 'symbolic'):
            stage_source(source, self.root/'rejected')
