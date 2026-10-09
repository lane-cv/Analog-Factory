"""Integration contracts for operation adapters and existing HTTP routes."""
import base64
import io
import json
import re
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from analog_factory.service import Workspace
from analog_factory.templates import tuning_workflow
from analog_factory.operation_catalog import register_operations, LEGACY_ROUTES
from analog_factory.operations import InvocationContext, OperationRegistry


class OperationCatalogTests(unittest.TestCase):
    def setUp(self):
        self.routes = LEGACY_ROUTES
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Workspace(Path(self.tmp.name))
        self.addCleanup(self.workspace.agents.close)
        self.registry = OperationRegistry(self.workspace)
        register_operations(self.registry)
        self.pid = self.workspace.projects.create('Catalog integration')['id']

    def invoke(self, operation, parameters):
        result = self.registry.invoke(operation, parameters,
                                      expected_version=self.registry.version(parameters.get('project_id')))
        self.assertTrue(result['ok'], result)
        return result['data']

    def legacy(self, method, path, query=None, body=None):
        matches = [(entry, re.fullmatch(entry[1], path)) for entry in self.routes if entry[0] == method]
        matches = [(entry, match) for entry, match in matches if match]
        self.assertEqual(len(matches), 1, (method, path))
        entry, match = matches[0]
        return self.invoke(entry[2], entry[3](match.groupdict(), query or {}, body or {}))

    def test_legacy_file_edit_and_restore_share_revision_history(self):
        path = f'/api/projects/{self.pid}/files'
        original = self.legacy('GET', path, {'path': ['testbenches/pre.cir']})['content']
        self.legacy('PUT', path, {'path': ['testbenches/pre.cir']}, {'content': '* reviewed replacement\n.end\n'})
        history = self.invoke('file.history', {'project_id': self.pid, 'path': 'testbenches/pre.cir'})
        self.assertEqual(len(history), 1)
        self.invoke('file.restore', {'project_id': self.pid, 'path': 'testbenches/pre.cir', 'revision': history[0]['id']})
        actual = self.invoke('file.read', {'project_id': self.pid, 'path': 'testbenches/pre.cir'})
        self.assertEqual(actual['content'], original)
        self.assertEqual(len(self.workspace.projects.history(self.pid, 'testbenches/pre.cir')), 2)

    def test_schema_rejects_bad_run_options_before_creating_evidence(self):
        result = self.registry.invoke('run.start', {'project_id': self.pid, 'kind': 'simulate',
                                                  'options': {'netlist_only': 'false'}})
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(self.workspace.runs(self.pid), [])

    def test_configuration_and_export_do_not_expose_provider_secret(self):
        result = self.invoke('settings.update', {'settings': {'provider': {
            'base_url': 'http://localhost:1/v1', 'model': 'catalog-test', 'api_key': 'catalog-secret'}}})
        self.assertNotIn('catalog-secret', str(result))
        exported = self.invoke('project.export', {'project_id': self.pid})
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(exported['content_base64']))) as archive:
            self.assertIn('project.json', archive.namelist())
            self.assertFalse(any('.credentials' in name for name in archive.namelist()))

    def test_simulation_configuration_and_saved_workflow_reach_project_files(self):
        output = self.invoke('simulation.configure', {'project_id': self.pid, 'config': {
            'analysis': 'ac', 'parameters': {'points': 20, 'start': 1, 'stop': '1Meg'}}})
        self.assertIn('.ac dec 20 1 1Meg', self.workspace.projects.read(self.pid, output['path'])['content'])
        workflow = tuning_workflow()
        workflow['name'] = 'Saved by operation'
        saved = self.invoke('workflow.save', {'project_id': self.pid, 'workflow': workflow})
        self.assertEqual(saved['path'], 'workflows/Saved-by-operation.json')
        self.assertIn('Saved by operation', [flow['name'] for flow in self.invoke('workflow.list', {'project_id': self.pid})])

    def test_agent_session_routes_use_existing_durable_sessions(self):
        prefix = f'/api/projects/{self.pid}/agent/sessions'
        session = self.legacy('POST', prefix, body={'backend': 'codex'})
        renamed = self.legacy('PUT', prefix + '/' + session['id'], body={'title': 'Reviewed circuit'})
        self.assertEqual(renamed['title'], 'Reviewed circuit')
        self.assertEqual(self.legacy('GET', prefix, {'q': ['circuit']})[0]['id'], session['id'])
        deleted = self.legacy('DELETE', prefix + '/' + session['id'])
        self.assertTrue(deleted['deleted'])
        self.assertEqual(self.workspace.agents.list(self.pid), [])

    def test_effort_is_discoverable_and_default_preserves_legacy_sessions(self):
        from analog_factory.operation_schema import validate
        from analog_factory.operation_ui import register_operations as register_ui
        register_ui(self.registry)
        create = self.registry.describe_operation('agent.session.create')['input_schema']
        config = self.registry.describe_operation('ui.agent.set_config')['input_schema']
        self.assertIsNone(create['properties']['effort']['default'])
        for value in (None, 'low', 'high', 'xhigh', 'max', 'ultra'):
            self.assertEqual(validate(value, config['properties']['effort']), value)
        for value in ('maximum', True, 3):
            with self.assertRaises(ValueError):
                validate(value, create['properties']['effort'])
        created = self.invoke('agent.session.create', {'project_id': self.pid, 'backend': 'codex'})
        self.assertIsNone(created['effort'])

    def test_custom_model_effort_discovery_uses_shared_route_handler(self):
        result = {'backend': 'claude', 'source': 'builtin', 'models': [],
                  'fast_mode': {'supported': False}, 'effort_levels': ['low', 'high'],
                  'selected_model': {'id': 'claude-sonnet-4-6', 'label': 'claude-sonnet-4-6',
                                     'effort_levels': ['low', 'high'], 'default_effort': None}}
        with patch('analog_factory.operation_catalog.model_catalog', return_value=result) as catalog:
            legacy = self.legacy('GET', '/api/agents/models', {
                'backend': ['claude'], 'project_id': [self.pid], 'model': ['claude-sonnet-4-6']})
            structured = self.invoke('agent.models', {'backend': 'claude', 'project_id': self.pid,
                                                      'model': 'claude-sonnet-4-6'})
        self.assertEqual(legacy, structured)
        self.assertEqual(catalog.call_args.kwargs['model'], 'claude-sonnet-4-6')
        self.assertEqual(structured['selected_model']['effort_levels'], ['low', 'high'])

    def test_ui_and_scoped_agent_persist_the_same_effort_configuration(self):
        options = {'backend': 'codex', 'model': 'fixture-model', 'effort': 'high'}
        metadata = {'models': [{'id': 'fixture-model', 'effort_levels': ['low', 'high']}]}
        with patch('analog_factory.agent_models.catalog', return_value=metadata):
            ui = self.legacy('POST', f'/api/projects/{self.pid}/agent/sessions', body=options)
            agent = self.registry.invoke('agent.session.create', options,
                context=InvocationContext(source='external-agent', project_id=self.pid,
                    permissions=frozenset({'write'}), confirmed=True))
        self.assertTrue(agent['ok'], agent)
        for key in ('backend', 'model', 'mode', 'effort'):
            self.assertEqual(ui[key], agent['data'][key])
        self.assertEqual(self.workspace.agents.get(self.pid, agent['data']['id'])['effort'], 'high')
        denied = self.registry.invoke('agent.session.create', options,
            context=InvocationContext(source='external-agent', project_id=self.pid,
                permissions=frozenset({'read'}), confirmed=True))
        self.assertEqual(denied['error']['code'], 'PERMISSION_DENIED')
        self.assertEqual(len(self.workspace.agents.list(self.pid)), 2)

    def test_external_call_cannot_select_private_evaluation_directory(self):
        result = self.registry.invoke('run.start', {'project_id': self.pid, 'episode': {
            'job_id': 'a' * 32, 'index': 0}})
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'PERMISSION_DENIED')
        self.assertEqual(self.workspace.runs(self.pid), [])

    def test_scoped_project_listing_does_not_disclose_other_projects(self):
        other = self.workspace.projects.create('Private unrelated design')['id']
        result = self.registry.invoke('project.list', {}, context=InvocationContext(
            principal='project-reader', project_id=self.pid, permissions=frozenset({'read'}), source='external-agent'))
        self.assertTrue(result['ok'], result)
        self.assertEqual([record['id'] for record in result['data']], [self.pid])
        self.assertNotIn(other, json.dumps(result))

    def test_discovered_creation_example_and_defaults_are_executable(self):
        descriptor = self.registry.describe_operation('project.create')
        self.assertEqual(descriptor['input_schema']['properties']['template'].get('default'), 'amplifier')
        created = self.invoke('project.create', descriptor['examples'][0]['parameters'])
        self.assertEqual(created['template'], 'amplifier')
        evaluation = self.registry.describe_operation('evaluation.run')
        self.assertEqual(evaluation['input_schema']['properties']['episodes']['default'], 3)
        self.assertEqual(evaluation['input_schema']['properties']['seed']['default'], 42)

    def test_query_results_describe_core_fields_and_public_credentials(self):
        expected = {
            'workspace.status': {'version', 'project_id', 'toolchain', 'capabilities', 'operation_availability'},
            'settings.get': {'provider', 'ui'}, 'settings.update': {'provider', 'ui'},
            'provider.test': {'models', 'protocol'}, 'agent.backends': {'backends'},
            'agent.models': {'backend', 'models', 'source', 'fast_mode'},
            'chat.send': {'reply', 'backend', 'model', 'usage', 'cost', 'status', 'context'},
            'run.waveforms': {'title', 'plot', 'variables', 'points'},
        }
        for operation_id, fields in expected.items():
            with self.subTest(operation_id=operation_id):
                schema = self.registry.describe_operation(operation_id)['output_schema']
                self.assertTrue(fields <= schema.get('properties', {}).keys(), schema)
                self.assertTrue(fields <= set(schema.get('required', [])), schema)
        settings = self.registry.describe_operation('settings.get')['output_schema']
        provider = settings['properties']['provider']['properties']
        self.assertNotIn('api_key', provider)
        self.assertNotIn('clear_api_key', provider)
        self.assertIn('api_key_configured', provider)
        self.assertEqual(set(provider['api_key_storage']['enum']), {'none', 'environment', 'session', 'local'})
        history = self.registry.describe_operation('file.history')['output_schema']['items']
        self.assertEqual(set(history['required']), {'id', 'path', 'sha256', 'time'})

    def test_provider_result_schemas_preserve_dynamic_identifiers_and_usage(self):
        # Providers may omit an identifier or return vendor-specific accounting.
        catalog = {'models': ['valid-model', None, 42], 'protocol': 'openai-compatible'}
        response = {'reply': 'Suggestion', 'backend': 'openai-compatible', 'model': 'model',
                    'usage': ['vendor-defined'], 'cost': None, 'status': 'suggestion', 'context': []}
        with patch('analog_factory.providers.Provider.models', return_value=catalog):
            self.assertEqual(self.invoke('provider.test', {}), catalog)
        with patch('analog_factory.providers.Provider.chat', return_value=response):
            self.assertEqual(self.invoke('chat.send', {'project_id': self.pid, 'message': 'Review'}), response)

    def test_missing_simulator_preserves_real_unavailable_run_record(self):
        self.workspace.configure_project(self.pid, {'tools': {'ngspice': '/missing/catalog-ngspice'}})
        availability = self.registry.describe_operation('run.start', parameters={'project_id': self.pid})['availability']
        self.assertFalse(availability['available'])
        result = self.registry.invoke('run.start', {'project_id': self.pid})
        if not result['ok']:
            self.assertEqual(result['error']['code'], 'UNAVAILABLE')
        record = self.workspace.runner.wait(self.workspace.projects.path(self.pid), result['data']['id'])
        self.assertEqual(record['status'], 'unavailable')
        self.assertTrue(result['task_id'])

    @unittest.skipUnless(shutil.which('ngspice'), 'ngspice is required for actual operation execution')
    def test_run_operation_produces_real_metrics_logs_and_artifact(self):
        run = self.invoke('run.start', {'project_id': self.pid, 'kind': 'simulate',
                                        'options': {'netlist': 'testbenches/pre.cir'}})
        record = self.workspace.runner.wait(self.workspace.projects.path(self.pid), run['id'])
        self.assertEqual(record['status'], 'succeeded', record)
        evidence = self.invoke('run.get', {'project_id': self.pid, 'run_id': run['id']})
        self.assertGreater(evidence['metrics']['vout_mean'], 0)
        log = self.invoke('run.log', {'project_id': self.pid, 'run_id': run['id']})
        self.assertIn('ngspice', log['content'].lower())
        artifact = self.invoke('run.artifact', {'project_id': self.pid, 'run_id': run['id'], 'path': 'waveforms.raw'})
        self.assertIn(b'Variables:', base64.b64decode(artifact['content_base64']))
        waveforms = self.invoke('run.waveforms', {'project_id': self.pid, 'run_id': run['id']})
        self.assertTrue(waveforms['variables'])
        self.assertTrue(waveforms['points'])


if __name__ == '__main__':
    unittest.main()
