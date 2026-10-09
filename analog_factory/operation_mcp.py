"""Bounded stdio MCP adapter for one server-owned, project-scoped agent turn.

The native CLI receives only a short-lived environment credential. All tool
validation, approvals and operation execution remain in the workspace server.
"""
import http.client
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analog_factory.agent_api import TOOLS, _redact

NAMES = {'list_operations', 'describe_operation', 'invoke_operation',
         'get_operation_status', 'cancel_operation', 'subscribe_events'}
READ_ONLY_NAMES = NAMES - {'invoke_operation', 'cancel_operation'}
MAX_MESSAGE = 1024 * 1024


def _request(name, arguments):
    base = urlsplit(os.environ.get('AF_OPERATION_URL', ''))
    token = os.environ.get('AF_OPERATION_TOKEN', '')
    if base.scheme != 'http' or not base.hostname or base.username or base.password or not token:
        raise ValueError('A local operation server and turn credential are required')
    connection = http.client.HTTPConnection(base.hostname, base.port, timeout=650)
    try:
        payload = json.dumps({'name': name, 'arguments': _redact(arguments)}, allow_nan=False).encode()
        connection.request('POST', '/api/agent-tools', payload,
            {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
        response = connection.getresponse()
        contents = response.read(MAX_MESSAGE + 1)
        if len(contents) > MAX_MESSAGE:
            raise ValueError('Operation response exceeds the MCP message limit')
        result = json.loads(contents)
        if not isinstance(result, dict):
            raise ValueError('Operation server returned an invalid result')
        return result
    finally:
        connection.close()


def _dispatch(request):
    method = request.get('method')
    params = request.get('params', {})
    if not isinstance(params, dict):
        raise ValueError('MCP parameters must be an object')
    if method == 'initialize':
        return {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {'listChanged': False}},
                'serverInfo': {'name': 'analog-factory', 'version': '1.0.0'},
                'instructions': 'Discover operations, inspect their schemas, and invoke them. '
                                'Mutations require approval in the workspace.'}
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return {'tools': [{'name': tool['function']['name'], 'description': tool['function']['description'],
                           'inputSchema': tool['function']['parameters'],
                           'annotations': {
                               'readOnlyHint': tool['function']['name'] in READ_ONLY_NAMES,
                               'destructiveHint': tool['function']['name'] not in READ_ONLY_NAMES,
                               'idempotentHint': tool['function']['name'] in READ_ONLY_NAMES,
                               'openWorldHint': tool['function']['name'] == 'invoke_operation',
                           }}
                          for tool in TOOLS if tool['function']['name'] in NAMES]}
    if method == 'tools/call':
        name, arguments = params.get('name'), params.get('arguments', {})
        if name not in NAMES or not isinstance(arguments, dict):
            raise ValueError('Unknown operation tool or invalid arguments')
        try:
            result = _request(name, arguments)
        except (OSError, ValueError, http.client.HTTPException):
            result = {'error': 'Operation bridge request failed; inspect the workspace connection'}
        return {'content': [{'type': 'text', 'text': json.dumps(result, allow_nan=False)}],
                'isError': result.get('ok') is False or bool(result.get('error'))}
    raise LookupError('Unknown MCP method')


def main():
    while True:
        line = sys.stdin.buffer.readline(MAX_MESSAGE + 1)
        if not line:
            return 0
        identifier = None
        try:
            if len(line) > MAX_MESSAGE:
                raise ValueError('MCP message exceeds the size limit')
            request = json.loads(line)
            if not isinstance(request, dict) or request.get('jsonrpc') != '2.0':
                raise ValueError('MCP message must be a JSON-RPC object')
            if 'id' not in request:
                continue
            identifier = request['id']
            response = {'jsonrpc': '2.0', 'id': identifier, 'result': _dispatch(request)}
        except (ValueError, TypeError):
            response = {'jsonrpc': '2.0', 'id': identifier,
                        'error': {'code': -32602, 'message': 'Invalid MCP message or tool arguments'}}
        except LookupError:
            response = {'jsonrpc': '2.0', 'id': identifier,
                        'error': {'code': -32601, 'message': 'Unknown MCP method'}}
        print(json.dumps(response, allow_nan=False), flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
