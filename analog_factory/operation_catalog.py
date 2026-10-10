"""Registered adapters for existing workspace operations and legacy HTTP routes.

Handlers retain the service/store owners of state. Binary downloads are JSON-safe
base64 envelopes; legacy transports decode them before sending attachment bytes.
LEGACY_ROUTES entries are (method, fullmatch_pattern, operation_id, mapper,
optional_transport_options). Mappers take (named_groups, query_lists, body).
"""
import base64
import copy
import mimetypes
import shutil

from . import eda
from .agent_models import catalog as model_catalog
from .agent_effort import EFFORT_LEVELS
from .operations import Operation
from .storage import confined
from .templates import tuning_workflow


def object_schema(properties=None, required=(), *, extra=False):
    return {'type': 'object', 'properties': properties or {},
            'required': list(required), 'additionalProperties': extra}


def array_schema(items, **limits):
    return {'type': 'array', 'items': items, **limits}


TEXT = {'type': 'string'}
ID = {'type': 'string', 'pattern': '^[a-f0-9]{32}$'}
PATH = {'type': 'string', 'minLength': 1, 'maxLength': 4096,
        'description': 'Visible project-relative path; hidden paths, traversal and symbolic links are rejected.'}
NUMBER = {'type': 'number'}
BOOLEAN = {'type': 'boolean'}
EFFORT = {'anyOf': [{'type': 'string', 'enum': list(EFFORT_LEVELS)}, {'type': 'null'}],
          'description': 'Reasoning effort override. Null inherits native defaults. Use agent.models effort_levels for the selected model; unsupported explicit levels are rejected.'}
OPEN_OBJECT = object_schema(extra=True)
FILE = object_schema({'path': TEXT, 'size': {'type': 'integer', 'minimum': 0}}, ['path'], extra=True)
WRITE = object_schema({'path': TEXT, 'sha256': TEXT,
                      'revision_id': {'anyOf': [ID, {'type': 'null'}]}}, ['path', 'sha256'])
DESIGN_PATH_ROLES = ('schematic', 'layout', 'netlist', 'post_netlist',
                     'schematic_netlist', 'layout_netlist')
DESIGN_IDENTIFIER = {'type': 'string', 'minLength': 1, 'maxLength': 128,
                     'pattern': '^[A-Za-z0-9_][A-Za-z0-9_.$-]*(?![\\s\\S])'}
PROJECT_DESIGN = object_schema({
    **{role: PATH for role in DESIGN_PATH_ROLES},
    'family': DESIGN_IDENTIFIER, 'cell': DESIGN_IDENTIFIER,
    'ports': array_schema(DESIGN_IDENTIFIER, minItems=1, maxItems=256, uniqueItems=True)})
PROJECT_DESIGN['description'] = ('Declarative source and derived-file defaults with visible project-relative paths. '
    'Missing derived files remain prerequisites. Family and port labels do not establish reference qualification, '
    'execution authority or verified results; imported evidence requires fresh local validation.')
PROJECT = object_schema({'id': ID, 'name': TEXT, 'config': OPEN_OBJECT, 'design': PROJECT_DESIGN},
                        ['id', 'name'], extra=True)
RUN = object_schema({'id': ID, 'kind': TEXT, 'status': TEXT, 'metrics': OPEN_OBJECT,
                     'verification': OPEN_OBJECT, 'stale': BOOLEAN}, ['id', 'kind', 'status'], extra=True)
JOB = object_schema({'id': ID, 'kind': TEXT, 'status': TEXT}, ['id', 'kind', 'status'], extra=True)
SESSION = object_schema({'id': ID, 'backend': TEXT, 'mode': TEXT, 'title': TEXT, 'status': TEXT, 'effort': EFFORT,
                         'auto_approval': BOOLEAN},
                        ['id', 'backend', 'mode', 'status'], extra=True)
DOWNLOAD = object_schema({'filename': TEXT, 'content_type': TEXT, 'content_base64': TEXT},
                         ['filename', 'content_type', 'content_base64'])
PROVIDER_CAPABILITIES = object_schema({'streaming': BOOLEAN, 'tools': BOOLEAN, 'images': BOOLEAN,
    'generation_parameters': array_schema({'type': 'string', 'enum':
        ['temperature', 'top_p', 'max_tokens', 'seed', 'stop', 'reasoning_effort']}, maxItems=6)})
PROVIDER_GENERATION = object_schema({
    'temperature': {'type': 'number', 'minimum': 0, 'maximum': 2},
    'top_p': {'type': 'number', 'minimum': 0, 'maximum': 1},
    'max_tokens': {'type': 'integer', 'minimum': 1, 'maximum': 131072},
    'seed': {'type': 'integer', 'minimum': 0, 'maximum': 2147483647},
    'stop': array_schema({'type': 'string', 'minLength': 1, 'maxLength': 200}, minItems=1, maxItems=4),
    'reasoning_effort': {'type': 'string', 'enum': ['low', 'medium', 'high']}})
PROVIDER = object_schema({
    'protocol': {'type': 'string', 'enum': ['openai-compatible', 'ollama']},
    'base_url': TEXT, 'model': TEXT, 'name': TEXT, 'api_key_env': TEXT,
    'api_key': {'type': 'string', 'maxLength': 4096, 'writeOnly': True},
    'api_key_storage': {'type': 'string', 'enum': ['session', 'local']},
    'api_key_configured': BOOLEAN, 'clear_api_key': BOOLEAN,
    'timeout': {'type': 'number', 'minimum': 1, 'maximum': 300},
    'retries': {'type': 'integer', 'minimum': 0, 'maximum': 3},
    'concurrency': {'type': 'integer', 'minimum': 1, 'maximum': 4},
    'stream': BOOLEAN, 'capabilities': PROVIDER_CAPABILITIES,
    'model_capabilities': object_schema(extra=PROVIDER_CAPABILITIES), 'generation': PROVIDER_GENERATION,
})
CLI_UPDATES = object_schema({'automatic': BOOLEAN,
    'interval_hours': {'type': 'integer', 'minimum': 6, 'maximum': 168,
                       'description': 'Hours between automatic official release checks while the server is running; default 24.'}})
CONFIG = object_schema({
    'tools': object_schema(extra=TEXT), 'pdk_root': TEXT,
    'pdk_variant': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]+$'}, 'pdk_version': TEXT,
    'provider': PROVIDER,
    'cli_updates': CLI_UPDATES,
    'ui': object_schema({'language': {'type': 'string', 'enum': ['en', 'zh-CN']},
                         'theme': {'type': 'string', 'enum': ['dark', 'light']}}),
})
PROJECT_CONFIG = copy.deepcopy(CONFIG)
PROJECT_CONFIG['properties'].pop('cli_updates')
for _credential_control in ('api_key', 'api_key_storage', 'api_key_configured', 'clear_api_key'):
    PROJECT_CONFIG['properties']['provider']['properties'].pop(_credential_control)

# Public result shapes are distinct from mutation inputs. In particular a
# settings read reports credential presence, never a credential value, and
# provider accounting/identifiers remain owned by the selected protocol.
NULLABLE_TEXT = {'oneOf': [TEXT, {'type': 'null'}]}
PUBLIC_PROVIDER = object_schema({
    **copy.deepcopy(PROJECT_CONFIG['properties']['provider']['properties']),
    'api_key_configured': BOOLEAN,
    'api_key_storage': {'type': 'string', 'enum': ['none', 'environment', 'session', 'local']},
}, ['api_key_configured', 'api_key_storage'], extra=True)
PUBLIC_SETTINGS = object_schema({
    **copy.deepcopy(PROJECT_CONFIG['properties']), 'provider': PUBLIC_PROVIDER,
    'cli_updates': CLI_UPDATES,
    'ui': object_schema({'language': TEXT, 'theme': TEXT}, ['language', 'theme'], extra=True),
}, ['provider', 'ui'], extra=True)
AVAILABILITY = object_schema({'available': BOOLEAN, 'reasons': array_schema(TEXT)},
                             ['available', 'reasons'], extra=True)
TOOL_STATUS = object_schema({'available': BOOLEAN, 'path': NULLABLE_TEXT, 'version': NULLABLE_TEXT,
                             'diagnostic': TEXT}, ['available'], extra=True)
TOOLCHAIN_STATUS = object_schema({
    'tools': object_schema(extra=TOOL_STATUS),
    'pdk': object_schema({'available': BOOLEAN, 'root': NULLABLE_TEXT, 'variant': TEXT,
                          'version': NULLABLE_TEXT, 'paths': object_schema(extra=NULLABLE_TEXT),
                          'configuration_hashes': object_schema(extra=TEXT), 'diagnostic': TEXT},
                         ['available', 'diagnostic'], extra=True),
    'desktop': object_schema({'available': BOOLEAN, 'display': NULLABLE_TEXT,
                              'wayland_display': NULLABLE_TEXT, 'diagnostic': TEXT,
                              'browser_url': TEXT}, ['available'], extra=True),
}, ['tools', 'pdk', 'desktop'], extra=True)
WORKSPACE_STATUS = object_schema({
    'version': TEXT, 'project_id': {'oneOf': [ID, {'type': 'null'}]}, 'toolchain': TOOLCHAIN_STATUS,
    'capabilities': object_schema(extra=object_schema({'available': BOOLEAN, 'status': TEXT, 'reason': TEXT},
                                                     ['available'], extra=True)),
    'operation_availability': object_schema({kind: AVAILABILITY for kind in
                                            ('simulate', 'xschem', 'magic', 'drc', 'lvs', 'extract')}, extra=AVAILABILITY),
}, ['version', 'project_id', 'toolchain', 'capabilities', 'operation_availability'], extra=True)
PROVIDER_MODELS = object_schema({
    'models': array_schema({'description': 'Provider-reported model identifier; a malformed entry may be null or vendor-defined.'}),
    'protocol': TEXT,
}, ['models', 'protocol'], extra=True)
AGENT_BACKENDS = object_schema({'backends': array_schema(object_schema({
    'id': TEXT, 'name': TEXT, 'available': BOOLEAN, 'diagnostic': TEXT,
    'path': NULLABLE_TEXT, 'modes': array_schema(TEXT),
}, ['id', 'name', 'available', 'diagnostic', 'modes'], extra=True))}, ['backends'], extra=True)
AGENT_MODEL = object_schema({'id': TEXT, 'label': TEXT, 'description': TEXT,
    'is_default': BOOLEAN, 'fast_mode': BOOLEAN, 'effort_levels': array_schema(TEXT),
    'default_effort': EFFORT}, ['id', 'label'], extra=True)
AGENT_MODELS = object_schema({
    'backend': TEXT, 'source': TEXT, 'diagnostic': TEXT,
    'models': array_schema(AGENT_MODEL),
    'selected_model': {'anyOf': [AGENT_MODEL, {'type': 'null'}]},
    'effort_levels': array_schema(TEXT),
    'fast_mode': object_schema({'supported': BOOLEAN, 'reason': TEXT}, ['supported'], extra=True),
}, ['backend', 'models', 'source', 'fast_mode'], extra=True)
FILE_REVISION = object_schema({'id': ID, 'path': TEXT, 'sha256': TEXT,
                               'time': {'type': 'string', 'format': 'date-time'}},
                              ['id', 'path', 'sha256', 'time'], extra=True)
CHAT_REPLY = object_schema({
    'reply': TEXT, 'backend': TEXT, 'model': TEXT,
    'usage': {'description': 'Provider-specific token/accounting metadata, or null when not reported.'},
    'cost': {'type': 'null', 'description': 'Cost is not calculated by this service.'},
    'status': TEXT, 'context': array_schema(TEXT),
}, ['reply', 'backend', 'model', 'usage', 'cost', 'status', 'context'], extra=True)
WAVEFORM_PLOT = object_schema({
    'title': TEXT, 'plot': TEXT,
    'original_point_count': {'type': 'integer', 'minimum': 0},
    'sample_indices': array_schema({'type': 'integer', 'minimum': 0}),
    'display_sampling': object_schema({'method': {'type': 'string', 'enum': ['bucket_extrema']},
        'returned_points': {'type': 'integer', 'minimum': 0},
        'original_points': {'type': 'integer', 'minimum': 0}, 'description': TEXT}),
    'variables': array_schema(object_schema({'name': TEXT, 'unit': TEXT, 'type': TEXT, 'component': TEXT},
                                            ['name', 'unit'], extra=True)),
    'points': {**array_schema(array_schema(NUMBER)),
               'description': 'Rows of numeric values in variables order; complex traces use magnitude and the scale keeps its real value.'},
    'complex_points': {**array_schema(array_schema(array_schema(NUMBER, minItems=2, maxItems=2))),
                       'description': 'Optional complex plots: [point][variable][real, imaginary].'},
}, ['title', 'plot', 'variables', 'points'], extra=True)
WAVEFORMS = copy.deepcopy(WAVEFORM_PLOT)
WAVEFORMS['properties']['plots'] = array_schema(WAVEFORM_PLOT)

WORKFLOW_V1 = object_schema({
    'schema_version': {'type': 'integer', 'const': 1}, 'name': TEXT,
    'nodes': array_schema(object_schema({
        'id': {'type': 'string', 'minLength': 1},
        'type': {'type': 'string', 'enum': ['specification', 'parameter', 'simulate', 'measure', 'condition', 'report']},
        'position': object_schema({'x': NUMBER, 'y': NUMBER}, ['x', 'y']), 'config': OPEN_OBJECT,
    }, ['id', 'type']), minItems=6, maxItems=6),
    'edges': array_schema(object_schema({'source': TEXT, 'target': TEXT}, ['source', 'target']), minItems=5, maxItems=5),
    'limits': {**object_schema({'iterations': {'type': 'integer', 'minimum': 3, 'maximum': 100, 'default': 8},
                                'runtime_seconds': {'type': 'number', 'minimum': 1, 'maximum': 3600,
                                                    'default': 120, 'units': 'seconds'}}), 'default': {}},
    'tuning': object_schema({'parameter': TEXT, 'metric': TEXT, 'netlist': {**PATH, 'default': 'testbenches/pre.cir'},
                             'minimum': NUMBER, 'maximum': NUMBER, 'target': NUMBER,
                             'tolerance': {'type': 'number', 'exclusiveMinimum': 0}},
                            ['parameter', 'metric', 'minimum', 'maximum', 'target', 'tolerance']),
}, ['schema_version', 'nodes', 'edges', 'tuning'])
from .operation_graph import GRAPH_SCHEMA
WORKFLOW = {'oneOf': [WORKFLOW_V1, GRAPH_SCHEMA]}
RUN_OPTIONS = object_schema({
    'netlist': PATH, 'schematic': PATH, 'layout': PATH,
    'layout_netlist': PATH, 'schematic_netlist': PATH, 'cell': TEXT,
    'netlist_only': BOOLEAN, 'create_missing': BOOLEAN,
    'timeout': {'type': 'number', 'exclusiveMinimum': 0, 'units': 'seconds',
                'description': 'Defaults to 86400 seconds for GUI editors or 60 seconds for batch tools.'},
    'conditions': OPEN_OBJECT,
})
RUN_METADATA = object_schema({
    'generated_files': object_schema(extra=TEXT), 'conditions': OPEN_OBJECT,
    'workflow_id': ID, 'evaluation_job': ID, 'iteration': {'type': 'integer', 'minimum': 1},
    'source_generation': {'type': 'integer', 'minimum': 0},
    'episode': {'type': 'integer', 'minimum': 0},
})
SIMULATION = object_schema({
    'source': {**PATH, 'default': 'testbenches/pre.cir'},
    'netlist': {**PATH, 'default': 'testbenches/configured.cir'},
    'analysis': {'type': 'string', 'enum': ['op', 'dc', 'ac', 'tran', 'noise'], 'default': 'tran'},
    'parameters': object_schema({key: {'oneOf': [TEXT, NUMBER]} for key in
                                 ('source', 'start', 'stop', 'step', 'points', 'output')}),
    'directives': {**TEXT, 'default': ''},
})
AGENT_SCOPE = object_schema({
    'operations': array_schema({'type': 'string', 'enum': ['file.write', 'run.start']}, minItems=1, maxItems=2),
    'paths': array_schema({'type': 'string', 'minLength': 1, 'maxLength': 500}, minItems=1, maxItems=20),
    'max_actions': {'type': 'integer', 'minimum': 1, 'maximum': 100},
    'max_runs': {'type': 'integer', 'minimum': 0, 'maximum': 20},
}, ['operations', 'paths', 'max_actions', 'max_runs'])
SESSION_OPTIONS = {
    'backend': {'type': 'string', 'enum': ['api', 'codex', 'claude'], 'default': 'api'},
    'mode': {'type': 'string', 'enum': ['read-only', 'confirm', 'automatic', 'workspace-write'], 'default': 'read-only'},
    'model': {'type': 'string', 'maxLength': 200, 'default': '',
              'description': 'Empty selects the configured API model or native CLI default.'},
    'fast_mode': {**BOOLEAN, 'default': False},
    'effort': {**EFFORT, 'default': None},
    'scope': AGENT_SCOPE,
}
ATTACHMENT = {'oneOf': [object_schema({
        'name': {'type': 'string', 'minLength': 1, 'maxLength': 200},
        'content': {'type': 'string', 'maxLength': 65536}}, ['name', 'content']),
    object_schema({'name': {'type': 'string', 'minLength': 1, 'maxLength': 200},
        'mime_type': {'type': 'string', 'enum': ['image/png', 'image/jpeg', 'image/webp']},
        'data_base64': {'type': 'string', 'minLength': 1, 'maxLength': 350000}},
        ['name', 'mime_type', 'data_base64'])]}
MESSAGE = {
    'message': {'type': 'string', 'minLength': 1, 'maxLength': 32000},
    'untrusted_request': {**BOOLEAN, 'description': 'Marks a message forwarded from automation; it cannot authorize independent AI approval.'},
    'files': array_schema(PATH, maxItems=20, default=[]), 'run_id': ID,
    'attachments': array_schema(ATTACHMENT, maxItems=20, default=[]),
}


_SAMPLE_ID = '0123456789abcdef0123456789abcdef'
_EXAMPLES = {
    'settings.update': {'settings': {'ui': {'language': 'en', 'theme': 'dark'}}},
    'project.create': {'name': 'Example amplifier', 'template': 'amplifier'},
    'project.configure': {'config': {'pdk_variant': 'sky130A'}},
    'agent.models': {'backend': 'codex', 'refresh': False},
    'file.write': {'path': 'reports/design-notes.txt', 'content': 'Review the measured output bias before changing RD.\n'},
    'file.change.preview': {'path': 'testbenches/pre.cir', 'content': 'Previewed resistor testbench\nV1 in 0 1\nR1 in 0 1000\n.op\n.end\n'},
    'simulation.configure': {'config': {'analysis': 'ac', 'parameters': {'points': 40, 'start': 1, 'stop': '1Meg'}}},
    'chat.send': {'message': 'Explain the amplifier bias point.', 'files': ['testbenches/pre.cir']},
    'workflow.save': {'workflow': tuning_workflow()},
    'workflow.run': {'workflow': tuning_workflow()},
    'evaluation.run': {'episodes': 3, 'seed': 42},
    'run.start': {'kind': 'simulate', 'options': {'netlist': 'testbenches/pre.cir'}, 'timeout': 60},
    'run.artifact': {'path': 'waveforms.raw'},
    'agent.session.list': {'query': 'bias'},
    'agent.session.create': {'backend': 'api', 'mode': 'read-only'},
    'agent.session.update': {'title': 'Amplifier bias review'},
    'agent.session.send': {'message': 'Review this netlist and explain its measurements.', 'files': ['testbenches/pre.cir']},
    'agent.session.approve': {'approved': False},
    'agent.session.set_auto_approval': {'enabled': False},
}


def _example(operation_id, schema):
    parameters = {}
    for key in schema.get('required', []):
        if key.endswith('_id') or key == 'revision':
            parameters[key] = _SAMPLE_ID
        elif key == 'path':
            parameters[key] = 'testbenches/pre.cir'
    parameters.update(copy.deepcopy(_EXAMPLES.get(operation_id, {})))
    return ({'description': 'Replace example identifiers with records from this workspace.', 'parameters': parameters},)


def _project(properties=None, required=()):
    return object_schema({'project_id': ID, **(properties or {})}, ['project_id', *required])


def _session(properties=None, required=()):
    return _project({'session_id': ID, **(properties or {})}, ['session_id', *required])


def _run(properties=None, required=()):
    return _project({'run_id': ID, **(properties or {})}, ['run_id', *required])


def _without(parameters, *keys):
    return {key: value for key, value in parameters.items() if key not in keys}


def _download(data, filename, content_type):
    return {'filename': filename, 'content_type': content_type,
            'content_base64': base64.b64encode(data).decode('ascii')}


def register_operations(registry):
    """Register real operations; called once when a workspace registry is built."""
    workspace = registry.workspace

    def add(operation_id, name, description, schema, output, handler, *, permissions=('read',),
            kind=None, confirmation=None, **metadata):
        mutates = any(permission != 'read' for permission in permissions)
        module = operation_id.split('.')[0]
        registry.register(Operation(
            operation_id=operation_id, name=name, description=description, module=module,
            input_schema=schema, output_schema=output, handler=handler,
            kind=kind or ('business' if mutates else 'query'), permissions=permissions,
            confirmation=confirmation or ('agent' if mutates else 'none'),
            tags=(module,), examples=_example(operation_id, schema), **metadata))

    def run_start(context, parameters):
        episode = parameters.get('episode')
        if episode is None:
            return workspace.run(parameters['project_id'], parameters.get('kind', 'simulate'),
                                 parameters.get('options'), parameters.get('timeout'), parameters.get('metadata'))
        # Evaluation owns an isolated episode directory. Public callers cannot
        # choose a hidden working directory or mutate evaluator records.
        if context.source != 'evaluation':
            raise PermissionError('Only the evaluation executor can run an isolated episode')
        root = workspace.projects.path(parameters['project_id'])
        target = confined(root, f'.af/episodes/{episode["job_id"]}/{episode["index"]}', hidden=True)
        if not target.is_dir():
            raise FileNotFoundError('Evaluation episode does not exist')
        kind = parameters.get('kind', 'simulate')
        availability = workspace.run_availability(parameters['project_id'], kind, parameters.get('options'))
        try:
            specification = eda.prepare(target, kind, parameters.get('options', {}), workspace.config(parameters['project_id']))
            if not availability['available']:
                raise RuntimeError('; '.join(availability['reasons']))
        except RuntimeError as error:
            reason = '; '.join(availability['reasons']) or str(error)
            unavailable = workspace.runner.start(target, kind, [], metadata={'unavailable': reason})
            return workspace.runner.wait(target, unavailable['id'], timeout=5)
        if parameters.get('timeout') is not None:
            specification['timeout'] = parameters['timeout']
        details = specification.setdefault('metadata', {})
        incoming = parameters.get('metadata', {})
        for key in ('generated_files', 'conditions'):
            details[key] = {**details.get(key, {}), **incoming.get(key, {})}
        details.update({key: value for key, value in incoming.items() if key not in ('generated_files', 'conditions')})
        return workspace.runner.start(target, kind, **specification)

    def run_available(context, parameters):
        pid = parameters.get('project_id') or context.project_id
        return workspace.run_availability(pid, parameters.get('kind', 'simulate'), parameters.get('options'))['reasons']

    def models(context, parameters):
        pid = parameters.get('project_id')
        root = workspace.projects.path(pid) if pid else workspace.root
        executable = shutil.which(workspace.config(pid).get('tools', {}).get(parameters['backend']) or parameters['backend'])
        selection = {'model': parameters['model']} if 'model' in parameters else {}
        return model_catalog(parameters['backend'], executable, root, refresh=parameters.get('refresh', False), **selection)

    def artifact(context, parameters):
        path = workspace.artifact(parameters['project_id'], parameters['run_id'], parameters['path'])
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('Artifact exceeds the 2 MiB inline download limit; use artifact.stat/read or the streaming artifact download route')
        return _download(path.read_bytes(), path.name, mimetypes.guess_type(path.name)[0] or 'application/octet-stream')

    def approve(context, parameters):
        if context.source in ('agent', 'workflow', 'evaluation'):
            raise PermissionError('Agent and workflow callers cannot decide user approvals')
        return workspace.agents.approve(parameters['project_id'], parameters['session_id'],
                                        parameters['approval_id'], parameters['approved'])

    def review_approval(context, parameters):
        require_local_approval(context)
        return workspace.agents.review_approval(parameters['project_id'], parameters['session_id'],
                                                parameters['approval_id'], authorized=True)

    def set_auto_approval(context, parameters):
        require_local_approval(context)
        return workspace.agents.set_auto_approval(parameters['project_id'], parameters['session_id'],
                                                  parameters['enabled'], authorized=True)

    def require_local_approval(context):
        if not local_human(context):
            raise PermissionError('Approval delegation requires a local human with approval authority')

    def local_human(context):
        return (context.principal == 'local-owner' and 'approve' in context.permissions
                and context.source in ('ui', 'legacy', 'cli'))

    def send_message(context, parameters):
        body = _without(parameters, 'project_id', 'session_id')
        untrusted = body.pop('untrusted_request', False)
        return workspace.agents.send(parameters['project_id'], parameters['session_id'], body,
                                      human_authorized=local_human(context) and not untrusted)

    add('workspace.status', 'Workspace status', 'Inspect actual toolchain, desktop and implemented capabilities.',
        object_schema({'project_id': ID}), WORKSPACE_STATUS,
        lambda c, p: workspace.status(p['project_id']) if p.get('project_id') else workspace.status(), idempotent=True)
    add('settings.get', 'Read workspace settings', 'Read public settings with credential-presence indicators, never secret values.',
        object_schema(), PUBLIC_SETTINGS, lambda c, p: workspace.settings(), idempotent=True)
    add('settings.update', 'Update workspace settings', 'Update tool, PDK, provider and UI configuration; store secrets separately.',
        object_schema({'settings': CONFIG}, ['settings']), PUBLIC_SETTINGS,
        lambda c, p: workspace.save_settings(p['settings']), permissions=('configure',), versioned=True,
        side_effects=('Updates settings.json and optionally endpoint-bound provider credential storage',))
    add('provider.test', 'Test model provider', 'Request the configured model provider catalog using endpoint-bound credentials.',
        object_schema(), PROVIDER_MODELS, lambda c, p: workspace.provider_test(),
        preconditions=('A model provider endpoint is configured',), side_effects=('Makes a network request to the configured provider',))
    add('agent.backends', 'Discover agent backends', 'Inspect API configuration and installed native agent executables.',
        object_schema({'project_id': ID}), AGENT_BACKENDS, lambda c, p: workspace.agents.backends(p.get('project_id')), idempotent=True)
    add('agent.models', 'Discover native agent models', 'Read native model and effort capabilities, optionally inspecting a custom selected model or refreshing.',
        object_schema({'project_id': ID, 'backend': {'type': 'string', 'enum': ['codex', 'claude']},
                       'model': {'type': 'string', 'maxLength': 200, 'description': 'Optional selected model ID for effort capability inspection.'},
                       'refresh': {**BOOLEAN, 'default': False}}, ['backend']), AGENT_MODELS, models)
    add('project.list', 'List projects', 'List workspace projects, restricted to the bound project for scoped callers.',
        object_schema(), array_schema(PROJECT),
        lambda c, p: [workspace.projects.get(c.project_id)] if c.project_id else workspace.projects.list(), idempotent=True)
    add('project.create', 'Create project', 'Create an educational amplifier, SKY130 starting project or native five-MOS OTA reference.',
        object_schema({'name': {'type': 'string', 'minLength': 1, 'maxLength': 100},
                       'template': {'type': 'string', 'enum': ['amplifier', 'sky130', 'ota5'], 'default': 'amplifier'}}, ['name']), PROJECT,
        lambda c, p: workspace.projects.create(p['name'], p.get('template', 'amplifier'), config=workspace.config()),
        permissions=('write',), side_effects=('Creates a new project directory and template sources',))
    def derive_reference(c, p):
        from .ota5_reference import derive_netlists
        return derive_netlists(workspace, p['project_id'], p['run_id'])
    add('reference.derive_netlists', 'Publish actual OTA netlists',
        'Derive project schematic or PEX/LVS sources only from current successful local native OTA tool outputs.',
        _project({'run_id': ID}, ['run_id']), OPEN_OBJECT, derive_reference,
        permissions=('write',), versioned=True,
        side_effects=('Writes versioned derived netlists and a report linking their hashes to the actual tool run',))
    add('project.detail', 'Inspect project', 'Read project metadata, safe effective configuration, visible files, runs and orchestration jobs.',
        _project(), object_schema({'project': PROJECT, 'configuration': PROJECT_CONFIG, 'files': array_schema(FILE), 'runs': array_schema(RUN),
                                   'jobs': array_schema(JOB)}, ['project', 'configuration', 'files', 'runs', 'jobs']),
        lambda c, p: workspace.detail(p['project_id']), idempotent=True)
    add('project.configure', 'Configure project', 'Replace project configuration overrides through the validated settings service.',
        _project({'config': PROJECT_CONFIG}, ['config']), PROJECT,
        lambda c, p: workspace.configure_project(p['project_id'], p['config']),
        permissions=('configure',), versioned=True, side_effects=('Replaces project.json configuration overrides',))
    add('file.list', 'List project files', 'List visible project files without following symbolic links.',
        _project(), array_schema(FILE), lambda c, p: workspace.projects.files(p['project_id']), idempotent=True)
    from .file_changes import prepare_change, read_consistent
    add('file.read', 'Read project file', 'Read a visible text file up to the existing 2 MiB editor limit; source_version is checked against the returned content.',
        _project({'path': PATH}, ['path']), object_schema({'path': TEXT, 'content': TEXT, 'source_version': TEXT}, ['path', 'content', 'source_version']),
        lambda c, p: read_consistent(workspace, p['project_id'], p['path']), idempotent=True)
    preview_fields = ('path', 'current_content', 'candidate_content', 'current_sha256',
                      'candidate_sha256', 'diff', 'project_version', 'proposal_sha256')
    add('file.change.preview', 'Preview exact source replacement',
        'Read a complete bounded diff for an existing visible text source. A preview grants no write permission.',
        _project({'path': PATH, 'content': {'type': 'string', 'maxLength': 64000},
                  'expected_sha256': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}}, ['path', 'content']),
        object_schema({key: TEXT for key in preview_fields}, preview_fields),
        lambda c, p: prepare_change(workspace, p['project_id'], p['path'], p['content'],
                                    p.get('expected_sha256'))[0], idempotent=True,
        preconditions=('Existing nonsecret regular text file; current and candidate fit 64000 UTF-8 bytes',
                       'Complete proposal fits 256 KiB; no hidden, symbolic-link or noncanonical paths'))
    add('file.write', 'Write project file', 'Replace a visible text file, preserving the previous content as a revision.',
        _project({'path': PATH, 'content': {'type': 'string', 'maxLength': 2 * 1024 * 1024}}, ['path', 'content']), WRITE,
        lambda c, p: workspace.projects.write(p['project_id'], p['path'], p['content']),
        permissions=('write',), versioned=True, undo=True,
        preconditions=('Path is visible and is not project.json; content fits the 2 MiB editor limit',),
        side_effects=('Writes a project file and preserves an existing file revision',))
    add('file.history', 'Read file history', 'Read revisions belonging to one visible project path.',
        _project({'path': PATH}, ['path']), array_schema(FILE_REVISION),
        lambda c, p: workspace.projects.history(p['project_id'], p['path']), idempotent=True)
    add('file.restore', 'Restore file revision', 'Restore a revision belonging to this file, preserving the current content first.',
        _project({'path': PATH, 'revision': ID}, ['path', 'revision']), WRITE,
        lambda c, p: workspace.projects.restore(p['project_id'], p['path'], p['revision']),
        permissions=('write',), versioned=True, undo=True, side_effects=('Replaces file content and creates a revision',))
    add('project.export', 'Export project', 'Create a ZIP of visible project sources and run/job/episode evidence, excluding credentials.',
        _project(), DOWNLOAD,
        lambda c, p: _download(workspace.projects.export(p['project_id']), f'analog-factory-{p["project_id"][:8]}.zip', 'application/zip'))
    add('simulation.configure', 'Configure simulation deck', 'Generate and save an op, dc, ac, transient or noise SPICE analysis deck.',
        _project({'config': SIMULATION}, ['config']), WRITE,
        lambda c, p: workspace.configure_simulation(p['project_id'], p['config']),
        permissions=('write',), versioned=True, undo=True, side_effects=('Writes the configured netlist with normal file revision history',))
    add('chat.send', 'Request model suggestion', 'Run the legacy read-only model suggestion call and save its conversation record.',
        _project({'message': MESSAGE['message'], 'files': MESSAGE['files']}, ['message']), CHAT_REPLY,
        lambda c, p: workspace.chat(p['project_id'], p['message'], p.get('files', [])),
        permissions=('execute',), side_effects=('Sends selected context to the configured provider and persists a conversation',))
    add('workflow.list', 'List saved workflows', 'Read saved workflow JSON files in the project.',
        _project(), array_schema(OPEN_OBJECT), lambda c, p: workspace.workflows(p['project_id']), idempotent=True)
    add('workflow.save', 'Save workflow', 'Validate and save a versioned tuning chain or typed execution graph.',
        _project({'workflow': WORKFLOW}, ['workflow']), WRITE,
        lambda c, p: workspace.save_workflow(p['project_id'], p['workflow']),
        permissions=('write',), versioned=True, undo=True, side_effects=('Writes workflow JSON with file revision history',))
    add('workflow.run', 'Run workflow', 'Execute a bounded v1 tuning chain or v2 graph with real operation evidence; explicit source patches wait for human diff review.',
        _project({'workflow': WORKFLOW}, ['workflow']), JOB,
        lambda c, p: workspace.start_workflow(p['project_id'], p['workflow']),
        permissions=('execute',), asynchronous=True, cancellable=True, task_kind='job',
        preconditions=('No active workflow or evaluation job in this project', 'Workflow passes the selected schema-version validation'),
        side_effects=('Creates an orchestration job and operation snapshots, logs and results',
                      'An approved source_patch node uses file.write to preserve a revision and replace one source',))
    add('evaluation.run', 'Run random-search evaluation', 'Evaluate 1–20 isolated educational-circuit episodes; no model training is performed.',
        _project({'episodes': {'type': 'integer', 'minimum': 1, 'maximum': 20, 'default': 3},
                  'seed': {'type': 'integer', 'default': 42}}), JOB,
        lambda c, p: workspace.evaluate(p['project_id'], _without(p, 'project_id')),
        permissions=('execute',), asynchronous=True, cancellable=True, task_kind='job',
        preconditions=('No active workflow or evaluation job in this project', 'Project has the supported RD testbench and specifications.json'),
        side_effects=('Creates isolated episode sources, a frozen evaluator record and real simulation evidence',))
    add('job.list', 'List orchestration jobs', 'List tuning and evaluation records, recovering abandoned job status when needed.',
        _project(), array_schema(JOB), lambda c, p: workspace.jobs.list(workspace.projects.path(p['project_id'])))
    add('job.get', 'Read orchestration job', 'Read a project-owned job and its progress or final outcome.',
        _project({'job_id': ID}, ['job_id']), JOB,
        lambda c, p: workspace.jobs.get(workspace.projects.path(p['project_id']), p['job_id']))
    add('job.cancel', 'Cancel orchestration job', 'Cooperatively stop an active workflow/evaluation and its active child run.',
        _project({'job_id': ID}, ['job_id']), JOB,
        lambda c, p: workspace.jobs.cancel(workspace.projects.path(p['project_id']), p['job_id']),
        permissions=('execute',), confirmation='none', idempotent=True,
        side_effects=('Signals job cancellation; completion is reported by the job record',))
    add('run.list', 'List EDA runs', 'List project and isolated evaluation-episode run records with current staleness.',
        _project(), array_schema(RUN), lambda c, p: workspace.runs(p['project_id']))
    add('run.start', 'Start EDA run', 'Launch simulation, schematic/layout editing, netlisting, DRC, extraction or LVS using the existing adapters.',
        _project({'kind': {'type': 'string', 'enum': ['simulate', 'xschem', 'magic', 'drc', 'extract', 'lvs'], 'default': 'simulate'},
                  'options': {**RUN_OPTIONS, 'default': {}},
                  'timeout': {'type': 'number', 'minimum': 0.1, 'maximum': 3600, 'units': 'seconds',
                              'description': 'Optional override of the adapter timeout; see options.timeout for conditional defaults.'},
                  'metadata': RUN_METADATA,
                  'episode': object_schema({'job_id': ID, 'index': {'type': 'integer', 'minimum': 0}}, ['job_id', 'index'])}),
        RUN, run_start, permissions=('execute',), asynchronous=True, cancellable=True,
        retryable=True, task_kind='run', availability=run_available,
        preconditions=('Required tools, source files, PDK assets and desktop session are available',),
        side_effects=('Creates a source snapshot and run evidence; GUI editors edit original project sources',))
    def read_run(context, parameters):
        record = workspace.runner.get(workspace.run_root(parameters['project_id'], parameters['run_id']), parameters['run_id'])
        if parameters.get('view', 'full') == 'summary':
            from .operation_summary import summarize_record
            return summarize_record(record, parameters['project_id'], progress=registry._progress(record))
        return record
    add('run.get', 'Read EDA run', 'Read authoritative run state. Use view=summary for bounded polling; full preserves all provenance.',
        _run({'view': {'type': 'string', 'enum': ['full', 'summary'], 'default': 'full'}}), RUN, read_run)
    add('run.cancel', 'Cancel EDA run', 'Signal cancellation of the run process group, including child processes.',
        _run(), RUN, lambda c, p: workspace.runner.cancel(workspace.run_root(p['project_id'], p['run_id']), p['run_id']),
        permissions=('execute',), confirmation='none', idempotent=True,
        side_effects=('Signals run process group cancellation; inspect the record for the terminal outcome',))
    add('run.retry', 'Retry EDA run', 'Retry a completed run with its adapter command and metadata, current sources, and a new run ID.',
        _run(), RUN, lambda c, p: workspace.runner.retry(workspace.run_root(p['project_id'], p['run_id']), p['run_id']),
        permissions=('execute',), asynchronous=True, cancellable=True, retryable=True, task_kind='run',
        preconditions=('The original run is terminal',), side_effects=('Creates a fresh snapshot and a new run linked to its parent',))
    add('run.log', 'Read EDA logs', 'Read the final 256 KiB of each recognized run log.', _run(),
        object_schema({'content': TEXT, 'limit': TEXT}, ['content', 'limit']),
        lambda c, p: workspace.log(p['project_id'], p['run_id']))
    add('run.waveforms', 'Read simulation waveforms', 'Parse the actual waveforms.raw artifact for plotting.', _run(), WAVEFORMS,
        lambda c, p: workspace.waveforms(p['project_id'], p['run_id']))
    add('run.artifact', 'Download run artifact', 'Read a confined artifact from a project-owned run as a base64 download envelope.',
        _run({'path': PATH}, ['path']), DOWNLOAD, artifact)
    add('agent.session.list', 'List conversations', 'List or search public session titles and message/reply text in one project.',
        _project({'query': {'type': 'string', 'maxLength': 200, 'default': ''}}), array_schema(SESSION),
        lambda c, p: workspace.agents.list(p['project_id'], p.get('query', '')))
    add('agent.session.create', 'Create conversation', 'Create a durable API or native CLI agent conversation with a fixed permission mode.',
        _project(SESSION_OPTIONS), SESSION,
        lambda c, p: workspace.agents.create(p['project_id'], _without(p, 'project_id'),
            scope_authorized=c.principal == 'local-owner' and 'approve' in c.permissions and c.source in ('ui', 'legacy', 'cli')),
        permissions=('write',), side_effects=('Creates a durable agent session record',))
    add('agent.session.get', 'Read conversation', 'Read a public conversation, events and pending approval, excluding private provider history.',
        _session(), SESSION, lambda c, p: workspace.agents.get(p['project_id'], p['session_id']))
    add('agent.session.update', 'Rename conversation', 'Rename a conversation after any active worker has finished draining.',
        _session({'title': {'type': 'string', 'minLength': 1, 'maxLength': 120}}, ['title']), SESSION,
        lambda c, p: workspace.agents.update(p['project_id'], p['session_id'], {'title': p['title']}),
        permissions=('write',), versioned=True, side_effects=('Changes the durable conversation title',))
    add('agent.session.delete', 'Delete conversation', 'Delete an idle conversation record without deleting project sources.',
        _session(), object_schema({'deleted': BOOLEAN, 'id': ID}, ['deleted', 'id']),
        lambda c, p: workspace.agents.delete(p['project_id'], p['session_id']),
        permissions=('write',), versioned=True, side_effects=('Deletes a conversation record permanently',))
    add('agent.session.send', 'Send agent message', 'Start one bounded agent turn with selected files, text attachments or run context.',
        _session(MESSAGE, ['message']), SESSION, send_message,
        permissions=('execute',), asynchronous=True, cancellable=True, task_kind='agent',
        preconditions=('No other active agent turn in the project', 'At most four active agent turns in the workspace'),
        side_effects=('Starts a model/CLI turn and persists its events and any explicitly permitted actions',))
    add('agent.session.cancel', 'Cancel agent turn', 'Cancel a pending or active turn and unblock any pending approval.',
        _session(), SESSION, lambda c, p: workspace.agents.cancel(p['project_id'], p['session_id']),
        permissions=('execute',), confirmation='none', idempotent=True,
        side_effects=('Cancels the turn; its active slot remains reserved until the worker drains',))
    add('agent.session.approve', 'Decide agent approval', 'Accept or reject the exact pending API-agent proposal; agent callers cannot self-approve.',
        _session({'approval_id': ID, 'approved': BOOLEAN}, ['approval_id', 'approved']), SESSION, approve,
        permissions=('approve',), confirmation='none', side_effects=('Releases a pending API-agent tool approval decision',))
    add('agent.session.review_approval', 'Review pending agent approval',
        'Ask the project model API to review the exact pending API or Claude action. Codex uses official automatic review configured before a turn and does not support this single-action request. Requires local human approval authority; uncertain results remain pending.',
        _session({'approval_id': ID}, ['approval_id']), SESSION, review_approval,
        permissions=('approve',), confirmation='none',
        side_effects=('Starts an independent model review which may approve or reject the pending action',))
    add('agent.session.set_auto_approval', 'Set automatic AI approval',
        'Enable or disable automatic approval review for this conversation. Codex uses official Auto-review and can be changed only while idle, with exclusively human-authored history. API and Claude use the project Model API. Requires local human authority; resets on server restart.',
        _session({'enabled': BOOLEAN}, ['enabled']), SESSION, set_auto_approval,
        permissions=('approve',), confirmation='none',
        side_effects=('Changes an in-memory conversation approval grant; API and Claude may also start review of a pending action',))
    from .operation_graph import register_operations as register_graph_operations
    register_graph_operations(registry)
    from .operation_waveforms import register_operations as register_waveform_operations
    register_waveform_operations(registry)


def _query(query, name, default=''):
    return query.get(name, [default])[0]


def _body(groups, query, body):
    # Path ownership always wins over any accidental body-supplied identifiers.
    return {**body, **groups}


def _groups(groups, query, body):
    return dict(groups)


def _wrapped(name):
    return lambda groups, query, body: {**groups, name: body}


P = r'/api/projects/(?P<project_id>[^/]+)'
S = P + r'/agent/sessions/(?P<session_id>[^/]+)'
R = P + r'/runs/(?P<run_id>[^/]+)'
J = P + r'/jobs/(?P<job_id>[^/]+)'
LEGACY_ROUTES = [
    ('GET', r'/api/status', 'workspace.status',
     lambda g, q, b: {'project_id': _query(q, 'project_id')} if _query(q, 'project_id') else {}),
    ('GET', r'/api/settings', 'settings.get', _groups),
    ('PUT', r'/api/settings', 'settings.update', _wrapped('settings')),
    ('POST', r'/api/provider/test', 'provider.test', _groups),
    ('GET', r'/api/agents', 'agent.backends', lambda g, q, b: {'project_id': _query(q, 'project_id')} if _query(q, 'project_id') else {}),
    ('GET', r'/api/agents/models', 'agent.models', lambda g, q, b: {
        'backend': _query(q, 'backend'), 'refresh': _query(q, 'refresh') == '1',
        **({'model': _query(q, 'model')} if 'model' in q else {}),
        **({'project_id': _query(q, 'project_id')} if _query(q, 'project_id') else {})}),
    ('GET', r'/api/projects', 'project.list', _groups),
    ('POST', r'/api/projects', 'project.create', _body, {'status': 201}),
    ('GET', P, 'project.detail', _groups),
    ('PUT', P + '/config', 'project.configure', _wrapped('config')),
    ('GET', P + '/files', 'file.read', lambda g, q, b: {**g, 'path': _query(q, 'path')}),
    ('PUT', P + '/files', 'file.write', lambda g, q, b: {**g, 'path': _query(q, 'path'), 'content': b.get('content')}),
    ('GET', P + '/history', 'file.history', lambda g, q, b: {**g, 'path': _query(q, 'path')}),
    ('POST', P + '/restore', 'file.restore', _body),
    ('POST', P + '/simulation', 'simulation.configure', _wrapped('config')),
    ('GET', P + '/export', 'project.export', _groups, {'response': 'export'}),
    ('POST', P + '/chat', 'chat.send', _body),
    ('GET', P + '/workflows', 'workflow.list', _groups),
    ('POST', P + '/workflows', 'workflow.save', _body),
    ('POST', P + '/workflows/run', 'workflow.run', _body, {'status': 202}),
    ('POST', P + '/evaluate', 'evaluation.run', _body, {'status': 202}),
    ('GET', P + '/jobs', 'job.list', _groups),
    ('GET', J, 'job.get', _groups),
    ('POST', J + '/cancel', 'job.cancel', _groups),
    ('GET', P + '/runs', 'run.list', _groups),
    ('POST', P + '/runs', 'run.start', _body, {'status': 202}),
    ('GET', R, 'run.get', lambda g, q, b: {**g, **({'view': _query(q, 'view')} if 'view' in q else {})}),
    ('POST', R + '/cancel', 'run.cancel', _groups),
    ('POST', R + '/retry', 'run.retry', _groups, {'status': 202}),
    ('GET', R + '/log', 'run.log', _groups),
    ('GET', R + '/waveforms', 'run.waveforms', _groups),
    ('GET', R + '/artifact', 'run.artifact', lambda g, q, b: {**g, 'path': _query(q, 'path')}, {'response': 'artifact'}),
    ('GET', P + '/agent/sessions', 'agent.session.list', lambda g, q, b: {**g, 'query': _query(q, 'q')}),
    ('POST', P + '/agent/sessions', 'agent.session.create', _body, {'status': 201}),
    ('GET', S, 'agent.session.get', _groups),
    ('PUT', S, 'agent.session.update', _body),
    ('DELETE', S, 'agent.session.delete', _groups),
    ('POST', S + '/messages', 'agent.session.send', _body, {'status': 202}),
    ('POST', S + '/cancel', 'agent.session.cancel', _groups),
    ('POST', S + '/approval', 'agent.session.approve', _body),
    ('POST', S + '/approval/review', 'agent.session.review_approval', _body),
    ('POST', S + '/auto-approval', 'agent.session.set_auto_approval', _body),
]
