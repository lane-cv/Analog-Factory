"""Shared source previews are complete evidence, never write authority."""
import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from analog_factory.operations import InvocationContext
from analog_factory.service import Workspace


class FileChangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Workspace(Path(self.tmp.name))
        self.addCleanup(self.workspace.agents.close)
        self.addCleanup(self.workspace.operations.close)
        self.pid = self.workspace.projects.create('Preview')['id']
        self.workspace.projects.write(self.pid, 'notes.txt', 'original\n')

    def preview(self, path='notes.txt', content='replacement\n', **extra):
        return self.workspace.operations.invoke('file.change.preview',
            {'project_id': self.pid, 'path': path, 'content': content, **extra},
            context=InvocationContext(source='agent', project_id=self.pid, permissions=frozenset({'read'})))

    def test_read_only_preview_is_complete_and_does_not_write(self):
        version = self.workspace.operations.version(self.pid)
        history = self.workspace.projects.history(self.pid, 'notes.txt')
        result = self.preview()
        self.assertTrue(result['ok'], result)
        data = result['data']
        self.assertEqual(data['current_content'], 'original\n')
        self.assertEqual(data['candidate_content'], 'replacement\n')
        self.assertEqual(data['current_sha256'], hashlib.sha256(b'original\n').hexdigest())
        self.assertEqual(data['candidate_sha256'], hashlib.sha256(b'replacement\n').hexdigest())
        self.assertEqual(data['project_version'], version)
        self.assertIn('-original\n+replacement\n', data['diff'])
        self.assertEqual(len(data['proposal_sha256']), 64)
        self.assertEqual(self.workspace.projects.read(self.pid, 'notes.txt')['content'], 'original\n')
        self.assertEqual(self.workspace.projects.history(self.pid, 'notes.txt'), history)

    def test_missing_stale_protected_and_noncanonical_paths_reject_before_preview(self):
        for path in ('missing.txt', '../notes.txt', '/tmp/notes.txt', '.af/jobs/x.json',
                     'project.json', 'settings.json', 'secrets.json', 'credentials.json',
                     'netlists//x.spice', './notes.txt', 'netlists\\x.spice', 'private.key'):
            with self.subTest(path=path):
                result = self.preview(path)
                self.assertFalse(result['ok'], result)
                self.assertIsNone(result['data'])
        result = self.preview(expected_sha256='0' * 64)
        self.assertFalse(result['ok'], result)

    def test_multibyte_bounds_nontext_symlinks_and_secret_json_reject(self):
        root = self.workspace.projects.path(self.pid)
        (root / 'link.txt').symlink_to(root / 'notes.txt')
        (root / 'binary.txt').write_bytes(b'hello\x00world')
        (root / 'secret-data.json').write_text('{"api_key":"private"}')
        for path in ('link.txt', 'binary.txt', 'secret-data.json'):
            self.assertFalse(self.preview(path)['ok'])
        self.assertFalse(self.preview(content='界' * 22000)['ok'])
        self.workspace.projects.write(self.pid, 'notes.txt', 'x' * 64001)
        self.assertFalse(self.preview()['ok'])

    def test_full_diff_contains_last_change_and_no_truncation(self):
        old = ''.join(f'old {i}\n' for i in range(3000))
        new = ''.join(f'new {i}\n' for i in range(3000))
        self.workspace.projects.write(self.pid, 'notes.txt', old)
        result = self.preview(content=new)
        self.assertTrue(result['ok'], result)
        self.assertIn('+new 2999\n', result['data']['diff'])
        self.assertNotIn('truncated', result['data']['diff'])

    def test_both_agent_write_paths_show_shared_complete_preview(self):
        import threading
        from analog_factory.agent_api import _execute
        for tool, arguments in (
                ('write_file', {'path': 'notes.txt', 'content': 'approved\n'}),
                ('invoke_operation', {'operation_id': 'file.write',
                                      'parameters': {'path': 'notes.txt', 'content': 'approved\n'}})):
            self.workspace.projects.write(self.pid, 'notes.txt', 'original\n')
            proposals = []
            def approve(name, value):
                proposals.append(value)
                return False
            _execute(tool, arguments, self.workspace, self.pid, 'confirm', lambda event: None,
                     threading.Event(), approve)
            self.assertIn('current_content', proposals[0])
            self.assertEqual(proposals[0]['current_content'], 'original\n')
            self.assertEqual(proposals[0]['candidate_content'], 'approved\n')
            self.assertIn('-original\n+approved\n', proposals[0]['diff'])
            self.assertEqual(self.workspace.projects.read(self.pid, 'notes.txt')['content'], 'original\n')

    def test_diff_marks_missing_trailing_newline_without_joining_changes(self):
        self.workspace.projects.write(self.pid, 'notes.txt', 'original')
        result = self.preview(content='replacement')
        self.assertTrue(result['ok'], result)
        self.assertIn('-original\n\\ No newline at end of file\n+replacement\n', result['data']['diff'])

    def test_scope_and_read_permission_are_required(self):
        other = self.workspace.projects.create('Other')['id']
        params = {'project_id': other, 'path': 'notes.txt', 'content': 'replacement'}
        result = self.workspace.operations.invoke('file.change.preview', params,
            context=InvocationContext(source='agent', project_id=self.pid))
        self.assertEqual(result['error']['code'], 'PERMISSION_DENIED')
        params['project_id'] = self.pid
        result = self.workspace.operations.invoke('file.change.preview', params,
            context=InvocationContext(permissions=frozenset({'execute'})))
        self.assertEqual(result['error']['code'], 'PERMISSION_DENIED')

    def test_complete_proposal_bound_never_returns_partial_evidence(self):
        self.workspace.projects.write(self.pid, 'notes.txt', '\t' * 60000)
        result = self.preview(content='x' * 60000)
        self.assertFalse(result['ok'])
        self.assertIn('256 KiB', result['error']['message'])
        self.assertIsNone(result['data'])

    def test_query_rejects_native_edit_between_read_and_version_capture(self):
        from unittest.mock import patch
        from analog_factory import file_changes
        original_read = file_changes._read
        fired = []
        def read(target, relative):
            result = original_read(target, relative)
            if not fired:
                fired.append(True)
                target.write_text('concurrent human text\n')
            return result
        with patch.object(file_changes, '_read', read):
            result = self.preview()
        self.assertFalse(result['ok'], result)
        self.assertIsNone(result['data'])
        self.assertEqual(self.workspace.projects.read(self.pid, 'notes.txt')['content'], 'concurrent human text\n')

    def test_both_agent_paths_reject_native_edit_between_read_and_version(self):
        import threading
        from unittest.mock import patch
        from analog_factory import file_changes
        from analog_factory.agent_api import _execute
        original_read = file_changes._read
        for tool, arguments in (
                ('write_file', {'path': 'notes.txt', 'content': 'agent replacement\n'}),
                ('invoke_operation', {'operation_id': 'file.write',
                                      'parameters': {'path': 'notes.txt', 'content': 'agent replacement\n'}})):
            with self.subTest(tool=tool):
                self.workspace.projects.write(self.pid, 'notes.txt', 'original\n')
                fired = []
                def read(target, relative):
                    result = original_read(target, relative)
                    if not fired:
                        fired.append(True)
                        target.write_text('concurrent human text\n')
                    return result
                with patch.object(file_changes, '_read', read):
                    try:
                        _execute(tool, arguments, self.workspace, self.pid, 'confirm', lambda event: None,
                                 threading.Event(), lambda name, proposal: True)
                    except ValueError:
                        pass
                self.assertEqual(self.workspace.projects.read(self.pid, 'notes.txt')['content'], 'concurrent human text\n')

    def test_both_agent_paths_recheck_private_identity_when_version_matches(self):
        import os
        import threading
        from analog_factory.agent_api import _execute
        target = self.workspace.projects.path(self.pid) / 'notes.txt'
        for tool, arguments in (
                ('write_file', {'path': 'notes.txt', 'content': 'agent replacement\n'}),
                ('invoke_operation', {'operation_id': 'file.write',
                                      'parameters': {'path': 'notes.txt', 'content': 'agent replacement\n'}})):
            with self.subTest(tool=tool):
                self.workspace.projects.write(self.pid, 'notes.txt', 'original\n')
                version = self.workspace.operations.version(self.pid)
                def approve(name, proposal):
                    info = target.stat()
                    target.write_text('original\n')
                    os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
                    self.assertEqual(self.workspace.operations.version(self.pid), version)
                    return True
                try:
                    _execute(tool, arguments, self.workspace, self.pid, 'confirm', lambda event: None,
                             threading.Event(), approve)
                except ValueError:
                    pass
                self.assertEqual(target.read_text(), 'original\n')

    def test_existing_file_read_returns_stable_source_version_and_preserves_large_json_reads(self):
        for path, content in (('large.txt', 'x' * (64000 + 1)),
                              ('readable.json', '{"token":"fixture-public-text"}')):
            self.workspace.projects.write(self.pid, path, content)
            version = self.workspace.operations.version(self.pid)
            result = self.workspace.operations.invoke('file.read', {'project_id': self.pid, 'path': path})
            self.assertTrue(result['ok'], result)
            self.assertIn('source_version', list(result['data']))
            self.assertEqual(result['data']['source_version'], version)
            self.assertEqual(result['data']['content'], content)
        self.assertTrue(self.workspace.operations.invoke('file.read',
            {'project_id': self.pid, 'path': 'project.json'})['ok'])

    def test_existing_file_read_rejects_native_content_and_aba_identity_races(self):
        import os
        from unittest.mock import patch
        real_read = self.workspace.projects.read
        target = self.workspace.projects.path(self.pid) / 'notes.txt'
        for race in ('text', 'same_bytes_restored_mtime'):
            self.workspace.projects.write(self.pid, 'notes.txt', 'original\n')
            def read(pid, path):
                value = real_read(pid, path)
                info = target.stat()
                target.write_text('concurrent human text\n' if race == 'text' else 'original\n')
                if race != 'text': os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
                return value
            with patch.object(self.workspace.projects, 'read', read):
                result = self.workspace.operations.invoke('file.read', {'project_id': self.pid, 'path': 'notes.txt'})
            self.assertFalse(result['ok'], result)
            self.assertEqual(result['error']['code'], 'VERSION_CONFLICT')
            self.assertIsNone(result['data'])

    def test_later_envelope_version_does_not_rebind_returned_file_content(self):
        registry = self.workspace.operations
        operation = registry.operations['file.read']
        original_version = registry.version(self.pid)
        target = self.workspace.projects.path(self.pid) / 'notes.txt'
        def read(context, params):
            value = operation.handler(context, params)
            target.write_text('later native edit\n')
            return value
        registry.operations['file.read'] = replace(operation, handler=read)
        result = registry.invoke('file.read', {'project_id': self.pid, 'path': 'notes.txt'})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['data']['content'], 'original\n')
        self.assertIn('source_version', result['data'])
        self.assertEqual(result['data']['source_version'], original_version)
        self.assertNotEqual(result['version'], result['data']['source_version'])
