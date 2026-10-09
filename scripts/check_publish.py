#!/usr/bin/env python3
"""Read-only, bounded publication preflight. No network, credential lookup or writes.

Run before staging and again before pushing, adding --history for reachable local
history. Exit 1 means a finding or an incomplete scan; exit 0 means only that the
selected checks passed. Local publish blockers are advisory metadata: an initial
repository can pass its content checks before it has a commit or remote.
"""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
from urllib.parse import urlsplit


GITHUB_FILE_LIMIT = 100 * 1024 * 1024
PRIVATE_DIRS = frozenset({'.workspace', '.tools', '.validation', '.codex', '.agents',
                          '.aws', '.af', '.credentials', '.venv', 'venv',
                          'node_modules', '.superpowers', '.git'})
KEY_NAMES = frozenset({'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519', '.netrc',
                       '.git-credentials', 'credentials', 'credentials.json'})
SAFE_ENV_SUFFIXES = ('.example', '.sample', '.template')
SECRET_PATTERNS = (
    ('private_key', re.compile(br'-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----')),
    ('provider_key', re.compile(br'\bsk-(?:proj-|ant-api\d+-)?[A-Za-z0-9_-]{32,}\b')),
    ('github_token', re.compile(br'\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b')),
    ('google_api_key', re.compile(br'\bAIza[A-Za-z0-9_-]{35}\b')),
    ('aws_access_key', re.compile(br'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b')),
    ('slack_token', re.compile(br'\bxox[baprs]-[A-Za-z0-9-]{24,}\b')),
)
LIMITATIONS = [
    'Candidates are exactly tracked files plus non-ignored untracked files; ignored untracked files are never opened.',
    'Private and credential paths are reported from metadata and their contents are not read.',
    'Working files and index blobs are checked; this is a local snapshot, so rerun immediately before publication.',
    'Secret detection covers listed obvious token formats and private-key headers; it cannot prove absence of secrets.',
    'Documented placeholders and single-character filler tokens are excluded; no entire test or template file is exempt.',
    'Archives, encoded/encrypted secrets, Git LFS payloads, submodule contents, remote history and authentication are not inspected.',
    'Optional history covers reachable local commit trees and blobs within configured limits, not reflogs, dangling objects, commit messages or author metadata.',
]


class GitError(Exception):
    """Git failed; stderr is deliberately not included because it can contain URLs."""


class Preflight:
    def __init__(self, root, *, history=False, max_content_bytes=16 * 1024 * 1024,
                 max_total_bytes=256 * 1024 * 1024, max_history_commits=5000,
                 max_history_blobs=20000):
        self.root = Path(root).resolve()
        self.max_content_bytes = max_content_bytes
        self.max_total_bytes = max_total_bytes
        self.max_history_commits = max_history_commits
        self.max_history_blobs = max_history_blobs
        self.bytes_checked = 0
        self.report = {'ok': False, 'complete': True, 'candidate_count': 0,
                       'findings': [], 'git': {},
                       'history': {'requested': history, 'commits_checked': 0, 'blobs_checked': 0},
                       'limits': {'max_content_bytes': max_content_bytes,
                                  'max_total_bytes': max_total_bytes,
                                  'max_history_commits': max_history_commits,
                                  'max_history_blobs': max_history_blobs},
                       'limitations': LIMITATIONS}

    def git(self, *args, optional=False):
        result = subprocess.run(['git', '-c', 'core.fsmonitor=false', '-C', str(self.root), *args],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0',
                                     'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_REPLACE_OBJECTS': '1'})
        if result.returncode and not optional:
            raise GitError()
        return result.stdout if result.returncode == 0 else b''

    def finding(self, code, path=None, *, source='worktree', line=None, **extra):
        item = {'code': code, 'source': source}
        if path is not None:
            item['path'] = path
        if line is not None:
            item['line'] = line
        item.update(extra)
        if item not in self.report['findings']:
            self.report['findings'].append(item)

    def allowed_path(self, path, source):
        parts = PurePosixPath(path).parts
        if any(part.lower() in PRIVATE_DIRS for part in parts):
            self.finding('private_path', path, source=source)
            return False
        name = parts[-1].lower() if parts else ''
        environment = name == '.env' or name.startswith('.env.')
        if ((environment and not name.endswith(SAFE_ENV_SUFFIXES)) or name in KEY_NAMES
                or name.endswith(('.pem', '.key', '.p12', '.pfx'))):
            self.finding('credential_path', path, source=source)
            return False
        return True

    def allow_size(self, size, path, source):
        if size > GITHUB_FILE_LIMIT:
            self.finding('github_file_limit', path, source=source, size_bytes=size)
            self.report['complete'] = False
            return False
        if size > self.max_content_bytes or self.bytes_checked + size > self.max_total_bytes:
            self.finding('content_scan_limit', path, source=source, size_bytes=size)
            self.report['complete'] = False
            return False
        self.bytes_checked += size
        return True

    @staticmethod
    def placeholder(token):
        # Only the token itself can qualify; comments saying "example" do not
        # suppress a real-looking key elsewhere on the same line.
        if token == b'AKIAIOSFODNN7EXAMPLE':
            return True
        payload = re.sub(br'^(?:sk-(?:proj-|ant-api\d+-)?|gh[pousr]_|github_pat_|AIza)', b'', token)
        if re.match(br'(?i)^(?:YOUR_|YOUR-|EXAMPLE_|PLACEHOLDER_|REPLACE_ME|DUMMY_|FAKE_)', payload):
            return True
        return len(set(payload)) == 1

    def scan(self, data, path, source):
        for line_number, line in enumerate(data.splitlines(), 1):
            for kind, pattern in SECRET_PATTERNS:
                if any(kind == 'private_key' or not self.placeholder(match.group())
                       for match in pattern.finditer(line)):
                    self.finding('secret_pattern', path, source=source,
                                 line=line_number, pattern=kind)

    def check_link(self, path, target, source):
        try:
            resolved = (self.root / PurePosixPath(path).parent / os.fsdecode(target)).resolve()
            if not resolved.is_relative_to(self.root):
                self.finding('escaping_symlink', path, source=source)
            elif any(part.lower() in PRIVATE_DIRS for part in resolved.relative_to(self.root).parts):
                self.finding('private_symlink_target', path, source=source)
        except (OSError, ValueError, RuntimeError):
            self.finding('invalid_symlink', path, source=source)

    def worktree(self, path):
        if not self.allowed_path(path, 'worktree'):
            return
        full = self.root / path
        try:
            # Check parent links before stat/open; a tracked directory may have
            # been replaced locally by a link into a private or external tree.
            parent = full.parent.resolve()
            if not parent.is_relative_to(self.root):
                self.finding('escaping_symlink', path)
                return
            if any(part.lower() in PRIVATE_DIRS for part in parent.relative_to(self.root).parts):
                self.finding('private_symlink_target', path)
                return
            info = full.lstat()
            if stat.S_ISLNK(info.st_mode):
                self.check_link(path, os.readlink(full), 'worktree')
            elif not stat.S_ISREG(info.st_mode):
                self.finding('unsupported_file_type', path)
                self.report['complete'] = False
            elif self.allow_size(info.st_size, path, 'worktree'):
                with full.open('rb') as stream:
                    data = stream.read(self.max_content_bytes + 1)
                if len(data) > self.max_content_bytes:
                    self.finding('content_scan_limit', path)
                    self.report['complete'] = False
                else:
                    self.scan(data, path, 'worktree')
        except FileNotFoundError:
            # Locally deleted tracked files still have their index blob checked.
            pass
        except (OSError, ValueError, RuntimeError):
            self.finding('unreadable_file', path)
            self.report['complete'] = False

    def blob(self, mode, oid, path, source, size=None):
        if not self.allowed_path(path, source):
            return
        if mode == b'160000':
            self.finding('submodule_not_scanned', path, source=source)
            self.report['complete'] = False
            return
        if size is None:
            size = int(self.git('cat-file', '-s', oid.decode('ascii')))
        if not self.allow_size(size, path, source):
            return
        data = self.git('cat-file', 'blob', oid.decode('ascii'))
        if mode == b'120000':
            self.check_link(path, data, source)
        else:
            self.scan(data, path, source)

    def index(self):
        for record in self.git('ls-files', '-z', '--stage').split(b'\0'):
            if not record:
                continue
            header, raw_path = record.split(b'\t', 1)
            mode, oid, stage = header.split()
            path = os.fsdecode(raw_path)
            if stage != b'0':
                self.finding('unmerged_index', path, source='index')
            self.blob(mode, oid, path, 'index')

    def history(self):
        if self.git('rev-parse', '--is-shallow-repository').strip() == b'true':
            self.finding('shallow_history', source='history')
            self.report['complete'] = False
        commits = self.git('rev-list', '--all', f'--max-count={self.max_history_commits + 1}').splitlines()
        if len(commits) > self.max_history_commits:
            self.finding('history_limit', source='history')
            self.report['complete'] = False
        seen = set()
        for commit in commits[:self.max_history_commits]:
            self.report['history']['commits_checked'] += 1
            for record in self.git('ls-tree', '-r', '-z', '-l', commit.decode('ascii')).split(b'\0'):
                if not record:
                    continue
                header, raw_path = record.split(b'\t', 1)
                mode, kind, oid, raw_size = header.split()
                path = os.fsdecode(raw_path)
                key = (oid, raw_path, mode)
                if key in seen:
                    continue
                if len(seen) >= self.max_history_blobs:
                    self.finding('history_limit', source='history')
                    self.report['complete'] = False
                    return
                seen.add(key)
                self.report['history']['blobs_checked'] += 1
                self.blob(mode, oid, path, 'history',
                          int(raw_size) if kind == b'blob' else None)

    def metadata(self):
        branch = self.git('symbolic-ref', '--short', '-q', 'HEAD', optional=True).decode('utf-8', 'replace').strip()
        has_commit = bool(self.git('rev-parse', '--verify', 'HEAD', optional=True))
        remote_config = self.git('config', '--local', '--no-includes', '--get-regexp',
                                 r'^remote\..*\.url$', optional=True)
        remote_count = github_count = 0
        for record in remote_config.decode('utf-8', 'replace').splitlines():
            fields = record.split(None, 1)
            if len(fields) != 2:
                continue
            remote_count += 1
            url = fields[1]
            try:
                host = urlsplit(url).hostname if '://' in url else url.split(':', 1)[0].rsplit('@', 1)[-1]
                github_count += host == 'github.com'
            except ValueError:
                pass
        upstream = bool(branch and self.git('config', '--local', '--no-includes', '--get',
                                           f'branch.{branch}.remote', optional=True)
                        and self.git('config', '--local', '--no-includes', '--get',
                                     f'branch.{branch}.merge', optional=True))
        blockers = []
        if not has_commit:
            blockers.append('no_commits')
        if not remote_count:
            blockers.append('no_remote')
        if not branch:
            blockers.append('detached_head')
        self.report['git'] = {'branch': branch or None, 'has_commit': has_commit,
                              'remote_count': remote_count, 'github_remote_count': github_count,
                              'upstream_configured': upstream, 'publish_blockers': blockers,
                              'authentication_checked': False, 'network_checked': False}

    def run(self):
        try:
            top = self.git('rev-parse', '--show-toplevel').rstrip(b'\n')
            if Path(os.fsdecode(top)).resolve() != self.root:
                raise GitError()
            self.metadata()
            candidates = sorted(set(self.git('ls-files', '-z', '--cached', '--others',
                                             '--exclude-standard').split(b'\0')) - {b''})
            self.report['candidate_count'] = len(candidates)
            for path in candidates:
                self.worktree(os.fsdecode(path))
            self.index()
            if self.report['history']['requested']:
                self.history()
        except (GitError, OSError, ValueError):
            self.finding('git_error', source='repository')
            self.report['complete'] = False
        self.report['bytes_checked'] = self.bytes_checked
        self.report['ok'] = self.report['complete'] and not self.report['findings']
        return self.report


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1],
                        help='Git repository root (defaults to this project)')
    parser.add_argument('--json', action='store_true', help='emit a structured, redacted report')
    parser.add_argument('--history', action='store_true', help='also check reachable local commit trees and blobs')
    parser.add_argument('--max-content-bytes', type=positive_int, default=16 * 1024 * 1024)
    parser.add_argument('--max-total-bytes', type=positive_int, default=256 * 1024 * 1024)
    parser.add_argument('--max-history-commits', type=positive_int, default=5000)
    parser.add_argument('--max-history-blobs', type=positive_int, default=20000)
    args = vars(parser.parse_args())
    as_json = args.pop('json')
    report = Preflight(**args).run()
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=True))
    else:
        print(f"Publication content checks: {'PASS' if report['ok'] else 'FAIL'} "
              f"({report['candidate_count']} candidates; {len(report['findings'])} findings)")
        for item in report['findings']:
            path = item.get('path', '')
            location = json.dumps(path, ensure_ascii=True)[1:-1]
            if 'line' in item:
                location += f":{item['line']}"
            print(f"  {item['source']}: {location}: {item['code']}")
        for blocker in report['git'].get('publish_blockers', []):
            print(f'  Local publish blocker: {blocker}')
        print('Limits: ' + json.dumps(report['limits'], sort_keys=True))
        for limitation in report['limitations']:
            print('  ' + limitation)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
