"""CLI tracking preferences and project availability preserve configuration scope."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from analog_factory.service import Workspace


class CliConfigurationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Workspace(Path(temporary.name))
        self.addCleanup(self.workspace.agents.close)
        self.addCleanup(lambda: self.workspace._operations and self.workspace._operations.close())

    def test_defaults_and_partial_updates_preserve_saved_interval(self):
        self.assertEqual(self.workspace.settings()['cli_updates'], {'automatic': False, 'interval_hours': 24})
        self.workspace.save_settings({'cli_updates': {'automatic': True, 'interval_hours': 48}})
        self.workspace.save_settings({'cli_updates': {'automatic': False}, 'ui': {'theme': 'light'}})
        self.assertEqual(self.workspace.settings()['cli_updates'], {'automatic': False, 'interval_hours': 48})
        loaded = Workspace(self.workspace.root)
        self.addCleanup(loaded.agents.close)
        self.assertEqual(loaded.settings()['cli_updates'], {'automatic': False, 'interval_hours': 48})

    def test_invalid_tracking_preferences_do_not_replace_valid_settings(self):
        self.workspace.save_settings({'cli_updates': {'automatic': False, 'interval_hours': 24}})
        for value in ({'automatic': 'yes'}, {'interval_hours': True}, {'interval_hours': 6.0},
                      {'interval_hours': 5}, {'interval_hours': 169}, {'url': 'http://localhost'}, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.workspace.save_settings({'cli_updates': value})
        self.assertEqual(self.workspace.settings()['cli_updates'], {'automatic': False, 'interval_hours': 24})

    def test_registered_settings_support_tracking_but_project_configuration_does_not(self):
        registry = self.workspace.operations
        version = registry.invoke('settings.get', {})['version']
        result = registry.invoke('settings.update', {'settings': {'cli_updates': {'automatic': False, 'interval_hours': 6}}}, expected_version=version)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['data']['cli_updates']['interval_hours'], 6)
        project = self.workspace.projects.create('CLI config scope')
        result = registry.invoke('project.configure', {'project_id': project['id'],
            'config': {'cli_updates': {'automatic': True}}})
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error']['code'], 'INVALID_ARGUMENT')
        self.assertNotIn('cli_updates', self.workspace.config(project['id']))

    def test_cli_availability_uses_selected_project_not_workspace_defaults(self):
        project = self.workspace.projects.create('Missing project CLI')
        def backends(project_id=None):
            return {'backends': [{'id': 'codex', 'available': project_id is None}]}
        with patch.object(self.workspace.agents, 'backends', side_effect=backends) as probe:
            status = self.workspace.status(project['id'])
        probe.assert_called_once_with(project['id'])
        self.assertFalse(status['capabilities']['cli_agents']['available'])


if __name__ == '__main__':
    unittest.main()
