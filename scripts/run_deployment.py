#!/usr/bin/env python3
"""Run a configured workspace and, optionally, an owned private desktop."""
import argparse
import contextlib
import ctypes
import ipaddress
import json
import os
from pathlib import Path
import secrets
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
MAX_CONFIG_BYTES = 65536


def absolute_path(value, field):
    if (not isinstance(value, str) or not value or len(value) > 4096
            or any(ord(character) < 32 for character in value)
            or not Path(value).is_absolute()):
        raise ValueError(f'{field} must be an absolute path of at most 4096 characters without controls')
    return value


def validate_config(data):
    allowed = {'schema_version', 'workspace', 'python', 'host', 'port',
               'extra_path', 'pdk_root', 'desktop', 'desktop_port'}
    if not isinstance(data, dict):
        raise ValueError('deployment config must be a JSON object')
    unknown = data.keys() - allowed
    if unknown:
        raise ValueError('Unknown config fields: ' + ', '.join(sorted(unknown)))
    if type(data.get('schema_version')) is not int or data['schema_version'] != 1:
        raise ValueError('schema_version must be 1')
    config = dict(data)
    for field in ('workspace', 'python'):
        config[field] = absolute_path(data.get(field), field)
    host = data.get('host', '127.0.0.1')
    try:
        if not isinstance(host, str):
            raise ValueError()
        address = ipaddress.IPv4Address(host)
        if address.is_unspecified or address.is_multicast or host == '255.255.255.255':
            raise ValueError()
    except ValueError:
        raise ValueError('host must be a concrete local IPv4 address') from None
    config['host'] = str(address)
    for field, default in (('port', 8765), ('desktop_port', 6080)):
        value = data.get(field, default)
        if type(value) is not int or not 1 <= value <= 65535:
            raise ValueError(f'{field} must be an integer from 1 to 65535')
        config[field] = value
    desktop = data.get('desktop', False)
    if type(desktop) is not bool:
        raise ValueError('desktop must be a boolean')
    config['desktop'] = desktop
    extra_path = data.get('extra_path', [])
    if not isinstance(extra_path, list) or len(extra_path) > 64:
        raise ValueError('extra_path must be a list of at most 64 absolute directories')
    config['extra_path'] = [absolute_path(value, 'extra_path') for value in extra_path]
    pdk = data.get('pdk_root')
    config['pdk_root'] = absolute_path(pdk, 'pdk_root') if pdk is not None else None
    return config


def load_config(path):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'duplicate config field: {key}')
            result[key] = value
        return result

    with Path(path).open('rb') as source:
        raw = source.read(MAX_CONFIG_BYTES + 1)
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError('deployment config is too large (maximum 65536 bytes)')
    try:
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_pairs)
    except (UnicodeDecodeError, RecursionError) as error:
        raise ValueError('deployment config is not bounded UTF-8 JSON') from error
    return validate_config(data)


def app_command(config):
    return [config['python'], '-m', 'analog_factory', '--workspace', config['workspace'],
            'serve', '--host', config['host'], '--port', str(config['port'])]


def child_environment(config):
    env = os.environ.copy()
    if config['extra_path']:
        env['PATH'] = os.pathsep.join(config['extra_path'] + [env.get('PATH', os.defpath)])
    if config['pdk_root'] is not None:
        env['PDK_ROOT'] = config['pdk_root']
    return env


def assert_port_free(host, port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            # Match HTTPServer's reuse policy: a stopped server's TIME_WAIT
            # connections must not prevent an immediate deployment restart.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((host, port))
    except OSError as error:
        raise ValueError(f'port {host}:{port} is unavailable: {error}') from error


@contextlib.contextmanager
def workspace_lock(workspace):
    import fcntl

    directory = Path(workspace).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / '.analog-factory-deployment.lock'
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f'workspace lock is not a regular file: {path}')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(f'workspace already has a deployment running: {directory}') from None
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f'{os.getpid()}\n'.encode())
        yield
    finally:
        # Keep the inode: unlinking a flock file allows overlapping owners.
        os.close(descriptor)


class Stopping(Exception):
    """The caller requested an orderly shutdown during startup or supervision."""


class Supervisor:
    def __init__(self):
        self.children = []
        self.stopping = False
        self.owned = {}
        self.direct = {}
        self.previous_subreaper = None

    @staticmethod
    def child_pids():
        """Only kernel-reported children of this launcher, including adoptees."""
        children = set()
        for task in Path('/proc/self/task').iterdir():
            try:
                children.update(map(int, (task / 'children').read_text().split()))
            except FileNotFoundError:
                # A thread may finish while its children transfer to another.
                continue
        return children

    @staticmethod
    def birth(pid):
        try:
            record = Path(f'/proc/{pid}/stat').read_text()
        except FileNotFoundError:
            return None
        fields = record[record.rfind(')') + 2:].split()
        return fields[19] if int(fields[1]) == os.getpid() else None

    def pin(self, pid):
        identity = self.birth(pid)
        if identity is None:
            return None
        key = (pid, identity)
        if key not in self.owned:
            try:
                descriptor = os.pidfd_open(pid)
            except ProcessLookupError:
                return None
            if self.birth(pid) != identity:
                os.close(descriptor)
                return None
            self.owned[key] = descriptor
        return key

    @staticmethod
    def subreaper(value=None):
        """Enable adoption without changing another process or its session."""
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            if value is None:
                current = ctypes.c_int()
                result = libc.prctl(37, ctypes.byref(current), 0, 0, 0)
            else:
                result = libc.prctl(36, int(value), 0, 0, 0)
            if result != 0:
                raise OSError(ctypes.get_errno(), 'prctl failed')
            return current.value if value is None else None
        except (AttributeError, OSError) as error:
            raise RuntimeError('Linux child-subreaper support is required for owned-process cleanup') from error

    def __enter__(self):
        if (not callable(getattr(os, 'pidfd_open', None))
                or not callable(getattr(signal, 'pidfd_send_signal', None))
                or not hasattr(os, 'P_PIDFD')):
            raise RuntimeError('Linux pidfd support is required for owned-process cleanup')
        # Imported use must not adopt an unrelated subtree already owned by the
        # caller. The standalone launcher has no children at this point.
        if self.child_pids():
            raise RuntimeError('Deployment supervision requires a process with no pre-existing children')
        descriptor = os.pidfd_open(os.getpid())
        os.close(descriptor)
        previous = self.subreaper()
        self.subreaper(1)
        self.previous_subreaper = previous
        return self

    def __exit__(self, kind, error, traceback):
        try:
            self.close()
        finally:
            if self.previous_subreaper is not None:
                self.subreaper(self.previous_subreaper)
                self.previous_subreaper = None

    def request_stop(self, signum, frame):
        self.stopping = True

    def start(self, name, command, env, **kwargs):
        self.check()
        process = subprocess.Popen(command, cwd=ROOT, env=env, start_new_session=True, **kwargs)
        self.children.append((name, process))
        key = self.pin(process.pid)
        if key is None:
            raise RuntimeError(f'Could not retain the owned process identity for {name}')
        self.direct[key] = process
        return process

    def check(self):
        if self.stopping:
            raise Stopping()
        for name, process in self.children:
            status = process.poll()
            if status is not None:
                raise RuntimeError(f'{name} exited unexpectedly with status {status}')
        self.reap_adopted()

    def reap_adopted(self):
        """Reap completed adoptees without interrupting their running siblings."""
        for pid in self.child_pids():
            self.pin(pid)
        for key in self.owned.keys() - self.direct.keys():
            descriptor = self.owned[key]
            try:
                result = os.waitid(os.P_PIDFD, descriptor, os.WEXITED | os.WNOHANG)
            except ChildProcessError:
                # Already reaped children must not retain an open pidfd forever.
                result = True
            if result is not None:
                os.close(self.owned.pop(key))

    def wait_port(self, host, port, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.check()
            try:
                with socket.create_connection((host, port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError(f'timed out waiting for desktop port {host}:{port}')

    def close(self):
        def send(key, signum):
            try:
                signal.pidfd_send_signal(self.owned[key], signum)
            except ProcessLookupError:
                pass

        def reap():
            # Popen retains the exit code of its own direct child. Other
            # children were adopted from this launcher's subtree by the kernel.
            for process in self.direct.values():
                process.poll()
            self.reap_adopted()

        terminated = set()
        try:
            for key, process in self.direct.items():
                name = next(name for name, child in self.children if child is process)
                send(key, signal.SIGINT if name == 'app' else signal.SIGTERM)
                terminated.add(key)
            grace = time.monotonic() + 8
            deadline = grace + 2
            while True:
                for pid in self.child_pids():
                    key = self.pin(pid)
                    if key is None:
                        continue
                    if time.monotonic() >= grace:
                        send(key, signal.SIGKILL)
                    elif key not in terminated:
                        send(key, signal.SIGTERM)
                        terminated.add(key)
                reap()
                if not self.child_pids():
                    return
                if time.monotonic() >= deadline:
                    raise RuntimeError('Timed out cleaning owned deployment descendants')
                time.sleep(.02)
        finally:
            for descriptor in self.owned.values():
                os.close(descriptor)
            self.owned.clear()


def desktop_dependencies(env):
    dependencies = {name: shutil.which(name, path=env.get('PATH'))
                    for name in ('Xvfb', 'xauth', 'fluxbox', 'x11vnc', 'websockify')}
    missing = [name for name, path in dependencies.items() if path is None]
    assets = Path('/usr/share/novnc')
    if not (assets / 'vnc.html').is_file():
        missing.append('noVNC (/usr/share/novnc/vnc.html)')
    if missing:
        raise ValueError('Missing desktop dependencies: ' + ', '.join(missing)
                         + '; install Ubuntu packages xvfb xauth fluxbox x11vnc websockify novnc')
    dependencies['novnc'] = str(assets)
    return dependencies


def write_xauthority(tools, authority, display, cookie, env):
    # Feed the secret on stdin rather than exposing it in the process argv.
    subprocess.run([tools['xauth'], '-f', str(authority), '-q'],
                   input=f'add {display} MIT-MAGIC-COOKIE-1 {cookie}\n',
                   env=env, text=True, check=True, timeout=5, stdout=subprocess.DEVNULL)


def start_desktop(config, env, supervisor, directory, tools):
    authority = directory / 'Xauthority'
    authority.touch(mode=0o600)
    cookie = secrets.token_hex(16)
    # Xvfb reads cookies independent of the display-number field. Once it has
    # allocated a display, add that number for X clients' authority lookup.
    write_xauthority(tools, authority, ':0', cookie, env)
    read_fd, write_fd = os.pipe()
    try:
        supervisor.start('Xvfb', [tools['Xvfb'], '-displayfd', str(write_fd),
                         '-screen', '0', '1600x1000x24', '-nolisten', 'tcp',
                         '-auth', str(authority), '-noreset'], env, pass_fds=(write_fd,))
    finally:
        os.close(write_fd)
    try:
        deadline = time.monotonic() + 10
        output = b''
        with selectors.DefaultSelector() as selector:
            selector.register(read_fd, selectors.EVENT_READ)
            while b'\n' not in output:
                supervisor.check()
                if time.monotonic() >= deadline:
                    raise RuntimeError('timed out waiting for Xvfb display allocation')
                if selector.select(timeout=0.1):
                    chunk = os.read(read_fd, 32)
                    if not chunk or len(output) + len(chunk) > 32:
                        raise RuntimeError('Xvfb returned an invalid display number')
                    output += chunk
        number = output.strip().decode('ascii')
        if not number.isdecimal() or len(number) > 6:
            raise RuntimeError('Xvfb returned an invalid display number')
    finally:
        os.close(read_fd)
    display = ':' + number
    write_xauthority(tools, authority, display, cookie, env)
    env = env | {'DISPLAY': display, 'XAUTHORITY': str(authority),
                 'AF_DESKTOP_URL': f'http://127.0.0.1:{config["desktop_port"]}/'}
    # Fluxbox creates ~/.fluxbox on first use; keep that configuration private
    # without changing the login HOME inherited by Analog Factory and its tools.
    fluxbox_home = directory / 'fluxbox-home'
    fluxbox_home.mkdir(mode=0o700)
    supervisor.start('fluxbox', [tools['fluxbox'], '-display', display],
                     env | {'HOME': str(fluxbox_home)})
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(('127.0.0.1', 0))
        vnc_port = probe.getsockname()[1]
    supervisor.start('x11vnc', [tools['x11vnc'], '-display', display, '-auth', str(authority),
                     '-listen', '127.0.0.1', '-no6', '-rfbport', str(vnc_port),
                     '-forever', '-shared', '-nopw', '-noxdamage'], env)
    supervisor.wait_port('127.0.0.1', vnc_port)
    supervisor.start('websockify', [tools['websockify'], '--web', tools['novnc'],
                     f'127.0.0.1:{config["desktop_port"]}', f'127.0.0.1:{vnc_port}'], env)
    supervisor.wait_port('127.0.0.1', config['desktop_port'])
    return env


def run(config):
    if not sys.platform.startswith('linux'):
        raise ValueError('deployment runtime currently supports Linux only')
    if not Path(config['python']).is_file() or not os.access(config['python'], os.X_OK):
        raise ValueError(f'configured python is not executable: {config["python"]}')
    env = child_environment(config)
    tools = desktop_dependencies(env) if config['desktop'] else None
    supervisor = Supervisor()
    handlers = {signum: signal.signal(signum, supervisor.request_stop)
                for signum in (signal.SIGINT, signal.SIGTERM)}
    try:
        with workspace_lock(config['workspace']), contextlib.ExitStack() as resources:
            assert_port_free(config['host'], config['port'])
            if config['desktop']:
                if config['host'] == '127.0.0.1' and config['port'] == config['desktop_port']:
                    raise ValueError('app port and desktop_port must be different')
                assert_port_free('127.0.0.1', config['desktop_port'])
            # Close owned children before disposing of authority or releasing lock.
            if config['desktop']:
                directory = Path(resources.enter_context(tempfile.TemporaryDirectory(prefix='af-desktop-')))
            resources.enter_context(supervisor)
            if config['desktop']:
                env = start_desktop(config, env, supervisor, directory, tools)
            supervisor.start('app', app_command(config), env)
            print(f'Analog Factory deployment: http://{config["host"]}:{config["port"]}', flush=True)
            if config['desktop']:
                print(f'Private desktop: {env["AF_DESKTOP_URL"]}', flush=True)
            while True:
                supervisor.check()
                time.sleep(0.1)
    except Stopping:
        return 0
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path, help='Deployment JSON config')
    args = parser.parse_args(argv)
    try:
        return run(load_config(args.config))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'Deployment failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
