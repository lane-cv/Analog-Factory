"""Enforcement tests catch omissions instead of mistaking text inventory for proof."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import check_operations as checker
from analog_factory.service import Workspace


class OperationCoverageTests(unittest.TestCase):
    def test_all_controller_action_namespaces_and_literal_calls_are_inventoried(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);static=root/'analog_factory/static';static.mkdir(parents=True)
            (static/'training-controls.js').write_text('''
                const button='<button data-training-action="start">';
                const replay='<button data-training-replay="3">';
                dialog.addEventListener('click', handler);
                api.invoke('training.start',{}); api.invoke(operation,{});
                operations.register('training',commands,snapshot);
            ''')
            with patch.object(checker,'ROOT',root):
                record=checker.declarations()['training-controls.js']
            self.assertEqual(record['action_attributes'],['start'])
            self.assertEqual(record['action_namespaces'],{'data-training-action':['start']})
            self.assertEqual(record['literal_operations'],['training.start'])
            self.assertEqual(record['registered_modules'],['training'])
            self.assertEqual(record['dynamic_operation_calls'],1)
            self.assertIn('data-training-replay',record['data_attributes'])
            self.assertEqual(record['event_types'],['click'])

    def test_new_controller_transport_bypass_and_unknown_operation_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);static=root/'analog_factory/static';static.mkdir(parents=True)
            (static/'new-controller.js').write_text("fetch('/api/unsafe'); operations.invoke('unknown.write',{});")
            with patch.object(checker,'ROOT',root):
                errors=checker.check_entry_points()
                errors+=checker.check_client_operations(SimpleNamespace(operations={}))
            self.assertTrue(any('classif' in item for item in errors),errors)
            self.assertTrue(any('transport' in item for item in errors),errors)
            self.assertTrue(any('unknown.write' in item for item in errors),errors)

    def test_graph_engine_direct_source_write_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);package=root/'analog_factory';package.mkdir()
            (package/'workflow_graph.py').write_text("def bypass(self):\n    self.workspace.projects.write('pid','deck','bad')\n")
            with patch.object(checker,'ROOT',root):
                errors=checker.check_entry_points()
            self.assertTrue(any('workflow_graph.py:2' in item and 'projects.write' in item for item in errors),errors)

    def test_generated_boundary_inventory_covers_registered_new_services(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace=Workspace(Path(directory))
            self.addCleanup(workspace.operations.close)
            documents=checker.generated(workspace.operations)
            inventory=json.loads(documents['boundary-inventory.json'])
            names={row['operation_id'] for row in inventory['registered_handlers']}
            self.assertTrue({'workflow.parse','workflow.catalog','simulation.batch.start','training.start','run.waveform_stats',
                             'native.terminal.start','agent.session.create'} <= names)
            self.assertEqual(len(names),len(workspace.operations.operations))
            self.assertEqual(inventory['static_proof'],'declarations_only')
            self.assertIn('native-terminal.js',inventory['frontend'])
            self.assertIn('workflow_graph.py',inventory['python_entry_points'])


if __name__=='__main__':unittest.main()
