#!/usr/bin/env python3
"""Connect local EDA windows to the user's already-running OSIC desktop."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import tempfile


REPO = Path(__file__).resolve().parents[1]
DESKTOP = REPO / '.tools/desktop'
DEFAULT_CONTAINER = 'iic-osic-tools_xvnc_uid_1000'


def inspect_desktop(container):
    # Select only the three display-related environment values. A container's
    # other environment variables can contain credentials and are not needed.
    template = ('{{json .Id}}\n{{json .State.Pid}}\n{{json .State.Running}}\n'
                '{{json .NetworkSettings.Ports}}\n'
                '{{range .Config.Env}}{{$key := index (split . "=") 0}}'
                '{{if or (eq $key "DISPLAY") (eq $key "HOME") (eq $key "XAUTHORITY")}}'
                '{{println .}}{{end}}{{end}}')
    result = subprocess.run(['docker', 'inspect', '--format', template, container],
                            capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError(f'Cannot inspect running desktop container {container!r}; check Docker access.')
    lines = result.stdout.splitlines()
    if len(lines) < 4:
        raise RuntimeError('Docker returned incomplete desktop information.')
    container_id, pid, running, ports = (json.loads(line) for line in lines[:4])
    if not running or not isinstance(pid, int) or pid <= 0:
        raise RuntimeError(f'Desktop container {container!r} is not running.')
    env = dict(line.split('=', 1) for line in lines[4:] if '=' in line)
    root = Path(f'/proc/{pid}/root')
    display = env.get('DISPLAY', '')
    if not display:
        # OSIC starts its desktop in the entrypoint, so Docker's static
        # configuration does not necessarily contain DISPLAY. Select an actual
        # socket only when the running container exposes exactly one display.
        sockets = [path for path in (root / 'tmp/.X11-unix').glob('X*')
                   if re.fullmatch(r'X\d+', path.name) and path.is_socket()]
        if len(sockets) != 1:
            raise RuntimeError('Cannot identify a unique running X11 display in the desktop container.')
        display = ':' + sockets[0].name[1:]
    match = re.fullmatch(r':(\d+)(?:\.\d+)?', display)
    if not match:
        raise RuntimeError('The desktop container must use a local X11 display such as :1.')
    authority = env.get('XAUTHORITY') or str(Path(env.get('HOME', '/headless')) / '.Xauthority')
    authority = Path(authority)
    if not authority.is_absolute() or '..' in authority.parts:
        raise RuntimeError('The desktop container has an invalid XAUTHORITY path.')
    socket = root / 'tmp/.X11-unix' / f'X{match[1]}'
    source_authority = root / authority.relative_to('/')
    if not stat.S_ISSOCK(socket.stat().st_mode):
        raise RuntimeError('The desktop X11 socket is not available.')
    if not source_authority.is_file():
        raise RuntimeError('The desktop X11 authority file is not available.')
    desktop_url = None
    for port in ('80/tcp', '6080/tcp', '5800/tcp'):
        for binding in (ports or {}).get(port) or []:
            host = binding.get('HostIp', '')
            if host in ('', '0.0.0.0', '127.0.0.1', '::', '::1'):
                desktop_url = f'http://127.0.0.1:{binding["HostPort"]}/'
                break
        if desktop_url:
            break
    return {'container_name': container, 'container_id': container_id, 'container_pid': pid,
            'container_display': display, 'socket_target': str(socket),
            'source_authority': str(source_authority), 'desktop_url': desktop_url}


def read_cookie(authority, display):
    # -i avoids creating lock files in the container. Output stays in memory;
    # the cookie is passed to the destination xauth on stdin, never argv/logs.
    result = subprocess.run(['xauth', '-i', '-f', authority, 'list'],
                            capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise RuntimeError('Cannot read the desktop X11 authorization.')
    number = display[1:].split('.')[0]
    cookies = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if (len(parts) == 3 and parts[0].endswith(':' + number)
                and parts[1] == 'MIT-MAGIC-COOKIE-1' and re.fullmatch(r'[0-9a-fA-F]{32}', parts[2])):
            cookies.add(parts[2])
    if len(cookies) != 1:
        raise RuntimeError('Expected one MIT-MAGIC-COOKIE-1 authorization for the desktop display.')
    return cookies.pop()


def connect_socket(alias, target, previous):
    """Adopt the same socket, or replace only our recorded, now-stale symlink."""
    if os.path.lexists(alias):
        if not alias.is_symlink() or alias.lstat().st_uid != os.getuid():
            raise RuntimeError(f'Refusing to replace an existing X11 socket or unowned alias: {alias}')
        try:
            if alias.samefile(target):
                return
        except FileNotFoundError:
            pass
        old_target = os.readlink(alias)
        owned = (previous.get('socket_alias') == str(alias)
                 and previous.get('socket_target') == old_target
                 and re.fullmatch(r'/proc/\d+/root/tmp/\.X11-unix/X\d+', old_target))
        if alias.exists() or not owned:
            raise RuntimeError(f'Refusing to replace an unrelated or live X11 alias: {alias}')
        alias.unlink()
    alias.symlink_to(target)


def write_private(path, content):
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def connect(container, number):
    for executable in ('docker', 'xauth', 'xdpyinfo'):
        if not shutil.which(executable):
            raise RuntimeError(f'Required command is missing: {executable}')
    connection = inspect_desktop(container)
    cookie = read_cookie(connection.pop('source_authority'), connection['container_display'])
    if DESKTOP.is_symlink():
        raise RuntimeError('The private desktop configuration directory cannot be a symlink.')
    DESKTOP.mkdir(parents=True, exist_ok=True, mode=0o700)
    if DESKTOP.stat().st_uid != os.getuid():
        raise RuntimeError('The private desktop configuration directory belongs to another user.')
    DESKTOP.chmod(0o700)
    metadata = DESKTOP / 'connection.json'
    previous = json.loads(metadata.read_text()) if metadata.is_file() else {}
    socket_directory = Path('/tmp/.X11-unix')
    if not socket_directory.exists():
        socket_directory.mkdir(mode=0o1777)
    alias = socket_directory / f'X{number}'
    connect_socket(alias, Path(connection['socket_target']), previous)
    # An adopted alias may use another PID in the same container's mount
    # namespace. Record its actual target so reconnect can replace it later.
    connection['socket_target'] = os.readlink(alias)
    display = f':{number}'
    authority = DESKTOP / 'Xauthority'
    fd, temporary = tempfile.mkstemp(prefix='.Xauthority-', dir=DESKTOP)
    os.close(fd)
    try:
        result = subprocess.run(['xauth', '-f', temporary, '-q'],
                                input=f'add {display} MIT-MAGIC-COOKIE-1 {cookie}\n',
                                capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise RuntimeError('Cannot prepare private X11 authorization.')
        probe = subprocess.run(['xdpyinfo'], capture_output=True, text=True, timeout=10,
                               env={**os.environ, 'DISPLAY': display, 'XAUTHORITY': temporary})
        if probe.returncode:
            raise RuntimeError('The X11 connection failed validation; desktop settings were not published.')
        os.replace(temporary, authority)
    finally:
        Path(temporary).unlink(missing_ok=True)
    connection.update(display=display, socket_alias=str(alias), authority_path=str(authority),
                      validated_at=datetime.now(timezone.utc).isoformat())
    write_private(metadata, json.dumps(connection, indent=2) + '\n')
    write_private(DESKTOP / 'env.sh', '# Generated by scripts/connect_remote_desktop.py\n'
                  f'export DISPLAY={shlex.quote(display)}\n'
                  f'export XAUTHORITY={shlex.quote(str(authority))}\n'
                  f'export AF_DESKTOP_URL={shlex.quote(connection["desktop_url"] or "")}\n')
    return connection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container', default=DEFAULT_CONTAINER)
    parser.add_argument('--display', type=int, default=101, help='Host display alias (default: 101)')
    args = parser.parse_args()
    if not 1 <= args.display <= 65535:
        parser.error('--display must be between 1 and 65535')
    try:
        connection = connect(args.container, args.display)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f'Desktop connection failed: {error}\n')
    print(f'X11 connection verified on {connection["display"]}.')
    if connection['desktop_url']:
        print(f'Desktop: {connection["desktop_url"]}')
    print(f'Environment: {DESKTOP / "env.sh"}')


if __name__ == '__main__':
    main()
