import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/install_ubuntu.py'


class UbuntuInstallerTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('af_installer', SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def args(self, *values):
        return self.module.parser(self.root).parse_args(values)

    def test_dry_run_has_no_mutations(self):
        args = self.args('--dry-run', '--profile', 'desktop', '--with-agents')
        plan = self.module.make_plan(args, self.root)
        self.assertIn('ngspice', plan['apt_packages'])
        self.assertIn('novnc', plan['apt_packages'])
        self.assertTrue(plan['with_agents'])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_core_excludes_eda_and_desktop(self):
        plan = self.module.make_plan(self.args('--profile', 'core'), self.root)
        self.assertNotIn('ngspice', plan['apt_packages'])
        self.assertNotIn('novnc', plan['apt_packages'])
        self.assertFalse(plan['desktop'])

    def test_invalid_bind_and_ports_rejected_before_mutation(self):
        for flags in [('--host', '0.0.0.0'), ('--port', '0'), ('--jobs', '0'),
                      ('--desktop-port', '65536')]:
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                self.module.make_plan(self.args(*flags), self.root)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_unknown_nonempty_workspace_cannot_be_adopted(self):
        workspace = self.root / '.workspace'
        workspace.mkdir()
        (workspace / 'settings.json').write_text('{"ui":{"language":"zh-CN"}}')
        plan = self.module.make_plan(self.args(), self.root)
        with self.assertRaisesRegex(ValueError, 'existing workspace'):
            self.module.check_destination(plan, self.root)
        self.assertFalse((self.root / '.tools').exists())

    def test_bootstrap_uses_registry_and_preserves_repeat_user_edits(self):
        workspace = self.root / 'workspace'
        patch_values = {'tools': {'ngspice': '/test/ngspice'}, 'ui': {'language': 'zh-CN'}}
        self.module.bootstrap_workspace(workspace, patch_values)
        data = json.loads((workspace / 'settings.json').read_text())
        self.assertEqual(data['tools'], patch_values['tools'])
        data['ui']['theme'] = 'light'
        (workspace / 'settings.json').write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'empty'):
            self.module.bootstrap_workspace(workspace, {'ui': {'theme': 'dark'}})
        self.assertEqual(json.loads((workspace / 'settings.json').read_text())['ui']['theme'], 'light')

    def test_bad_pdk_and_missing_netgen_explicit(self):
        with self.assertRaisesRegex(ValueError, 'PDK'):
            self.module.validate_pdk(self.root)
        with patch.object(self.module.shutil, 'which', return_value=None), \
                patch.object(self.module.os, 'access', return_value=False):
            with self.assertRaisesRegex(ValueError, 'netgen'):
                self.module.find_netgen()

    def test_plan_rejects_unknown_platform(self):
        with patch.object(self.module.platform, 'system', return_value='Darwin'):
            with self.assertRaisesRegex(ValueError, 'Linux'):
                self.module.check_platform()

    def test_resume_belongs_to_checkout_and_workspace(self):
        plan = self.module.make_plan(self.args(), self.root)
        marker = self.root / '.tools/deployment/installation.json'
        marker.parent.mkdir(parents=True)
        marker.write_text(json.dumps({'schema_version': 1, 'root': '/elsewhere',
                                     'workspace': plan['workspace']}))
        with self.assertRaisesRegex(ValueError, 'another'):
            self.module.check_destination(plan, self.root)

    def test_completed_outputs_must_exist_and_target_same_workspace(self):
        plan = self.module.make_plan(self.args('--profile', 'core'), self.root)
        marker = self.root / '.tools/deployment/installation.json'
        marker.parent.mkdir(parents=True)
        record = {'schema_version': 1, 'root': str(self.root), 'workspace': plan['workspace'],
                  'plan': plan, 'phases': ['workspace', 'configuration'], 'complete': True}
        marker.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'settings are missing'):
            self.module.check_destination(plan, self.root)
        workspace = Path(plan['workspace']); workspace.mkdir()
        (workspace / 'settings.json').write_text('{}')
        with self.assertRaises(FileNotFoundError):
            self.module.check_destination(plan, self.root)
        config = {k: plan[k] for k in ('schema_version', 'workspace', 'python')}
        config['workspace'] = str(self.root / 'different')
        (marker.parent / 'config.json').write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, 'another workspace'):
            self.module.check_destination(plan, self.root)
        config['workspace'] = plan['workspace']
        config['port'] = 9876  # User edits are preserved, not reset to initial plan.
        (marker.parent / 'config.json').write_text(json.dumps(config))
        self.assertEqual(self.module.check_destination(plan, self.root), record)

    def test_selected_training_must_have_verified_outputs_on_every_install(self):
        with self.assertRaisesRegex(ValueError, 'Training'):
            self.module.validate_training(self.root)
        training = self.root / '.tools/training'
        for name in ('venv/bin/python', 'model/model.safetensors', 'model/config.json',
                     'model/tokenizer.json', 'model-source.json', 'runtime.json'):
            path = training / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Training model'):
            self.module.validate_training(self.root)


if __name__ == '__main__':
    unittest.main()
