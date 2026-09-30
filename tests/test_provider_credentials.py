import io
import json
import os
import stat
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from analog_factory.service import Workspace


class ProviderCredentialTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = Workspace(self.root)
        self.base = {'protocol': 'openai-compatible', 'base_url': 'https://example.invalid/v1',
                     'model': 'demo'}
        self.keyfile = self.root / '.credentials/provider.json'

    def save_key(self, key='private-api-key', storage='local'):
        return self.workspace.save_settings({'provider': {**self.base, 'api_key': key,
                                                         'api_key_storage': storage}})

    def test_local_key_survives_restart_in_private_file_without_public_leakage(self):
        result = self.save_key()
        self.assertEqual(result['provider']['api_key_storage'], 'local')
        self.assertTrue(result['provider']['api_key_configured'])
        restarted = Workspace(self.root)
        self.assertEqual(restarted.provider().secret, 'private-api-key')
        self.assertEqual(stat.S_IMODE(self.keyfile.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.keyfile.parent.stat().st_mode), 0o700)
        project = self.workspace.projects.create('Private credential export')
        self.workspace.configure_project(project['id'], {'provider': {'api_key': 'project-key',
                                                                      'model': 'other'}})
        public = [result, restarted.settings(), restarted.config(),
                  restarted.detail(project['id'])]
        self.assertNotIn('private-api-key', json.dumps(public))
        self.assertNotIn('project-key', json.dumps(public))
        self.assertNotIn('private-api-key', (self.root / 'settings.json').read_text())
        with zipfile.ZipFile(io.BytesIO(self.workspace.projects.export(project['id']))) as archive:
            self.assertFalse(any('.credentials' in name for name in archive.namelist()))
            self.assertTrue(all(b'private-api-key' not in archive.read(name) and
                                b'project-key' not in archive.read(name) for name in archive.namelist()))

    def test_session_key_defaults_to_memory_only(self):
        result = self.workspace.save_settings({'provider': {**self.base, 'api_key': 'transient-key'}})
        self.assertTrue(result['provider']['api_key_configured'])
        self.assertEqual(result['provider']['api_key_storage'], 'session')
        self.assertFalse(self.keyfile.exists())
        self.assertIsNone(Workspace(self.root).provider().secret)
        self.assertEqual(Workspace(self.root).settings()['provider']['api_key_storage'], 'none')

    def test_ui_and_model_updates_keep_key_and_storage_can_migrate_without_retyping(self):
        self.save_key()
        self.workspace.save_settings({'ui': {'language': 'zh-CN'}})
        self.workspace.save_settings({'provider': {'model': 'new-model'}})
        self.assertEqual(self.workspace.provider().secret, 'private-api-key')
        self.assertEqual(self.workspace.provider().model, 'new-model')
        self.workspace.save_settings({'provider': {'api_key_storage': 'session'}})
        self.assertEqual(self.workspace.provider().secret, 'private-api-key')
        self.assertFalse(self.keyfile.exists())
        self.assertIsNone(Workspace(self.root).provider().secret)
        self.workspace.save_settings({'provider': {'api_key_storage': 'local'}})
        self.assertEqual(Workspace(self.root).provider().secret, 'private-api-key')

    def test_clear_and_replace_remove_previous_key(self):
        self.save_key()
        self.save_key('replacement-key')
        self.assertNotIn('private-api-key', self.keyfile.read_text())
        self.assertEqual(Workspace(self.root).provider().secret, 'replacement-key')
        for clear in ({'clear_api_key': True}, {'api_key': ''}):
            self.save_key()
            result = self.workspace.save_settings({'provider': clear})
            self.assertFalse(result['provider']['api_key_configured'])
            self.assertEqual(result['provider']['api_key_storage'], 'none')
            self.assertFalse(self.keyfile.exists())
            self.assertIsNone(Workspace(self.root).provider().secret)

    def test_endpoint_binding_normalizes_url_but_clears_on_endpoint_or_protocol_change(self):
        self.save_key()
        self.workspace.save_settings({'provider': {'base_url': 'https://EXAMPLE.invalid:443/v1/'}})
        self.assertEqual(self.workspace.provider().secret, 'private-api-key')
        self.workspace.save_settings({'provider': {'base_url': 'https://elsewhere.invalid/v1'}})
        self.assertIsNone(self.workspace.provider().secret)
        self.assertFalse(self.keyfile.exists())
        self.workspace.save_settings({'provider': {'base_url': self.base['base_url']}})
        self.assertIsNone(self.workspace.provider().secret)
        self.save_key()
        self.workspace.save_settings({'provider': {'protocol': 'ollama'}})
        self.assertIsNone(self.workspace.provider().secret)
        self.assertFalse(self.keyfile.exists())

    def test_invalid_updates_preserve_configuration_and_credential(self):
        self.save_key()
        before_settings = (self.root / 'settings.json').read_bytes()
        before_key = self.keyfile.read_bytes()
        invalid = [{'api_key': value} for value in (None, 2, True, [], {}, 'bad\nkey', 'bad key', '密钥', 'x' * 4097)]
        invalid += [{'clear_api_key': value} for value in ('yes', 1, None)]
        invalid += [{'api_key_storage': value} for value in ('cloud', 'environment', None, [])]
        invalid += [{'api_key': 'new-key', 'clear_api_key': True}, {'model': []}, {'api_key_env': []},
                    {'timeout': 'nan'}, {'base_url': []}, {'name': {}}]
        for provider in invalid:
            with self.subTest(provider=provider), self.assertRaises(ValueError):
                self.workspace.save_settings({'provider': provider, 'pdk_root': '/do-not-change'})
            self.assertEqual((self.root / 'settings.json').read_bytes(), before_settings)
            self.assertEqual(self.keyfile.read_bytes(), before_key)
            self.assertEqual(self.workspace.provider().secret, 'private-api-key')

    def test_environment_fallback_is_reported_and_does_not_follow_changed_endpoint(self):
        with patch.dict(os.environ, {'AF_TEST_PROVIDER_KEY': 'environment-key'}):
            self.workspace.save_settings({'provider': {**self.base, 'api_key_env': 'AF_TEST_PROVIDER_KEY'}})
            self.assertEqual(self.workspace.settings()['provider']['api_key_storage'], 'environment')
            self.assertTrue(self.workspace.settings()['provider']['api_key_configured'])
            self.workspace.save_settings({'provider': {'base_url': 'https://elsewhere.invalid/v1'}})
            self.assertNotIn('api_key_env', self.workspace.config()['provider'])
            self.assertFalse(self.workspace.settings()['provider']['api_key_configured'])

    def test_settings_write_failure_rolls_back_replacement_clear_and_endpoint_changes(self):
        updates = [
            {'api_key': 'replacement-key', 'api_key_storage': 'local'},
            {'clear_api_key': True},
            {'api_key_storage': 'session'},
            {'base_url': 'https://other.invalid/v1'},
        ]
        for provider in updates:
            with self.subTest(provider=provider):
                self.save_key()
                before_settings = self.workspace.settings()
                before_file = self.keyfile.read_bytes()
                with patch('analog_factory.service.atomic_json', side_effect=OSError('Settings disk write failed')):
                    with self.assertRaisesRegex(OSError, 'Settings disk write failed'):
                        self.workspace.save_settings({'provider': provider, 'pdk_root': '/not-saved'})
                self.assertEqual(self.workspace.settings(), before_settings)
                self.assertEqual(self.workspace.provider().secret, 'private-api-key')
                self.assertTrue(self.keyfile.exists())
                self.assertEqual(self.keyfile.read_bytes(), before_file)
                restarted = Workspace(self.root)
                self.assertEqual(restarted.settings(), before_settings)
                self.assertEqual(restarted.provider().secret, 'private-api-key')
                self.assertEqual(restarted.provider().base_url, 'https://example.invalid/v1')
                self.assertEqual(list(self.keyfile.parent.iterdir()), [self.keyfile])

    def test_settings_write_failure_does_not_leave_new_local_key(self):
        self.workspace.save_settings({'provider': self.base})
        before_settings = self.workspace.settings()
        with patch('analog_factory.service.atomic_json', side_effect=OSError('Settings disk write failed')):
            with self.assertRaisesRegex(OSError, 'Settings disk write failed'):
                self.save_key()
        self.assertFalse(self.keyfile.exists())
        self.assertEqual(self.workspace.settings(), before_settings)
        self.assertEqual(Workspace(self.root).settings(), before_settings)
        self.assertIsNone(Workspace(self.root).provider().secret)

    def test_saved_key_is_sent_to_bound_endpoint_only_and_redirects_are_not_followed(self):
        received = []
        class Endpoint(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append((self.path, self.headers.get('Authorization')))
                if self.path == '/redirect/models':
                    self.send_response(302)
                    self.send_header('Location', '/untrusted/models')
                    self.end_headers()
                    return
                payload = b'{"data":[{"id":"demo"}]}'
                self.send_response(200)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        origin = f'http://127.0.0.1:{server.server_port}'
        self.base['base_url'] = origin + '/trusted'
        self.save_key()
        restarted = Workspace(self.root)
        self.assertEqual(restarted.provider_test()['models'], ['demo'])
        project = restarted.projects.create('Different endpoint')
        restarted.configure_project(project['id'], {'provider': {'base_url': origin + '/untrusted'}})
        restarted.provider(project['id']).models()
        self.assertEqual(received, [('/trusted/models', 'Bearer private-api-key'), ('/untrusted/models', None)])
        restarted.save_settings({'provider': {'base_url': origin + '/redirect', 'api_key': 'redirect-key'}})
        with self.assertRaisesRegex(ValueError, 'HTTP 302'):
            restarted.provider_test()
        self.assertEqual(received[-1], ('/redirect/models', 'Bearer redirect-key'))
        self.assertEqual(len(received), 3)


if __name__ == '__main__':
    unittest.main()
