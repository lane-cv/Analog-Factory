"""First-open behavior without requiring an interactive desktop in unit tests."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from analog_factory.service import Workspace


class GUILaunchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = Workspace(self.root / 'workspace')
        self.pid = self.workspace.projects.create('Draw manually', 'sky130')['id']
        self.project = self.workspace.projects.path(self.pid)
        editor = self.root / 'editor'
        editor.write_text('#!/usr/bin/env python3\nimport pathlib, sys\nprint(pathlib.Path(sys.argv[-1]).read_text())\n')
        editor.chmod(0o755)
        rc = self.root / 'technology.rc'
        rc.write_text('# Test tool configuration\n')
        self.detected = {
            'tools': {kind: {'available': True, 'path': str(editor), 'version': 'test'}
                      for kind in ('xschem', 'magic')},
            'pdk': {'available': True, 'root': str(self.root / 'pdk'), 'variant': 'sky130A',
                    'paths': {'xschem': str(rc), 'magic': str(rc)}, 'diagnostic': 'Ready'},
            'desktop': {'available': True, 'diagnostic': 'Ready'},
        }
        self.probe = patch('analog_factory.eda.detect', return_value=self.detected)
        self.probe.start()
        self.addCleanup(self.probe.stop)

    def launch(self, kind, options=None, **kwargs):
        run = self.workspace.run(self.pid, kind, {'create_missing': True, **(options or {})}, **kwargs)
        return self.workspace.runner.wait(self.project, run['id'], timeout=10)

    def test_first_open_creates_editable_sources_in_project_and_run_snapshot(self):
        for kind, relative, marker in (
            ('xschem', 'schematics/amplifier.sch', 'file_version=1.2'),
            ('magic', 'layout/amplifier.mag', 'tech sky130A'),
        ):
            with self.subTest(kind=kind):
                run = self.launch(kind)
                self.assertEqual(run['status'], 'succeeded', run)
                source = self.project / relative
                self.assertTrue(source.is_file())
                self.assertIn(marker, source.read_text())
                self.assertEqual(run['command'][-1], str(source))
                self.assertEqual((self.project / '.af/runs' / run['id'] / 'input' / relative).read_bytes(), source.read_bytes())

    def test_reopen_preserves_existing_drawing(self):
        source = self.project / 'schematics/amplifier.sch'
        source.write_text('Existing drawing must remain byte-for-byte unchanged\n')
        run = self.launch('xschem')
        self.assertEqual(run['status'], 'succeeded')
        self.assertEqual(source.read_text(), 'Existing drawing must remain byte-for-byte unchanged\n')

    def test_unavailable_desktop_does_not_create_source(self):
        self.detected['desktop']['available'] = False
        run = self.launch('magic')
        self.assertEqual(run['status'], 'unavailable')
        self.assertFalse((self.project / 'layout/amplifier.mag').exists())

    def test_creation_rejects_unsafe_paths_and_wrong_file_types(self):
        (self.project / 'linked').symlink_to(self.root, target_is_directory=True)
        for relative in ('../escaped.sch', '.af/hidden.sch', 'linked/escaped.sch', 'project.json'):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                self.launch('xschem', {'schematic': relative})
        self.assertFalse((self.root / 'escaped.sch').exists())

    def test_netlisting_and_verification_still_require_saved_sources(self):
        for kind, options in (('xschem', {'netlist_only': True}), ('drc', {}), ('extract', {})):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.launch(kind, options)
        self.assertFalse((self.project / 'schematics/amplifier.sch').exists())
        self.assertFalse((self.project / 'layout/amplifier.mag').exists())

    def test_invalid_timeout_does_not_create_source(self):
        with self.assertRaises(ValueError):
            self.launch('magic', timeout=-1)
        self.assertFalse((self.project / 'layout/amplifier.mag').exists())
