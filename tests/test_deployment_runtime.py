"""Deployment boundaries use isolated workspaces, ports, and real child processes."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'


def module(name):
    assert (SCRIPTS / (name + '.py')).is_file(), f'Deployment module {name} is missing'
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


def unused_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_file(path, process, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            raise AssertionError(f'Launcher exited {process.returncode}: {process.communicate()[1]}')
        time.sleep(0.03)
    raise AssertionError(f'Timed out waiting for {path}')


class DeploymentConfigTests(unittest.TestCase):
    def setUp(self):
        self.runtime = module('run_deployment')
        self.base = {'schema_version': 1, 'workspace': '/tmp/example workspace',
                     'python': sys.executable}

    def test_defaults_and_shell_metacharacters_are_preserved_as_argv(self):
        self.base['workspace'] = '/tmp/work $HOME %value "quoted" ; untouched'
        config = self.runtime.validate_config(self.base)
        self.assertEqual(self.runtime.app_command(config), [sys.executable, '-m',
                         'analog_factory', '--workspace', self.base['workspace'],
                         'serve', '--host', '127.0.0.1', '--port', '8765'])
        self.assertFalse(config['desktop'])
        self.assertEqual(config['desktop_port'], 6080)

    def test_malformed_or_unbounded_config_fails_before_launch(self):
        cases = [({'schema_version': True}, 'schema_version'),
                 ({'schema_version': 2}, 'schema_version'),
                 ({'workspace': 'relative'}, 'workspace'),
                 ({'python': '/tmp/line\nbreak'}, 'python'),
                 ({'workspace': '/' + 'x' * 5000}, 'workspace'),
                 ({'host': '0.0.0.0'}, 'host'),
                 ({'host': '255.255.255.255'}, 'host'),
                 ({'host': '224.0.0.1'}, 'host'),
                 ({'host': 'localhost'}, 'host'),
                 ({'host': '::1'}, 'host'),
                 ({'port': True}, 'port'), ({'port': 0}, 'port'),
                 ({'port': 65536}, 'port'), ({'desktop': 'false'}, 'desktop'),
                 ({'desktop_port': -1}, 'desktop_port'),
                 ({'extra_path': '/tmp/bin'}, 'extra_path'),
                 ({'extra_path': ['relative']}, 'extra_path'),
                 ({'extra_path': ['/tmp'] * 65}, 'extra_path'),
                 ({'pdk_root': 'relative'}, 'pdk_root'),
                 ({'unexpected': 1}, 'unexpected')]
        for update, field in cases:
            with self.subTest(update=update), self.assertRaisesRegex(ValueError, field):
                self.runtime.validate_config(self.base | update)

    def test_config_file_rejects_duplicates_and_oversized_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'config.json'
            path.write_text('{"schema_version": 1, "schema_version": 1}')
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                self.runtime.load_config(path)
            path.write_text(' ' * 65537)
            with self.assertRaisesRegex(ValueError, 'large'):
                self.runtime.load_config(path)

    def test_utf16_config_is_rejected_instead_of_accepting_non_utf8_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'config.json'
            path.write_bytes(json.dumps(self.base).encode('utf-16'))
            with self.assertRaisesRegex(ValueError, 'UTF-8'):
                self.runtime.load_config(path)

    def test_environment_preserves_login_authority_and_prepends_tools(self):
        config = self.runtime.validate_config(self.base | {
            'extra_path': ['/tmp/tool one', '/tmp/tool-two'], 'pdk_root': '/tmp/pdk'})
        with patch.dict(os.environ, {'PATH': '/usr/bin', 'HOME': '/tmp/login',
                                    'OPENAI_API_KEY': 'placeholder', 'DISPLAY': ':77'}, clear=True):
            env = self.runtime.child_environment(config)
        self.assertEqual(env['PATH'], '/tmp/tool one:/tmp/tool-two:/usr/bin')
        self.assertEqual(env['HOME'], '/tmp/login')
        self.assertEqual(env['OPENAI_API_KEY'], 'placeholder')
        self.assertEqual(env['DISPLAY'], ':77')
        self.assertEqual(env['PDK_ROOT'], '/tmp/pdk')


class DeploymentLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.events = self.folder / 'events.json'
        self.stopped = self.folder / 'stopped'
        self.python = self.folder / 'fake python $literal%'
        self.python.write_text('#!' + sys.executable + '\n' + '''
import json, os, signal, sys, time
from pathlib import Path
events = Path(os.environ['AF_TEST_EVENTS'])
def stop(signum, frame):
    Path(os.environ['AF_TEST_STOPPED']).write_text(str(signum))
    raise SystemExit(0)
signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
events.write_text(json.dumps({'argv': sys.argv[1:], 'pid': os.getpid(),
                             'path': os.environ['PATH'],
                             'home': os.environ.get('HOME'),
                             'display': os.environ.get('DISPLAY'),
                             'authority': os.environ.get('XAUTHORITY'),
                             'desktop_url': os.environ.get('AF_DESKTOP_URL')}))
if os.environ.get('AF_TEST_EXIT'):
    raise SystemExit(int(os.environ['AF_TEST_EXIT']))
while True:
    time.sleep(.1)
''')
        self.python.chmod(0o700)
        self.config = self.folder / 'config.json'
        self.data = {'schema_version': 1, 'workspace': str(self.folder / 'workspace'),
                     'python': str(self.python), 'port': unused_port(),
                     'extra_path': [str(self.folder / 'bin')]}
        self.config.write_text(json.dumps(self.data))

    def launch(self, desktop_tools=None, **env_update):
        env = os.environ | {'AF_TEST_EVENTS': str(self.events),
                            'AF_TEST_STOPPED': str(self.stopped)} | env_update
        command = [sys.executable, str(SCRIPTS / 'run_deployment.py'), '--config', str(self.config)]
        if desktop_tools:
            code = ('import sys; sys.path.insert(0, sys.argv[1]); import run_deployment as r; '
                    'r.desktop_dependencies = lambda env: ' + repr(desktop_tools) + '; '
                    'sys.exit(r.main(["--config", sys.argv[2]]))')
            command = [sys.executable, '-c', code, str(SCRIPTS), str(self.config)]
        process = subprocess.Popen(command, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.cleanup_process, process)
        return process

    @staticmethod
    def cleanup_process(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=12)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
        else:
            process.communicate()

    def test_foreground_forwards_interrupt_and_preserves_argv(self):
        process = self.launch()
        wait_file(self.events, process)
        events = json.loads(self.events.read_text())
        self.assertEqual(events['argv'], ['-m', 'analog_factory', '--workspace',
                         self.data['workspace'], 'serve', '--host', '127.0.0.1',
                         '--port', str(self.data['port'])])
        self.assertTrue(events['path'].startswith(self.data['extra_path'][0] + ':'))
        self.assertEqual(events['home'], os.environ.get('HOME'))
        process.send_signal(signal.SIGTERM)
        _, errors = process.communicate(timeout=12)
        self.assertEqual(process.returncode, 0, errors)
        self.assertEqual(self.stopped.read_text(), str(signal.SIGINT))
        with self.assertRaises(ProcessLookupError):
            os.kill(events['pid'], 0)

    def test_port_check_allows_restart_after_server_connection_enters_time_wait(self):
        runtime = module('run_deployment')
        with socket.socket() as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(('127.0.0.1', self.data['port']))
            server.listen()
            client = socket.create_connection(server.getsockname())
            accepted, _ = server.accept()
            accepted.close()
            client.close()
        runtime.assert_port_free('127.0.0.1', self.data['port'])

    def test_same_workspace_cannot_start_twice_even_on_another_port(self):
        first = self.launch()
        wait_file(self.events, first)
        self.data['port'] = unused_port()
        self.config.write_text(json.dumps(self.data))
        second = self.launch()
        _, errors = second.communicate(timeout=8)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn('workspace', errors.lower())
        self.assertIsNone(first.poll())
        self.assertFalse(self.stopped.exists())

    def test_busy_app_port_refuses_without_spawning_or_stopping_foreign_owner(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', self.data['port']))
            listener.listen()
            process = self.launch()
            _, errors = process.communicate(timeout=8)
            self.assertNotEqual(process.returncode, 0)
            self.assertIn('port', errors.lower())
            self.assertFalse(self.events.exists())
            self.assertEqual(listener.getsockname()[1], self.data['port'])

    def test_unexpected_app_exit_is_reported_as_failure(self):
        process = self.launch(AF_TEST_EXIT='7')
        _, errors = process.communicate(timeout=8)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn('7', errors)
        self.assertIn('app', errors.lower())

    def detached_worker_app(self, *, exit_early=False, worker_exits=False):
        worker_record = self.folder / 'detached-worker.json'
        worker = self.folder / 'detached-worker.py'
        worker.write_text('''import json, os, signal, sys, time
from pathlib import Path
signal.signal(signal.SIGINT, signal.SIG_IGN)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).write_text(json.dumps({'pid': os.getpid()}))
''' + ('raise SystemExit(0)\n' if worker_exits else '') + '''
while True:
    time.sleep(.1)
''')
        self.python.write_text('#!' + sys.executable + '\n' + f'''
import json, os, signal, subprocess, sys, time
from pathlib import Path
signal.signal(signal.SIGINT, lambda signum, frame: sys.exit(0))
# A short-lived intermediate process creates an independent session and exits.
# The surviving worker is not in either the app or launcher's process group.
code = 'import subprocess, sys; subprocess.Popen(sys.argv[1:], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)'
subprocess.run([sys.executable, '-c', code, sys.executable, {str(worker)!r},
                {str(worker_record)!r}], check=True)
while not Path({str(worker_record)!r}).exists():
    time.sleep(.01)
Path(os.environ['AF_TEST_EVENTS']).write_text(json.dumps({{'pid': os.getpid()}}))
if {exit_early!r}:
    raise SystemExit(7)
while True:
    time.sleep(.1)
''')
        return worker_record

    @staticmethod
    def close_worker_handle(descriptor):
        # Cleanup a failed regression via a stable reference to our fixture only.
        try:
            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
        except ProcessLookupError:
            pass
        finally:
            os.close(descriptor)

    def test_shutdown_reaps_detached_descendants_without_stopping_unrelated_process(self):
        record = self.detached_worker_app()
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                     start_new_session=True)
        self.addCleanup(self.cleanup_process, unrelated)
        process = self.launch()
        wait_file(record, process)
        worker = json.loads(record.read_text())['pid']
        descriptor = os.pidfd_open(worker)
        self.addCleanup(self.close_worker_handle, descriptor)
        wait_file(self.events, process)
        process.send_signal(signal.SIGTERM)
        _, errors = process.communicate(timeout=12)
        self.assertEqual(process.returncode, 0, errors)
        self.assertIsNone(unrelated.poll(), 'A process outside this deployment was stopped')
        with self.assertRaises(ProcessLookupError):
            os.kill(worker, 0)

    def test_unexpected_app_exit_cleans_its_detached_workers_and_still_fails(self):
        record = self.detached_worker_app(exit_early=True)
        process = self.launch()
        wait_file(record, process)
        worker = json.loads(record.read_text())['pid']
        descriptor = os.pidfd_open(worker)
        self.addCleanup(self.close_worker_handle, descriptor)
        _, errors = process.communicate(timeout=12)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn('app', errors.lower())
        with self.assertRaises(ProcessLookupError):
            os.kill(worker, 0)

    def test_exited_adopted_worker_is_reaped_while_application_keeps_running(self):
        record = self.detached_worker_app(worker_exits=True)
        process = self.launch()
        wait_file(record, process)
        worker = json.loads(record.read_text())['pid']
        wait_file(self.events, process)
        deadline = time.monotonic() + 2
        while Path(f'/proc/{worker}').exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertFalse(Path(f'/proc/{worker}').exists(), 'Exited adopted worker remained a zombie')
        self.assertIsNone(process.poll(), 'Reaping a completed worker stopped the deployment')

    def test_supervision_rejects_existing_unrelated_child_without_signalling_it(self):
        code = '''import subprocess, sys
sys.path.insert(0, sys.argv[1])
import run_deployment as runtime
other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
try:
    try:
        with runtime.Supervisor():
            raise AssertionError('Accepted an existing unrelated subtree')
    except RuntimeError as error:
        assert 'pre-existing children' in str(error)
        assert other.poll() is None
finally:
    other.terminate()
    other.wait(timeout=5)
'''
        result = subprocess.run([sys.executable, '-c', code, str(SCRIPTS)],
                                capture_output=True, text=True, timeout=12)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unsupported_subreaper_fails_before_launching_any_process(self):
        code = '''import sys
sys.path.insert(0, sys.argv[1])
import run_deployment as runtime
runtime.os.pidfd_open = None
raise SystemExit(runtime.main(['--config', sys.argv[2]]))
'''
        result = subprocess.run([sys.executable, '-c', code, str(SCRIPTS), str(self.config)],
                                capture_output=True, text=True, timeout=12)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('pidfd', result.stderr.lower())
        self.assertFalse(self.events.exists())

    def test_missing_desktop_tools_fail_explicitly_before_starting_app(self):
        self.data['desktop'] = True
        self.data['desktop_port'] = unused_port()
        self.config.write_text(json.dumps(self.data))
        process = self.launch(PATH=str(self.folder / 'no-tools'))
        _, errors = process.communicate(timeout=8)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn('desktop', errors.lower())
        self.assertIn('Xvfb', errors)
        self.assertFalse(self.events.exists())

    def desktop_tools(self):
        tool = self.folder / 'desktop-tool'
        tool.write_text('#!' + sys.executable + '\n' + '''
import json, os, signal, socket, sys, time
from pathlib import Path
name = Path(sys.argv[0]).name
if name == 'xauth':
    sys.stdin.read()
    raise SystemExit(0)
folder = Path(os.environ['AF_TEST_EVENTS']).parent
(folder / (name + '.json')).write_text(json.dumps({'pid': os.getpid(), 'argv': sys.argv[1:],
                                                'home': os.environ.get('HOME')}))
listener = None
if name == 'Xvfb':
    fd = int(sys.argv[sys.argv.index('-displayfd') + 1])
    os.write(fd, b'73\\n')
    os.close(fd)
elif name == 'x11vnc':
    address = ('127.0.0.1', int(sys.argv[sys.argv.index('-rfbport') + 1]))
elif name == 'websockify':
    host, port = sys.argv[-2].split(':')
    address = (host, int(port))
if name in ('x11vnc', 'websockify'):
    listener = socket.socket()
    listener.bind(address)
    listener.listen()
    listener.settimeout(.1)
deadline = time.monotonic() + .8
while True:
    if name == os.environ.get('AF_TEST_FAIL_COMPONENT') and time.monotonic() > deadline:
        raise SystemExit(8)
    if listener:
        try:
            client, _ = listener.accept()
            client.close()
        except TimeoutError:
            pass
    else:
        time.sleep(.1)
''')
        tool.chmod(0o700)
        result = {}
        for name in ('Xvfb', 'xauth', 'fluxbox', 'x11vnc', 'websockify'):
            path = self.folder / name
            path.symlink_to(tool)
            result[name] = str(path)
        assets = self.folder / 'novnc'
        assets.mkdir()
        (assets / 'vnc.html').write_text('test noVNC assets')
        result['novnc'] = str(assets)
        self.data['desktop'] = True
        self.data['desktop_port'] = unused_port()
        self.config.write_text(json.dumps(self.data))
        return result

    def test_private_desktop_propagates_authority_and_cleans_owned_processes(self):
        process = self.launch(desktop_tools=self.desktop_tools(), DISPLAY=':987')
        wait_file(self.events, process)
        events = json.loads(self.events.read_text())
        self.assertEqual(events['display'], ':73')
        authority = Path(events['authority'])
        self.assertEqual(authority.stat().st_mode & 0o777, 0o600)
        self.assertEqual(authority.parent.stat().st_mode & 0o777, 0o700)
        components = [json.loads((self.folder / (name + '.json')).read_text())
                      for name in ('Xvfb', 'fluxbox', 'x11vnc', 'websockify')]
        self.assertTrue(Path(components[1]['home']).is_relative_to(authority.parent))
        self.assertEqual(events['home'], os.environ.get('HOME'))
        self.assertIn('-nolisten', components[0]['argv'])
        self.assertIn('tcp', components[0]['argv'])
        self.assertIn(str(authority), components[0]['argv'])
        self.assertIn('127.0.0.1', components[2]['argv'])
        self.assertIn('-no6', components[2]['argv'])
        self.assertEqual(components[3]['argv'][-2], f'127.0.0.1:{self.data["desktop_port"]}')
        self.assertTrue(components[3]['argv'][-1].startswith('127.0.0.1:'))
        process.send_signal(signal.SIGINT)
        _, errors = process.communicate(timeout=12)
        self.assertEqual(process.returncode, 0, errors)
        self.assertFalse(authority.parent.exists())
        for component in components:
            with self.assertRaises(ProcessLookupError):
                os.kill(component['pid'], 0)

    def test_private_desktop_backend_url_is_accepted_by_the_real_application_proxy(self):
        from analog_factory.desktop import DesktopProxy

        process = self.launch(desktop_tools=self.desktop_tools())
        wait_file(self.events, process)
        events = json.loads(self.events.read_text())
        proxy = DesktopProxy(events['desktop_url'])
        try:
            self.assertEqual(proxy.port, self.data['desktop_port'])
        finally:
            proxy.close()

    def test_desktop_component_death_stops_app_and_reports_failure(self):
        process = self.launch(desktop_tools=self.desktop_tools(), AF_TEST_FAIL_COMPONENT='fluxbox')
        wait_file(self.events, process)
        _, errors = process.communicate(timeout=12)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn('fluxbox', errors)
        self.assertEqual(self.stopped.read_text(), str(signal.SIGINT))

    def test_busy_desktop_port_refuses_before_any_child(self):
        tools = self.desktop_tools()
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', self.data['desktop_port']))
            listener.listen()
            process = self.launch(desktop_tools=tools)
            _, errors = process.communicate(timeout=8)
            self.assertNotEqual(process.returncode, 0)
            self.assertIn('port', errors.lower())
            self.assertFalse(self.events.exists())
            self.assertFalse((self.folder / 'Xvfb.json').exists())


class DeploymentServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = module('manage_service')
        self.runtime = module('run_deployment')
        self.config = self.runtime.validate_config({
            'schema_version': 1, 'workspace': '/tmp/workspace', 'python': sys.executable})

    def test_unit_escapes_systemd_specifiers_quotes_and_environment_syntax(self):
        rendered = self.service.render_unit(self.config, Path('/tmp/config %x "$y".json'),
                                            Path('/tmp/root %n "$HOME"'))
        self.assertIn('WorkingDirectory=/tmp/root %%n "$HOME"/\n', rendered)
        self.assertIn('"/tmp/config %%x \\"$$y\\".json"', rendered)
        self.assertIn('"/tmp/root %%n \\"$$HOME\\"/scripts/run_deployment.py"', rendered)
        self.assertIn('KillSignal=SIGINT\n', rendered)
        self.assertIn('KillMode=control-group\n', rendered)
        self.assertNotIn('ExecStart=/bin/sh', rendered)

    @unittest.skipUnless(shutil.which('systemd-analyze'), 'systemd parser is unavailable')
    def test_systemd_accepts_actual_paths_with_spaces_quotes_dollars_and_percent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'root %n "$HOME"'
            root.mkdir()
            python = root / 'python $literal'
            python.symlink_to(sys.executable)
            unit = Path(temporary) / 'af-test.service'
            unit.write_text(self.service.render_unit(self.config | {'python': str(python)},
                                                     root / 'config %x.json', root))
            result = subprocess.run(['systemd-analyze', 'verify', str(unit)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_unit_name_is_stable_for_workspace_aliases(self):
        self.assertEqual(self.service.unit_name('/tmp/workspace'),
                         self.service.unit_name('/tmp/unused/../workspace'))
        self.assertNotEqual(self.service.unit_name('/tmp/workspace'),
                            self.service.unit_name('/tmp/other'))

    def test_install_refuses_existing_foreign_unit(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'unit.service'
            target.write_text('[Service]\nExecStart=/usr/bin/sleep infinity\n')
            with self.assertRaisesRegex(ValueError, 'foreign'):
                self.service.install_unit(target, '# Managed by Analog Factory\nnew')
            self.assertIn('/usr/bin/sleep', target.read_text())

    def test_service_commands_only_install_start_and_remove_when_requested(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            config = folder / 'config.json'
            config.write_text(json.dumps(self.config))
            control = folder / 'systemctl'
            log = folder / 'systemctl.jsonl'
            control.write_text('#!' + sys.executable + '\n' + '''
import json, os, sys
with open(os.environ['AF_TEST_SYSTEMCTL'], 'a') as output:
    output.write(json.dumps(sys.argv[1:]) + '\\n')
raise SystemExit(3 if 'status' in sys.argv else 0)
''')
            control.chmod(0o700)
            env = os.environ | {'PATH': str(folder), 'XDG_CONFIG_HOME': str(folder / 'xdg'),
                                'AF_TEST_SYSTEMCTL': str(log)}

            def invoke(command):
                return subprocess.run([sys.executable, str(SCRIPTS / 'manage_service.py'),
                                       command, '--config', str(config)], env=env,
                                      capture_output=True, text=True)

            result = invoke('print')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('KillMode=control-group', result.stdout)
            self.assertFalse(log.exists())
            self.assertFalse((folder / 'xdg').exists())
            result = invoke('install')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([json.loads(line) for line in log.read_text().splitlines()],
                             [['--user', 'daemon-reload']])
            units = list((folder / 'xdg/systemd/user').glob('*.service'))
            self.assertEqual(len(units), 1)
            unit = units[0]
            self.assertIn(str(config), unit.read_text())
            self.assertEqual(invoke('enable').returncode, 0)
            self.assertEqual(invoke('start').returncode, 0)
            self.assertEqual(invoke('status').returncode, 3)
            self.assertEqual(invoke('disable').returncode, 0)
            self.assertEqual(invoke('uninstall').returncode, 0)
            self.assertFalse(unit.exists())
            self.assertEqual([json.loads(line) for line in log.read_text().splitlines()],
                             [['--user', 'daemon-reload'], ['--user', 'enable', unit.name],
                              ['--user', 'start', unit.name],
                              ['--user', 'status', unit.name],
                              ['--user', 'disable', unit.name],
                              ['--user', 'disable', '--now', unit.name],
                              ['--user', 'daemon-reload']])

    def test_service_helper_refuses_to_follow_a_unit_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            foreign = folder / 'foreign.service'
            foreign.write_text('unchanged')
            unit = folder / 'unit.service'
            unit.symlink_to(foreign)
            with self.assertRaisesRegex(ValueError, 'foreign'):
                self.service.install_unit(unit, self.service.MARKER + '[Service]\n')
            self.assertEqual(foreign.read_text(), 'unchanged')


if __name__ == '__main__':
    unittest.main()
