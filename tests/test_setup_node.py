"""Local Node provisioning verifies archive bytes, extraction, and real tools."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/setup_node.py'
ARCHIVE_NAME = 'node-v22.23.3-linux-x64.tar.xz'
ARCHIVE_ROOT = 'node-v22.23.3-linux-x64'


class SetupNodeTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'The local Node installer is not implemented')
        spec = importlib.util.spec_from_file_location('setup_node', SCRIPT)
        self.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.builder)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.directory = self.base / 'local node'
        self.archives = self.base / 'archives'
        self.archives.mkdir()
        self.probes = self.base / 'probe.calls'
        self.fixture_archive()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(self.builder.platform, 'system', return_value='Linux').start()
        mock.patch.object(self.builder.platform, 'machine', return_value='x86_64').start()

    def fixture_archive(self, version='22.23.3', npm_status=0, extra_member=None):
        node = (f'#!{sys.executable}\nimport os, sys\nfrom pathlib import Path\n'
                f'with Path({str(self.probes)!r}).open("a") as calls: calls.write(sys.argv[1] + "\\n")\n'
                'if os.environ.get("NODE_FIXTURE_PROBE_FAIL"): sys.exit(8)\n'
                f'if sys.argv[1:] == ["--version"]: print("v{version}")\n'
                'elif sys.argv[1:] == ["--npm-version"]:\n'
                f' print("10.9.7")\n sys.exit({npm_status})\n'
                'else: sys.exit(2)\n')
        archive = self.archives / ARCHIVE_NAME
        with tarfile.open(archive, 'w:xz') as contents:
            for relative, text, mode in [('bin/node', node, 0o755),
                                         ('lib/npm-cli', '#!/bin/sh\nexec node --npm-version\n', 0o755),
                                         ('README.md', 'fixture runtime resources', 0o644)]:
                member = tarfile.TarInfo(f'{ARCHIVE_ROOT}/{relative}')
                data = text.encode()
                member.size = len(data)
                member.mode = mode
                contents.addfile(member, io.BytesIO(data))
            link = tarfile.TarInfo(f'{ARCHIVE_ROOT}/bin/npm')
            link.type = tarfile.SYMTYPE
            link.linkname = '../lib/npm-cli'
            contents.addfile(link)
            if extra_member is not None:
                contents.addfile(extra_member)
        self.builder.SOURCE_SHA256 = hashlib.sha256(archive.read_bytes()).hexdigest()

    def install(self, **overrides):
        options = dict(directory=self.directory, archives_dir=self.archives, skip_download=True)
        options.update(overrides)
        return self.builder.install(**options)

    def test_offline_install_probes_local_node_and_npm_and_reuses_manifest(self):
        manifest = self.install()
        self.assertEqual(manifest['node'], str(self.directory / 'bin/node'))
        self.assertEqual(manifest['nodebin'], str(self.directory / 'bin'))
        self.assertEqual(manifest['version'], '22.23.3')
        self.assertEqual(manifest['npm_version'], '10.9.7')
        marker = self.directory / 'installation.json'
        self.assertEqual(json.loads(marker.read_text()), manifest)
        before = marker.stat().st_mtime_ns
        calls_before = len(self.probes.read_text().splitlines())
        self.assertEqual(self.install(), manifest)
        self.assertEqual(marker.stat().st_mtime_ns, before)
        self.assertGreater(len(self.probes.read_text().splitlines()), calls_before)

    def test_bad_source_checksum_fails_before_running_archive(self):
        (self.archives / ARCHIVE_NAME).write_bytes(b'corrupt')
        with self.assertRaisesRegex(self.builder.SetupError, 'SHA256|checksum'):
            self.install()
        self.assertFalse(self.probes.exists())
        self.assertFalse(self.directory.exists())

    def test_missing_offline_archive_fails_without_creating_installation(self):
        (self.archives / ARCHIVE_NAME).unlink()
        with self.assertRaisesRegex(self.builder.SetupError, 'Missing|missing'):
            self.install()
        self.assertFalse(self.directory.exists())

    def test_traversal_and_external_symlink_are_rejected_before_probe(self):
        for name, link in [(f'{ARCHIVE_ROOT}/../../escaped', None),
                           ('other-root/bin/node', None),
                           (f'{ARCHIVE_ROOT}/lib/escape', '../../../escaped')]:
            with self.subTest(name=name):
                member = tarfile.TarInfo(name)
                if link:
                    member.type = tarfile.SYMTYPE
                    member.linkname = link
                self.fixture_archive(extra_member=member)
                with self.assertRaisesRegex(self.builder.SetupError, 'archive|Unsafe|source'):
                    self.install()
                self.assertFalse(self.probes.exists())
                self.assertFalse((self.base / 'escaped').exists())

    def test_wrong_node_version_and_failed_npm_never_write_success_manifest(self):
        for options in ({'version': '20.1.0'}, {'npm_status': 7}):
            with self.subTest(options=options):
                self.fixture_archive(**options)
                target = self.base / next(iter(options))
                with self.assertRaisesRegex(self.builder.SetupError, 'version|probe'):
                    self.install(directory=target)
                self.assertFalse((target / 'installation.json').exists())

    def test_unknown_nonempty_directory_is_preserved(self):
        self.directory.mkdir()
        marker = self.directory / 'keep'
        marker.write_text('existing data')
        with self.assertRaisesRegex(self.builder.SetupError, 'directory|installation'):
            self.install()
        self.assertEqual(marker.read_text(), 'existing data')
        self.assertEqual(list(self.directory.iterdir()), [marker])
        self.assertFalse(self.probes.exists())

    def test_modified_runtime_or_missing_binary_is_not_reinstalled(self):
        self.install()
        (self.directory / 'README.md').write_text('modified runtime')
        with self.assertRaisesRegex(self.builder.SetupError, 'integrity|changed|match'):
            self.install()
        self.assertEqual((self.directory / 'README.md').read_text(), 'modified runtime')
        (self.directory / 'bin/node').unlink()
        with self.assertRaises(self.builder.SetupError):
            self.install()

    def test_reuse_requires_live_successful_version_probes(self):
        self.install()
        with mock.patch.dict(os.environ, {'NODE_FIXTURE_PROBE_FAIL': '1'}), \
                self.assertRaisesRegex(self.builder.SetupError, 'probe'):
            self.install()

    def test_reuse_refuses_changed_source_pin(self):
        self.install()
        self.builder.SOURCE_SHA256 = '0' * 64
        with self.assertRaisesRegex(self.builder.SetupError, 'inputs|match'):
            self.install()

    def test_unsupported_architecture_is_rejected_before_work(self):
        with mock.patch.object(self.builder.platform, 'machine', return_value='aarch64'), \
                self.assertRaisesRegex(self.builder.SetupError, 'Linux|x64'):
            self.install()
        self.assertFalse(self.directory.exists())
        self.assertFalse(self.probes.exists())

    def test_cli_offline_failure_returns_nonzero_without_installing(self):
        result = subprocess.run([sys.executable, str(SCRIPT), '--directory', str(self.directory),
                                 '--archives-dir', str(self.base / 'missing'), '--skip-download'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing', result.stderr)
        self.assertFalse(self.directory.exists())

    def test_download_publishes_only_verified_complete_bytes(self):
        payload = (self.archives / ARCHIVE_NAME).read_bytes()
        for data in (payload, b'corrupt download'):
            with self.subTest(valid=data == payload):
                target = self.archives / 'download.tar.xz'
                response = io.BytesIO(data)
                response.headers = {'Content-Length': str(len(data))}
                opener = mock.Mock()
                opener.open.return_value = response
                with mock.patch.object(self.builder.urllib.request, 'build_opener', return_value=opener):
                    if data == payload:
                        self.builder.download_archive(target)
                        self.assertEqual(target.read_bytes(), data)
                        target.unlink()
                    else:
                        with self.assertRaises(self.builder.SetupError):
                            self.builder.download_archive(target)
                        self.assertFalse(target.exists())
                self.assertFalse(list(self.archives.glob('*.part')))


if __name__ == '__main__':
    unittest.main()
