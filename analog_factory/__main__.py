"""Run with python3 -m analog_factory; no third-party packages are required."""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from .service import Workspace
from .templates import tuning_workflow


def _context(project_id=None, *, confirmed=False, read_only=False, source='cli'):
    from .operations import InvocationContext
    permissions = {'read'} if read_only else {'read', 'write', 'execute', 'configure', 'ui', 'approve'}
    return InvocationContext(principal='local-cli', source=source, project_id=project_id,
                             permissions=frozenset(permissions), confirmed=confirmed)


def _invoke(workspace, operation_id, parameters=None):
    parameters = parameters or {}
    envelope = workspace.operations.invoke(operation_id, parameters,
        context=_context(parameters.get('project_id'), confirmed=True),
        expected_version=workspace.operations.version(parameters.get('project_id')))
    if not envelope['ok']:
        raise ValueError(envelope['error']['message'])
    return envelope


def _wait(workspace, envelope, project_id, timeout):
    deadline = time.monotonic() + timeout
    while envelope.get('status') in ('queued', 'running', 'pending', 'waiting_approval'):
        if time.monotonic() >= deadline:
            raise TimeoutError('Operation is still running; inspect it with operations status')
        time.sleep(.05)
        envelope = workspace.operations.get_operation_status(envelope['task_id'], context=_context(project_id))
        if not envelope['ok']:
            raise ValueError(envelope['error']['message'])
    return envelope['data']


def _failure(operation_id, code, message):
    return {'ok': False, 'operation_id': operation_id, 'trace_id': None, 'data': None,
            'error': {'code': code, 'message': message}, 'task_id': None,
            'status': 'failed', 'progress': None, 'artifacts': [], 'version': None}


def _remote_request(args, path, *, query=None, body=None):
    from .providers import normalized_base_url, validate_api_key
    url = normalized_base_url(args.url)
    if not url:
        raise ValueError('A server URL is required for remote operations')
    headers = {'Content-Type': 'application/json'}
    token = os.environ.get('AF_OPERATION_TOKEN') or os.environ.get('AF_AGENT_TOKEN')
    if token:
        validate_api_key(token)
        headers['Authorization'] = 'Bearer ' + token
    if query:
        path += '?' + urllib.parse.urlencode(query)
    request = urllib.request.Request(url + path, headers=headers,
        data=None if body is None else json.dumps(body, allow_nan=False).encode())

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    try:
        response = urllib.request.build_opener(NoRedirect).open(request, timeout=60)
    except urllib.error.HTTPError as error:
        response = error
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ValueError('Could not connect to the workspace server; check --url and server availability') from None
    with response:
        status = response.status
        try:
            result = json.load(response)
        except (ValueError, UnicodeError):
            raise ValueError(f'Workspace server returned HTTP {status} without a JSON operation result') from None
    if not isinstance(result, dict):
        raise ValueError('Workspace server returned an invalid operation response')
    if status >= 400 and result.get('ok') is not False:
        code = {400: 'INVALID_ARGUMENT', 403: 'PERMISSION_DENIED', 404: 'NOT_FOUND'}.get(status, 'EXECUTION_FAILED')
        return _failure(getattr(args, 'operation_id', None), code,
                        str(result.get('error', f'Workspace server returned HTTP {status}')))
    return result


def _remote_operation_command(args, parameters):
    # The server remains the authorization boundary. These checks also preserve
    # the CLI's explicit-confirmation/read-only contract when using a trusted
    # owner connection without a configured external token.
    query = {key: parameters[key] for key in ('project_id', 'client_id', 'kind') if key in parameters}
    if args.operation_command == 'list':
        result = _remote_request(args, '/api/operations', query={**query, 'module': args.module, 'keyword': args.keyword})
        if args.read_only:
            for entry in result.get('operations', []):
                if set(entry.get('permissions', [])) - {'read'}:
                    entry['availability'] = {'available': False, 'reasons': ['CLI is read-only'],
                                              'prerequisites': entry.get('preconditions', [])}
        return result
    if args.operation_command in ('describe', 'invoke'):
        descriptor = _remote_request(args, '/api/operations/' + urllib.parse.quote(args.operation_id, safe=''), query=query)
        if descriptor.get('ok') is False:
            return descriptor
        mutating = bool(set(descriptor.get('permissions', [])) - {'read'})
        if args.operation_command == 'describe':
            if args.read_only and mutating:
                descriptor['availability'] = {'available': False, 'reasons': ['CLI is read-only'],
                                               'prerequisites': descriptor.get('preconditions', [])}
            return descriptor
        if args.read_only and mutating:
            return _failure(args.operation_id, 'PERMISSION_DENIED', 'CLI is read-only')
        if descriptor.get('confirmation') == 'agent' and not args.confirm:
            return _failure(args.operation_id, 'CONFIRMATION_REQUIRED', 'Pass --confirm to approve this requested mutation')
        return _remote_request(args, '/api/operations/invoke', body={
            'operation_id': args.operation_id, 'parameters': parameters,
            'expected_version': args.expected_version, 'idempotency_key': args.idempotency_key,
            'confirmed': args.confirm})
    if args.operation_command == 'status':
        return _remote_request(args, '/api/operations/status', query={'task_id': args.task_id, 'view': args.view})
    if args.read_only:
        return _failure(None, 'PERMISSION_DENIED', 'CLI is read-only')
    return _remote_request(args, '/api/operations/cancel', body={'task_id': args.task_id,
                                                               'confirmed': args.confirm})


def _wait_local_operation(workspace, envelope, context):
    """Keep this process alive while it owns asynchronous daemon workers."""
    task_id = envelope.get('task_id')
    if not task_id:
        return envelope
    registry = workspace.operations
    try:
        while envelope.get('status') in ('queued', 'running', 'pending', 'waiting_approval'):
            time.sleep(.05)
            current = registry.get_operation_status(task_id, context=context)
            envelope = {**envelope, **current}
            if not current['ok']:
                return envelope
    except KeyboardInterrupt:
        registry.cancel_operation(task_id, context=context)
        if task_id.startswith('agent:'):
            workspace.agents.close()
        raise
    envelope['version'] = registry.version(context.project_id)
    if envelope.get('status') in ('failed', 'timed_out', 'unavailable', 'interrupted'):
        envelope['ok'] = False
        envelope['error'] = {'code': 'UNAVAILABLE' if envelope['status'] == 'unavailable' else 'EXECUTION_FAILED',
                             'message': envelope.get('data', {}).get('error') or 'Operation did not complete successfully'}
    return envelope


def _operation_command(workspace, args):
    parameters = json.loads(args.parameters) if hasattr(args, 'parameters') else {}
    if not isinstance(parameters, dict):
        raise ValueError('Operation parameters must be a JSON object')
    if args.project:
        if parameters.get('project_id', args.project) != args.project:
            raise ValueError('Project argument and parameters must agree')
        parameters['project_id'] = args.project
        if args.operation_command in ('status', 'cancel'):
            parts = args.task_id.split(':')
            if len(parts) < 3:
                raise ValueError('A task ID must identify its kind, project and record')
            if parts[1] != args.project:
                return _failure(None, 'PERMISSION_DENIED', 'Task belongs to a different project')
    if args.url:
        return _remote_operation_command(args, parameters)
    context = _context(parameters.get('project_id'), confirmed=getattr(args, 'confirm', False),
                       read_only=getattr(args, 'read_only', False),
                       source='cli' if getattr(args, 'confirm', False) else 'external-agent')
    registry = workspace.operations
    if args.operation_command == 'list':
        return {'operations': registry.list_operations(context=context, module=args.module,
                    keyword=args.keyword, parameters=parameters)}
    if args.operation_command == 'describe':
        return registry.describe_operation(args.operation_id, context=context, parameters=parameters)
    if args.operation_command == 'invoke':
        result = registry.invoke(args.operation_id, parameters, context=context,
            expected_version=args.expected_version, idempotency_key=args.idempotency_key)
        return _wait_local_operation(workspace, result, context)
    if args.operation_command == 'status':
        return registry.get_operation_status(args.task_id, context=context, view=args.view)
    return registry.cancel_operation(args.task_id, context=context)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Analog-factory local analog IC workspace')
    parser.add_argument('--workspace', type=Path, default=Path.cwd() / '.workspace', help='Data directory (default: ./.workspace)')
    sub = parser.add_subparsers(dest='command', required=True)
    serve = sub.add_parser('serve', help='Launch local browser workspace')
    serve.add_argument('--host', default='127.0.0.1',
                       help='Local IPv4 address to bind (default: 127.0.0.1); LAN access is unauthenticated')
    serve.add_argument('--port', type=int, default=8765)
    serve.add_argument('--open', action='store_true', help='Open the browser')
    sub.add_parser('doctor', help='Report tools, PDK, desktop and integration readiness')
    create = sub.add_parser('create', help='Create a project')
    create.add_argument('name')
    create.add_argument('--template', choices=['amplifier', 'sky130'], default='amplifier')
    sub.add_parser('projects', help='List projects')
    run = sub.add_parser('run', help='Execute a real EDA task and wait for completion')
    run.add_argument('project')
    run.add_argument('--kind', default='simulate', choices=['simulate', 'drc', 'lvs', 'extract', 'xschem', 'magic'])
    run.add_argument('--options', default='{}', help='JSON adapter options')
    run.add_argument('--timeout', type=float, default=60)
    tune = sub.add_parser('tune', help='Run bounded bias tuning with ngspice')
    tune.add_argument('project')
    tune.add_argument('--workflow', type=Path)
    evaluation = sub.add_parser('evaluate', help='Collect isolated random-search baseline episodes')
    evaluation.add_argument('project')
    evaluation.add_argument('--episodes', type=int, default=3)
    evaluation.add_argument('--seed', type=int, default=42)
    demo = sub.add_parser('demo', help='Create amplifier, simulate, and tune; preserve evidence')
    demo.add_argument('--name', default='Amplifier validation')
    export = sub.add_parser('export', help='Archive source files and run evidence')
    export.add_argument('project')
    export.add_argument('output', type=Path)
    operations = sub.add_parser('operations', help='Discover and invoke shared workspace operations')
    operations.add_argument('--url', help='Use a running workspace server; authentication comes from AF_OPERATION_TOKEN or AF_AGENT_TOKEN')
    operation_sub = operations.add_subparsers(dest='operation_command', required=True)
    for command in ('list', 'describe', 'invoke', 'status', 'cancel'):
        operation = operation_sub.add_parser(command)
        operation.add_argument('--url', default=argparse.SUPPRESS,
                               help='Use a running server; asynchronous tasks remain owned by that server')
        operation.add_argument('--project', help='Bind this operation to one project')
        operation.add_argument('--read-only', action='store_true', help='Allow only read operations')
        if command in ('describe', 'invoke'):
            operation.add_argument('operation_id')
        if command in ('list', 'describe', 'invoke'):
            operation.add_argument('--parameters', default='{}', help='JSON operation parameters')
        if command == 'list':
            operation.add_argument('--module', default='')
            operation.add_argument('--keyword', default='')
        if command in ('status', 'cancel'):
            operation.add_argument('task_id')
        if command == 'status':
            operation.add_argument('--view', choices=('full', 'summary'), default='full',
                                   help='Use summary for bounded status polling; full retains all evidence')
        if command in ('invoke', 'cancel'):
            operation.add_argument('--confirm', action='store_true', help='Confirm the requested mutation')
        if command == 'invoke':
            operation.add_argument('--expected-version', help='Reject a mutation if workspace sources changed')
            operation.add_argument('--idempotency-key', help='Reuse the result of this logical request')
    args = parser.parse_args(argv)
    try:
        workspace = None if args.command == 'operations' and args.url else Workspace(args.workspace)
        if args.command == 'serve':
            from .server import create_server
            server = create_server(workspace, args.port, host=args.host)
            url = f'http://{server.server_address[0]}:{server.server_port}'
            print(f'Analog-factory: {url}\nWorkspace: {workspace.root}', flush=True)
            if args.open:
                webbrowser.open(url)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
        if args.command == 'doctor':
            result = _invoke(workspace, 'workspace.status')['data']
        elif args.command == 'operations':
            result = _operation_command(workspace, args)
        elif args.command == 'projects':
            result = _invoke(workspace, 'project.list')['data']
        elif args.command == 'create':
            result = _invoke(workspace, 'project.create', {'name': args.name, 'template': args.template})['data']
        elif args.command == 'run':
            run = _invoke(workspace, 'run.start', {'project_id': args.project, 'kind': args.kind,
                'options': json.loads(args.options), 'timeout': args.timeout})
            result = _wait(workspace, run, args.project, args.timeout + 10)
        elif args.command == 'tune':
            workflow = json.loads(args.workflow.read_text()) if args.workflow else tuning_workflow()
            job = _invoke(workspace, 'workflow.run', {'project_id': args.project, 'workflow': workflow})
            result = _wait(workspace, job, args.project, 3610)
        elif args.command == 'evaluate':
            job = _invoke(workspace, 'evaluation.run', {'project_id': args.project,
                'episodes': args.episodes, 'seed': args.seed})
            result = _wait(workspace, job, args.project, 1300)
        elif args.command == 'export':
            archive = _invoke(workspace, 'project.export', {'project_id': args.project})['data']
            args.output.write_bytes(base64.b64decode(archive['content_base64']))
            result = {'archive': str(args.output.resolve())}
        elif args.command == 'demo':
            project = _invoke(workspace, 'project.create', {'name': args.name})['data']
            pid = project['id']
            run = _invoke(workspace, 'run.start', {'project_id': pid, 'kind': 'simulate', 'options': {}})
            baseline = _wait(workspace, run, pid, 130)
            if baseline['status'] != 'succeeded':
                print(json.dumps(baseline, indent=2))
                return 1
            job = _invoke(workspace, 'workflow.run', {'project_id': pid, 'workflow': tuning_workflow()})
            tuned = _wait(workspace, job, pid, 3610)
            result = {'project': project, 'baseline': baseline, 'tuning': tuned}
            print(json.dumps(result, indent=2))
            return 0 if tuned.get('satisfied') and tuned['status'] == 'succeeded' else 1
        print(json.dumps(result, indent=2))
        return 1 if isinstance(result, dict) and (result.get('ok') is False or
            result.get('status') in ('failed', 'cancelled', 'timed_out', 'unavailable', 'interrupted')) else 0
    except (ValueError, OSError, RuntimeError, TimeoutError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
