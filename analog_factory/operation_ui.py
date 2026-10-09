"""Explicit browser operation adapters; a live browser must acknowledge execution.

The browser controllers remain authoritative for drafts, selections, and geometry.
This catalog never maps arbitrary selectors or JavaScript strings to tool calls.
"""
from .operations import Operation
from .operation_catalog import EFFORT, AGENT_SCOPE, ATTACHMENT as INPUT_ATTACHMENT
from .operation_training import OPTIONS as TRAINING_OPTIONS, JOB as TRAINING_JOB, SPECIFICATION as TRAINING_SPECIFICATION
from .operation_native_canvas import STATE as NATIVE_STATE, IDENTIFIER as NATIVE_IDENTIFIER
from .operation_cli_updates import STATUS as CLI_STATUS


def obj(properties=None, required=(), **extra):
    return {'type': 'object', 'properties': properties or {}, 'required': list(required),
            'additionalProperties': False, **extra}


def string(maximum=2048, **extra):
    return {'type': 'string', 'maxLength': maximum, **extra}


def enum(*values):
    return {'type': 'string', 'enum': list(values)}


def array(items, maximum=1000):
    return {'type': 'array', 'items': items, 'maxItems': maximum}


BOOL = {'type': 'boolean'}
NUMBER = {'type': 'number'}
INTEGER = {'type': 'integer', 'minimum': 0}
OBJECT = {'type': 'object'}
PATHS = array(string(), 20)
NULLABLE_ID = {'anyOf': [string(200), {'type': 'null'}]}
FORMS = enum('simulation', 'verification', 'evaluation', 'new_project', 'new_file', 'workflow')
VALUES = {'type': 'object', 'additionalProperties': {'anyOf': [string(2 * 1024 * 1024), NUMBER, BOOL]}}


def record(properties, required=(), **annotations):
    """Describe published state while allowing additional controller fields."""
    return obj(properties, required, additionalProperties=True, **annotations)


def items(schema):
    return {'type': 'array', 'items': schema}


TEXT_RESULT = {'type': 'string'}
NULLABLE_TEXT = {'anyOf': [TEXT_RESULT, {'type': 'null'}]}
DISCLOSURE = record({'id': TEXT_RESULT, 'open': BOOL})
ATTACHMENT = record({'id': INTEGER, 'name': TEXT_RESULT})
AGENT_STATE = record({
    'project_id': NULLABLE_TEXT, 'session_id': NULLABLE_TEXT, 'status': TEXT_RESULT,
    'config': record({'backend': TEXT_RESULT, 'model': TEXT_RESULT, 'mode': TEXT_RESULT, 'fast_mode': BOOL, 'effort': EFFORT, 'scope': AGENT_SCOPE}),
    'history_open': BOOL, 'context': items(TEXT_RESULT), 'attachments': items(ATTACHMENT),
    'run_id': NULLABLE_TEXT, 'has_draft': BOOL, 'draft_revision': INTEGER,
    'quick_dialog_open': BOOL, 'disclosures': items(DISCLOSURE), 'popovers': items(TEXT_RESULT),
    'sessions': items(record({'id': TEXT_RESULT, 'title': TEXT_RESULT, 'status': TEXT_RESULT, 'backend': TEXT_RESULT})),
    'accepted_turn_id': TEXT_RESULT,
})
LIFECYCLE_STATE = record({
    'open': BOOL, 'action': TEXT_RESULT, 'busy': BOOL, 'project_id': NULLABLE_TEXT,
    'source_path': NULLABLE_TEXT, 'name': TEXT_RESULT, 'destination': TEXT_RESULT,
    'trash_id': NULLABLE_TEXT, 'archive_loaded': BOOL, 'archive_name': TEXT_RESULT,
    'draft_revision': INTEGER,
    'project_trash': items(record({'id': TEXT_RESULT, 'name': TEXT_RESULT})),
    'file_trash': items(record({'id': TEXT_RESULT, 'path': TEXT_RESULT})),
})
SIMULATION_BATCH_STATE = record({
    'open': BOOL, 'project_id': NULLABLE_TEXT, 'busy': BOOL, 'draft_revision': INTEGER,
    'spec': {'anyOf': [OBJECT, {'type': 'null'}]}, 'scenario_count': INTEGER,
    'preview_current': BOOL, 'job_id': NULLABLE_TEXT, 'status': NULLABLE_TEXT,
    'completed_runs': INTEGER, 'active_run': NULLABLE_TEXT,
    'qualification_jobs': items(record({'id': TEXT_RESULT, 'kind': TEXT_RESULT, 'section': TEXT_RESULT, 'model_semantics': TEXT_RESULT})),
    'evidence_run_id': NULLABLE_TEXT, 'error': TEXT_RESULT,
})
DRC_NAVIGATION_STATE = record({'project_id':NULLABLE_TEXT,'run_id':NULLABLE_TEXT,'source_run_id':NULLABLE_TEXT,
    'error_index':INTEGER,'busy':BOOL,'focused':BOOL,'status':NULLABLE_TEXT,
    'box':{'anyOf':[{'type':'array','items':{'type':'number'},'minItems':4,'maxItems':4},{'type':'null'}]},'error':TEXT_RESULT})
EDITOR_RECOVERY_STATE = record({'project_id':NULLABLE_TEXT,'path':NULLABLE_TEXT,'busy':BOOL,
    'recovery_count':INTEGER,'draft_revision':INTEGER,'inspection_id':NULLABLE_TEXT,
    'status':TEXT_RESULT,'error':TEXT_RESULT,'storage_error':TEXT_RESULT})
RECOVERY_METADATA = obj({'project_id':TEXT_RESULT,'path':TEXT_RESULT,'version':TEXT_RESULT,
    'draft_id':TEXT_RESULT,'owner_id':TEXT_RESULT,'own':BOOL,
    'updated_at':NUMBER,'bytes':INTEGER}, ('project_id','path','version','draft_id','owner_id','own','updated_at','bytes'))
TRAINING_STATE = record({
    'open': BOOL, 'project_id': NULLABLE_TEXT, 'busy': BOOL,
    'options': {'anyOf': [TRAINING_OPTIONS, {'type': 'null'}]},
    'job_id': NULLABLE_TEXT, 'status': NULLABLE_TEXT, 'phase': NULLABLE_TEXT,
    'completed_episodes': INTEGER, 'model_weights_updated': BOOL,
    'taskset': {'anyOf': [OBJECT, {'type': 'null'}]},
    'resources': {'anyOf': [OBJECT, {'type': 'null'}]},
    'checkpoint': {'anyOf': [OBJECT, {'type': 'null'}]}, 'error': TEXT_RESULT,
    'taskset_draft': TEXT_RESULT, 'taskset_preview_id': NULLABLE_TEXT,
})
TERMINAL_OPTIONS = obj({
    'backend': enum('codex', 'claude'), 'mode': enum('read-only', 'workspace-write'),
    'model': string(256), 'cols': {'type':'integer','minimum':20,'maximum':240},
    'rows': {'type':'integer','minimum':5,'maximum':100},
    'runtime_seconds': {'type':'number','minimum':1,'maximum':14400},
})
NATIVE_TERMINAL_STATE = record({
    'open': BOOL, 'project_id': NULLABLE_TEXT, 'busy': BOOL, 'terminal_id': NULLABLE_TEXT,
    'status': NULLABLE_TEXT, 'options': TERMINAL_OPTIONS, 'output_offset': INTEGER,
    'sessions': items(record({'id':TEXT_RESULT,'backend':TEXT_RESULT,'status':TEXT_RESULT,'mode':TEXT_RESULT})),
    'error': TEXT_RESULT,
})
NATIVE_CANVAS_OPTIONS = obj({
    'tool': enum('xschem','magic'), 'source': string(),
    'instance_id': string(128), 'direction': enum('left','right','up','down'),
    'grid_steps': {'type':'integer','minimum':1,'maximum':999},
    'from_instance': string(128), 'from_pin': string(128),
    'to_instance': string(128), 'to_pin': string(128),
    'route': enum('horizontal_first','vertical_first'), 'layer': string(128),
})
# The shared browser transport strips token fields from both replies and state.
# Paint commands use the private controller's freshly inspected native box token;
# public browser discovery needs only the actual box geometry.
NATIVE_BROWSER_STATE = obj(
    {key:value for key,value in NATIVE_STATE['properties'].items() if key != 'box_token'},
    tuple(key for key in NATIVE_STATE['required'] if key != 'box_token'))
NATIVE_CANVAS_STATE = obj({
    'open': BOOL, 'project_id': NULLABLE_TEXT, 'busy': BOOL,
    'options': {'anyOf':[NATIVE_CANVAS_OPTIONS,{'type':'null'}]},
    'source_version': NULLABLE_TEXT, 'run_id': NULLABLE_TEXT, 'session_id': NULLABLE_TEXT,
    'native': {'anyOf':[NATIVE_BROWSER_STATE,{'type':'null'}]},
    'unknown_outcome': BOOL, 'needs_inspection': BOOL, 'error': TEXT_RESULT,
}, ('open','project_id','busy','options','source_version','run_id','session_id','native',
    'unknown_outcome','needs_inspection','error'))
CLI_TRACKING = obj({'automatic':BOOL,'interval_hours':{'type':'integer','minimum':6,'maximum':168}}, ('automatic','interval_hours'))
AGENT_CLI_STATE = obj({
    'open':BOOL,'busy':BOOL,'project_id':NULLABLE_TEXT,
    'paths':obj({'codex':TEXT_RESULT,'claude':TEXT_RESULT}, ('codex','claude')),
    'project_version':NULLABLE_TEXT,'workspace_version':NULLABLE_TEXT,
    'tracking':CLI_TRACKING,'status':{'anyOf':[CLI_STATUS,{'type':'null'}]},
    'error':TEXT_RESULT,'uncertain':BOOL,
}, ('open','busy','project_id','paths','project_version','workspace_version','tracking','status','error','uncertain'))
BROWSER_STATE = record({
    'project_id': NULLABLE_TEXT,
    'workspace': record({
        'project_id': NULLABLE_TEXT, 'tab': TEXT_RESULT, 'files': items(TEXT_RESULT),
        'context': items(TEXT_RESULT), 'run_id': NULLABLE_TEXT, 'connected': BOOL,
        'preferences': record({'language': TEXT_RESULT, 'theme': TEXT_RESULT}),
        'draft_revision': INTEGER, 'disclosures': items(DISCLOSURE),
        'folders': items(record({'path': TEXT_RESULT, 'open': BOOL})), 'dialogs': items(TEXT_RESULT),
    }),
    'editor': record({'path': NULLABLE_TEXT, 'dirty': BOOL, 'readonly': BOOL}),
    'editor_recovery': EDITOR_RECOVERY_STATE,
    'runs': record({'selected': NULLABLE_TEXT, 'comparison': items(NULLABLE_TEXT),
                    'job_id': NULLABLE_TEXT, 'evaluation_id': NULLABLE_TEXT}),
    'waveform': record({'loaded': BOOL, 'plot': INTEGER, 'component': TEXT_RESULT, 'x_scale': TEXT_RESULT,
                       'reference': {'anyOf':[INTEGER,{'type':'null'}]}, 'unwrap': BOOL,
                       'traces': items(INTEGER), 'zoom': items(NUMBER),
                       'cursor': {'anyOf': [record({'index': INTEGER, 'x': NUMBER}), {'type': 'null'}]},
                       'sample': {'anyOf': [INTEGER, {'type': 'null'}]}}),
    'workflow': record({'selected_node': NULLABLE_TEXT, 'name': NULLABLE_TEXT,
                       'schema_version': {'anyOf':[INTEGER,{'type':'null'}]},
                       'nodes': INTEGER, 'draft_revision': INTEGER}),
    'agent': AGENT_STATE,
    'agent_cli': AGENT_CLI_STATE,
    'lifecycle': LIFECYCLE_STATE,
    'simulation_batch': SIMULATION_BATCH_STATE,
    'characterization': SIMULATION_BATCH_STATE,
    'training': TRAINING_STATE,
    'native_terminal': NATIVE_TERMINAL_STATE,
    'native_canvas': NATIVE_CANVAS_STATE,
    'drc_navigation': DRC_NAVIGATION_STATE,
    'layout': record({'sizes': record({key: NUMBER for key in ('tree', 'ai', 'log', 'composer')}),
                     'visible': record({key: BOOL for key in ('tree', 'ai', 'log')}),
                     'agent_expanded': BOOL, 'overlay': NULLABLE_TEXT}),
}, description='Redacted controller state. Modules can be absent during startup; source and composer text require explicit read operations.')
SESSION_STATE = obj({'client_id': TEXT_RESULT, 'version': INTEGER, 'state': BROWSER_STATE},
                    ('client_id', 'version', 'state'))
DATA_SCHEMAS = {
    **{f'agent_cli.{name}':AGENT_CLI_STATE for name in ('open','close','refresh','check_updates','set_paths','save_paths','reset_paths','set_tracking_draft','set_tracking')},
    **{f'native_canvas.{name}': NATIVE_CANVAS_STATE for name in ('open','close','set_options','start','refresh','select','move','connect_pins','paint_box','save','stop')},
    **{f'editor_recovery.{name}':EDITOR_RECOVERY_STATE for name in ('restore','discard','resolve')},
    'editor_recovery.list':items(RECOVERY_METADATA),
    'editor_recovery.read':obj({'project_id':TEXT_RESULT,'path':TEXT_RESULT,'version':TEXT_RESULT,
        'draft_id':TEXT_RESULT,'owner_id':TEXT_RESULT,
        'updated_at':NUMBER,'content':TEXT_RESULT,'base':TEXT_RESULT}, ('project_id','path','version','draft_id','owner_id','updated_at','content','base')),
    'editor_recovery.inspect':obj({'inspection_id':TEXT_RESULT,'project_id':TEXT_RESULT,'path':TEXT_RESULT,
        'base':TEXT_RESULT,'latest':TEXT_RESULT,'draft':TEXT_RESULT,'latest_version':TEXT_RESULT,
        'preview':{'anyOf':[OBJECT,{'type':'null'}]}}, ('inspection_id','project_id','path','base','latest','draft','latest_version','preview')),
    **{f'characterization.{name}': SIMULATION_BATCH_STATE for name in ('open','close','set_draft','refresh','cancel')},
    **{f'drc_navigation.{name}':DRC_NAVIGATION_STATE for name in ('set_index','focus','refresh','stop')},
    **{f'native_terminal.{name}': NATIVE_TERMINAL_STATE for name in ('open','close','set_options','start','refresh','resize','stop')},
    'native_terminal.input': obj({'accepted_bytes':INTEGER}, ('accepted_bytes',)),
    'workspace.read': BROWSER_STATE,
    'runs.derive_reference': OBJECT,
    'workspace.disclosures': items(record({'id': TEXT_RESULT, 'open': BOOL, 'label': TEXT_RESULT, 'hidden': BOOL})),
    'form.read': VALUES, 'form.set': VALUES, 'settings.set_draft': VALUES,
    'settings.project_environment_read': VALUES, 'settings.project_environment_draft': VALUES,
    'settings.project_provider_read': VALUES, 'settings.project_provider_draft': VALUES,
    'editor.read': obj({'path': NULLABLE_TEXT, 'content': TEXT_RESULT, 'dirty': BOOL, 'readonly': BOOL},
                       ('path', 'content', 'dirty', 'readonly')),
    'agent.read': AGENT_STATE, 'agent.send': AGENT_STATE, 'agent.stop': AGENT_STATE,
    'agent.read_draft': obj({'message': TEXT_RESULT, 'files': items(TEXT_RESULT),
                           'attachments': items(ATTACHMENT), 'run_id': NULLABLE_TEXT},
                          ('message', 'files', 'attachments', 'run_id')),
    'agent.quick_messages': items(record({'title': TEXT_RESULT, 'message': TEXT_RESULT}, ('title', 'message'))),
    **{f'lifecycle.{name}': LIFECYCLE_STATE for name in ('open', 'close', 'set_draft', 'refresh_trash')},
    'lifecycle.backup': obj({'filename': TEXT_RESULT, 'size': INTEGER}, ('filename', 'size')),
    **{f'simulation_batch.{name}': SIMULATION_BATCH_STATE for name in ('open', 'close', 'set_draft', 'cancel', 'refresh')},
    **{f'training.{name}': TRAINING_STATE for name in ('open', 'close', 'set_options', 'set_taskset_draft', 'validate_taskset', 'create_taskset', 'refresh', 'select_job', 'cancel')},
    **{f'training.{name}': TRAINING_JOB for name in ('start', 'evaluate', 'resume_evaluation', 'replay')},
}

# Each row has a stable name, actionable description, parameters, required fields,
# and additional permission(s) beyond browser control. No approval action exists.
COMMANDS = [
    ('agent_cli.open', 'Open Codex/Claude project paths, detected versions and cached official release observations.', {}, (), ('read',)),
    ('agent_cli.close', 'Close CLI controls without saving drafts or changing CLI processes.', {}, (), ('read',)),
    ('agent_cli.refresh', 'Refresh CLI status and release cache while preserving unsaved drafts and their original concurrency guards.', {}, (), ('read',)),
    ('agent_cli.check_updates', 'Check fixed official latest published releases through the shared handler and show the resulting cache, including failures. No installation or upgrade occurs.', {'backends':{'type':'array','items':enum('codex','claude'),'minItems':1,'maxItems':2,'uniqueItems':True},'force':BOOL}, (), ('read',)),
    ('agent_cli.set_paths', 'Edit project Codex/Claude path drafts without persisting them.', {'codex':string(4096),'claude':string(4096)}, (), ('read',)),
    ('agent_cli.save_paths', 'Save the selected project CLI path overrides using the opened project version, preserving other configuration.', {}, (), ('configure',)),
    ('agent_cli.reset_paths', 'Remove project Codex/Claude overrides using the opened project version and inherit workspace/PATH configuration.', {}, (), ('configure',)),
    ('agent_cli.set_tracking_draft', 'Edit automatic update-check preferences without persisting them.', CLI_TRACKING['properties'], (), ('read',)),
    ('agent_cli.set_tracking', 'Save workspace-only automatic release checks and interval using the opened settings version. Checks run only while this server is running; CLIs are never upgraded.', CLI_TRACKING['properties'], ('automatic',), ('configure',)),
    ('native_canvas.open', 'Open managed native canvas controls, optionally inspecting a specific owned native run and session.', {'session_id':string(32,pattern='^[a-f0-9]{32}$'),'run_id':string(32,pattern='^[a-f0-9]{32}$')}, (), ('read',)),
    ('native_canvas.close', 'Close native controls without stopping or saving the managed native editor.', {}, (), ('read',)),
    ('native_canvas.set_options', 'Update bounded native control drafts only, with no native action. Tool and source stay locked while connected; instance, movement, pin, route and layer drafts use the same handler as manual controls.', NATIVE_CANVAS_OPTIONS['properties'], (), ('read',)),
    ('native_canvas.start', 'Open the selected source in a new managed native editor and inspect its actual acknowledged state.', {}, (), ('read','execute')),
    ('native_canvas.refresh', 'Inspect the selected owned native session, including current unsaved edits and revision.', {}, (), ('read',)),
    ('native_canvas.select', 'Select a named instance in the actual managed Xschem window.', {'instance_id':NATIVE_IDENTIFIER}, (), ('read','execute')),
    ('native_canvas.move', 'Move a named native instance in bounded grid steps, using the inspected native revision.', {'instance_id':NATIVE_IDENTIFIER,'direction':enum('left','right','up','down'),'grid_steps':{'type':'integer','minimum':1,'maximum':999}}, (), ('read','execute','write')),
    ('native_canvas.connect_pins', 'Connect two actual named Xschem pins through the shared native handler.', {**{key:NATIVE_IDENTIFIER for key in ('from_instance','from_pin','to_instance','to_pin')},'route':enum('horizontal_first','vertical_first')}, (), ('read','execute','write')),
    ('native_canvas.paint_box', 'Paint a discovered Magic layer inside the previously inspected native box.', {'layer':NATIVE_IDENTIFIER}, (), ('read','execute','write')),
    ('native_canvas.save', 'Explicitly save the inspected native document through registered file.write with source guards and history.', {}, (), ('read','execute','write')),
    ('native_canvas.stop', 'Cancel the selected owned native run without implicitly saving its document.', {}, (), ('read','execute')),
    ('drc_navigation.set_index','Select an error tile index within the visible DRC report and update the browser draft without launching Magic.',{'error_index':{'type':'integer','minimum':1,'maximum':100000}},('error_index',),('read',)),
    ('drc_navigation.focus','Open managed Magic navigation for the selected completed local DRC report; focus is confirmed only by a native acknowledgment.',{'error_index':{'type':'integer','minimum':1,'maximum':100000}},('error_index',),('execute',)),
    ('drc_navigation.refresh','Read the native Magic DRC focus acknowledgment and update the displayed status.',{},(),('read',)),
    ('drc_navigation.stop','Stop the managed Magic DRC navigation window.',{},(),('execute',)),
    ('native_terminal.open', 'Open the human native terminal panel, optionally selecting a recorded terminal.', {'terminal_id':string(32,pattern='^[a-f0-9]{32}$')}, (), ('read','approve')),
    ('native_terminal.close', 'Detach the human terminal panel without stopping its native process.', {}, (), ('approve',)),
    ('native_terminal.set_options', 'Set bounded native launch options; arbitrary commands and arguments are not accepted.', {'options':TERMINAL_OPTIONS}, ('options',), ('approve',)),
    ('native_terminal.start', 'Launch the selected native CLI with its human approval policy and current project directory.', {}, (), ('execute','approve')),
    ('native_terminal.refresh', 'Refresh native status and render in-memory output without publishing terminal bytes in browser state.', {}, (), ('read','approve')),
    ('native_terminal.input', 'Send literal human input to the selected native terminal; agents cannot approve native prompts.', {'text':string(8192,minLength=1)}, ('text',), ('execute','approve')),
    ('native_terminal.resize', 'Resize the active native terminal and its browser renderer.', {'cols':{'type':'integer','minimum':20,'maximum':240},'rows':{'type':'integer','minimum':5,'maximum':100}}, ('cols','rows'), ('execute','approve')),
    ('native_terminal.stop', 'Stop the owned native terminal process group and show its final status.', {}, (), ('execute','approve')),
    ('training.open', 'Open model training, resources, fixed task sets, checkpoints and actual evaluation evidence.', {}, (), ('read',)),
    ('training.close', 'Close training controls without cancelling the selected job.', {}, (), ()),
    ('training.set_options', 'Set bounded local training/evaluation options without executing a job.', {'options': TRAINING_OPTIONS}, ('options',), ()),
    ('training.set_taskset_draft', 'Edit trusted circuit-family task configuration without publishing or executing it.', {'specification': TRAINING_SPECIFICATION}, ('specification',), ()),
    ('training.validate_taskset', 'Validate the taskset draft and deterministic independent splits without publishing.', {}, (), ('read',)),
    ('training.create_taskset', 'Publish the validated immutable taskset using owner configuration permission.', {}, (), ('configure',)),
    ('training.start', 'Start a bounded local language-model GRPO pilot using the selected options.', {}, (), ('execute',)),
    ('training.refresh', 'Refresh training status, curves, resources and comparison evidence.', {}, (), ('read',)),
    ('training.select_job', 'Inspect a training, evaluation or replay job in the selected project.', {'job_id': string(32, pattern='^[a-f0-9]{32}$')}, ('job_id',), ('read',)),
    ('training.evaluate', 'Reload a local checkpoint and run independent held-out comparison against base model and random search.', {'checkpoint_job_id': string(32, pattern='^[a-f0-9]{32}$')}, (), ('execute',)),
    ('training.resume_evaluation', 'Verify stopped local evaluation evidence and resume only missing held-out cases under a fresh time budget.', {}, (), ('execute',)),
    ('training.cancel', 'Cancel the selected trainer and its active simulator children.', {}, (), ('execute',)),
    ('training.replay', 'Replay a bounded recorded simulator-action trajectory in a fresh episode.', {'episode': {'type': 'integer', 'minimum': 0, 'maximum': 100000}}, ('episode',), ('execute',)),
    ('characterization.open', 'Open the OTA PVT characterization dialog, optionally inspecting a recorded job.', {'job_id': string(32, pattern='^[a-f0-9]{32}$')}, (), ('read',)),
    ('characterization.close', 'Close the characterization dialog.', {}, (), ()),
    ('characterization.set_draft', 'Set characterization cases, analyses and optional electrical targets.', {'spec': OBJECT}, ('spec',), ()),
    ('characterization.start', 'Run actual pre/post OTA characterization through reference.characterize.', {}, (), ('execute',)),
    ('characterization.refresh', 'Refresh actual characterization job evidence.', {}, (), ('read',)),
    ('characterization.cancel', 'Cancel the characterization job and its active simulator run.', {}, (), ('execute',)),
    ('characterization.inspect', 'Inspect an actual characterization run and simulator log.', {'run_id': string(32, pattern='^[a-f0-9]{32}$')}, ('run_id',), ('read',)),
    ('simulation_batch.open', 'Open advanced simulation for the current project, optionally selecting a recorded batch or qualification job.', {'job_id': string(32, pattern='^[a-f0-9]{32}$')}, (), ('read',)),
    ('simulation_batch.close', 'Close the advanced simulation dialog without cancelling its running job.', {}, (), ()),
    ('simulation_batch.set_draft', 'Set the editable scenario draft; simulation.batch.preview validates the completed specification.', {'spec': OBJECT}, ('spec',), ()),
    ('simulation_batch.preview', 'Validate the current draft and capture its project revision before execution.', {}, (), ('read',)),
    ('simulation_batch.start', 'Run the successfully previewed scenario draft through the shared simulation operation.', {}, (), ('execute',)),
    ('simulation_batch.qualify', 'Run real matched-device statistical qualification probes for the selected library, section and explicitly chosen sampling semantics.', {'kind': enum('global_process', 'device_mismatch'), 'section': string(100), 'model_semantics': enum('installed', 'cache_global_fet_draws')}, (), ('execute',)),
    ('simulation_batch.cancel', 'Cancel the selected active batch and its simulator children.', {}, (), ('execute',)),
    ('simulation_batch.refresh', 'Refresh actual job results and statistical qualifications in the dialog.', {}, (), ('read',)),
    ('simulation_batch.inspect', 'Display the selected scenario run log, conditions, measurement provenance and tool versions.', {'run_id': string(32, pattern='^[a-f0-9]{32}$')}, ('run_id',), ('read',)),
    ('lifecycle.open', 'Open project/file management or a versioned lifecycle draft; restoration identifiers come from shared trash-list operations.',
     {'action': enum('manage','rename','trash','move','file_trash','import','restore_project','restore_file'),
      'project_id': string(32, pattern='^[a-f0-9]{32}$'), 'trash_id': string(32, pattern='^[a-f0-9]{32}$')}, (), ('read',)),
    ('lifecycle.close', 'Close the lifecycle dialog and discard its local draft without changing project files.', {}, (), ()),
    ('lifecycle.set_draft', 'Set a lifecycle name, destination, or explicit base64 archive without submitting it.',
     {'name': string(100), 'destination': string(4096), 'content_base64': string(2796204)}, (), ()),
    ('lifecycle.submit', 'Apply the reviewed lifecycle draft through its shared business operation with the revision captured when the dialog opened.', {}, (), ('write',)),
    ('lifecycle.backup', 'Download a bounded complete backup for the selected idle project through the shared backup operation.', {}, (), ('read',)),
    ('lifecycle.refresh_trash', 'Refresh the reversible project and file trash lists in the lifecycle dialog.', {}, (), ('read',)),
    ('workspace.navigate', 'Open a workspace view; the existing controller saves dirty source before leaving Editor.', {'tab': enum('overview','editor','desktop','simulation','verification','workflows','evaluation','settings')}, ('tab',), ('write',)),
    ('workspace.select_project', 'Select a project or clear selection; save the current dirty file through the existing editor.', {'project_id': string(200)}, ('project_id',), ('write',)),
    ('workspace.refresh', 'Refresh the selected project, file tree, runs and jobs.', {}, (), ('read',)),
    ('workspace.set_context', 'Replace the selected project files attached to the assistant draft.', {'paths': PATHS}, ('paths',), ()),
    ('workspace.set_folder', 'Expand or collapse a named project folder.', {'path': string(), 'open': BOOL}, ('path','open'), ()),
    ('workspace.dialog', 'Open or close the new-project or new-file dialog without submitting it.', {'name': enum('new_project','new_file'), 'open': BOOL}, ('name','open'), ()),
    ('workspace.set_preferences', 'Set and persist UI language or appearance using the existing preference controller.', {'language': enum('en','zh-CN'), 'theme': enum('light','dark')}, (), ('configure',)),
    ('workspace.reconnect_desktop', 'Refresh desktop availability and reconnect the embedded drawing desktop; does not draw or enter credentials.', {}, (), ('read',)),
    ('workspace.read', 'Read the explicitly redacted current browser state.', {}, (), ('read',)),
    ('workspace.disclosures', 'List the stable IDs and open state of known disclosure panels in the current view.', {}, (), ('read',)),
    ('workspace.set_disclosure', 'Expand or collapse a known visible disclosure from workspace.disclosures.', {'id':string(200),'open':BOOL}, ('id','open'), ()),
    ('workspace.scroll', 'Scroll a named existing panel to its start or end without arbitrary coordinates.', {'panel':enum('workspace','desktop','explorer','editor','log','conversation','history','workflow'),'position':enum('start','end'),'axis':enum('vertical','horizontal')}, ('panel','position'), ()),
    ('form.read', 'Read a named visible form draft, excluding credential and file-picker values.', {'form': FORMS}, ('form',), ('read',)),
    ('form.set', 'Update named visible form fields without submitting. Field names come from form.read; secret inputs are not supported.', {'form': FORMS, 'values': VALUES}, ('form','values'), ()),
    ('settings.set_draft', 'Update the visible safe settings fields without saving. Credential inputs are excluded.', {'values': VALUES}, ('values',), ('configure',)),
    ('settings.project_environment_read', 'Read visible per-project EDA tool and PDK override fields.', {}, (), ('read',)),
    ('settings.project_environment_draft', 'Update visible project EDA environment fields; empty strings inherit workspace defaults.', {'values': VALUES}, ('values',), ('configure',)),
    ('settings.project_environment_save', 'Save project EDA overrides against the form revision and refresh tool availability.', {}, (), ('configure',)),
    ('settings.project_environment_reset', 'Remove project EDA tool and PDK overrides while preserving provider and native agent configuration.', {}, (), ('configure',)),
    ('settings.project_provider_read', 'Read the visible project provider override fields; no credential values are present.', {}, (), ('read',)),
    ('settings.project_provider_draft', 'Update the visible project provider fields without saving; declare capabilities before setting generation values.', {'values': VALUES}, ('values',), ('configure',)),
    ('settings.project_provider_save', 'Save the project provider override against the revision captured when its form opened.', {}, (), ('configure',)),
    ('settings.project_provider_reset', 'Remove the project provider override and inherit workspace defaults, preserving other project configuration.', {}, (), ('configure',)),
    ('editor.open', 'Open a project file in the editor, saving any previous dirty file.', {'path': string()}, ('path',), ('read','write')),
    ('editor.set_content', 'Replace the selected editable file draft without saving.', {'content': string(2 * 1024 * 1024)}, ('content',), ('write',)),
    ('editor.read', 'Read the current source draft, selected path and unsaved state.', {}, (), ('read',)),
    ('editor.save', 'Save the current dirty file and record a recoverable project revision.', {}, (), ('write',)),
    ('editor.history', 'Load and display revisions for the current file.', {}, (), ('read',)),
    ('editor_recovery.list', 'List this browser\'s retained drafts for the selected project without source text.', {}, (), ('read',)),
    ('editor_recovery.read', 'Explicitly read a retained base, draft and original version; supply draft_id when the selected-project path has multiple variants.', {'path':string(),'draft_id':string(200)}, ('path',), ('read',)),
    ('editor_recovery.restore', 'Restore one variant into its opened file with the original base/version; retain foreign backups until explicit discard. Does not save.', {'path':string(),'draft_id':string(200)}, ('path',), ('read','write')),
    ('editor_recovery.discard', 'Explicitly discard one browser recovery variant; draft_id is required for ambiguous paths. Leaves editor text unchanged.', {'path':string(),'draft_id':string(200)}, ('path',), ('write',)),
    ('editor_recovery.inspect', 'Read and display opened base, latest saved source and current draft; rejects selection or draft changes during inspection.', {}, (), ('read',)),
    ('editor_recovery.resolve', 'Explicitly prepare merged text as an unsaved draft pinned to the inspected disk version; ordinary editor.save remains required.', {'inspection_id':string(200),'content':string(2*1024*1024)}, ('inspection_id','content'), ('write',)),
    ('runs.netlist_reference', 'Netlist the current OTA reference schematic with Xschem in batch mode.', {}, (), ('execute',)),
    ('runs.derive_reference', 'Publish a current local reference tool output as versioned schematic or extracted netlists, preserving its artifact hash.', {'run_id':string(32,pattern='^[a-f0-9]{32}$')}, (), ('write',)),
    ('runs.simulate_reference_post', 'Run the current OTA reference post-layout testbench against its published extraction.', {}, (), ('execute',)),
    ('runs.select', 'Select a recorded run and display its log, evidence and available waveforms.', {'run_id': string(200)}, ('run_id',), ('read',)),
    ('runs.compare', 'Choose baseline and comparison recorded runs for the comparison table.', {'baseline': string(200), 'comparison': string(200)}, ('baseline','comparison'), ('read',)),
    ('runs.inspect_metric', 'Display the selected run metric and its recorded provenance.', {'name': string(200)}, ('name',), ('read',)),
    ('runs.copy_log', 'Copy the selected execution log to the browser clipboard; browser restrictions may reject the action.', {}, (), ('read',)),
    ('waveform.select_plot', 'Select a plot from the loaded rawfile; resets display to absolute values and linear axes.', {'index':INTEGER}, ('index',), ()),
    ('waveform.set_display', 'Set complex component, phase unwrapping, trace ratio and horizontal axis scale. Undefined ratios and zero dB values are explicit gaps; nonpositive log scales are rejected.', {'component':enum('magnitude','db','phase','real','imaginary'),'x_scale':enum('linear','log'),'reference':{'anyOf':[{'type':'integer','minimum':1},{'type':'null'}]},'unwrap':BOOL}, (), ()),
    ('waveform.set_traces', 'Select waveform variable indices, where index 0 is the horizontal axis.', {'indices': array({'type':'integer','minimum':1})}, ('indices',), ()),
    ('waveform.set_view', 'Set normalized horizontal waveform bounds within [0,1].', {'start': {'type':'number','minimum':0,'maximum':1}, 'end': {'type':'number','minimum':0,'maximum':1}}, ('start','end'), ()),
    ('waveform.set_cursor', 'Inspect a sample by index, or clear the cursor with null.', {'index': {'anyOf':[INTEGER,{'type':'null'}]}}, ('index',), ()),
    ('waveform.zoom', 'Zoom the loaded waveform by a positive factor around a normalized position.', {'factor': {'type':'number','exclusiveMinimum':0}, 'position': {'type':'number','minimum':0,'maximum':1,'default':0.5}}, ('factor',), ()),
    ('waveform.pan', 'Pan the loaded waveform by a fraction of the current view width.', {'amount': NUMBER}, ('amount',), ()),
    ('waveform.reset', 'Reset waveform zoom and cursor.', {}, (), ()),
    ('waveform.export', 'Download the loaded waveform as CSV in the connected browser.', {}, (), ('read',)),
    ('workflow.read', 'Read the current workflow draft.', {}, (), ('read',)),
    ('workflow.new_graph', 'Create a new v2 graph draft or a backwards-compatible v1 tuning draft without saving or executing.', {'schema_version':{'type':'integer','enum':[1,2],'default':2}}, (), ()),
    ('workflow.set_graph', 'Replace the visible workflow draft with a v1 or v2 graph; this does not execute it.', {'workflow': OBJECT}, ('workflow',), ()),
    ('workflow.select_node', 'Select a workflow node and open its configuration controls.', {'node_id': string(200)}, ('node_id',), ()),
    ('workflow.add_node', 'Add an executable node to a v2 graph, or one of the six tuning node types to a v1 draft.', {'type': enum('input','specification','parameter','simulate','measure','condition','report','model','project','netlist','drc','lvs','extract','loop','human_review')}, ('type',), ()),
    ('workflow.update_node', 'Update node configuration, typed mappings, retry/timeout policy or position; coordinates clamp to the board.', {'node_id': string(200), 'config': OBJECT, 'inputs':OBJECT,'outputs':OBJECT,'retries':{'type':'integer','minimum':0,'maximum':5},'timeout_seconds':{'type':'number','minimum':.1,'maximum':3600},'on_error':enum('fail','continue'),'position': obj({'x':NUMBER,'y':NUMBER}, ('x','y'))}, ('node_id',), ()),
    ('workflow.remove_node', 'Remove a workflow draft node and its incident connections.', {'node_id': string(200)}, ('node_id',), ()),
    ('workflow.connect', 'Connect existing nodes, optionally selecting a boolean branch and success/failure outcome in v2.', {'source': string(200), 'target': string(200),'when':BOOL,'on':enum('success','failure','always')}, ('source','target'), ()),
    ('workflow.disconnect', 'Remove a connection; supply its when/on selector to distinguish parallel branches.', {'source': string(200), 'target': string(200),'when':BOOL,'on':enum('success','failure','always')}, ('source','target'), ()),
    ('workflow.save', 'Save the current workflow draft into the project.', {}, (), ('write',)),
    ('workflow.list_saved', 'List saved workflows in the visible workflow editor.', {}, (), ('read',)),
    ('workflow.export', 'Validate and download the current workflow draft as JSON or optional safe YAML in the browser.', {'format':enum('json','yaml')}, (), ('read',)),
    ('workflow.apply_document', 'Validate JSON or optional safe YAML and apply it to the draft without saving or running; invalid documents preserve the draft.', {'content':string(2 * 1024 * 1024),'format':enum('json','yaml')}, ('content','format'), ()),
    ('workflow.apply_json', 'Parse and apply workflow JSON to the draft without saving or running.', {'content': string(2 * 1024 * 1024)}, ('content',), ()),
    ('agent.read', 'Read agent UI selection, configuration, conversation summaries and attachment names.', {}, (), ('read',)),
    ('agent.new', 'Start a fresh local conversation draft. The server session is created only on sending.', {}, (), ()),
    ('agent.select_session', 'Select an existing project conversation and restore its browser draft.', {'session_id': string(200)}, ('session_id',), ('read',)),
    ('agent.set_config', 'Set backend, model, reasoning effort, permission mode or speed for a new conversation. Automatic scopes require local human approval authority.', {'backend':enum('api','codex','claude'),'model':string(200),'mode':enum('read-only','confirm','automatic','workspace-write'),'fast_mode':BOOL,'effort':EFFORT,'scope':AGENT_SCOPE}, (), ('configure',)),
    ('agent.set_draft', 'Replace the assistant composer draft without sending.', {'message':string(32000)}, ('message',), ()),
    ('agent.read_draft', 'Read the composer text, selected project paths and attachment names.', {}, (), ('read',)),
    ('agent.set_context', 'Replace the selected existing project files in the assistant draft.', {'paths':PATHS}, ('paths',), ()),
    ('agent.attach_run', 'Attach a recorded project run to the composer, or detach with null.', {'run_id':NULLABLE_ID}, ('run_id',), ('read',)),
    ('agent.add_attachments', 'Attach named UTF-8 text or explicitly supported bounded images. Does not edit project files.', {'files':array(INPUT_ATTACHMENT,20)}, ('files',), ()),
    ('agent.remove_attachment', 'Remove an attachment from the assistant draft by its browser ID.', {'attachment_id':INTEGER}, ('attachment_id',), ()),
    ('agent.send', 'Send the current composer using the chosen backend and permissions. May cause an external model request and project actions.', {}, (), ('execute','write')),
    ('agent.stop', 'Request cancellation of the selected running agent session.', {}, (), ('execute',)),
    ('agent.history', 'Open or close conversation history and optionally search titles/messages.', {'open':BOOL,'query':string(200)}, ('open',), ('read',)),
    ('agent.search_models', 'Filter the model picker by text.', {'query':string(200)}, ('query',), ()),
    ('agent.refresh_models', 'Refresh models reported by the selected installed CLI.', {}, (), ('read',)),
    ('agent.favorite_model', 'Set the favorite state of a model in this browser.', {'model':string(200,minLength=1),'favorite':BOOL}, ('model','favorite'), ()),
    ('agent.popover', 'Open or close an assistant context, model, command or export menu.', {'name':enum('export','context','models','commands'),'open':BOOL}, ('name','open'), ()),
    ('agent.prompt', 'Insert an explain/review/simulation prompt without sending or executing tools.', {'name':enum('explain','review','simulate')}, ('name',), ()),
    ('agent.export', 'Download the selected conversation as Markdown, JSON or text in the browser.', {'format':enum('md','json','txt')}, ('format',), ('read',)),
    ('agent.edit_turn', 'Restore a previous user message and its context into the draft without sending.', {'turn_id':string(200)}, ('turn_id',), ('read',)),
    ('agent.copy_turn', 'Copy a selected user or assistant message into the browser clipboard.', {'turn_id':string(200),'role':enum('user','assistant')}, ('turn_id','role'), ('read',)),
    ('agent.scroll_latest', 'Scroll the conversation to its latest message.', {}, (), ()),
    ('agent.disclosure', 'Expand or collapse a specific tool/reasoning disclosure by its stable conversation disclosure ID.', {'id':string(500),'open':BOOL}, ('id','open'), ()),
    ('agent.quick_messages', 'List reusable prompt templates stored in this browser.', {}, (), ('read',)),
    ('agent.manage_quick', 'Open or close the browser dialog for managing reusable prompts.', {'open':BOOL}, ('open',), ()),
    ('agent.save_quick', 'Add a reusable title and prompt to this browser, retaining at most 30.', {'title':string(80,minLength=1),'message':string(32000,minLength=1)}, ('title','message'), ()),
    ('agent.remove_quick', 'Remove a reusable browser prompt by its listed index.', {'index':INTEGER}, ('index',), ()),
    ('layout.set_panel', 'Set explorer, assistant or execution log visibility with the existing responsive controller.', {'panel':enum('tree','ai','log'),'visible':BOOL}, ('panel','visible'), ()),
    ('layout.resize', 'Set a panel or composer size in CSS pixels, clamped to current viewport limits.', {'panel':enum('tree','ai','log','composer'),'size':{'type':'number','minimum':0,'unit':'CSS pixels'}}, ('panel','size'), ()),
    ('layout.expand_agent', 'Expand the assistant workspace or return it to the sidebar.', {'expanded':BOOL}, ('expanded',), ()),
    ('layout.reset', 'Reset browser panel dimensions and visibility to defaults.', {}, (), ()),
    ('layout.close_overlay', 'Close the current compact explorer or assistant overlay.', {}, (), ()),
]


def register_operations(registry):
    registry.register(Operation(
        operation_id='ui.sessions', name='Connected browser sessions',
        description='List live browser clients, their redacted state and acknowledged state version.',
        module='frontend', input_schema=obj(), output_schema=obj({'sessions':items(SESSION_STATE)}, ('sessions',)),
        handler=lambda context, params: {'sessions':[session for session in registry.sessions.list()
            if registry._frontend_scope_allowed(context, session['state'])]},
        kind='query', permissions=('read',), idempotent=True,
    ))
    for name, description, fields, required, permissions in COMMANDS:
        operation_id = 'ui.' + name
        params = {'client_id':string(200,minLength=1),
                  'ui_version': {'type':'integer','minimum':0,'description':'Optional version returned by ui.sessions; rejects stale browser state.'}, **fields}

        def handler(context, values, operation_id=operation_id):
            if operation_id == 'ui.lifecycle.submit' and 'configure' not in context.permissions:
                with registry.sessions.lock:
                    lifecycle = registry.sessions._get(values['client_id'])['state'].get('lifecycle', {})
                    if lifecycle.get('action') == 'import':
                        raise PermissionError('Frontend archive import requires configure permission because it can restore project tool overrides')
            human = context.principal == 'local-owner' and 'approve' in context.permissions and context.source in ('ui', 'legacy', 'cli')
            if operation_id in ('ui.agent.set_config', 'ui.agent.send') and not human:
                with registry.sessions.lock:
                    state = registry.sessions._get(values['client_id'])['state'].get('agent', {})
                    automatic = state.get('config', {}).get('mode') == 'automatic'
                    creates_grant = (operation_id == 'ui.agent.set_config' and
                        (values.get('mode') == 'automatic' or 'scope' in values or automatic and values.get('mode') not in ('read-only', 'confirm', 'workspace-write'))
                        or operation_id == 'ui.agent.send' and automatic and not state.get('session_id'))
                    if creates_grant:
                        raise PermissionError('Automatic scope grants require a local human; remote frontend commands cannot grant or renew them')
            parameters = {key:value for key,value in values.items() if key not in ('client_id','ui_version')}
            return registry.sessions.command(values['client_id'], operation_id, parameters,
                                             expected_version=values.get('ui_version'))

        changes = bool(set(permissions) & {'write','execute','configure'})
        result_schema = obj({'client_id': TEXT_RESULT, 'data': DATA_SCHEMAS.get(name, {
            'description': 'Controller result; commands with no specific payload return an object containing state.'}),
            'state': BROWSER_STATE, 'version': INTEGER}, ('client_id','data','state','version'))
        registry.register(Operation(
            operation_id=operation_id, name=name.replace('_',' ').replace('.',' / '),
            description=description, module='settings' if 'configure' in permissions else 'frontend',
            input_schema=obj(params, ('client_id',*required)), output_schema=result_schema,
            handler=handler, kind='frontend', permissions=('ui',*permissions),
            confirmation='agent' if changes else 'none',
            versioned=name in ('native_canvas.start', 'native_canvas.save', 'characterization.start', 'runs.derive_reference', 'lifecycle.submit', 'simulation_batch.start', 'simulation_batch.qualify', 'native_terminal.start', 'settings.project_provider_save', 'settings.project_provider_reset', 'settings.project_environment_save', 'settings.project_environment_reset'),
            preconditions=('A live connected browser from ui.sessions is required.',),
            side_effects=('Browser state changes only after the target browser executes the semantic handler.',),
            tags=('frontend',name.split('.')[0]),
            usage='Find client_id and version with ui.sessions. Invoke this operation with client_id and its declared parameters. Supply ui_version to reject stale browser state. The result contains the actual acknowledged browser state; timeouts have unknown outcome.',
        ))
