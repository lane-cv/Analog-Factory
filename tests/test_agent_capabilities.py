import base64
import json
import os
import shutil
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from analog_factory.operations import InvocationContext
from analog_factory.providers import Provider
from analog_factory.service import Workspace
from tests import test_agent_api as fixture
from tests.test_agent_api import call, completion


PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jvWQAAAAASUVORK5CYII='


class AgentCapabilityTests(unittest.TestCase):
    setUp = fixture.ApiAgentTests.setUp
    endpoint = fixture.ApiAgentTests.endpoint

    def wait(self, sid, statuses=('succeeded', 'failed', 'cancelled', 'waiting_approval')):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            record = self.workspace.agents.get(self.pid, sid)
            if record['status'] in statuses:
                return record
            time.sleep(.01)
        self.fail('Agent did not reach expected status')

    def configure(self, responses, **config):
        provider = self.endpoint(responses)
        self.workspace.save_settings({'provider': {**provider.config, **config}})
        return provider

    def scope(self, **changes):
        return {'operations': ['file.write', 'run.start'], 'paths': ['notes/', 'testbenches/'],
                'max_actions': 2, 'max_runs': 1, **changes}

    def test_automatic_run_scope_uses_effective_project_design_paths(self):
        from analog_factory import agent_scope
        from analog_factory.storage import atomic_json, read_json
        metadata=self.workspace.projects.path(self.pid)/'project.json'
        project=read_json(metadata)
        project['design']={'layout':'layout/ota5.mag','layout_netlist':'netlists/extracted-lvs.spice',
                           'schematic_netlist':'netlists/ota5.spice','netlist':'testbenches/ota.cir'}
        atomic_json(metadata,project)
        def record(paths):
            scope=self.scope(operations=['run.start'],paths=paths,max_actions=4,max_runs=4)
            value={'id':'a'*32,'scope':scope}
            value['scope_grant']=agent_scope.grant(self.workspace,self.pid,value['id'],scope)
            return value
        legacy=record(['layout/amplifier.mag','netlists/extracted.spice','netlists/schematic.spice','testbenches/pre.cir'])
        actual=record(list(project['design'].values()))
        for kind in ('drc','extract','lvs','simulate'):
            arguments={'operation_id':'run.start','parameters':{'kind':kind}}
            with self.subTest(kind=kind):
                self.assertFalse(agent_scope.consume(self.workspace,self.pid,legacy,'invoke_operation',arguments))
                self.assertTrue(agent_scope.consume(self.workspace,self.pid,actual,'invoke_operation',arguments))
        explicit={'operation_id':'run.start','parameters':{'kind':'drc','options':{'layout':'layout/amplifier.mag'}}}
        self.assertTrue(agent_scope.consume(self.workspace,self.pid,legacy,'invoke_operation',explicit))
        project['design']['layout']='../outside.mag';atomic_json(metadata,project)
        with self.assertRaises(ValueError):
            agent_scope.consume(self.workspace,self.pid,legacy,'invoke_operation',{'operation_id':'run.start','parameters':{'kind':'drc'}})

    def test_images_reach_protocol_content_and_remain_selected_attachments(self):
        self.configure([completion('Image inspected.')], capabilities={'images': True})
        image = {'name': 'plot.png', 'mime_type': 'image/png', 'data_base64': PNG}
        session = self.workspace.agents.create(self.pid, {'backend': 'api'})
        self.workspace.agents.send(self.pid, session['id'], {'message': 'Inspect this plot', 'attachments': [image]})
        result = self.wait(session['id'])
        self.assertEqual(result['status'], 'succeeded', result)
        content = self.requests[0]['payload']['messages'][-1]['content']
        self.assertEqual(content[-1]['image_url']['url'], 'data:image/png;base64,' + PNG)
        self.assertEqual(result['turns'][0]['attachments'], [image])
        self.assertFalse((self.workspace.projects.path(self.pid) / 'plot.png').exists())

    def test_unverified_image_capability_and_invalid_image_fail_before_request(self):
        self.configure([completion('unexpected')])
        session = self.workspace.agents.create(self.pid, {'backend': 'api'})
        for image in ({'name': 'x.png', 'mime_type': 'image/png', 'data_base64': PNG},
                      {'name': 'x.png', 'mime_type': 'image/png', 'data_base64': base64.b64encode(b'not an image').decode()}):
            with self.assertRaises(ValueError):
                self.workspace.agents.send(self.pid, session['id'], {'message': 'Inspect', 'attachments': [image]})
        self.assertEqual(self.requests, [])

    def test_project_generation_override_validates_effective_model_capabilities(self):
        self.workspace.save_settings({'provider': {'base_url': 'http://127.0.0.1:1', 'model': 'declared',
            'capabilities': {'images': True, 'generation_parameters': ['temperature']}}})
        self.workspace.configure_project(self.pid, {'provider': {'generation': {'temperature': .3}}})
        effective = self.workspace.provider(self.pid)
        self.assertEqual(effective.generation['temperature'], .3)
        self.assertTrue(effective.capabilities['images'])

    def test_project_model_or_endpoint_change_does_not_inherit_another_models_capabilities(self):
        self.workspace.save_settings({'provider': {'base_url': 'http://127.0.0.1:1', 'model': 'declared',
            'capabilities': {'images': True}, 'model_capabilities': {'other': {'images': True}}}})
        self.workspace.configure_project(self.pid, {'provider': {'model': 'unknown'}})
        self.assertFalse(self.workspace.provider(self.pid).capabilities['images'])
        self.workspace.configure_project(self.pid, {'provider': {'model': 'other'}})
        self.assertTrue(self.workspace.provider(self.pid).capabilities['images'])
        self.workspace.configure_project(self.pid, {'provider': {'base_url': 'http://127.0.0.1:2', 'model': 'other'}})
        self.assertFalse(self.workspace.provider(self.pid).capabilities['images'])

    def test_tools_disabled_capability_omits_tools_and_rejects_unadvertised_call(self):
        from analog_factory.agent_api import run_api_turn
        provider = self.configure([completion(calls=[call('list_files', {})])], capabilities={'tools': False})
        provider = self.workspace.provider(self.pid)
        with self.assertRaisesRegex(ValueError, 'tool'):
            run_api_turn(provider, [], 'Inspect', self.workspace, self.pid, 'read-only',
                         self.events.append, self.cancel, lambda *args: False)
        self.assertNotIn('tools', self.requests[0]['payload'])

    def test_scope_requires_human_authority_even_with_agent_confirmation(self):
        self.configure([completion('unexpected')])
        options = {'backend': 'api', 'mode': 'automatic', 'scope': self.scope()}
        with self.assertRaises(PermissionError):
            self.workspace.agents.create(self.pid, options)
        registry = self.workspace.operations
        for context in (InvocationContext(source='agent', confirmed=True),
                        InvocationContext(principal='external-agent', source='external-agent', confirmed=True),
                        InvocationContext(source='ui', permissions=frozenset({'read', 'write', 'execute'}))):
            result = registry.invoke('agent.session.create', {'project_id': self.pid, **options}, context)
            self.assertEqual(result['error']['code'], 'PERMISSION_DENIED', result)
        result = registry.invoke('agent.session.create', {'project_id': self.pid, **options})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['data']['scope'], self.scope())
        self.assertNotIn('scope_grant', result['data'])

    def test_automatic_scope_writes_allowed_path_and_outside_scope_waits_for_human(self):
        self.configure([
            completion(calls=[call('write_file', {'path': 'notes/allowed.txt', 'content': 'scoped'})]),
            completion(calls=[call('write_file', {'path': 'outside.txt', 'content': 'forbidden'}, 'outside')]),
            completion('Scope respected.')])
        session = self.workspace.operations.invoke('agent.session.create', {'project_id': self.pid,
            'backend': 'api', 'mode': 'automatic', 'scope': self.scope()})['data']
        self.workspace.agents.send(self.pid, session['id'], {'message': 'Write notes'})
        waiting = self.wait(session['id'])
        self.assertEqual(waiting['status'], 'waiting_approval', waiting)
        self.assertEqual(self.workspace.projects.read(self.pid, 'notes/allowed.txt')['content'], 'scoped')
        self.assertFalse((self.workspace.projects.path(self.pid) / 'outside.txt').exists())
        self.workspace.agents.approve(self.pid, session['id'], waiting['pending_approval']['id'], False)
        result = self.wait(session['id'], ('succeeded', 'failed'))
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertFalse((self.workspace.projects.path(self.pid) / 'outside.txt').exists())
        self.assertTrue(any(event.get('status') == 'scope_authorized' for event in result['turns'][0]['events']))

    def test_scope_budget_is_durable_and_forged_or_copied_session_cannot_grant_authority(self):
        self.configure([completion(calls=[call('write_file', {'path': 'notes/one.txt', 'content': 'one'})]),
                        completion('Done.')])
        session = self.workspace.operations.invoke('agent.session.create', {'project_id': self.pid,
            'backend': 'api', 'mode': 'automatic', 'scope': self.scope(max_actions=1)})['data']
        self.workspace.agents.send(self.pid, session['id'], {'message': 'Write one'})
        self.assertEqual(self.wait(session['id'])['status'], 'succeeded')
        restarted = Workspace(Path(self.tmp.name))
        record = restarted.agents._load(self.pid, session['id'])
        record['scope']['max_actions'] = 100
        restarted.agents._save(self.pid, record)
        with self.assertRaisesRegex(PermissionError, 'scope|grant'):
            restarted.agents.send(self.pid, session['id'], {'message': 'Extend scope'})
        other = restarted.projects.create('Other project')['id']
        record['scope']['max_actions'] = 1
        restarted.agents._save(other, record)
        with self.assertRaisesRegex(PermissionError, 'scope|grant'):
            restarted.agents.send(other, session['id'], {'message': 'Copy grant'})

    def test_exhausted_scope_remains_exhausted_after_restart_and_requires_human(self):
        self.configure([completion(calls=[call('write_file', {'path': 'notes/one.txt', 'content': 'one'})]),
                        completion('Done.'),
                        completion(calls=[call('write_file', {'path': 'notes/two.txt', 'content': 'two'})]),
                        completion('Denied.')])
        session = self.workspace.operations.invoke('agent.session.create', {'project_id': self.pid,
            'backend': 'api', 'mode': 'automatic', 'scope': self.scope(max_actions=1)})['data']
        self.workspace.agents.send(self.pid, session['id'], {'message': 'First'})
        self.assertEqual(self.wait(session['id'])['status'], 'succeeded')
        self.workspace.agents.close()
        self.workspace = Workspace(Path(self.tmp.name))
        self.workspace.agents.send(self.pid, session['id'], {'message': 'Second'})
        pending = self.wait(session['id'])
        self.assertEqual(pending['status'], 'waiting_approval', pending)
        self.assertFalse((self.workspace.projects.path(self.pid) / 'notes/two.txt').exists())
        self.workspace.agents.approve(self.pid, session['id'], pending['pending_approval']['id'], False)
        self.assertEqual(self.wait(session['id'], ('succeeded', 'failed'))['status'], 'succeeded')

    @unittest.skipUnless(shutil.which('ngspice'), 'ngspice required for scoped execution evidence')
    def test_automatic_scope_runs_actual_simulator_and_retains_its_result(self):
        self.configure([completion(calls=[call('run_simulation', {'netlist': 'testbenches/pre.cir'})]),
                        completion('Simulation record returned.')])
        session = self.workspace.operations.invoke('agent.session.create', {'project_id': self.pid,
            'backend': 'api', 'mode': 'automatic', 'scope': self.scope(operations=['run.start'])})['data']
        self.workspace.agents.send(self.pid, session['id'], {'message': 'Simulate'})
        result = self.wait(session['id'])
        self.assertEqual(result['status'], 'succeeded', result)
        runs = self.workspace.runs(self.pid)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]['status'], 'succeeded')
        tool_result = next(event for event in result['turns'][0]['events'] if event['type'] == 'tool_result')
        self.assertIn(runs[0]['id'], tool_result['text'])

    def test_http_agent_cannot_forge_human_source_or_approval_permission(self):
        from analog_factory.server import create_server
        self.workspace.save_settings({'provider': {'base_url': 'http://127.0.0.1:1', 'model': 'fixture'}})
        server = create_server(self.workspace, port=0, desktop_url='')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        body = {'operation_id': 'agent.session.create', 'parameters': {'project_id': self.pid,
                'backend': 'api', 'mode': 'automatic', 'scope': self.scope()}, 'confirmed': True}
        def invoke(value):
            req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/operations/invoke',
                data=json.dumps(value).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer local-test-token'})
            try:
                response = urllib.request.urlopen(req)
            except urllib.error.HTTPError as response:
                with response:
                    return response.code, json.loads(response.read())
            with response:
                return response.status, json.loads(response.read())
        with patch.dict(os.environ, {'AF_AGENT_TOKEN': 'local-test-token', 'AF_AGENT_PERMISSIONS': 'read,write,execute,configure,ui,approve'}):
            status, result = invoke(body)
            self.assertEqual(status, 403, result)
            self.assertEqual(result['error']['code'], 'PERMISSION_DENIED')
            status, result = invoke({**body, 'source': 'ui', 'principal': 'local-owner'})
            self.assertEqual(status, 400, result)
        self.assertEqual(self.workspace.agents.list(self.pid), [])

    def test_remote_frontend_cannot_manufacture_automatic_scope_grant(self):
        registry = self.workspace.operations
        browser = registry.sessions.connect({'project_id': self.pid})
        result = registry.invoke('ui.agent.set_config', {'client_id': browser['client_id'],
            'mode': 'automatic', 'scope': self.scope()},
            InvocationContext(source='external-agent', confirmed=True))
        self.assertEqual(result['error']['code'], 'PERMISSION_DENIED', result)
        self.assertEqual(registry.sessions.poll(browser['client_id'], browser['token'], {'project_id': self.pid})['commands'], [])

    def test_remote_frontend_cannot_reset_or_create_grants_from_existing_automatic_draft(self):
        registry = self.workspace.operations
        state = {'project_id': self.pid, 'agent': {'config': {'mode': 'automatic', 'scope': self.scope()}, 'session_id': None}}
        browser = registry.sessions.connect(state)
        context = InvocationContext(source='external-agent', confirmed=True)
        for operation, parameters in (('ui.agent.set_config', {'model': 'replacement'}), ('ui.agent.send', {})):
            result = registry.invoke(operation, {'client_id': browser['client_id'], **parameters}, context)
            self.assertEqual(result['error']['code'], 'PERMISSION_DENIED', result)
        self.assertEqual(registry.sessions.poll(browser['client_id'], browser['token'], state)['commands'], [])


if __name__ == '__main__':
    unittest.main()
