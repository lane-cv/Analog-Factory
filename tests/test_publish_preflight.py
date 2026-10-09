"""Publication preflight contracts using disposable, local Git repositories."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'check_publish.py'


class PublishPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'repository'
        self.root.mkdir()
        self.env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull,
                    'GIT_CONFIG_NOSYSTEM': '1'}
        self.git('init', '-q', '-b', 'main')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], env=self.env,
                              check=True, capture_output=True, timeout=15).stdout

    def write(self, name, content='safe source\n'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def commit(self):
        self.git('add', '-A')
        self.git('-c', 'user.name=Preflight Test', '-c', 'user.email=test@example.invalid',
                 'commit', '-q', '-m', 'test fixture')

    def run_check(self, *args, expected=0):
        result = subprocess.run([sys.executable, str(SCRIPT), '--root', str(self.root),
                                 '--json', *args], env=self.env, capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, expected, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_clean_untracked_source_passes_and_reports_local_publish_blockers(self):
        self.write('source.py', 'answer = 42\n')
        report = self.run_check()
        self.assertTrue(report['ok'])
        self.assertEqual(report['candidate_count'], 1)
        self.assertEqual(report['findings'], [])
        self.assertIn('no_commits', report['git']['publish_blockers'])
        self.assertIn('no_remote', report['git']['publish_blockers'])

    def test_private_tracked_path_fails_even_when_gitignored(self):
        self.write('.gitignore', '.workspace/\n')
        self.write('.workspace/private.json', 'private value must not appear')
        self.git('add', '-f', '.workspace/private.json')
        report = self.run_check(expected=1)
        self.assertTrue(any(f['code'] == 'private_path' and
                            f['path'] == '.workspace/private.json' for f in report['findings']))
        self.assertNotIn('private value must not appear', json.dumps(report))

    def test_ignored_untracked_files_are_not_candidates_or_read(self):
        self.write('.gitignore', '.workspace/\nignored/\n')
        self.write('.workspace/private.json', self.token())
        (self.root / 'ignored').mkdir()
        os.mkfifo(self.root / 'ignored' / 'never-open-this')
        report = self.run_check()
        self.assertEqual(report['candidate_count'], 1)

    def test_env_and_keyfiles_fail_but_safe_templates_are_scanned(self):
        for name in ('.env', '.env.production', 'keys/id_ed25519', 'keys/private.pem'):
            with self.subTest(name=name):
                path = self.write(name)
                report = self.run_check(expected=1)
                self.assertTrue(any(f['code'] == 'credential_path' and f['path'] == name
                                    for f in report['findings']))
                path.unlink()
        self.write('.env.example', 'API_KEY=your-api-key\n')
        self.write('.env.production.template', 'API_KEY=<replace-me>\n')
        self.run_check()
        self.write('.env.example', self.token())
        self.assertTrue(any(f['code'] == 'secret_pattern'
                            for f in self.run_check(expected=1)['findings']))

    def test_oversized_file_fails_without_reading_its_contents(self):
        path = self.write('large.bin', '')
        with path.open('wb') as stream:
            stream.truncate(100 * 1024 * 1024 + 1)
        report = self.run_check(expected=1)
        self.assertTrue(any(f['code'] == 'github_file_limit' for f in report['findings']))

    def test_symlink_escaping_repository_fails_without_disclosing_target(self):
        target = Path(self.tmp.name) / 'private-outside.txt'
        target.write_text(self.token())
        (self.root / 'external').symlink_to(target)
        report = self.run_check(expected=1)
        self.assertTrue(any(f['code'] == 'escaping_symlink' for f in report['findings']))
        self.assertNotIn(str(target), json.dumps(report))
        self.assertNotIn(self.token(), json.dumps(report))

    def test_candidate_under_symlink_directory_never_reads_external_file(self):
        self.write('folder/secret.txt', 'safe\n')
        self.git('add', 'folder/secret.txt')
        (self.root / 'folder/secret.txt').unlink()
        (self.root / 'folder').rmdir()
        external = Path(self.tmp.name) / 'outside'
        external.mkdir()
        (external / 'secret.txt').write_text(self.token())
        (self.root / 'folder').symlink_to(external)
        report = self.run_check(expected=1)
        self.assertTrue(any(f['code'] == 'escaping_symlink' for f in report['findings']))
        self.assertNotIn(self.token(), json.dumps(report))

    @staticmethod
    def token():
        # Construct a synthetic token so the scanner's own tests remain publishable.
        return 'sk-' + 'proj-' + 'Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4z' * 2

    def test_secret_findings_are_redacted_in_json_and_human_output(self):
        self.write('config.py', 'safe line\nKEY=' + self.token() + '\n')
        report = self.run_check(expected=1)
        self.assertTrue(any(f['code'] == 'secret_pattern' and f['path'] == 'config.py'
                            and f['line'] == 2 for f in report['findings']))
        self.assertNotIn(self.token(), json.dumps(report))
        human = subprocess.run([sys.executable, str(SCRIPT), '--root', str(self.root)],
                               env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(human.returncode, 1)
        self.assertIn('config.py:2', human.stdout)
        self.assertNotIn(self.token(), human.stdout + human.stderr)

    def test_private_key_block_is_detected_without_printing_key_material(self):
        self.write('notes.txt', '-----BEGIN ' + 'PRIVATE KEY-----\nprivate-material\n')
        report = self.run_check(expected=1)
        self.assertTrue(any(f['code'] == 'secret_pattern' for f in report['findings']))
        self.assertNotIn('private-material', json.dumps(report))

    def test_documented_placeholder_tokens_pass_without_blanket_test_exemption(self):
        self.write('tests/example.py', 'sk-' + 'x' * 48 + '\n' +
                   'ghp_' + 'YOUR_EXAMPLE_TOKEN_REPLACE_WITH_REAL_VALUE' + '\n')
        self.run_check()
        self.write('tests/example.py', self.token())
        self.run_check(expected=1)

    def test_staged_secret_fails_even_after_worktree_is_cleaned(self):
        self.write('settings.py', self.token())
        self.git('add', 'settings.py')
        self.write('settings.py', 'safe\n')
        report = self.run_check(expected=1)
        self.assertTrue(any(f['code'] == 'secret_pattern' and f['source'] == 'index'
                            for f in report['findings']))

    def test_history_finds_removed_private_paths_and_deleted_secrets(self):
        self.write('.workspace/old.txt')
        self.write('old-config.py', self.token())
        self.commit()
        (self.root / '.workspace/old.txt').unlink()
        (self.root / 'old-config.py').unlink()
        self.write('README.md')
        self.commit()
        self.run_check()
        report = self.run_check('--history', expected=1)
        self.assertTrue(any(f['code'] == 'private_path' and f['source'] == 'history'
                            for f in report['findings']))
        self.assertTrue(any(f['code'] == 'secret_pattern' and f['source'] == 'history'
                            for f in report['findings']))
        self.assertNotIn(self.token(), json.dumps(report))
        self.assertEqual(report['history']['commits_checked'], 2)

    def test_history_limit_is_a_failure_and_never_claims_a_complete_scan(self):
        self.write('README.md')
        self.commit()
        self.write('README.md', 'changed\n')
        self.commit()
        report = self.run_check('--history', '--max-history-commits', '1', expected=1)
        self.assertFalse(report['complete'])
        self.assertTrue(any(f['code'] == 'history_limit' for f in report['findings']))

    def test_remote_credentials_are_never_reported(self):
        password = 'fixture-sensitive-url-password'
        self.git('remote', 'add', 'origin', 'https://username:' + password +
                 '@github.com/example/project.git')
        report = self.run_check()
        self.assertEqual(report['git']['github_remote_count'], 1)
        self.assertNotIn(password, json.dumps(report))
        self.assertNotIn('username', json.dumps(report))

    def test_nonrepository_returns_structured_failure(self):
        self.root = Path(self.tmp.name) / 'not-a-repository'
        self.root.mkdir()
        report = self.run_check(expected=1)
        self.assertFalse(report['ok'])
        self.assertTrue(any(f['code'] == 'git_error' for f in report['findings']))

    def test_content_limit_fails_closed_without_reading_rest_of_file(self):
        self.write('large.txt', 'safe\n' * 20 + self.token())
        report = self.run_check('--max-content-bytes', '16', expected=1)
        self.assertFalse(report['complete'])
        self.assertTrue(any(f['code'] == 'content_scan_limit' for f in report['findings']))
        self.assertNotIn(self.token(), json.dumps(report))

    def test_total_scan_budget_fails_closed(self):
        self.write('a.txt', 'a' * 20)
        self.write('b.txt', 'b' * 20)
        report = self.run_check('--max-total-bytes', '30', expected=1)
        self.assertFalse(report['complete'])
        self.assertEqual(report['bytes_checked'], 20)

    def test_history_blob_budget_fails_closed(self):
        self.write('a.txt')
        self.write('b.txt')
        self.commit()
        report = self.run_check('--history', '--max-history-blobs', '1', expected=1)
        self.assertFalse(report['complete'])
        self.assertTrue(any(f['code'] == 'history_limit' for f in report['findings']))

    def test_fifo_candidate_fails_without_opening_it(self):
        self.write('pipe')
        self.git('add', 'pipe')
        (self.root / 'pipe').unlink()
        os.mkfifo(self.root / 'pipe')
        report = self.run_check(expected=1)
        self.assertTrue(any(f['code'] == 'unsupported_file_type' for f in report['findings']))

    def test_upstream_configuration_is_reported_without_fetching_remote_refs(self):
        self.git('remote', 'add', 'origin', 'git@github.com:example/repository.git')
        self.git('config', 'branch.main.remote', 'origin')
        self.git('config', 'branch.main.merge', 'refs/heads/main')
        report = self.run_check()
        self.assertTrue(report['git']['upstream_configured'])

    def test_shallow_history_never_claims_complete_review(self):
        self.write('README.md')
        self.commit()
        head = self.git('rev-parse', 'HEAD')
        (self.root / '.git/shallow').write_bytes(head)
        report = self.run_check('--history', expected=1)
        self.assertFalse(report['complete'])
        self.assertTrue(any(f['code'] == 'shallow_history' for f in report['findings']))


if __name__ == '__main__':
    unittest.main()
