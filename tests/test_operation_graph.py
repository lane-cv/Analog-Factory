import importlib
import tempfile
import time
import unittest
from pathlib import Path

from analog_factory.operations import InvocationContext
from analog_factory.service import Workspace
from tests.test_workflow_graph import graph, node


class GraphOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Workspace(Path(self.tmp.name))
        self.addCleanup(self.workspace.agents.close)
        self.pid = self.workspace.projects.create('Graph operations')['id']
        self.root = self.workspace.projects.path(self.pid)
        self.registry = self.workspace.operations
        self.addCleanup(self.registry.close)

    def wait_state(self, job_id, field):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            record = self.workspace.jobs.get(self.root, job_id)
            if record.get(field):
                return record
            time.sleep(.01)
        self.fail(f'Job did not expose {field}')

    def test_catalog_is_read_only_and_v2_save_uses_existing_version_contract(self):
        readonly = InvocationContext(source='agent', permissions=frozenset({'read'}))
        catalog = self.registry.invoke('workflow.catalog', {}, context=readonly)
        self.assertTrue(catalog['ok'], catalog)
        self.assertIn('human_review', [item['type'] for item in catalog['data']['node_types']])
        flow = graph([node('p', 'parameter', {'value': 1})])
        params = {'project_id': self.pid, 'workflow': flow}
        missing = self.registry.invoke('workflow.save', params)
        self.assertEqual(missing['error']['code'], 'VERSION_REQUIRED')
        saved = self.registry.invoke('workflow.save', params, expected_version=self.registry.version(self.pid))
        self.assertTrue(saved['ok'], saved)
        self.assertIn('"schema_version": 2', self.workspace.projects.read(self.pid, saved['data']['path'])['content'])

    def test_owner_can_step_and_review_and_agent_cannot_approve_itself(self):
        start = importlib.import_module('analog_factory.operation_graph').start_graph
        job = start(self.workspace, self.pid, graph([node('review', 'human_review')], debug={'step': True}))
        self.addCleanup(lambda: self.workspace.jobs.cancel(self.root, job['id']))
        self.wait_state(job['id'], 'debug_state')
        params = {'project_id': self.pid, 'job_id': job['id'], 'action': 'step'}
        denied = self.registry.invoke('job.control', params, context=InvocationContext(source='agent', project_id=self.pid))
        self.assertEqual(denied['error']['code'], 'CONFIRMATION_REQUIRED')
        stepped = self.registry.invoke('job.control', params)
        self.assertTrue(stepped['ok'], stepped)
        self.wait_state(job['id'], 'pending_review')
        params.update(action='review', node_id='review', approved=True)
        denied = self.registry.invoke('job.control', params, context=InvocationContext(
            source='external-agent', project_id=self.pid, confirmed=True))
        self.assertEqual(denied['error']['code'], 'PERMISSION_DENIED')
        denied = self.registry.invoke('job.control', params, context=InvocationContext(
            permissions=frozenset({'execute'})))
        self.assertEqual(denied['error']['code'], 'PERMISSION_DENIED')
        approved = self.registry.invoke('job.control', params)
        self.assertTrue(approved['ok'], approved)
        result = self.workspace.jobs.wait(self.root, job['id'], 5)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertTrue(result['nodes']['review']['outputs']['approved'])
        self.assertNotIn((self.pid, job['id']), self.workspace._graph_controls)


if __name__ == '__main__':
    unittest.main()
