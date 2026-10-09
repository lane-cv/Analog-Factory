"""Graph operation contracts and live-control integration for Workspace jobs."""
from .operations import Operation, OperationError
from .workflow_graph import GraphControl, NODE_PORTS, VALUE_TYPES, execute_graph, graph_catalog, validate_graph


def _object(properties=None, required=(), extra=False):
    return {'type': 'object', 'properties': properties or {}, 'required': list(required), 'additionalProperties': extra}


TEXT = {'type': 'string'}
NUMBER = {'type': 'number'}
ID = {'type': 'string', 'pattern': '^[a-f0-9]{32}$'}
OPEN = _object(extra=True)
MAPPING = {'oneOf': [_object({'value': {}}, ['value']),
                     _object({'node': TEXT, 'port': TEXT, 'default': {}}, ['node', 'port'])]}
GRAPH_SCHEMA = _object({
    'schema_version': {'type': 'integer', 'const': 2}, 'name': TEXT,
    'nodes': {'type': 'array', 'minItems': 1, 'maxItems': 200, 'items': _object({
        'id': {'type': 'string', 'pattern': '^[A-Za-z][A-Za-z0-9_-]{0,79}$'},
        'type': {'type': 'string', 'enum': list(NODE_PORTS)},
        'position': _object({'x': NUMBER, 'y': NUMBER}, ['x', 'y']),
        'config': OPEN, 'inputs': _object(extra=MAPPING),
        'outputs': _object(extra=_object({'path': TEXT, 'type': {'type': 'string', 'enum': list(VALUE_TYPES)}}, ['path', 'type'])),
        'retries': {'type': 'integer', 'minimum': 0, 'maximum': 5},
        'timeout_seconds': {'type': 'number', 'minimum': .1, 'maximum': 3600},
        'on_error': {'type': 'string', 'enum': ['fail', 'continue']},
    }, ['id', 'type'])},
    'edges': {'type': 'array', 'maxItems': 2000, 'items': _object({
        'source': TEXT, 'target': TEXT, 'when': {'type': 'boolean'},
        'on': {'type': 'string', 'enum': ['success', 'failure', 'always']},
    }, ['source', 'target'])},
    'inputs': OPEN, 'outputs': _object(extra=MAPPING),
    'limits': _object({'runtime_seconds': {'type': 'number', 'minimum': .1, 'maximum': 3600},
                       'max_node_executions': {'type': 'integer', 'minimum': 1, 'maximum': 10000},
                       'max_iterations': {'type': 'integer', 'minimum': 1, 'maximum': 1000},
                       'cost_usd': {'type': 'number', 'minimum': 0, 'maximum': 1000000}}),
    'debug': _object({'step': {'type': 'boolean'}}),
}, ['schema_version', 'nodes'])


def start_graph(workspace, project_id, workflow):
    """Workspace.start_workflow delegates schema-v2 jobs here after dispatch."""
    flow = validate_graph(workflow)
    root = workspace.projects.path(project_id)
    control = GraphControl(step=flow['debug'].get('step', False))
    with workspace.lock:
        if any(job['status'] in ('running', 'queued') for job in workspace.jobs.list(root)):
            raise ValueError('A workflow or evaluation is already active for this project')
        controls = workspace.__dict__.setdefault('_graph_controls', {})
        def execute(job, event, update):
            with workspace.lock:
                controls[(project_id, job['id'])] = control
            try:
                return execute_graph(workspace, project_id, flow, job, event, update, control=control)
            finally:
                with workspace.lock:
                    controls.pop((project_id, job['id']), None)
        job = workspace.jobs.start(root, 'workflow', execute, {'workflow': flow, 'schema_version': 2})
        controls[(project_id, job['id'])] = control
        return job


def register_operations(registry):
    from .operation_catalog import WORKFLOW
    from . import workflow_io
    workspace = registry.workspace
    format_schema = {'type': 'string', 'enum': ['json', 'yaml'], 'default': 'json'}
    def interchange_available(context, params):
        if params.get('format') == 'yaml':
            try:
                workflow_io.yaml_module()
            except RuntimeError as error:
                return [str(error)]
        return []
    def interchange(context, params, function, field):
        reasons = interchange_available(context, params)
        if reasons:
            raise OperationError('UNAVAILABLE', reasons[0])
        return function(params[field], params.get('format', 'json'))
    registry.register(Operation('workflow.parse', 'Validate an imported workflow document',
        'Parse bounded JSON or optional safe YAML and validate the selected executable workflow schema without saving or running.',
        'workflow', _object({'content': {'type': 'string', 'maxLength': workflow_io.MAX_BYTES}, 'format': format_schema}, ['content']),
        _object({'workflow': WORKFLOW, 'format': format_schema}, ['workflow', 'format']),
        lambda c, p: interchange(c, p, workflow_io.parse_document, 'content'),
        kind='query', idempotent=True, availability=interchange_available, tags=('workflow',),
        preconditions=('YAML format requires the optional yaml dependency',)))
    registry.register(Operation('workflow.export', 'Serialize an executable workflow',
        'Validate and serialize a versioned workflow as JSON or optional safe YAML without modifying the project.',
        'workflow', _object({'workflow': WORKFLOW, 'format': format_schema}, ['workflow']),
        _object({'content': TEXT, 'content_type': TEXT, 'filename': TEXT}, ['content', 'content_type', 'filename']),
        lambda c, p: interchange(c, p, workflow_io.export_document, 'workflow'),
        kind='query', idempotent=True, availability=interchange_available, tags=('workflow',),
        preconditions=('YAML format requires the optional yaml dependency',)))
    def command(context, params):
        if params['action'] == 'review':
            if 'approve' not in context.permissions or context.source not in ('ui', 'legacy', 'cli'):
                raise PermissionError('Only the human owner may decide a workflow review')
            if 'node_id' not in params or 'approved' not in params:
                raise ValueError('A review requires the pending node ID and explicit approval decision')
        elif any(key in params for key in ('approved', 'node_id', 'review_token')):
            raise ValueError('node_id, approved and review_token are only valid for review decisions')
        key = (params['project_id'], params['job_id'])
        job = workspace.jobs.get(workspace.projects.path(params['project_id']), params['job_id'])
        if job['status'] not in ('queued', 'running'):
            raise ValueError('This job is historical; start a new execution to use graph controls')
        with workspace.lock:
            control = getattr(workspace, '_graph_controls', {}).get(key)
            if control is None:
                raise ValueError('This graph has no active control session; inspect its historical job record')
            with control.lock:
                if params['action'] == 'review' and control.review_token is not None and 'write' not in context.permissions:
                    raise PermissionError('Source patch reviews require human write permission')
                state = control.command(params['action'], params.get('node_id'), params.get('approved'),
                                        params.get('review_token'))
        return {'job_id': params['job_id'], **state}

    registry.register(Operation('workflow.catalog', 'Inspect executable graph nodes',
        'Read schema-v2 typed node inputs, outputs and execution limits.', 'workflow', _object(),
        _object({'schema_version': {'type': 'integer', 'const': 2},
                 'node_types': {'type': 'array', 'items': _object({'type': TEXT, 'inputs': OPEN, 'outputs': OPEN}, ['type', 'inputs', 'outputs'])},
                 'limits': OPEN, 'cost_accounting': TEXT}, ['schema_version', 'node_types', 'limits', 'cost_accounting']),
        lambda c, p: graph_catalog(), kind='query', idempotent=True, tags=('workflow',), examples=({},)))
    registry.register(Operation('job.control', 'Control graph execution',
        'Step, pause or continue a live schema-v2 graph, or submit an explicit human review decision.', 'job',
        _object({'project_id': ID, 'job_id': ID,
                 'action': {'type': 'string', 'enum': ['step', 'pause', 'continue', 'review']},
                 'node_id': TEXT, 'approved': {'type': 'boolean'},
                 'review_token': {'type': 'string', 'minLength': 64, 'maxLength': 64}}, ['project_id', 'job_id', 'action']),
        _object({'job_id': ID, 'action': TEXT, 'paused': {'type': 'boolean'},
                 'pending_review': {'oneOf': [TEXT, {'type': 'null'}]}}, ['job_id', 'action', 'paused', 'pending_review']),
        command, permissions=('execute',), confirmation='agent', tags=('workflow', 'job'),
        preconditions=('Graph job is active in this service process', 'Review decisions require human approve permission',
                       'Source patch review also requires write permission and its exact current review token'),
        side_effects=('Releases a bounded graph step or changes its next pause/review decision',),
        examples=({'project_id': '0' * 32, 'job_id': '1' * 32, 'action': 'step'},)))
