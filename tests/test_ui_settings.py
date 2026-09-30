import json
import tempfile
import unittest
from pathlib import Path

from analog_factory.service import Workspace


class InterfaceSettingsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.workspace = Workspace(self.root)

    def test_existing_workspace_defaults_to_english_and_dark(self):
        (self.root / 'settings.json').write_text(json.dumps({'tools': {'magic': '/local/magic'}}))
        workspace = Workspace(self.root)
        self.assertEqual(workspace.settings()['ui'], {'language': 'en', 'theme': 'dark'})
        self.assertEqual(workspace.settings()['tools'], {'magic': '/local/magic'})

    def test_preferences_persist_without_changing_tool_or_provider_configuration(self):
        initial = self.workspace.save_settings({
            'tools': {'magic': '/local/magic', 'netgen': '/local/netgen'},
            'pdk_root': '/local/pdk',
            'provider': {'protocol': 'openai-compatible', 'base_url': 'http://localhost:1/v1',
                         'model': 'test', 'api_key': 'session-secret'},
        })
        updated = self.workspace.save_settings({'ui': {'language': 'zh-CN', 'theme': 'light'}})
        for key in ('tools', 'pdk_root', 'provider'):
            self.assertEqual(updated[key], initial[key])
        self.assertEqual(self.workspace.provider().secret, 'session-secret')
        self.assertEqual(Workspace(self.root).settings()['ui'], {'language': 'zh-CN', 'theme': 'light'})
        self.assertNotIn('session-secret', (self.root / 'settings.json').read_text())

    def test_partial_preference_updates_preserve_other_selection(self):
        self.workspace.save_settings({'ui': {'language': 'zh-CN'}})
        updated = self.workspace.save_settings({'ui': {'theme': 'light'}})
        self.assertEqual(updated['ui'], {'language': 'zh-CN', 'theme': 'light'})
        updated = self.workspace.save_settings({'pdk_variant': 'sky130A'})
        self.assertEqual(updated['ui'], {'language': 'zh-CN', 'theme': 'light'})
        updated['ui']['language'] = 'en'
        self.assertEqual(self.workspace.settings()['ui']['language'], 'zh-CN')

    def test_invalid_preferences_do_not_change_settings_or_disk(self):
        self.workspace.save_settings({'ui': {'language': 'zh-CN', 'theme': 'light'}})
        original = (self.root / 'settings.json').read_bytes()
        invalid = [None, [], 'dark', {'language': 'fr'}, {'language': None},
                   {'theme': 'system'}, {'theme': 1}, {'appearance': 'dark'}]
        for ui in invalid:
            with self.subTest(ui=ui), self.assertRaises(ValueError):
                self.workspace.save_settings({'ui': ui, 'pdk_root': '/should-not-change'})
            self.assertEqual((self.root / 'settings.json').read_bytes(), original)
            self.assertEqual(self.workspace.settings()['ui'], {'language': 'zh-CN', 'theme': 'light'})


if __name__ == '__main__':
    unittest.main()
