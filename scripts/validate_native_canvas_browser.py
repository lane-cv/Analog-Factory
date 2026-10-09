#!/usr/bin/env python3
"""Run real native/browser acceptance in an owned disposable Xvfb container.

Requires an already prepared Docker image and host Node/Playwright/Chromium.
Browser dependency paths are inherited; this driver never installs or publishes.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.validate_ubuntu24 import SOURCE_ROOTS, copy_source, digest
KEY_SOURCES = (
    'scripts/validate_native_canvas_browser.py', 'tests/browser-native-canvas.cjs',
    'analog_factory/native_canvas.py', 'analog_factory/native_canvas_common.tcl',
    'analog_factory/native_canvas_xschem.tcl', 'analog_factory/native_canvas_magic.tcl',
    'analog_factory/operation_native_canvas.py', 'analog_factory/operation_ui.py',
    'analog_factory/operations.py', 'analog_factory/operation_sessions.py',
    'analog_factory/operation_http.py', 'analog_factory/operation_catalog.py',
    'analog_factory/projects.py', 'analog_factory/execution.py',
    'analog_factory/eda.py', 'analog_factory/file_changes.py',
    'analog_factory/storage.py', 'analog_factory/operation_schema.py',
    'analog_factory/server.py', 'analog_factory/service.py',
    'analog_factory/static/native-canvas.js', 'analog_factory/static/operations.js',
    'analog_factory/static/app.js', 'analog_factory/static/index.html',
    'analog_factory/static/style.css',
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def owned_container(output):
    path = output / 'container.id'
    if not path.exists():
        return None
    identifier = path.read_text().strip()
    if not re.fullmatch('[a-f0-9]{64}', identifier):
        raise RuntimeError('Owned container ID file is invalid')
    return identifier


def wait_ready(process, output, port):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Owned server container exited before readiness: {process.returncode}')
        identifier = owned_container(output)
        if identifier:
            inspected = subprocess.run(['docker', 'inspect', '--format',
                '{{json .State.Running}} {{json .NetworkSettings.Ports}}', identifier],
                capture_output=True, text=True, timeout=5)
            if inspected.returncode == 0:
                running, _, ports_json = inspected.stdout.strip().partition(' ')
                ports = json.loads(ports_json)
                mappings = ports.get(f'{port}/tcp') or []
                if running == 'true' and any(row.get('HostIp') == '127.0.0.1'
                        and row.get('HostPort') == str(port) for row in mappings):
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/operations', timeout=1):
                            return
                    except OSError:
                        pass
        time.sleep(.2)
    raise TimeoutError('Owned native server did not become ready within 45 seconds')


def close_owned(process, output, evidence, log):
    identifier = owned_container(output)
    if identifier:
        try:
            stopped = subprocess.run(['docker', 'stop', '--time', '8', identifier],
                stdout=log, stderr=subprocess.STDOUT, timeout=20)
            evidence['cleanup_exit_code'] = stopped.returncode
            if stopped.returncode and process.poll() is None:
                raise RuntimeError('Owned container did not acknowledge shutdown')
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
            evidence['cleanup_fallback'] = str(error)
            removed = subprocess.run(['docker', 'rm', '--force', identifier],
                stdout=log, stderr=subprocess.STDOUT, timeout=20)
            evidence['cleanup_exit_code'] = removed.returncode
            if removed.returncode and process.poll() is None:
                raise RuntimeError('Unable to close the owned native container') from error
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    evidence['server_exit_code'] = process.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True,
        help='New isolated evidence directory; existing directories are rejected')
    parser.add_argument('--pdk-root', type=Path, required=True,
        help='Prepared PDK root containing sky130A; mounted read-only')
    parser.add_argument('--image', default='analog-factory-ubuntu24:validation',
        help='Already installed acceptance image (never pulled)')
    parser.add_argument('--port', type=int, default=8922,
        help='Unused localhost port for this owned server (default: 8922)')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/:@-]{0,255}', args.image):
        parser.error('--image must name an already installed Docker image')
    output = args.output.resolve()
    pdk = args.pdk_root.resolve()
    if args.output.is_symlink() or output.exists():
        parser.error('--output must be a new directory')
    if output.is_relative_to(ROOT / '.workspace'):
        parser.error('--output must not be the user workspace')
    if any(output.is_relative_to(ROOT / name) for name in SOURCE_ROOTS):
        parser.error('--output must be outside the allowlisted source trees')
    if not pdk.is_dir():
        parser.error('--pdk-root must be an existing prepared directory')
    if output.is_relative_to(pdk):
        parser.error('--output must be outside the read-only PDK')
    try:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', args.port))
    except OSError as error:
        parser.error('Localhost port is unavailable: ' + str(error))
    output.mkdir(parents=True, exist_ok=False)
    (output / 'home').mkdir()
    source = output / 'source'
    manifest = copy_source(ROOT, source)
    manifest_path = output / 'source-manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    server = output / 'server.py'
    server.write_text(f'''import signal,socket,threading
from analog_factory.service import Workspace
from analog_factory.server import create_server
workspace=Workspace('/evidence/workspace')
server=create_server(workspace,port={args.port},host=socket.gethostbyname(socket.gethostname()))
signal.signal(signal.SIGTERM,lambda *args:threading.Thread(target=server.shutdown,daemon=True).start())
try:
    server.serve_forever()
finally:
    server.server_close()
    workspace.operations.close()
    workspace.agents.close()
''')
    name = 'af-native-browser-' + uuid.uuid4().hex
    command = ['docker', 'run', '--pull=never', '--rm', '--init', '--name', name,
        '--cidfile', str(output / 'container.id'), '--network', 'bridge',
        '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=256',
        '--memory=2g', '--user', f'{os.getuid()}:{os.getgid()}',
        '-p', f'127.0.0.1:{args.port}:{args.port}',
        '-v', str(source) + ':/app:ro', '-v', str(output) + ':/evidence:rw',
        # /evidence is writable, so protect the second alias of the same snapshot.
        '-v', str(source) + ':/evidence/source:ro',
        '-v', str(pdk) + ':/pdk:ro', '-e', 'HOME=/evidence/home',
        '-e', 'PYTHONPATH=/app', '-e', 'PYTHONDONTWRITEBYTECODE=1',
        args.image, 'xvfb-run', '-a', 'python3', '/evidence/server.py']
    evidence = {'schema_version': 1, 'status': 'failed', 'started_at': utc_now(),
        'image': args.image, 'pdk_root': str(pdk), 'port': args.port,
        'container_name': name, 'command': command, 'browser_exit_code': None,
        'source_sha256': {name: manifest[name] for name in KEY_SOURCES},
        'source_manifest_sha256': digest(manifest_path), 'server_script_sha256': digest(server),
        'scope': 'Actual owned native editors and browser semantic controls; no model calls',
        'artifacts': ['source-manifest.json', 'server.log', 'browser.log', 'native-browser-report.json', 'server.py']}
    process = None
    exit_code = 1
    with (output / 'server.log').open('w') as server_log:
        try:
            process = subprocess.Popen(command, stdout=server_log, stderr=subprocess.STDOUT)
            wait_ready(process, output, args.port)
            evidence['container_id'] = owned_container(output)
            env = dict(os.environ, AF_NATIVE_REAL='1', AF_URL=f'http://127.0.0.1:{args.port}',
                AF_NATIVE_REPORT=str(output / 'native-browser-report.json'))
            with (output / 'browser.log').open('w') as browser_log:
                result = subprocess.run(['node', 'tests/browser-native-canvas.cjs'],
                    cwd=source, env=env, stdout=browser_log, stderr=subprocess.STDOUT,
                    text=True, timeout=240)
            evidence['browser_exit_code'] = result.returncode
            report = json.loads((output / 'native-browser-report.json').read_text())
            evidence['report_status'] = report.get('status')
            if result.returncode or report.get('status') != 'passed':
                exit_code = result.returncode or 1
                raise RuntimeError('Native browser acceptance failed; inspect browser.log and native-browser-report.json')
            evidence['status'] = 'passed'
            exit_code = 0
        except KeyboardInterrupt:
            evidence['error'] = 'Validation interrupted; owned resources are being closed'
            exit_code = 130
        except subprocess.TimeoutExpired as error:
            evidence['error'] = 'Validation command timed out: ' + str(error)
            exit_code = 124
        except (OSError, ValueError, RuntimeError) as error:
            evidence['error'] = str(error)
        finally:
            if process is not None:
                try:
                    close_owned(process, output, evidence, server_log)
                except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
                    evidence['cleanup_error'] = str(error)
                    evidence['status'] = 'failed'
                    exit_code = exit_code or 1
            try:
                after = {name: digest(source / name) for name in manifest}
                evidence['source_manifest_consistent'] = after == manifest
                evidence['source_sha256_after'] = {name: after[name] for name in KEY_SOURCES}
            except OSError as error:
                evidence['source_manifest_consistent'] = False
                evidence['source_snapshot_error'] = str(error)
            if not evidence['source_manifest_consistent']:
                evidence['error'] = 'Frozen source snapshot changed during acceptance; inspect the retained manifest'
                evidence['status'] = 'failed'
                exit_code = exit_code or 1
            evidence['finished_at'] = utc_now()
            evidence['exit_code'] = exit_code
            (output / 'evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps({'status': evidence['status'], 'exit_code': exit_code,
        'output': str(output), 'evidence': str(output / 'evidence.json')}))
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
