"""A clean-environment replay must not mount or copy local user authority."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DeploymentTests(unittest.TestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location('validate_ubuntu24', ROOT / 'scripts/validate_ubuntu24.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_replay_copy_excludes_workspaces_credentials_and_outside_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'checkout'
            root.mkdir()
            for name in ('analog_factory', 'tests', '.workspace', '.codex', '.aws'):
                (root / name).mkdir()
            (root / 'analog_factory/core.py').write_text('core')
            (root / 'analog_factory/.env').write_text('secret')
            (root / '.aws/credentials').write_text('secret')
            (root / 'analog_factory/alias.py').symlink_to(root / '.aws/credentials')
            target = Path(temporary) / 'copy'
            manifest = self.module().copy_source(root, target)
            self.assertEqual((target / 'analog_factory/core.py').read_text(), 'core')
            for relative in ('.workspace', '.codex', '.aws', 'analog_factory/.env', 'analog_factory/alias.py'):
                self.assertFalse((target / relative).exists(), relative)
            self.assertEqual(set(manifest), {'analog_factory/core.py'})

    def test_runtime_has_no_network_or_privileged_host_mounts(self):
        command = self.module().runtime_command(Path('/source'), Path('/pdk'), Path('/output'), 'test-image')
        self.assertIn('--network=none', command)
        self.assertIn('--cap-drop=ALL', command)
        self.assertNotIn('--privileged', command)
        self.assertIn('/source:/app:ro', command)
        self.assertIn('/pdk:/pdk:ro', command)
        self.assertFalse(any('docker.sock' in part for part in command))


if __name__ == '__main__':
    unittest.main()
