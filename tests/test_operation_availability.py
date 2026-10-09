"""Capability predicates shared by status, discovery and real EDA launch."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analog_factory.operation_catalog import LEGACY_ROUTES
from analog_factory.operations import InvocationContext
from analog_factory.service import Workspace


class OperationAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = Workspace(self.root / 'workspace')
        self.amplifier = self.workspace.projects.create('Educational amplifier')['id']
        self.sky130 = self.workspace.projects.create('SKY130 amplifier', 'sky130')['id']
        self.detected = {
            'tools': {name: {'available': True, 'path': sys.executable, 'version': 'fixture', 'diagnostic': 'Ready'}
                      for name in ('ngspice', 'xschem', 'magic', 'netgen', 'codex', 'claude')},
            'pdk': {'available': False, 'root': None, 'variant': 'sky130A', 'version': None,
                    'paths': {'xschem': None, 'magic': None, 'netgen': None, 'models': None},
                    'diagnostic': 'Prepared PDK assets are missing'},
            'desktop': {'available': True, 'diagnostic': 'Desktop available'},
        }
        self.probe = patch('analog_factory.eda.detect', return_value=self.detected)
        self.probe.start()
        self.addCleanup(self.probe.stop)

    def status(self, project_id=None):
        result = self.workspace.operations.invoke('workspace.status', {'project_id': project_id} if project_id else {})
        self.assertTrue(result['ok'], result)
        self.assertIn('operation_availability', result['data'])
        return result['data']

    def launch(self, project_id, kind, options=None):
        result = self.workspace.operations.invoke('run.start', {'project_id': project_id, 'kind': kind,
                                                               'options': options or {}})
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error']['code'], 'UNAVAILABLE')
        self.assertEqual(result['data']['status'], 'unavailable')
        self.assertIsNone(result['data']['pid'])
        return result['data']

    def test_status_without_project_disables_every_launch(self):
        status = self.status()
        self.assertIsNone(status['project_id'])
        self.assertEqual(set(status['operation_availability']), {'simulate', 'xschem', 'magic', 'drc', 'lvs', 'extract'})
        self.assertTrue(all(not item['available'] and item['reasons'] for item in status['operation_availability'].values()))

    def test_legacy_status_query_and_scoped_discovery_use_selected_project(self):
        route = next(route for route in LEGACY_ROUTES if route[:2] == ('GET', '/api/status'))
        parameters = route[3]({}, {'project_id': [self.sky130]}, {})
        self.assertEqual(parameters, {'project_id': self.sky130})
        result = self.workspace.operations.invoke(route[2], parameters)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['data']['project_id'], self.sky130)
        scoped = self.workspace.operations.invoke('workspace.status', {}, context=InvocationContext(
            principal='project-reader', project_id=self.sky130, permissions=frozenset({'read'}), source='external-agent'))
        self.assertEqual(scoped['data']['project_id'], self.sky130)
        self.assertEqual(scoped['data']['operation_availability'], result['data']['operation_availability'])

    def test_sky130_pdk_requirement_agrees_between_status_discovery_and_launch(self):
        status = self.status(self.sky130)
        availability = status['operation_availability']['simulate']
        self.assertFalse(availability['available'])
        self.assertIn('SKY130 template', availability['reasons'][0])
        discovered = self.workspace.operations.describe_operation('run.start',
            parameters={'project_id': self.sky130, 'kind': 'simulate'})['availability']
        self.assertEqual(discovered['reasons'], availability['reasons'])
        record = self.launch(self.sky130, 'simulate')
        self.assertIn(availability['reasons'][0], record['error'])
        self.assertTrue(self.status(self.amplifier)['operation_availability']['simulate']['available'])

    def test_partial_pdk_does_not_launch_magic_or_create_editor_source(self):
        magicrc = self.root / 'partial.magicrc'
        magicrc.write_text('# Only one PDK asset exists\n')
        self.detected['pdk']['paths']['magic'] = str(magicrc)
        status = self.status(self.amplifier)
        for kind in ('magic', 'drc', 'extract', 'lvs'):
            self.assertFalse(status['operation_availability'][kind]['available'])
        record = self.launch(self.amplifier, 'magic', {'create_missing': True})
        self.assertIn('Prepared PDK assets are missing', record['error'])
        self.assertFalse((self.workspace.projects.path(self.amplifier) / 'layout/amplifier.mag').exists())

    def test_gui_desktop_requirement_does_not_disable_headless_netlisting(self):
        self.detected['desktop'] = {'available': False, 'diagnostic': 'No graphical desktop'}
        status = self.status(self.amplifier)
        self.assertEqual(status['operation_availability']['xschem']['reasons'], ['No graphical desktop'])
        discovered = self.workspace.operations.describe_operation('run.start', parameters={
            'project_id': self.amplifier, 'kind': 'xschem', 'options': {'netlist_only': True}})['availability']
        self.assertTrue(discovered['available'], discovered)
        record = self.launch(self.amplifier, 'xschem', {'create_missing': True})
        self.assertIn('No graphical desktop', record['error'])

    def test_effective_project_tool_override_is_shared_by_status_and_launch(self):
        self.workspace.configure_project(self.amplifier, {'tools': {'ngspice': '/missing/project-ngspice'}})
        def detection(config):
            value = copy.deepcopy(self.detected)
            if config.get('tools', {}).get('ngspice') == '/missing/project-ngspice':
                value['tools']['ngspice'] = {'available': False, 'path': '/missing/project-ngspice',
                    'version': None, 'diagnostic': 'Configured project ngspice is missing'}
            return value
        self.probe.stop()
        with patch('analog_factory.eda.detect', side_effect=detection):
            status = self.status(self.amplifier)
            self.assertEqual(status['toolchain']['tools']['ngspice']['path'], '/missing/project-ngspice')
            reason = status['operation_availability']['simulate']['reasons'][0]
            record = self.launch(self.amplifier, 'simulate')
            self.assertIn(reason, record['error'])

    def test_project_configuration_rejects_unsupported_credential_updates(self):
        for field, value in [('api_key', 'do-not-silently-discard'), ('api_key_storage', 'local'),
                             ('clear_api_key', True), ('api_key_configured', True)]:
            with self.subTest(field=field):
                result = self.workspace.operations.invoke('project.configure', {'project_id': self.amplifier,
                    'config': {'provider': {field: value}}}, expected_version=self.workspace.operations.version(self.amplifier))
                self.assertFalse(result['ok'], result)
                self.assertEqual(result['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(self.workspace.projects.get(self.amplifier)['config'], {})
        self.assertIsNone(self.workspace._secret)
        result = self.workspace.operations.invoke('project.configure', {'project_id': self.amplifier,
            'config': {'provider': {'api_key_env': 'PROJECT_PROVIDER_TOKEN'}}},
            expected_version=self.workspace.operations.version(self.amplifier))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['data']['config']['provider']['api_key_env'], 'PROJECT_PROVIDER_TOKEN')


if __name__ == '__main__':
    unittest.main()
