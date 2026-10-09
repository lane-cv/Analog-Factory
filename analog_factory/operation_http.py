"""HTTP discovery and compatibility adapters. Business logic lives in the registry."""
import base64
import os
import re
import secrets

from .operations import InvocationContext, PERMISSIONS
from .desktop import BROWSER_URL

HTTP_ERRORS = {'INVALID_ARGUMENT': 400, 'NOT_FOUND': 404, 'FRONTEND_DISCONNECTED': 404,
               'PERMISSION_DENIED': 403, 'CONFIRMATION_REQUIRED': 403, 'VERSION_REQUIRED': 409,
               'VERSION_CONFLICT': 409, 'IDEMPOTENCY_CONFLICT': 409, 'UNAVAILABLE': 409,
               'TIMEOUT': 504, 'EXECUTION_FAILED': 409, 'INVALID_RESULT': 500, 'INTERNAL_ERROR': 500}


def context_for(handler, body=None, *, legacy=False, registry=None):
    """Preserve the existing trusted local owner boundary; tokens add scoped callers.

    A configured external token never grants the approval capability. The existing
    local HTTP origin guard remains mandatory, including on every adapter route.
    """
    authorization = handler.headers.get('Authorization', '')
    if authorization:
        if registry and authorization.startswith('Bearer '):
            context = registry.resolve_context_token(authorization.removeprefix('Bearer '))
            if context:
                return context
        configured = os.environ.get('AF_AGENT_TOKEN', '')
        if not configured or not secrets.compare_digest(authorization, 'Bearer ' + configured):
            raise PermissionError('Invalid agent credential')
        grants = frozenset(os.environ.get('AF_AGENT_PERMISSIONS', 'read').split(','))
        if grants - PERMISSIONS:
            raise PermissionError('Server agent permission configuration is invalid')
        return InvocationContext(principal='external-agent', permissions=grants - {'approve'},
                                 source='external-agent', project_id=os.environ.get('AF_AGENT_PROJECT') or None,
                                 confirmed=(body or {}).get('confirmed') is True)
    return InvocationContext(source='legacy' if legacy else 'ui', client_id=handler.headers.get('X-AF-Client'))


def serve_api(handler, workspace, method, path, query, body, desktop=None):
    registry = workspace.operations
    registry.start_monitor()
    arg = lambda key, default='': query.get(key, [default])[0]
    context = context_for(handler, body, registry=registry)
    if path in ('/api/transfers/download', '/api/artifacts/download') and method == 'GET':
        if path == '/api/transfers/download':
            parameters = {'transfer_id': arg('transfer_id')}
            if arg('project_id'): parameters['project_id'] = arg('project_id')
            metadata_operation, read_operation = 'transfer.get', 'transfer.read'
        else:
            parameters = {key: arg(key) for key in ('project_id', 'kind', 'path', 'run_id', 'job_id') if arg(key)}
            metadata_operation, read_operation = 'artifact.stat', 'artifact.read'
        result = registry.invoke(metadata_operation, parameters, context)
        if not result['ok']:
            return handler._json(result, HTTP_ERRORS.get(result['error']['code'], 500))
        metadata = result['data']
        if read_operation == 'artifact.read':
            if arg('artifact_version') and arg('artifact_version') != metadata['artifact_version']:
                raise ValueError('Artifact changed; refresh its metadata before downloading')
            parameters['artifact_version'] = metadata['artifact_version']
        elif metadata['status'] != 'ready':
            raise ValueError('Transfer is not ready for download')
        # Authorize/read the first chunk before emitting binary headers. Every
        # subsequent chunk goes through the same registry scope and size checks.
        result = registry.invoke(read_operation, {**parameters, 'offset': 0}, context)
        if not result['ok']:
            return handler._json(result, HTTP_ERRORS.get(result['error']['code'], 500))
        handler._headers(200, metadata['content_type'], metadata['size'], metadata['filename'])
        offset = 0
        while offset < metadata['size']:
            if not result['ok']:
                # A changed artifact/expired handle makes Content-Length visibly
                # incomplete. Never append a JSON error into a binary download.
                handler.close_connection = True
                return
            chunk = result['data']
            if chunk['next_offset'] <= offset:
                handler.close_connection = True
                return
            handler.wfile.write(base64.b64decode(chunk['content_base64']))
            offset = chunk['next_offset']
            if offset < metadata['size']:
                result = registry.invoke(read_operation, {**parameters, 'offset': offset}, context)
        if read_operation == 'transfer.read':
            registry.invoke('transfer.release', parameters, context)
        return
    if path == '/api/agent-tools' and method == 'POST':
        token = handler.headers.get('Authorization', '').removeprefix('Bearer ')
        return handler._json(registry.invoke_agent_tool(token, body.get('name'), body.get('arguments')))
    if path == '/api/operations' and method == 'GET':
        params = {key: arg(key) for key in ('project_id', 'client_id', 'kind') if arg(key)}
        return handler._json({'protocol_version': 1, 'operations': registry.list_operations(
            context, module=arg('module'), keyword=arg('keyword'), parameters=params,
            available_only=arg('available') == 'true')})
    if path == '/api/operations/invoke' and method == 'POST':
        unknown = set(body) - {'operation_id', 'parameters', 'expected_version', 'idempotency_key', 'confirmed'}
        if unknown:
            raise ValueError('Unknown invocation fields')
        result = registry.invoke(body.get('operation_id'), body.get('parameters'), context,
                                 expected_version=body.get('expected_version'),
                                 idempotency_key=body.get('idempotency_key'))
        return handler._json(result, 200 if result['ok'] else HTTP_ERRORS.get(result['error']['code'], 500))
    if path == '/api/operations/events' and method == 'GET':
        return handler._json(registry.subscribe_events(int(arg('after', '0')), float(arg('wait', '0')), context))
    if path == '/api/operations/status' and method == 'GET':
        result = registry.get_operation_status(arg('task_id'), context, view=arg('view', 'full'))
        return handler._json(result, 200 if result['ok'] else HTTP_ERRORS.get(result['error']['code'], 400))
    if path == '/api/operations/cancel' and method == 'POST':
        result = registry.cancel_operation(body.get('task_id'), context)
        return handler._json(result, 200 if result['ok'] else HTTP_ERRORS.get(result['error']['code'], 400))
    if path == '/api/operations/ui.sessions' and method == 'GET':
        result = registry.invoke('ui.sessions', {}, context)
        return handler._json(result, 200 if result['ok'] else 403)
    if path.startswith('/api/operations/') and method == 'GET':
        return handler._json(registry.describe_operation(path.removeprefix('/api/operations/'), context,
            {key: arg(key) for key in ('project_id', 'client_id', 'kind') if arg(key)}))
    if path == '/api/ui/sessions' and method == 'POST':
        if 'ui' not in context.permissions or context.source not in ('ui', 'legacy'):
            raise PermissionError('Only a local frontend may register a session')
        return handler._json(registry.sessions.connect(body.get('state', {})), 201)
    match = re.fullmatch(r'/api/ui/sessions/([a-f0-9]{32})/(poll|ack)', path)
    if match and method == 'POST':
        if context.source not in ('ui', 'legacy'):
            raise PermissionError('External agents cannot acknowledge frontend commands')
        cid, action = match.groups()
        if action == 'poll':
            return handler._json(registry.sessions.poll(cid, body.get('token', ''), body.get('state', {}), body.get('version')))
        return handler._json(registry.sessions.ack(cid, body.get('token', ''), body.get('command_id'),
            body.get('ok'), body.get('data'), body.get('error'), body.get('state')))
    from .operation_catalog import LEGACY_ROUTES
    for route in LEGACY_ROUTES:
        verb, pattern, operation_id, mapper, *extra = route
        match = re.fullmatch(pattern, path)
        if verb != method or not match:
            continue
        options = extra[0] if extra else {}
        parameters = mapper(match.groupdict(), query, body)
        legacy_context = context_for(handler, body, legacy=not bool(handler.headers.get('X-AF-Client')), registry=registry)
        expected = handler.headers.get('If-Match')
        if expected:
            expected = expected.strip('"')
        result = registry.invoke(operation_id, parameters, legacy_context, expected_version=expected,
                                 idempotency_key=handler.headers.get('Idempotency-Key'))
        # Existing run endpoints return an authoritative unavailable/failed record
        # after a real attempted launch. Keep that shape for existing clients.
        if not result['ok'] and not (result.get('data') is not None and result['error']['code'] in ('UNAVAILABLE', 'EXECUTION_FAILED')):
            handler._operation_version = result.get('version')
            return handler._json({'error': result['error']['message'], 'code': result['error']['code'],
                                  'trace_id': result['trace_id']}, HTTP_ERRORS.get(result['error']['code'], 500))
        handler._operation_version = result.get('version')
        data = result['data']
        if path == '/api/status' and desktop:
            data['toolchain']['desktop']['browser_url'] = BROWSER_URL
        if options.get('response') in ('export', 'artifact'):
            return handler._bytes(base64.b64decode(data['content_base64']), data['content_type'], data['filename'])
        return handler._json(data, options.get('status', 200))
    raise FileNotFoundError('API route not found')
