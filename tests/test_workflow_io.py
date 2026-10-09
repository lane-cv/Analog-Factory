import json
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analog_factory.service import Workspace
from analog_factory.templates import tuning_workflow


FLOW = {'schema_version': 2, 'name': 'YAML simulation', 'nodes': [
    {'id': 'simulate', 'type': 'simulate', 'config': {'options': {'netlist': 'testbenches/pre.cir'}}},
    {'id': 'report', 'type': 'report', 'inputs': {'run_id': {'node': 'simulate', 'port': 'run_id'}}}],
    'edges': [{'source': 'simulate', 'target': 'report', 'on': 'success'}]}
YAML_AVAILABLE = importlib.util.find_spec('yaml') is not None


class WorkflowIOTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Workspace(Path(self.tmp.name))
        self.registry = self.workspace.operations
        self.addCleanup(self.registry.close)

    def invoke(self, name, parameters):
        result = self.registry.invoke(name, parameters)
        self.assertTrue(result['ok'], result)
        return result['data']

    def test_json_and_available_yaml_roundtrip_preserve_v1_and_v2_validated_graphs(self):
        for flow in (FLOW, tuning_workflow()):
            for format in (('json', 'yaml') if YAML_AVAILABLE else ('json',)):
                with self.subTest(schema=flow['schema_version'], format=format):
                    exported = self.invoke('workflow.export', {'workflow': flow, 'format': format})
                    parsed = self.invoke('workflow.parse', {'content': exported['content'], 'format': format})
                    canonical = self.invoke('workflow.parse', {'content': json.dumps(flow), 'format': 'json'})
                    self.assertEqual(parsed['workflow'], canonical['workflow'])
                    self.assertTrue(exported['filename'].endswith('.' + format))
        events = self.registry.subscribe_events()['events']
        self.assertEqual(events, [], 'Interchange queries do not save or execute workflows')

    @unittest.skipUnless(YAML_AVAILABLE, 'optional yaml extra required')
    def test_yaml_uses_true_false_booleans_and_preserves_on_edge_key(self):
        content = '''schema_version: 2
name: on
nodes:
  - id: condition
    type: condition
    config: {operator: eq}
    inputs: {left: {value: true}, right: {value: false}}
  - id: report
    type: report
edges:
  - source: condition
    target: report
    on: success
    when: false
'''
        flow = self.invoke('workflow.parse', {'content': content, 'format': 'yaml'})['workflow']
        self.assertEqual(flow['name'], 'on')
        self.assertEqual(flow['edges'][0]['on'], 'success')
        self.assertIs(flow['edges'][0]['when'], False)

    @unittest.skipUnless(YAML_AVAILABLE, 'optional yaml extra required')
    def test_unsafe_ambiguous_or_nonjson_documents_are_rejected(self):
        from analog_factory.workflow_io import parse_document
        for content in ('!!python/object/apply:os.system ["touch /tmp/forbidden-yaml"]',
                        'schema_version: 2\nschema_version: 1',
                        'nodes: &nodes [*nodes]', 'nodes: &nodes []\ncopy: *nodes',
                        '? [complex, key]\n: value', 'value: .nan',
                        '---\nschema_version: 2\n---\nschema_version: 1',
                        'value: ' + '[' * 70 + '1' + ']' * 70):
            with self.subTest(content=content[:50]), self.assertRaises(ValueError):
                parse_document(content, 'yaml')
        for content in ('{"schema_version":2,"schema_version":1}', '{"value":NaN}'):
            with self.subTest(content=content), self.assertRaises(ValueError):
                parse_document(content, 'json')

    def test_json_is_bounded_and_rejects_ambiguous_keys_without_yaml(self):
        from analog_factory.workflow_io import parse_document
        with patch('analog_factory.workflow_io.yaml_module', side_effect=AssertionError('JSON must not load YAML')):
            self.assertEqual(parse_document(json.dumps(FLOW))['workflow']['schema_version'], 2)
            for content in ('{"schema_version":2,"schema_version":1}', '{"value":NaN}',
                            ' ' * (2 * 1024 * 1024 + 1), '{"value":' + '[' * 70 + '1' + ']' * 70 + '}'):
                with self.subTest(length=len(content)), self.assertRaises(ValueError):
                    parse_document(content)

    def test_missing_optional_yaml_dependency_reports_unavailable_before_mutation(self):
        with patch('analog_factory.workflow_io.yaml_module', side_effect=RuntimeError('Install the yaml extra')):
            result = self.registry.invoke('workflow.parse', {'content': 'schema_version: 2', 'format': 'yaml'})
            self.assertEqual(result['error']['code'], 'UNAVAILABLE', result)
            self.assertFalse(self.registry.describe_operation('workflow.parse', parameters={'format': 'yaml'})['availability']['available'])
            self.assertTrue(self.registry.describe_operation('workflow.parse', parameters={'format': 'json'})['availability']['available'])

    @unittest.skipUnless(shutil.which('ngspice'), 'ngspice required')
    @unittest.skipUnless(YAML_AVAILABLE, 'optional yaml extra required')
    def test_yaml_import_executes_real_ngspice_and_preserves_source(self):
        project = self.workspace.projects.create('YAML real run')['id']
        source = self.workspace.projects.read(project, 'testbenches/pre.cir')['content']
        exported = self.invoke('workflow.export', {'workflow': FLOW, 'format': 'yaml'})
        flow = self.invoke('workflow.parse', {'content': exported['content'], 'format': 'yaml'})['workflow']
        job = self.invoke('workflow.run', {'project_id': project, 'workflow': flow})
        done = self.workspace.jobs.wait(self.workspace.projects.path(project), job['id'], 15)
        self.assertEqual(done['status'], 'succeeded', done)
        self.assertEqual(len(self.workspace.runs(project)), 1)
        self.assertEqual(self.workspace.runs(project)[0]['status'], 'succeeded')
        self.assertEqual(self.workspace.projects.read(project, 'testbenches/pre.cir')['content'], source)


if __name__ == '__main__':
    unittest.main()
