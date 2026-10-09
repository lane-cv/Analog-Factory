"""Browser semantic controls retain explicit native targets and permissions."""
import tempfile
import unittest
from unittest.mock import Mock

from analog_factory.service import Workspace


class NativeCanvasUIContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Workspace(self.temp.name)
        self.addCleanup(self.workspace.agents.close)
        self.registry = self.workspace.operations
        self.addCleanup(self.registry.close)

    def test_commands_are_discoverable_with_declared_permissions_and_state(self):
        for name in ('open', 'close', 'set_options', 'start', 'refresh', 'select',
                     'move', 'connect_pins', 'paint_box', 'save', 'stop'):
            with self.subTest(name=name):
                operation = self.registry.operations['ui.native_canvas.' + name]
                self.assertEqual(operation.kind, 'frontend')
                self.assertIn('client_id', operation.input_schema['required'])
                self.assertIn('native', operation.output_schema['properties']['data']['properties'])
                self.assertIn('ui', operation.permissions)
                if name in ('move', 'connect_pins', 'paint_box', 'save'):
                    self.assertTrue({'read', 'execute', 'write'} <= set(operation.permissions))
                if name in ('start', 'save'):
                    self.assertTrue(operation.versioned)

    def test_shutdown_closes_owned_native_manager(self):
        manager = Mock()
        self.workspace.native_canvas = manager
        self.registry.close()
        manager.close.assert_called_once_with()
        del self.workspace.native_canvas

    def test_draft_operation_covers_every_control_without_native_execution_permission(self):
        from analog_factory.operation_ui import NATIVE_CANVAS_OPTIONS
        operation = self.registry.operations['ui.native_canvas.set_options']
        fields = operation.input_schema['properties']
        for name, schema in NATIVE_CANVAS_OPTIONS['properties'].items():
            with self.subTest(control=name):
                self.assertIn(name, fields)
                self.assertEqual(fields[name], schema)
        self.assertEqual(set(operation.permissions), {'read', 'ui'})
        self.assertEqual(operation.confirmation, 'none')
        self.assertFalse(operation.versioned)
        connected = self.registry.sessions.connect({})
        result = self.registry.invoke('ui.native_canvas.set_options',
            {'client_id': connected['client_id'], 'grid_steps': 0})
        self.assertEqual(result['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(self.registry.sessions._get(connected['client_id'])['commands'], {})


if __name__ == '__main__':
    unittest.main()
