"""Real filesystem lifecycle, archive safety and shared operation policy."""
import base64
import hashlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import warnings
import zipfile
from pathlib import Path

from analog_factory.projects import ProjectStore
from analog_factory.storage import atomic_json


def archive_bytes(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries:
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore', message='Duplicate name:', category=UserWarning)
                archive.writestr(name, value)
    return output.getvalue()


def backup_bytes(entries):
    from analog_factory.project_lifecycle import BACKUP_FORMAT, BACKUP_MANIFEST
    values = [(name, value.encode() if isinstance(value, str) else value) for name, value in entries]
    manifest = {'format': BACKUP_FORMAT, 'schema_version': 1,
                'files': {name: hashlib.sha256(value).hexdigest() for name, value in values}}
    return archive_bytes(values + [(BACKUP_MANIFEST, json.dumps(manifest))])


class ProjectLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProjectStore(Path(self.tmp.name))
        self.pid = self.store.create('Original')['id']
        self.assertIsNotNone(importlib.util.find_spec('analog_factory.project_lifecycle'),
                             'The lifecycle service must exist')
        from analog_factory.project_lifecycle import ProjectLifecycle
        self.lifecycle = ProjectLifecycle(self.store)

    def test_rename_and_project_trash_restore_keep_identity_and_content(self):
        self.store.write(self.pid, 'notes.txt', 'one')
        self.store.write(self.pid, 'notes.txt', 'two')
        renamed = self.lifecycle.rename_project(self.pid, '  Reviewed design  ')
        self.assertEqual((renamed['name'], renamed['id']), ('Reviewed design', self.pid))
        trashed = self.lifecycle.trash_project(self.pid)
        self.assertTrue(trashed['trashed_at'])
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.lifecycle.trashed_projects()[0]['id'], self.pid)
        restored = self.lifecycle.restore_project(self.pid)
        self.assertNotIn('trashed_at', restored)
        self.assertEqual(self.store.list()[0]['id'], self.pid)
        self.assertEqual(self.store.read(self.pid, 'notes.txt')['content'], 'two')
        self.assertEqual(len(self.store.history(self.pid, 'notes.txt')), 1)

    def test_move_carries_history_and_restores_old_revision_at_new_path(self):
        self.store.write(self.pid, 'notes.txt', 'first')
        self.store.write(self.pid, 'notes.txt', 'second')
        revision = self.store.history(self.pid, 'notes.txt')[0]['id']
        result = self.lifecycle.move_file(self.pid, 'notes.txt', 'docs/review.txt')
        self.assertEqual(result['path'], 'docs/review.txt')
        self.assertFalse((self.store.path(self.pid) / 'notes.txt').exists())
        history = self.store.history(self.pid, 'docs/review.txt')
        self.assertEqual(history[0]['id'], revision)
        self.assertEqual(history[0]['path'], 'docs/review.txt')
        self.store.restore(self.pid, 'docs/review.txt', revision)
        self.assertEqual(self.store.read(self.pid, 'docs/review.txt')['content'], 'first')

    def test_file_trash_and_conflict_restore_preserve_both_histories(self):
        self.store.write(self.pid, 'notes.txt', 'first')
        self.store.write(self.pid, 'notes.txt', 'second')
        trash = self.lifecycle.trash_file(self.pid, 'notes.txt')
        self.assertFalse((self.store.path(self.pid) / 'notes.txt').exists())
        self.assertEqual(self.lifecycle.trashed_files(self.pid)[0]['id'], trash['id'])
        self.store.write(self.pid, 'notes.txt', 'new file')
        with self.assertRaises(FileExistsError):
            self.lifecycle.restore_file(self.pid, trash['id'])
        self.assertEqual(self.lifecycle.trashed_files(self.pid)[0]['id'], trash['id'])
        result = self.lifecycle.restore_file(self.pid, trash['id'], 'recovered.txt')
        self.assertEqual(result['path'], 'recovered.txt')
        self.assertEqual(self.store.read(self.pid, 'notes.txt')['content'], 'new file')
        self.assertEqual(self.store.read(self.pid, 'recovered.txt')['content'], 'second')
        old = self.store.history(self.pid, 'recovered.txt')[0]['id']
        self.store.restore(self.pid, 'recovered.txt', old)
        self.assertEqual(self.store.read(self.pid, 'recovered.txt')['content'], 'first')
        self.assertEqual(self.lifecycle.trashed_files(self.pid), [])

    def test_move_rejects_conflicts_internal_paths_and_symlinks_without_loss(self):
        root = self.store.path(self.pid)
        self.store.write(self.pid, 'a.txt', 'a')
        self.store.write(self.pid, 'b.txt', 'b')
        (root / 'linked').symlink_to(root / 'a.txt')
        for source, destination in [('a.txt', 'b.txt'), ('project.json', 'x'),
                                    ('a.txt', './project.json'), ('a.txt', '../escape'),
                                    ('linked', 'x'), ('a.txt', '.af/hidden'),
                                    ('a.txt', 'a.txt/child')]:
            with self.subTest(source=source, destination=destination):
                with self.assertRaises((ValueError, FileExistsError, NotADirectoryError)):
                    self.lifecycle.move_file(self.pid, source, destination)
        self.assertEqual(self.store.read(self.pid, 'a.txt')['content'], 'a')
        self.assertEqual(self.store.read(self.pid, 'b.txt')['content'], 'b')

    def test_export_import_roundtrip_preserves_sources_with_new_identity(self):
        self.store.write(self.pid, 'notes.txt', 'portable content')
        restored = self.lifecycle.import_archive(self.store.export(self.pid), name='Imported')
        self.assertNotEqual(restored['id'], self.pid)
        self.assertEqual(restored['name'], 'Imported')
        self.assertEqual(self.store.read(restored['id'], 'notes.txt')['content'], 'portable content')
        self.assertEqual(len(self.store.list()), 2)

    def test_import_validates_design_roles_before_publication(self):
        metadata = self.store.get(self.pid)
        invalid = [None, [], {'cell': 'ota5\nexec evil'}, {'cell': 'ota5\n'}, {'ports': ['in', 'in']},
                   {'qualified': True}, {'command': ['/bin/true']}, {'owner_identity': 'forged'},
                   *({'layout': path} for path in ('../outside.mag', '/tmp/outside.mag',
                      'layout/../outside.mag', '.af/runs/a/output.mag', 'layout\\file.mag',
                      'layout//file.mag', 'project.json', 'directory'))]
        for design in invalid:
            with self.subTest(design=design):
                blob = archive_bytes([('project.json', json.dumps({**metadata, 'design': design})),
                                      ('directory/child', 'source')])
                with self.assertRaises(ValueError):
                    self.lifecycle.import_archive(blob)
                self.assertEqual(len(self.store.list()), 1)

    def test_backup_roundtrip_preserves_history_trash_evidence_and_sessions(self):
        self.store.write(self.pid, 'notes.txt', 'original')
        self.store.write(self.pid, 'notes.txt', 'revised')
        self.store.write(self.pid, 'removed.txt', 'recover me')
        trash = self.lifecycle.trash_file(self.pid, 'removed.txt')
        root = self.store.path(self.pid)
        run_id = 'a' * 32
        atomic_json(root / f'.af/runs/{run_id}/manifest.json',
                    {'id': run_id, 'kind': 'simulate', 'status': 'succeeded', 'metrics': {'gain': 12.5},
                     'created_at': '2026-10-08T00:00:00+00:00'})
        session_id = 'b' * 32
        atomic_json(root / f'.af/agent-sessions/{session_id}.json',
                    {'id': session_id, 'backend': 'codex', 'status': 'idle', 'turns': [],
                     'title': 'Design review', 'backend_session_id': 'native-private-session',
                     'created_at': '2026-10-08T00:00:00+00:00', 'updated_at': '2026-10-08T00:00:00+00:00'})
        secret = root / '.af/credentials.json'
        secret.write_text('{"api_key":"do-not-back-up"}')
        backup = self.lifecycle.backup(self.pid)
        self.assertNotIn(b'do-not-back-up', backup)
        with zipfile.ZipFile(io.BytesIO(backup)) as archive:
            self.assertNotIn('.af/credentials.json', archive.namelist())
            self.assertNotIn(b'native-private-session', archive.read(f'.af/agent-sessions/{session_id}.json'))
            self.assertIn('.af/history/' + hashlib.sha256(b'notes.txt').hexdigest() + '/',
                          '\n'.join(archive.namelist()))
        restored = self.lifecycle.import_archive(backup)
        pid = restored['id']
        revision = self.store.history(pid, 'notes.txt')[0]['id']
        self.store.restore(pid, 'notes.txt', revision)
        self.assertEqual(self.store.read(pid, 'notes.txt')['content'], 'original')
        self.lifecycle.restore_file(pid, trash['id'])
        self.assertEqual(self.store.read(pid, 'removed.txt')['content'], 'recover me')
        newroot = self.store.path(pid)
        evidence = json.loads((newroot / f'.af/runs/{run_id}/manifest.json').read_text())
        self.assertEqual(evidence['metrics']['gain'], 12.5)
        session = json.loads((newroot / f'.af/agent-sessions/{session_id}.json').read_text())
        self.assertEqual(session['title'], 'Design review')
        self.assertIsNone(session['backend_session_id'])

    def test_archive_rejection_never_publishes_partial_project(self):
        metadata = json.dumps(self.store.get(self.pid))
        bad_paths = ['../escape', '/absolute', 'a/../../escape', 'a\\..\\escape',
                     'C:/escape', '.env', '.af/credentials.json', '.git/config',
                     'good/.secret', './alias.txt', 'a//b', 'credentials.json']
        for path in bad_paths:
            with self.subTest(path=path):
                blob = archive_bytes([('project.json', metadata), ('valid.txt', 'valid'), (path, 'bad')])
                with self.assertRaises(ValueError):
                    self.lifecycle.import_archive(blob)
                self.assertEqual(len(self.store.list()), 1)
        self.assertFalse((Path(self.tmp.name) / 'escape').exists())

    def test_duplicate_case_and_directory_conflicts_are_rejected(self):
        for entries in [[('x', 'a'), ('x', 'b')], [('A.txt', 'a'), ('a.txt', 'b')],
                        [('a', 'file'), ('a/b', 'child')], [('a/b', 'child'), ('a', 'file')]]:
            with self.subTest(entries=entries):
                with self.assertRaises(ValueError):
                    self.lifecycle.import_archive(archive_bytes(entries))
                self.assertEqual(len(self.store.list()), 1)

    def test_symlink_archive_and_bad_zip_are_rejected(self):
        link = zipfile.ZipInfo('linked')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for blob in (archive_bytes([(link, '../../outside')]), b'not a zip'):
            with self.assertRaises(ValueError):
                self.lifecycle.import_archive(blob)
        self.assertEqual(len(self.store.list()), 1)

    def test_decompression_and_entry_count_limits_are_enforced(self):
        from analog_factory.project_lifecycle import ArchiveLimits, ProjectLifecycle
        limited = ProjectLifecycle(self.store, limits=ArchiveLimits(
            compressed_bytes=2048, expanded_bytes=64, file_bytes=32, entries=3, ratio=10))
        blobs = [archive_bytes([('large.txt', 'x' * 33)]),
                 archive_bytes([(str(i), 'x') for i in range(4)]),
                 archive_bytes([(str(i), 'x' * 24) for i in range(3)]),
                 archive_bytes([('ratio.txt', 'x' * 1000)])]
        for blob in blobs:
            with self.subTest(size=len(blob)):
                with self.assertRaises(ValueError):
                    limited.import_archive(blob)
        self.assertEqual(len(self.store.list()), 1)

    def test_import_metadata_credentials_and_nonfinite_json_are_rejected(self):
        records = [dict(self.store.get(self.pid), config={'provider': {'api_key': 'secret'}}),
                   {'name': 'Invalid', 'config': {'timeout': float('nan')}},
                   {'name': '', 'config': {}}, {'name': 'Bad', 'config': []}]
        for record in records:
            with self.subTest(record=record):
                with self.assertRaises(ValueError):
                    self.lifecycle.import_archive(archive_bytes([('project.json', json.dumps(record))]))
        self.assertEqual(len(self.store.list()), 1)

    def test_backup_tampering_and_unknown_hidden_records_are_rejected(self):
        backup = self.lifecycle.backup(self.pid)
        with zipfile.ZipFile(io.BytesIO(backup)) as archive:
            entries = [(info.filename, archive.read(info)) for info in archive.infolist()]
        for replacement in [('netlists/amplifier.cir', b'tampered'), ('.af/credentials.json', b'{}')]:
            changed = [(name, content) for name, content in entries if name != replacement[0]] + [replacement]
            with self.assertRaises(ValueError):
                self.lifecycle.import_archive(archive_bytes(changed))
        self.assertEqual(len(self.store.list()), 1)

    def test_self_consistent_manifest_cannot_authorize_invalid_hidden_records(self):
        from analog_factory.project_lifecycle import BACKUP_MANIFEST, BACKUP_FORMAT
        invalid = [('.af/jobs/not-an-id.json', '{}'),
                   ('.af/jobs/' + 'c' * 32 + '.json', '{}'),
                   ('.af/agent-sessions/' + 'a' * 32 + '.json', '[]'),
                   ('.af/history/' + 'd' * 64 + '/' + 'e' * 32 + '.json', '{}'),
                   ('.af/trash/files/' + 'f' * 32 + '/content', 'orphan content')]
        for path, content in invalid:
            with self.subTest(path=path):
                entries = [('project.json', json.dumps(self.store.get(self.pid))), (path, content)]
                manifest = {'format': BACKUP_FORMAT, 'schema_version': 1,
                            'files': {name: hashlib.sha256(value.encode()).hexdigest() for name, value in entries}}
                entries.append((BACKUP_MANIFEST, json.dumps(manifest)))
                with self.assertRaises(ValueError):
                    self.lifecycle.import_archive(archive_bytes(entries))
                self.assertEqual(len(self.store.list()), 1)

    def test_normal_archive_cannot_install_noncanonical_directory_or_device_aliases(self):
        for path in ('notes.txt.', 'notes.txt ', 'NUL', 'CON.txt', 'aux/source.cir'):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    self.lifecycle.import_archive(archive_bytes([(path, 'bad')]))
        self.assertEqual(len(self.store.list()), 1)

    def test_imported_run_dependencies_never_read_external_fifo_on_detail(self):
        fifo = Path(self.tmp.name) / 'external-fifo'
        os.mkfifo(fifo)
        for prefix in ('.af/runs/', '.af/episodes/' + 'b' * 32 + '/0/.af/runs/'):
            with self.subTest(prefix=prefix):
                record = {'id': 'a' * 32, 'kind': 'simulate', 'status': 'succeeded',
                          'created_at': '2026-10-08', 'external_dependencies': {str(fifo): 'untrusted'},
                          'verification': {'status': 'pass'}, 'provenance': {'complete': True}}
                restored = self.lifecycle.import_archive(backup_bytes([
                    ('project.json', json.dumps(self.store.get(self.pid))),
                    (prefix + record['id'] + '/manifest.json', json.dumps(record))]))
                script = ('import json,sys; from pathlib import Path; from analog_factory.service import Workspace; '
                          'w=Workspace(Path(sys.argv[1])); print(json.dumps(w.detail(sys.argv[2]))); w.agents.close()')
                try:
                    result = subprocess.run([sys.executable, '-c', script, self.tmp.name, restored['id']],
                                            capture_output=True, text=True, timeout=2)
                except subprocess.TimeoutExpired:
                    self.fail('Read-only project detail blocked on an imported external FIFO dependency')
                self.assertEqual(result.returncode, 0, result.stderr)
                run = json.loads(result.stdout)['runs'][0]
                self.assertEqual(run['external_dependencies'], {})
                self.assertTrue(run['imported_evidence'])
                self.assertFalse(run['provenance']['complete'])
                self.assertEqual(run['verification']['status'], 'unknown')
                self.assertTrue(run['stale'])
                self.assertEqual(run['imported_provenance']['external_dependencies'], {str(fifo): 'untrusted'})

    def test_unicode_episode_indices_cannot_bypass_runtime_record_sanitization(self):
        record = {'id': 'a' * 32, 'kind': 'simulate', 'status': 'succeeded', 'created_at': '2026-10-08',
                  'owner_pid': 12345, 'pid': 12345, 'process_identity': 'untrusted'}
        for index in ('²', '١', '１２'):
            with self.subTest(index=index):
                path = '.af/episodes/' + 'b' * 32 + '/' + index + '/.af/runs/' + record['id'] + '/manifest.json'
                with self.assertRaises(ValueError):
                    self.lifecycle.import_archive(backup_bytes([(path, json.dumps(record))]))
                self.assertEqual(len(self.store.list()), 1)

    def test_every_imported_run_clears_process_authority_and_cannot_claim_verification(self):
        record = {'id': 'a' * 32, 'kind': 'simulate', 'status': 'running', 'created_at': '2026-10-08',
                  'owner_pid': 12345, 'owner_identity': 'untrusted-owner', 'pid': 12345,
                  'process_identity': 'untrusted-process', 'imported_evidence': False,
                  'verification': {'status': 'pass'}, 'external_dependencies': {'/outside/model': 'claimed'}}
        for prefix in ('.af/runs/', '.af/episodes/' + 'b' * 32 + '/17/.af/runs/'):
            with self.subTest(prefix=prefix):
                relative = prefix + record['id'] + '/manifest.json'
                restored = self.lifecycle.import_archive(backup_bytes([(relative, json.dumps(record))]))
                manifest = json.loads((self.store.path(restored['id']) / relative).read_text())
                self.assertEqual(manifest['status'], 'failed')
                for field in ('owner_pid', 'owner_identity', 'pid', 'process_identity'):
                    self.assertIsNone(manifest[field])
                self.assertTrue(manifest['imported_evidence'])
                self.assertEqual(manifest['verification']['status'], 'unknown')
                self.assertEqual(manifest['external_dependencies'], {})

    def test_repeated_backup_restoration_keeps_original_unverified_provenance(self):
        record = {'id': 'a' * 32, 'kind': 'simulate', 'status': 'succeeded', 'created_at': '2026-10-08',
                  'external_dependencies': {'/original/model': 'recorded-hash'}}
        relative = '.af/runs/' + record['id'] + '/manifest.json'
        first = self.lifecycle.import_archive(backup_bytes([(relative, json.dumps(record))]))
        second = self.lifecycle.import_archive(self.lifecycle.backup(first['id']))
        manifest = json.loads((self.store.path(second['id']) / relative).read_text())
        self.assertEqual(manifest['imported_provenance']['external_dependencies'], record['external_dependencies'])
        self.assertEqual(manifest['external_dependencies'], {})

    def test_unknown_imported_run_status_is_rejected_before_publish(self):
        record = {'id': 'a' * 32, 'kind': 'simulate', 'status': 'not-a-state', 'created_at': '2026-10-08'}
        relative = '.af/runs/' + record['id'] + '/manifest.json'
        with self.assertRaises(ValueError):
            self.lifecycle.import_archive(backup_bytes([(relative, json.dumps(record))]))
        self.assertEqual(len(self.store.list()), 1)

    def test_imported_run_retry_cannot_execute_archived_command(self):
        from analog_factory.execution import RunManager
        marker = Path(self.tmp.name) / 'must-not-run'
        record = {'id': 'a' * 32, 'kind': 'simulate', 'status': 'succeeded', 'created_at': '2026-10-08',
                  'command_template': [sys.executable, '-c', 'from pathlib import Path; Path(' + repr(str(marker)) + ').touch()'],
                  'command': ['archived command'], 'timeout': 5, 'metadata': {}, 'environment': {}}
        project = self.lifecycle.import_archive(backup_bytes([
            ('.af/runs/' + record['id'] + '/manifest.json', json.dumps(record))]))
        manager = RunManager()
        root = self.store.path(project['id'])
        try:
            with self.assertRaisesRegex(ValueError, 'import|archive|fresh'):
                manager.retry(root, record['id'])
        finally:
            for run in manager.list(root):
                if run['id'] != record['id']:
                    manager.cancel(root, run['id'])
                    manager.wait(root, run['id'], timeout=5)
        self.assertFalse(marker.exists())

    def test_authorized_import_restores_valid_project_configuration(self):
        config = {'tools': {'ngspice': '/tmp/untrusted-program'},
                  'provider': {'base_url': 'https://invalid.example', 'api_key_env': 'UNTRUSTED_ENV', 'model': 'x'},
                  'pdk_root': '/tmp/untrusted-pdk'}
        metadata = dict(self.store.get(self.pid), config=config)
        restored = self.lifecycle.import_archive(archive_bytes([('project.json', json.dumps(metadata))]))
        self.assertEqual(restored['config'], config)


class LifecycleOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from analog_factory.service import Workspace
        from analog_factory.operations import OperationRegistry
        self.workspace = Workspace(Path(self.tmp.name))
        self.addCleanup(self.workspace.agents.close)
        self.registry = OperationRegistry(self.workspace)
        self.addCleanup(self.registry.close)
        self.assertIsNotNone(importlib.util.find_spec('analog_factory.operation_lifecycle'),
                             'Lifecycle operations must exist')
        from analog_factory.operation_lifecycle import register_lifecycle
        register_lifecycle(self.registry)
        self.pid = self.workspace.projects.create('Shared lifecycle')['id']

    def test_versions_and_project_agent_scope_protect_real_rename(self):
        from analog_factory.operations import InvocationContext
        params = {'project_id': self.pid, 'name': 'Reviewed'}
        required = self.registry.invoke('project.rename', params)
        self.assertEqual(required['error']['code'], 'VERSION_REQUIRED')
        version = self.registry.version(self.pid)
        changed = self.registry.invoke('project.rename', params, expected_version=version)
        self.assertTrue(changed['ok'], changed)
        stale = self.registry.invoke('project.rename', {**params, 'name': 'Stale'}, expected_version=version)
        self.assertEqual(stale['error']['code'], 'VERSION_CONFLICT')
        context = InvocationContext(source='agent', project_id=self.pid)
        denied = self.registry.invoke('project.rename', params, context=context,
                                      expected_version=self.registry.version(self.pid))
        self.assertEqual(denied['error']['code'], 'CONFIRMATION_REQUIRED')
        foreign = self.workspace.projects.create('Other')['id']
        result = self.registry.invoke('project.rename', {**params, 'project_id': foreign},
                                      context=InvocationContext(source='agent', project_id=self.pid, confirmed=True),
                                      expected_version=self.registry.version(foreign))
        self.assertEqual(result['error']['code'], 'PERMISSION_DENIED')

    def test_scoped_agent_cannot_import_a_different_project(self):
        from analog_factory.operations import InvocationContext
        blob = archive_bytes([('readme.txt', 'new project')])
        result = self.registry.invoke('project.import', {'content_base64': base64.b64encode(blob).decode()},
                                      context=InvocationContext(source='agent', project_id=self.pid, confirmed=True),
                                      expected_version=self.registry.version(self.pid))
        self.assertEqual(result['error']['code'], 'PERMISSION_DENIED')
        self.assertEqual(len(self.workspace.projects.list()), 1)

    def test_active_job_blocks_project_and_file_trash_until_finished(self):
        released = threading.Event()
        self.addCleanup(released.set)
        root = self.workspace.projects.path(self.pid)
        ready = threading.Event()
        def worker(record, cancel, update):
            ready.set()
            released.wait(5)
            return {'status': 'succeeded'}
        job = self.workspace.jobs.start(root, 'lifecycle-test', worker)
        try:
            self.assertTrue(ready.wait(2), 'Test worker must be running')
            for operation, parameters in [('project.trash', {'project_id': self.pid}),
                                           ('file.trash', {'project_id': self.pid, 'path': 'netlists/amplifier.cir'})]:
                result = self.registry.invoke(operation, parameters, expected_version=self.registry.version(self.pid))
                self.assertFalse(result['ok'], result)
                self.assertIn('active', result['error']['message'].lower())
            self.assertEqual(len(self.workspace.projects.list()), 1)
            self.assertTrue((root / 'netlists/amplifier.cir').exists())
        finally:
            released.set()
            self.workspace.jobs.wait(root, job['id'], timeout=5)

    def test_project_trash_restore_operation_results_and_new_revision(self):
        version = self.registry.version(self.pid)
        result = self.registry.invoke('project.trash', {'project_id': self.pid}, expected_version=version)
        self.assertTrue(result['ok'], result)
        self.assertNotEqual(result['version'], version)
        restored = self.registry.invoke('project.restore', {'project_id': self.pid},
                                        expected_version=result['version'])
        self.assertTrue(restored['ok'], restored)
        self.assertEqual(self.workspace.projects.list()[0]['id'], self.pid)

    def test_catalog_examples_and_agent_file_move_match_real_effects(self):
        from analog_factory.operation_schema import validate
        from analog_factory.operations import InvocationContext
        for operation in self.registry.operations.values():
            for example in operation.examples:
                validate(example['parameters'], operation.input_schema)
        self.workspace.projects.write(self.pid, 'original.txt', 'keep this')
        result = self.registry.invoke('file.move', {'project_id': self.pid, 'path': 'original.txt',
                                                   'destination': 'moved.txt'},
                                      context=InvocationContext(source='agent', project_id=self.pid, confirmed=True),
                                      expected_version=self.registry.version(self.pid))
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.workspace.projects.read(self.pid, 'moved.txt')['content'], 'keep this')

    def test_import_example_is_runnable_and_has_stable_archive_metadata(self):
        example = self.registry.operations['project.import'].examples[0]['parameters']
        blob = base64.b64decode(example['content_base64'])
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            self.assertEqual(archive.getinfo('README.txt').date_time, (1980, 1, 1, 0, 0, 0))
        result = self.registry.invoke('project.import', example, expected_version=self.registry.version())
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.workspace.projects.read(result['data']['id'], 'README.txt')['content'],
                         'Imported project source files.\n')

    def test_active_subprocess_run_blocks_file_move_and_backup(self):
        root = self.workspace.projects.path(self.pid)
        run = self.workspace.runner.start(root, 'simulate', [sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            for operation, parameters in [('project.backup', {'project_id': self.pid}),
                                           ('file.move', {'project_id': self.pid, 'path': 'netlists/amplifier.cir',
                                                          'destination': 'moved.cir'})]:
                result = self.registry.invoke(operation, parameters, expected_version=self.registry.version(self.pid))
                self.assertFalse(result['ok'], result)
                self.assertIn('active', result['error']['message'].lower())
            self.assertTrue((root / 'netlists/amplifier.cir').is_file())
        finally:
            self.workspace.runner.cancel(root, run['id'])
            self.workspace.runner.wait(root, run['id'], timeout=5)

    def test_live_agent_turn_blocks_trash_without_discarding_session(self):
        from unittest.mock import patch
        ready, released = threading.Event(), threading.Event()
        self.workspace.save_settings({'provider': {'base_url': 'http://localhost:1/v1', 'model': 'fixture'}})
        session = self.workspace.agents.create(self.pid, {'backend': 'api'})
        def provider_driver(provider, history, prompt, workspace, pid, mode, emit, cancel, approve):
            ready.set()
            released.wait(5)
            return {'reply': 'done', 'history': []}
        with patch('analog_factory.agents.run_api_turn', provider_driver):
            self.workspace.agents.send(self.pid, session['id'], {'message': 'Review the circuit'})
            try:
                self.assertTrue(ready.wait(2))
                result = self.registry.invoke('project.trash', {'project_id': self.pid},
                                              expected_version=self.registry.version(self.pid))
                self.assertFalse(result['ok'], result)
                self.assertIn('active agent', result['error']['message'].lower())
                self.assertEqual(self.workspace.projects.list()[0]['id'], self.pid)
            finally:
                released.set()
                self.workspace.agents.close()

    def test_workspace_registry_registers_lifecycle_and_blocks_trashed_source_operations(self):
        registry = self.workspace.operations
        self.addCleanup(registry.close)
        result = registry.invoke('project.trash', {'project_id': self.pid}, expected_version=registry.version(self.pid))
        self.assertTrue(result['ok'], result)
        for operation, parameters in [('file.read', {'path': 'netlists/amplifier.cir'}),
                                       ('file.write', {'path': 'new.txt', 'content': 'blocked'}),
                                       ('run.start', {'kind': 'simulate'})]:
            with self.subTest(operation=operation):
                result = registry.invoke(operation, {'project_id': self.pid, **parameters},
                                          expected_version=registry.version(self.pid))
                self.assertFalse(result['ok'], result)
                self.assertIn('trash', result['error']['message'].lower())
        detail = registry.invoke('project.detail', {'project_id': self.pid})
        self.assertTrue(detail['ok'], detail)
        restored = registry.invoke('project.restore', {'project_id': self.pid}, expected_version=detail['version'])
        self.assertTrue(restored['ok'], restored)
        read = registry.invoke('file.read', {'project_id': self.pid, 'path': 'netlists/amplifier.cir'})
        self.assertTrue(read['ok'], read)

    def test_project_metadata_changes_invalidate_workspace_import_revision(self):
        registry = self.workspace.operations
        self.addCleanup(registry.close)
        version = registry.version()
        self.workspace.projects.create('Concurrent project')
        self.assertNotEqual(registry.version(), version)
        blob = archive_bytes([('readme.txt', 'import')])
        result = registry.invoke('project.import', {'content_base64': base64.b64encode(blob).decode()}, expected_version=version)
        self.assertEqual(result['error']['code'], 'VERSION_CONFLICT')
        self.assertEqual(len(self.workspace.projects.list()), 2)

    def test_scoped_frontend_cannot_submit_an_import_or_foreign_restore(self):
        from analog_factory.operations import InvocationContext
        registry = self.workspace.operations
        self.addCleanup(registry.close)
        context = InvocationContext(source='agent', project_id=self.pid, confirmed=True)
        for action, target in [('import', self.pid), ('restore_project', 'b' * 32)]:
            with self.subTest(action=action):
                client = registry.sessions.connect({'project_id': self.pid,
                                                     'lifecycle': {'open': True, 'action': action, 'project_id': target}})
                result = registry.invoke('ui.lifecycle.submit', {'client_id': client['client_id']},
                                          context=context, expected_version=registry.version(self.pid))
                self.assertEqual(result['error']['code'], 'PERMISSION_DENIED')

    def test_scoped_import_is_unavailable_before_handler_execution(self):
        from analog_factory.operations import InvocationContext
        registry = self.workspace.operations
        self.addCleanup(registry.close)
        self.assertIn('project.import', list(registry.operations))
        description = registry.describe_operation('project.import',
                      InvocationContext(source='agent', project_id=self.pid, confirmed=True))
        self.assertFalse(description['availability']['available'])
        self.assertIn('import', ' '.join(description['availability']['reasons']).lower())

    def test_write_only_import_cannot_restore_config_authority(self):
        from analog_factory.operations import InvocationContext
        metadata = dict(self.workspace.projects.get(self.pid), config={'tools': {'ngspice': '/bin/true'}})
        blob = archive_bytes([('project.json', json.dumps(metadata))])
        context = InvocationContext(source='external-agent', permissions=frozenset({'read', 'write'}), confirmed=True)
        result = self.registry.invoke('project.import', {'content_base64': base64.b64encode(blob).decode()},
                                      context=context, expected_version=self.registry.version())
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error']['code'], 'PERMISSION_DENIED', result)
        self.assertEqual(len(self.workspace.projects.list()), 1)


if __name__ == '__main__':
    unittest.main()
