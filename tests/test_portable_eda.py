"""Portable installs must prove their sources and the tools they actually run."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/build_portable_eda.py'


class PortableEdaTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'The portable source builder is not implemented')
        spec = importlib.util.spec_from_file_location('build_portable_eda', SCRIPT)
        self.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.builder)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.prefix = self.base / 'portable install'
        self.archives = self.base / 'archives'
        self.archives.mkdir()
        self.pins = json.loads((ROOT / 'deployment/ubuntu24/sources.json').read_text())
        self.calls = self.base / 'configure.calls'
        self.ngspice = self.base / "native simulator's binary"
        self.write_simulator()
        self.sources_file = self.base / 'sources.json'
        self.builder.SOURCES_FILE = self.sources_file
        self.fixture_sources()

    def write_simulator(self, version='45.2', status=0):
        self.ngspice.write_text(
            f'#!{sys.executable}\nimport json, sys\n'
            f'if "--version" in sys.argv:\n print("ngspice-{version} : Circuit level simulation program")\n sys.exit({status})\n'
            'print(json.dumps(sys.argv[1:]))\n')
        self.ngspice.chmod(0o755)

    def fixture_sources(self, fail_configure=None, bad_version=None, version_exit=None,
                        extra_member=None, startup_messages=False):
        for name, metadata in self.pins.items():
            root = f'{name}-{metadata["commit"]}'
            version = '0.0.1' if bad_version == name else metadata['version']
            banner = version if name == 'magic' else f'XSCHEM V{version}\nCopyright fixture'
            startup = ''
            if name == 'xschem' and startup_messages:
                startup = ('from pathlib import Path\n'
                           'config = Path(os.environ["HOME"]) / ".xschem"\n'
                           'if not config.exists():\n'
                           ' config.mkdir()\n print(f"Created {config} dir with template xschemrc")\n')
            probe_args = ['--no_rcload', '--version'] if name == 'xschem' else ['--version']
            binary = (f'#!{sys.executable}\nimport os, sys\n'
                      f'assert sys.argv[1:] == {probe_args!r}\n'
                      'if os.environ.get("PORTABLE_TEST_PROBE_FAIL"): sys.exit(9)\n'
                      + startup +
                      f'print({banner!r})\nsys.exit({4 if version_exit == name else 0})\n')
            install = (
                'import json\nfrom pathlib import Path\n'
                'prefix = Path(json.loads(Path("configured.json").read_text())[0].split("=", 1)[1])\n'
                '(prefix / "bin").mkdir(parents=True, exist_ok=True)\n'
                f'target = prefix / "bin/{name}"\ntarget.write_text({binary!r})\ntarget.chmod(0o755)\n'
                f'(prefix / "share/{name}").mkdir(parents=True, exist_ok=True)\n'
                f'(prefix / "share/{name}/runtime.dat").write_text("installed runtime data")\n')
            flags = ['--without-opengl', '--disable-magic-builddate'] if name == 'magic' else []
            makefile = 'all:\n\t@true\ninstall:\n\t@' + shlex.quote(sys.executable) + ' install.py\n'
            configure = (
                f'#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\n'
                f'with Path({str(self.calls)!r}).open("a") as calls: calls.write({name!r} + "\\n")\n'
                f'assert sys.argv[2:] == {flags!r}\n'
                'assert sys.argv[1].startswith("--prefix=/")\n'
                f'if {fail_configure == name!r}:\n print("fixture configure failed")\n sys.exit(7)\n'
                'Path("configured.json").write_text(json.dumps(sys.argv[1:]))\n'
                f'Path("Makefile").write_text({makefile!r})\n')
            archive = self.archives / f'{name}.tar.gz'
            with tarfile.open(archive, 'w:gz') as contents:
                for relative, data, mode in [('configure', configure, 0o755),
                                              ('install.py', install, 0o644)]:
                    member = tarfile.TarInfo(f'{root}/{relative}')
                    encoded = data.encode()
                    member.size = len(encoded)
                    member.mode = mode
                    contents.addfile(member, io.BytesIO(encoded))
                if extra_member is not None and name == 'magic':
                    contents.addfile(extra_member)
            metadata['sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.sources_file.write_text(json.dumps(self.pins))

    def build(self, **changes):
        options = dict(prefix=self.prefix, archives_dir=self.archives,
                       skip_download=True, jobs=2, ngspice=str(self.ngspice))
        options.update(changes)
        return self.builder.build(**options)

    def test_verified_fixture_install_and_reuse_run_real_executables(self):
        manifest = self.build()
        self.assertEqual(manifest['tools']['magic']['version'], '8.3.684')
        self.assertEqual(manifest['tools']['xschem']['version'], '3.4.7')
        self.assertEqual(manifest['tools']['ngspice']['version'], '45.2')
        self.assertEqual(manifest['inputs']['sources'], self.pins)
        self.assertEqual(manifest, json.loads((self.prefix / 'installation.json').read_text()))
        self.assertIn('bin/magic', manifest['files'])
        self.assertIn('share/magic/runtime.dat', manifest['files'])
        self.assertTrue((self.prefix / 'logs/magic-configure.log').is_file())
        before = (self.prefix / 'installation.json').stat().st_mtime_ns
        self.assertEqual(self.build(), manifest)
        self.assertEqual((self.prefix / 'installation.json').stat().st_mtime_ns, before)
        self.assertEqual(self.calls.read_text().splitlines(), ['magic', 'xschem'])

    def test_wrapper_preserves_native_path_and_argument_boundaries(self):
        self.build()
        result = subprocess.run([str(self.prefix / 'bin/ngspice-sky130'), 'a b', '$(touch nope)', "x'y"],
                                capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), ['-D', 'ngbehavior=hsa', '-D', 'skywaterpdk',
                                                    '-D', 'ng_nomodcheck', 'a b', '$(touch nope)', "x'y"])

    def test_corrupt_archive_fails_before_configure_or_install(self):
        (self.archives / 'xschem.tar.gz').write_bytes(b'corrupt')
        with self.assertRaisesRegex(self.builder.BuildError, 'checksum|SHA256'):
            self.build()
        self.assertFalse(self.calls.exists())
        self.assertFalse((self.prefix / 'installation.json').exists())

    def test_missing_offline_archive_is_reported_without_download(self):
        (self.archives / 'magic.tar.gz').unlink()
        with self.assertRaisesRegex(self.builder.BuildError, 'Missing.*magic|magic.*missing'):
            self.build()
        self.assertFalse(self.calls.exists())

    def test_unexpected_source_url_is_rejected_before_any_build(self):
        self.pins['magic']['url'] = 'https://example.invalid/untrusted.tar.gz'
        self.sources_file.write_text(json.dumps(self.pins))
        with self.assertRaisesRegex(self.builder.BuildError, 'source|URL'):
            self.build()
        self.assertFalse(self.calls.exists())

    def test_tar_traversal_and_escaping_links_are_rejected(self):
        root = f'magic-{self.pins["magic"]["commit"]}'
        for name, link in [(f'{root}/../../escaped', None), ('unexpected/configure', None),
                           (f'{root}/unsafe', '../../escaped')]:
            with self.subTest(name=name):
                member = tarfile.TarInfo(name)
                if link:
                    member.type = tarfile.SYMTYPE
                    member.linkname = link
                self.fixture_sources(extra_member=member)
                with self.assertRaisesRegex(self.builder.BuildError, 'archive|source|Unsafe'):
                    self.build(prefix=self.base / f'bad-{len(name)}-{bool(link)}')
                self.assertFalse(self.calls.exists())
                self.assertFalse((self.base / 'escaped').exists())

    def test_safe_source_symlinks_are_supported(self):
        member = tarfile.TarInfo(f'magic-{self.pins["magic"]["commit"]}/alias.py')
        member.type = tarfile.SYMTYPE
        member.linkname = 'install.py'
        self.fixture_sources(extra_member=member)
        self.assertEqual(self.build()['tools']['magic']['version'], '8.3.684')

    def test_existing_unknown_prefix_is_preserved(self):
        self.prefix.mkdir()
        existing = self.prefix / 'existing-data'
        existing.write_text('keep this')
        with self.assertRaisesRegex(self.builder.BuildError, 'prefix|installation'):
            self.build()
        self.assertEqual(existing.read_text(), 'keep this')
        self.assertEqual(list(self.prefix.iterdir()), [existing])

    def test_failed_configure_retains_logs_without_success_manifest(self):
        self.fixture_sources(fail_configure='magic')
        with self.assertRaisesRegex(self.builder.BuildError, 'magic.*configure|configure.*magic'):
            self.build()
        self.assertIn('fixture configure failed', (self.prefix / 'logs/magic-configure.log').read_text())
        self.assertFalse((self.prefix / 'installation.json').exists())

    def test_installed_wrong_or_unsuccessful_version_is_not_success(self):
        for parameters in ({'bad_version': 'magic'}, {'version_exit': 'xschem'}):
            with self.subTest(parameters=parameters):
                self.fixture_sources(**parameters)
                destination = self.base / next(iter(parameters))
                with self.assertRaisesRegex(self.builder.BuildError, 'version|probe'):
                    self.build(prefix=destination)
                self.assertFalse((destination / 'installation.json').exists())

    def test_xschem_banner_does_not_hide_failed_tcl_initialization(self):
        # Xschem prints its version before Tcl_AppInit. A missing HOME makes
        # initialization fail with exit 1 even though the correct banner exists.
        executable = self.base / 'xschem-probe'
        executable.write_text(f'#!{sys.executable}\nimport sys\n'
                              'print("XSCHEM V3.4.7")\nsys.exit(1)\n')
        executable.chmod(0o755)
        with self.assertRaisesRegex(self.builder.BuildError, 'probe exited 1'):
            self.builder.probe(executable, 'xschem', '3.4.7')

    def test_xschem_probe_isolates_home_cwd_and_disables_rc_side_effects(self):
        ambient_home = self.base / 'ambient-home'
        (ambient_home / '.xschem').mkdir(parents=True)
        system_rc = self.base / 'system-xschemrc'
        markers = [self.base / name for name in ('cwd-rc-ran', 'home-rc-ran', 'system-rc-ran')]
        for rc, marker in zip((self.base / 'xschemrc', ambient_home / '.xschem/xschemrc', system_rc), markers):
            rc.write_text(str(marker))
        observation = self.base / 'probe-environment.json'
        executable = self.base / 'xschem-probe'
        executable.write_text(
            f'#!{sys.executable}\nimport json, os, sys\nfrom pathlib import Path\n'
            'home = Path(os.environ["HOME"])\n'
            '(home / ".probe-side-effect").write_text("private startup state")\n'
            f'Path({str(observation)!r}).write_text(json.dumps({{"home": str(home), "cwd": str(Path.cwd())}}))\n'
            'if "--no_rcload" not in sys.argv:\n'
            f' for rc in (Path.cwd() / "xschemrc", home / ".xschem/xschemrc", Path({str(system_rc)!r})):\n'
            '  if rc.is_file(): Path(rc.read_text()).write_text("rc executed")\n'
            'print("XSCHEM V3.4.7")\n')
        executable.chmod(0o755)
        with contextlib.chdir(self.base), mock.patch.dict(os.environ, {'HOME': str(ambient_home)}):
            self.builder.probe(executable, 'xschem', '3.4.7')
        self.assertFalse((ambient_home / '.probe-side-effect').exists())
        for marker in markers:
            self.assertFalse(marker.exists(), marker.name)
        actual = json.loads(observation.read_text())
        self.assertNotEqual(actual['home'], str(ambient_home))
        self.assertNotEqual(actual['cwd'], str(self.base))
        self.assertFalse(Path(actual['home']).exists(), 'Probe temporary HOME should be cleaned')
        self.assertFalse(Path(actual['cwd']).exists(), 'Probe temporary cwd should be cleaned')

    def test_xschem_startup_messages_do_not_invalidate_installation_reuse(self):
        ambient_home = self.base / 'ambient-home'
        ambient_home.mkdir()
        self.fixture_sources(startup_messages=True)
        with mock.patch.dict(os.environ, {'HOME': str(ambient_home)}):
            manifest = self.build()
            try:
                reused = self.build()
            except self.builder.BuildError as exc:
                self.fail(f'Verified tools must remain reusable after startup messages change: {exc}')
            self.assertEqual(reused, manifest)
        self.assertNotIn('Created', manifest['tools']['xschem']['version_output'])
        self.assertFalse((ambient_home / '.xschem').exists())

    def test_changed_installed_runtime_is_not_silently_reused_or_overwritten(self):
        self.build()
        runtime = self.prefix / 'share/magic/runtime.dat'
        runtime.write_text('modified')
        with self.assertRaisesRegex(self.builder.BuildError, 'changed|integrity|match'):
            self.build()
        self.assertEqual(runtime.read_text(), 'modified')
        self.assertEqual(self.calls.read_text().splitlines(), ['magic', 'xschem'])

    def test_missing_installed_executable_is_not_reused(self):
        self.build()
        (self.prefix / 'bin/xschem').unlink()
        with self.assertRaises(self.builder.BuildError):
            self.build()
        self.assertEqual(self.calls.read_text().splitlines(), ['magic', 'xschem'])

    def test_reuse_probes_unchanged_executables_in_current_environment(self):
        self.build()
        with mock.patch.dict(os.environ, {'PORTABLE_TEST_PROBE_FAIL': '1'}), \
                self.assertRaisesRegex(self.builder.BuildError, 'probe'):
            self.build()
        self.assertEqual(self.calls.read_text().splitlines(), ['magic', 'xschem'])

    def test_recorded_inputs_must_match_requested_sources_and_simulator(self):
        self.build()
        self.pins['magic']['sha256'] = '0' * 64
        self.sources_file.write_text(json.dumps(self.pins))
        with self.assertRaisesRegex(self.builder.BuildError, 'inputs|match'):
            self.build()
        self.assertEqual(self.calls.read_text().splitlines(), ['magic', 'xschem'])

    def test_changed_native_simulator_is_rejected(self):
        self.build()
        self.write_simulator(version='46')
        with self.assertRaisesRegex(self.builder.BuildError, 'changed|match|integrity'):
            self.build()

    def test_missing_or_unsuccessful_native_simulator_fails_before_build(self):
        self.write_simulator(status=3)
        with self.assertRaisesRegex(self.builder.BuildError, 'ngspice|probe'):
            self.build()
        with self.assertRaisesRegex(self.builder.BuildError, 'ngspice|executable'):
            self.build(ngspice=str(self.base / 'does-not-exist'))
        self.assertFalse(self.calls.exists())

    def test_jobs_outside_bounded_range_are_rejected(self):
        for jobs in (0, -1, 65, True, 1.5):
            with self.subTest(jobs=jobs), self.assertRaises(self.builder.BuildError):
                self.build(jobs=jobs)
        result = subprocess.run([sys.executable, str(SCRIPT), '--jobs', '65'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('1', result.stderr)
        self.assertFalse(self.calls.exists())

    def download_response(self, payload, declared_size=None):
        response = io.BytesIO(payload)
        response.headers = {'Content-Length': str(len(payload) if declared_size is None else declared_size)}
        response.geturl = lambda: self.pins['magic']['url']
        return response

    def test_download_is_verified_and_atomic(self):
        payload = (self.archives / 'magic.tar.gz').read_bytes()
        target = self.archives / 'download.tar.gz'
        response = self.download_response(payload)
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(self.builder.urllib.request, 'build_opener', return_value=opener):
            self.builder.download_archive(self.pins['magic'], target)
        self.assertEqual(target.read_bytes(), payload)
        self.assertEqual(sorted(path.name for path in self.archives.iterdir()),
                         ['download.tar.gz', 'magic.tar.gz', 'xschem.tar.gz'])

    def test_bad_or_oversized_download_never_publishes_archive(self):
        for payload, limit, declared_size in [(b'wrong content', 1024, None),
                                               (b'too much', 4, None),
                                               (b'incomplete', 1024, 100)]:
            with self.subTest(payload=payload):
                response = self.download_response(payload, declared_size)
                opener = mock.Mock()
                opener.open.return_value = response
                with mock.patch.object(self.builder.urllib.request, 'build_opener', return_value=opener), \
                        mock.patch.object(self.builder, 'MAX_ARCHIVE_BYTES', limit), \
                        self.assertRaises(self.builder.BuildError):
                    self.builder.download_archive(self.pins['magic'], self.archives / 'download.tar.gz')
                self.assertEqual(sorted(path.name for path in self.archives.iterdir()),
                                 ['magic.tar.gz', 'xschem.tar.gz'])

    def test_download_limits_stream_without_content_length(self):
        response = self.download_response(b'12345678')
        response.headers = {}
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(self.builder.urllib.request, 'build_opener', return_value=opener), \
                mock.patch.object(self.builder, 'MAX_ARCHIVE_BYTES', 4), \
                self.assertRaisesRegex(self.builder.BuildError, 'size limit'):
            self.builder.download_archive(self.pins['magic'], self.archives / 'download.tar.gz')
        self.assertFalse((self.archives / 'download.tar.gz').exists())

    def test_download_failure_preserves_existing_cache(self):
        target = self.archives / 'download.tar.gz'
        target.write_bytes(b'previous cache')
        opener = mock.Mock()
        opener.open.side_effect = TimeoutError('network timeout')
        with mock.patch.object(self.builder.urllib.request, 'build_opener', return_value=opener), \
                self.assertRaisesRegex(self.builder.BuildError, 'timeout'):
            self.builder.download_archive(self.pins['magic'], target)
        self.assertEqual(target.read_bytes(), b'previous cache')
        self.assertFalse(list(self.archives.glob('*.part')))

    def test_insecure_source_redirect_is_refused(self):
        handler = self.builder._PinnedRedirectHandler()
        request = self.builder.urllib.request.Request(self.pins['magic']['url'])
        for target in ('http://codeload.github.com/unsafe', 'https://example.invalid/unsafe'):
            with self.subTest(target=target), self.assertRaises(self.builder.BuildError):
                handler.redirect_request(request, None, 302, 'Redirect', {}, target)


if __name__ == '__main__':
    unittest.main()
