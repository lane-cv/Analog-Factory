"""Local installer contracts; executable fixtures never contact a registry."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/setup_agent_clis.py'


class SetupAgentClisTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'Project-local CLI installer is not implemented')
        spec = importlib.util.spec_from_file_location('setup_agent_clis', SCRIPT)
        self.installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.installer)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.runtime = self.base / 'runtime/bin'
        self.runtime.mkdir(parents=True)
        self.directory = self.base / 'agent tools'
        self.node = self.runtime / 'node'
        self.make_node('v22.14.0')
        self.make_npm()

    def executable(self, path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#!' + sys.executable + '\n' + body)
        path.chmod(0o755)

    def make_node(self, version):
        self.executable(self.node, 'import subprocess, sys\n'
                        'if sys.argv[1:] == ["--version"]:\n'
                        f'    print({version!r})\n'
                        'else:\n'
                        '    raise SystemExit(subprocess.call([sys.executable, *sys.argv[1:]]))\n')

    def make_npm(self, *, exit_code=0, codex_version='0.159.2', claude_version='2.1.280'):
        self.executable(self.runtime / 'npm', f'''import json, os, pathlib, sys
if sys.argv[1:] == ['--version']:
    cache = pathlib.Path(os.environ['npm_config_cache'])
    assert pathlib.Path.cwd() == cache.parent
    userconfig = pathlib.Path(os.environ['npm_config_userconfig'])
    globalconfig = pathlib.Path(os.environ['npm_config_globalconfig'])
    assert userconfig != globalconfig
    assert userconfig.read_bytes() == globalconfig.read_bytes() == b''
    cache.mkdir(exist_ok=True)
    print('10.9.2')
    raise SystemExit(0)
args = sys.argv[1:]
directory = pathlib.Path(args[args.index('--prefix') + 1])
assert os.environ['npm_config_cache'] == str(directory / '.npm-cache')
assert os.environ['npm_config_userconfig'] == str(directory / '.npm-userconfig')
assert os.environ['npm_config_globalconfig'] == str(directory / '.npm-globalconfig')
assert pathlib.Path(os.environ['PATH'].split(os.pathsep)[0]) == pathlib.Path({str(self.runtime)!r})
log = directory / 'fixture-install-calls.json'
calls = json.loads(log.read_text()) if log.exists() else []
log.write_text(json.dumps(calls + [args]))
if {exit_code}:
    print('fixture-sensitive-npm-output', file=sys.stderr)
    raise SystemExit({exit_code})
for name, version in [('codex', {codex_version!r}), ('claude', {claude_version!r})]:
    target = directory / 'node_modules/.bin' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    output = 'codex-cli ' + version if name == 'codex' else version + ' (Claude Code)'
    target.write_text('#!' + sys.executable + '\\nimport os\\n'
                      + 'assert os.environ["PATH"].split(os.pathsep)[0] == '
                      + repr({str(self.runtime)!r}) + '\\nprint(' + repr(output) + ')\\n')
    target.chmod(0o755)
''')

    def install(self):
        return self.installer.install(directory=self.directory, node=str(self.node))

    def test_installs_pinned_packages_locally_and_records_verified_executables(self):
        manifest = self.install()
        self.assertEqual(manifest['packages'], {'@openai/codex': '0.159.2',
                                                '@anthropic-ai/claude-code': '2.1.280'})
        self.assertEqual(manifest['node'], {'path': str(self.node), 'version': '22.14.0'})
        self.assertEqual(manifest['npm'], {'path': str(self.runtime / 'npm'), 'version': '10.9.2'})
        self.assertEqual(manifest['clis']['codex']['version'], '0.159.2')
        self.assertEqual(manifest['clis']['claude']['version'], '2.1.280')
        for name in ('codex', 'claude'):
            self.assertEqual(manifest['clis'][name]['path'],
                             str(self.directory / 'bin' / name))
        self.assertEqual(json.loads((self.directory / 'installation.json').read_text()), manifest)
        self.assertEqual(json.loads((self.directory / 'fixture-install-calls.json').read_text()), [[
            'install', '--prefix', str(self.directory), '--no-audit', '--no-fund', '--save-exact',
            '@openai/codex@0.159.2', '@anthropic-ai/claude-code@2.1.280']])

    def test_unsupported_node_fails_before_creating_installation(self):
        self.make_node('v20.19.0')
        with self.assertRaisesRegex(RuntimeError, 'Node.js 22'):
            self.install()
        self.assertFalse(self.directory.exists())

    def test_missing_node_explains_how_to_select_node22(self):
        with self.assertRaisesRegex(RuntimeError, r'Node.js 22.*--node'):
            self.installer.install(directory=self.directory, node=str(self.base / 'missing-node'))
        self.assertFalse(self.directory.exists())

    def test_missing_npm_fails_before_creating_installation(self):
        (self.runtime / 'npm').unlink()
        with patch.dict(os.environ, {'PATH': str(self.runtime)}):
            with self.assertRaisesRegex(RuntimeError, 'npm'):
                self.install()
        self.assertFalse(self.directory.exists())

    def test_unknown_existing_directory_is_never_overwritten(self):
        self.directory.mkdir()
        existing = self.directory / 'valuable.txt'
        existing.write_text('preserve me')
        with self.assertRaisesRegex(RuntimeError, 'existing|Existing'):
            self.install()
        self.assertEqual(existing.read_text(), 'preserve me')
        self.assertEqual(sorted(path.name for path in self.directory.iterdir()), ['valuable.txt'])

    def test_failed_npm_install_never_publishes_success_manifest(self):
        self.make_npm(exit_code=2)
        with self.assertRaisesRegex(RuntimeError, 'npm installation failed') as error:
            self.install()
        self.assertNotIn('fixture-sensitive-npm-output', str(error.exception))
        self.assertFalse((self.directory / 'installation.json').exists())

    def test_wrong_real_cli_version_never_publishes_success_manifest(self):
        self.make_npm(codex_version='0.159.1')
        with self.assertRaisesRegex(RuntimeError, 'codex.*0.159.2'):
            self.install()
        self.assertFalse((self.directory / 'installation.json').exists())

    def test_verified_installation_is_reused_without_npm_install_or_manifest_rewrite(self):
        initial = self.install()
        marker = self.directory / 'installation.json'
        timestamp = marker.stat().st_mtime_ns
        reused = self.install()
        self.assertEqual(reused, initial)
        self.assertEqual(marker.stat().st_mtime_ns, timestamp)
        self.assertEqual(len(json.loads((self.directory / 'fixture-install-calls.json').read_text())), 1)

    def test_reuse_checks_actual_cli_instead_of_trusting_manifest(self):
        self.install()
        self.executable(self.directory / 'node_modules/.bin/claude', 'print("2.0.0 (Claude Code)")\n')
        with self.assertRaisesRegex(RuntimeError, 'claude.*2.1.280'):
            self.install()
        self.assertEqual(len(json.loads((self.directory / 'fixture-install-calls.json').read_text())), 1)

    def test_unrecognized_manifest_does_not_authorize_overwrite(self):
        self.directory.mkdir()
        marker = self.directory / 'installation.json'
        marker.write_text('{"unrelated": true}\n')
        with self.assertRaisesRegex(RuntimeError, 'manifest'):
            self.install()
        self.assertEqual(marker.read_text(), '{"unrelated": true}\n')

    def test_symlink_installation_directory_is_rejected(self):
        outside = self.base / 'another-installation'
        outside.mkdir()
        self.directory.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            self.install()
        self.assertEqual(list(outside.iterdir()), [])

    def test_manifest_does_not_store_environment_or_unstructured_tool_output(self):
        with patch.dict(os.environ, {'TEST_INSTALLER_SECRET': 'do-not-save-this-value',
                                    'NPM_CONFIG_USERCONFIG': '/private/user/config'}):
            manifest = self.install()
        self.assertNotIn('do-not-save-this-value', json.dumps(manifest))
        self.assertNotIn('/private/user/config', json.dumps(manifest))

    def test_command_line_reports_verified_installation(self):
        result = subprocess.run([sys.executable, str(SCRIPT), '--directory', str(self.directory),
                                 '--node', str(self.node)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['clis']['codex']['version'], '0.159.2')

    def test_installed_launchers_retain_selected_node_with_an_unrelated_path(self):
        manifest = self.install()
        for name in ('codex', 'claude'):
            result = subprocess.run([manifest['clis'][name]['path'], '--version'],
                                    env={**os.environ, 'PATH': os.defpath},
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(manifest['clis'][name]['version'], result.stdout)

    def test_modified_local_npm_configuration_is_rejected_without_overwriting(self):
        self.install()
        config = self.directory / '.npm-userconfig'
        config.write_text('custom-configuration\n')
        with self.assertRaisesRegex(RuntimeError, 'npm configuration'):
            self.install()
        self.assertEqual(config.read_text(), 'custom-configuration\n')


if __name__ == '__main__':
    unittest.main()
