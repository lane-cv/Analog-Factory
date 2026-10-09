"""Public project detail uses the same effective configuration as execution."""
from pathlib import Path
import tempfile
import unittest
from analog_factory.service import Workspace


class ProjectConfigurationViewTests(unittest.TestCase):
    def test_different_model_and_endpoint_invalidate_inherited_capabilities_without_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace=Workspace(Path(temporary))
            self.addCleanup(workspace.agents.close)
            workspace.save_settings({'provider':{'base_url':'http://localhost:1/v1','model':'workspace-model',
                'api_key':'fixture-only-secret','api_key_env':'FIXTURE_CREDENTIAL_NAME',
                'capabilities':{'images':True,'streaming':True,'tools':True,'generation_parameters':['temperature']},
                'model_capabilities':{'catalog-model':{'images':True}}}})
            project=workspace.projects.create('Effective configuration');pid=project['id']
            workspace.configure_project(pid,{'provider':{'model':'different-model'}})
            response=workspace.operations.invoke('project.detail',{'project_id':pid})
            self.assertTrue(response['ok'],response)
            effective=response['data']['configuration']['provider']
            self.assertNotIn('capabilities',effective)
            self.assertIn('model_capabilities',effective)
            self.assertNotIn('fixture-only-secret',str(response))
            self.assertNotIn('api_key',effective)
            workspace.configure_project(pid,{'provider':{'base_url':'http://localhost:2/v1','model':'different-model'}})
            effective=workspace.operations.invoke('project.detail',{'project_id':pid})['data']['configuration']['provider']
            self.assertNotIn('capabilities',effective)
            self.assertNotIn('model_capabilities',effective)
            self.assertNotIn('api_key_env',effective)
            workspace.operations.close()


if __name__=='__main__':unittest.main()
