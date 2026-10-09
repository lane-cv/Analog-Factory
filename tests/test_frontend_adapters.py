"""Registry ownership for non-browser frontend adapters; transport is a fixture."""
import tempfile
import unittest

from analog_factory.operations import InvocationContext, Operation, OperationError
from analog_factory.service import Workspace


class FrontendAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Workspace(self.temp.name)
        self.addCleanup(self.workspace.agents.close)
        self.addCleanup(self.workspace.operations.close)
        self.registry = self.workspace.operations
        self.pid = self.workspace.projects.create('Native target contract fixture')['id']
        self.other = self.workspace.projects.create('Other target')['id']
        self.targets = {'session-a': self.pid, 'session-b': self.other}
        self.calls = []

    def operation(self, *, resolver=None, handler=None, adapter='fixture-native'):
        def resolve(context, parameters):
            pid = self.targets.get(parameters.get('session_id'))
            if pid is None:
                raise OperationError('FRONTEND_DISCONNECTED', 'Native fixture session is disconnected')
            return {'state': {'project_id': pid}}
        def invoke(context, parameters):
            self.calls.append(parameters['session_id'])
            return {'state': {'project_id': self.targets[parameters['session_id']]}, 'acknowledged': True}
        return Operation('fixture.native_select', 'Select fixture native instance',
            'Contract test for acknowledged session ownership; no real native tool is claimed.',
            'fixture', {'type': 'object', 'properties': {
                'session_id': {'type': 'string'}}, 'required': ['session_id'], 'additionalProperties': False},
            {'type': 'object', 'properties': {
                'state': {'type': 'object', 'properties': {'project_id': {'type': 'string'}},
                          'required': ['project_id'], 'additionalProperties': False},
                'acknowledged': {'type': 'boolean'}},
             'required': ['state', 'acknowledged'], 'additionalProperties': False},
            handler or invoke, kind='frontend', permissions=('ui',),
            frontend_adapter=adapter, frontend_target=resolver or resolve)

    def context(self, pid=None, permissions=('ui',)):
        return InvocationContext(source='external-agent', project_id=pid,
                                 permissions=frozenset(permissions))

    def test_nonbrowser_session_discovery_and_invocation_require_real_target(self):
        self.registry.register(self.operation())
        description = self.registry.describe_operation('fixture.native_select',
            self.context(self.pid), {'session_id': 'session-a'})
        self.assertTrue(description['availability']['available'])
        self.assertEqual(description['frontend_adapter'], 'fixture-native')
        self.assertNotIn('frontend_target', description)
        result = self.registry.invoke('fixture.native_select', {'session_id': 'session-a'},
                                      context=self.context(self.pid))
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['data']['acknowledged'])
        self.assertEqual(self.calls, ['session-a'])

    def test_cross_project_disconnected_and_permission_denials_never_execute(self):
        self.registry.register(self.operation())
        for session, context, code in [
                ('session-b', self.context(self.pid), 'PERMISSION_DENIED'),
                ('missing', self.context(self.pid), 'FRONTEND_DISCONNECTED'),
                ('session-a', self.context(self.pid, ('read',)), 'PERMISSION_DENIED')]:
            with self.subTest(session=session, code=code):
                result = self.registry.invoke('fixture.native_select', {'session_id': session}, context=context)
                self.assertFalse(result['ok'], result)
                self.assertEqual(result['error']['code'], code)
        self.assertEqual(self.calls, [])

    def test_unbound_owner_still_requires_a_live_native_session(self):
        self.registry.register(self.operation())
        result = self.registry.invoke('fixture.native_select', {'session_id': 'missing'})
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'FRONTEND_DISCONNECTED')
        self.assertEqual(self.calls, [])

    def test_changed_project_after_ack_cannot_be_reported_as_success(self):
        def changed(context, parameters):
            return {'state': {'project_id': self.other}, 'acknowledged': True}
        self.registry.register(self.operation(handler=changed))
        result = self.registry.invoke('fixture.native_select', {'session_id': 'session-a'},
                                      context=self.context(self.pid))
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'PERMISSION_DENIED')

    def test_custom_adapter_cannot_register_without_target_resolver(self):
        from dataclasses import replace
        with self.assertRaisesRegex(ValueError, 'target'):
            self.registry.register(replace(self.operation(), frontend_target=None))
        with self.assertRaisesRegex(ValueError, 'frontend'):
            self.registry.register(replace(self.operation(), kind='business'))
        with self.assertRaisesRegex(ValueError, 'browser'):
            self.registry.register(replace(self.operation(), frontend_adapter='browser'))

    def test_missing_target_parameter_is_unavailable_in_every_discovery_path(self):
        resolver_calls = []
        def required_target(context, parameters):
            resolver_calls.append(parameters.copy())
            return {'state': {'project_id': self.targets[parameters['session_id']]}}
        self.registry.register(self.operation(resolver=required_target))
        descriptions = [
            lambda: self.registry.describe_operation('fixture.native_select'),
            lambda: self.registry.list_operations(module='fixture')[0],
            lambda: self.registry.list_operations_page(module='fixture')['operations'][0],
        ]
        for discover in descriptions:
            with self.subTest(discovery=discover):
                availability = discover()['availability']
                self.assertFalse(availability['available'])
                self.assertTrue(any('session_id' in reason and 'parameter' in reason
                                    for reason in availability['reasons']))
        self.assertEqual(len(resolver_calls), 3)
        result = self.registry.invoke('fixture.native_select', {})
        self.assertEqual(result['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(len(resolver_calls), 3, 'Invocation validates inputs before resolving a target')
        self.assertEqual(self.calls, [])

    def test_resolver_internal_missing_field_is_an_unavailable_error_not_missing_input(self):
        def broken_target(context, parameters):
            return {}['internal_target_state']
        self.registry.register(self.operation(resolver=broken_target))
        availability = self.registry.describe_operation('fixture.native_select',
            parameters={'session_id': 'session-a'})['availability']
        self.assertFalse(availability['available'])
        self.assertIn('Availability check failed', availability['reasons'][0])
        self.assertIn('internal_target_state', availability['reasons'][0])
        self.assertEqual(self.calls, [])

    def test_invalid_resolver_state_is_unavailable_and_never_invokes_handler(self):
        from dataclasses import replace
        for index, target in enumerate((None, [], {}, {'state': None}, {'state': []})):
            with self.subTest(target=target):
                operation = replace(self.operation(resolver=lambda c, p, target=target: target),
                                    operation_id=f'fixture.invalid_target_case{index}')
                self.registry.register(operation)
                availability = self.registry.describe_operation(operation.operation_id,
                    parameters={'session_id': 'session-a'})['availability']
                self.assertFalse(availability['available'])
                self.assertIn('invalid target state', availability['reasons'][0])
                result = self.registry.invoke(operation.operation_id, {'session_id': 'session-a'})
                self.assertFalse(result['ok'])
                self.assertEqual(result['error']['code'], 'INVALID_RESULT')
        self.assertEqual(self.calls, [])

    def test_invalid_availability_result_reports_contract_error_not_dictionary_keys(self):
        from dataclasses import replace
        for index, returned in enumerate(({'available': True, 'reasons': []}, 'ready', [1], False)):
            with self.subTest(returned=returned):
                operation = replace(self.operation(), operation_id=f'fixture.invalid_availability_case{index}',
                                    availability=lambda c, p, returned=returned: returned)
                self.registry.register(operation)
                availability = self.registry.describe_operation(operation.operation_id,
                    parameters={'session_id': 'session-a'})['availability']
                self.assertFalse(availability['available'])
                self.assertIn('Availability callback', availability['reasons'][0])
                self.assertNotEqual(availability['reasons'], ['available', 'reasons'])
        self.assertEqual(self.calls, [])

    def test_availability_callback_type_error_is_unavailable_without_execution(self):
        from dataclasses import replace
        def broken_availability(context, parameters):
            raise TypeError('Invalid target configuration')
        self.registry.register(replace(self.operation(), availability=broken_availability))
        availability = self.registry.describe_operation('fixture.native_select',
            parameters={'session_id': 'session-a'})['availability']
        self.assertFalse(availability['available'])
        self.assertIn('Invalid target configuration', availability['reasons'][0])
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
