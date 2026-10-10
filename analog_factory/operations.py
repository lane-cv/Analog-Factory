"""Unified operation definitions and execution policy for every application adapter."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from .operation_schema import check_schema, validate
from .operation_sessions import FrontendSessions
from .operation_journal import OperationJournal, secret_values, unknown_response

PERMISSIONS = frozenset({'read', 'write', 'execute', 'configure', 'ui', 'approve'})
ERRORS = ('INVALID_ARGUMENT', 'NOT_FOUND', 'PERMISSION_DENIED', 'CONFIRMATION_REQUIRED',
          'VERSION_REQUIRED', 'VERSION_CONFLICT', 'IDEMPOTENCY_CONFLICT', 'UNAVAILABLE',
          'FRONTEND_DISCONNECTED', 'TIMEOUT', 'EXECUTION_FAILED', 'OUTCOME_UNKNOWN', 'INVALID_RESULT', 'INTERNAL_ERROR')
TERMINAL = frozenset({'succeeded', 'completed', 'failed', 'cancelled', 'timed_out', 'unavailable', 'interrupted'})
TRASH_OPERATIONS = frozenset({'project.detail', 'project.restore', 'project.backup',
                              'project.trash.list', 'file.trash.list'})
DISCOVERY_PAGE_BYTES = 48 * 1024
DISCOVERY_PAGE_ITEMS = 64


@dataclass(frozen=True)
class InvocationContext:
    principal: str = 'local-owner'
    permissions: frozenset = PERMISSIONS
    source: str = 'ui'
    project_id: str | None = None
    confirmed: bool = False
    client_id: str | None = None


@dataclass(frozen=True)
class Operation:
    operation_id: str
    name: str
    description: str
    module: str
    input_schema: dict
    output_schema: dict
    handler: Callable
    kind: str = 'business'
    permissions: tuple = ('read',)
    confirmation: str = 'none'
    asynchronous: bool = False
    cancellable: bool = False
    retryable: bool = False
    idempotent: bool = False
    undo: bool = False
    preconditions: tuple = ()
    side_effects: tuple = ()
    tags: tuple = ()
    examples: tuple = ()
    task_kind: str | None = None
    versioned: bool = False
    version: str = '1.0.0'
    usage: str = ''
    availability: Callable | None = None
    volatile_response: bool = False
    source_revision: bool = True
    frontend_adapter: str = 'browser'
    frontend_target: Callable | None = None

    def describe(self):
        result = {key: copy.deepcopy(value) for key, value in vars(self).items()
                  if key not in ('handler', 'availability', 'frontend_target')}
        if self.kind != 'frontend':
            result.pop('frontend_adapter')
        result['purpose'] = self.description
        result['usage'] = self.usage or self.description
        result['execution'] = 'asynchronous' if self.asynchronous else 'synchronous'
        result['errors'] = [{'code': code, 'description': code.replace('_', ' ').lower()} for code in ERRORS]
        result['idempotency'] = ('Natural; ' if self.idempotent else '') + (
            'Optional durable request key: completed responses retained for 30 days / 1,000 entries; '
            'interrupted requests never reexecute. Sensitive or >1 MiB responses cannot be replayed.')
        if self.volatile_response:
            result['idempotency'] = 'Request keys are rejected; private volatile output is never journaled'
        result['revision_policy'] = ('Project/workspace source revision in response' if self.source_revision else
            'Response version is null; bounded read consistency uses its artifact token or immutable transfer handle. Explicit expected_version still checks project state.')
        return result


class OperationError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class OperationRegistry:
    def __init__(self, workspace):
        self.workspace = workspace
        self.operations = {}
        # Reuse the source-store lock so legacy approval revalidation and registry
        # writes have the same lock order and form one in-process transaction.
        self.lock = workspace.projects.lock
        self.journal = OperationJournal(workspace.root)
        durable = self.journal.snapshot()
        self.event_condition = threading.Condition(threading.RLock())
        self.events = deque(durable['events'], maxlen=self.journal.event_limit)
        self.cursor = durable['cursor']
        self.journal_error = None
        self.request_condition = threading.Condition(threading.RLock())
        self.pending_requests = {}
        self.tasks = durable['tasks']
        self.sessions = FrontendSessions()
        self.closed = threading.Event()
        self.capabilities = {}
        self.capability_lock = threading.RLock()
        self.watched_versions = {}
        self.monitor_started = False

    def register(self, operation):
        if not isinstance(operation, Operation) or not re.fullmatch(r'[a-z][a-z0-9]*(?:[._][a-z][a-z0-9]*)+', operation.operation_id):
            raise ValueError('operation_id must be a stable dotted lowercase identifier')
        if operation.operation_id in self.operations:
            raise ValueError('Duplicate operation_id: ' + operation.operation_id)
        if not all(isinstance(v, str) and v.strip() for v in (operation.name, operation.description, operation.module, operation.version)):
            raise ValueError('Operation metadata must include name, description, module, and version')
        if not callable(operation.handler) or not operation.permissions or set(operation.permissions) - PERMISSIONS:
            raise ValueError('Operation requires a callable handler and known permission declarations')
        if operation.kind not in ('business', 'query', 'frontend') or operation.confirmation not in ('none', 'agent'):
            raise ValueError('Invalid operation kind or confirmation policy')
        if not isinstance(operation.frontend_adapter, str) or not re.fullmatch(r'[a-z][a-z0-9_-]*', operation.frontend_adapter):
            raise ValueError('Invalid frontend adapter name')
        if operation.frontend_adapter != 'browser':
            if operation.kind != 'frontend':
                raise ValueError('A custom frontend adapter requires a frontend operation')
            if not callable(operation.frontend_target):
                raise ValueError('A custom frontend adapter requires a live target resolver')
        elif operation.frontend_target is not None:
            raise ValueError('The browser frontend target resolver cannot be replaced')
        if operation.task_kind not in (None, 'run', 'job', 'agent', 'terminal'):
            raise ValueError('Unknown task adapter')
        if not operation.source_revision and (operation.kind != 'query' or operation.versioned or set(operation.permissions) - {'read'}):
            raise ValueError('Only read-only unversioned queries may omit source revisions')
        check_schema(operation.input_schema)
        check_schema(operation.output_schema)
        if operation.input_schema.get('type') != 'object':
            raise ValueError('Operation input must be an object schema')
        self.operations[operation.operation_id] = operation
        return operation

    def _scope(self, context, parameters):
        pid = parameters.get('project_id', context.project_id)
        if context.project_id and pid != context.project_id:
            raise PermissionError('Operation belongs to a different project')
        return pid

    def _allowed(self, operation, context):
        if set(operation.permissions) - context.permissions:
            raise PermissionError('Required permissions: ' + ', '.join(operation.permissions))
        if 'approve' in operation.permissions and context.source in ('agent', 'external-agent'):
            raise PermissionError('An agent cannot decide its own human approval')
        if context.project_id and operation.module in ('settings', 'workspace', 'provider') and operation.kind != 'query':
            raise PermissionError('Project-scoped agents cannot change workspace configuration')
        if context.project_id and operation.operation_id in ('project.create', 'project.import'):
            raise PermissionError('Project-scoped agents cannot create or import a different project')

    def _project_state_allowed(self, operation_id, pid):
        if pid and operation_id not in TRASH_OPERATIONS and self.workspace.projects.get(pid).get('trashed_at'):
            raise ValueError('Project is in trash; restore it before using project operations')

    @staticmethod
    def _frontend_scope_allowed(context, state, operation_id='', parameters=None):
        if not context.project_id:
            return True
        if state.get('project_id') != context.project_id:
            return False
        lifecycle = state.get('lifecycle') or {}
        if lifecycle.get('open') and (lifecycle.get('action') == 'import'
                or lifecycle.get('project_id') not in (None, context.project_id)):
            return False
        if operation_id == 'ui.lifecycle.open' and (parameters or {}).get('action') == 'import':
            return False
        return True

    def _frontend_target(self, operation, context, parameters, *, bind=False):
        """Resolve live target ownership without assuming every frontend is a browser.

        Native adapters must revalidate their target and revision at execution,
        and return an actual acknowledgment. This resolver alone is not success.
        """
        if operation.frontend_adapter != 'browser':
            target = operation.frontend_target(context, parameters)
            if not isinstance(target, dict) or not isinstance(target.get('state'), dict):
                raise OperationError('INVALID_RESULT', 'Frontend adapter returned an invalid target state')
            if not self._frontend_scope_allowed(context, target['state'], operation.operation_id, parameters):
                raise PermissionError('Frontend session belongs to a different project')
            return target
        with self.sessions.lock:
            target = self.sessions._get(parameters.get('client_id'))
            if not self._frontend_scope_allowed(context, target['state'], operation.operation_id, parameters):
                raise PermissionError('Frontend selection or lifecycle dialog belongs to a different project')
            if bind and context.project_id:
                parameters.setdefault('ui_version', target['version'])
            return target

    def availability(self, operation, context, parameters):
        reasons = []
        try:
            self._allowed(operation, context)
            pid = self._scope(context, parameters)
            if 'project_id' in operation.input_schema.get('required', []):
                if not pid:
                    reasons.append('Select an existing project and provide project_id')
                else:
                    self.workspace.projects.path(pid)
            if pid:
                self._project_state_allowed(operation.operation_id, pid)
            if operation.kind == 'frontend':
                if operation.frontend_adapter == 'browser' and not parameters.get('client_id'):
                    reasons.append('Provide a connected frontend client_id from ui.sessions')
                else:
                    self._frontend_target(operation, context, parameters)
            if operation.availability and not reasons:
                callback_reasons = operation.availability(context, parameters)
                if callback_reasons is not None:
                    if (not isinstance(callback_reasons, (list, tuple))
                            or not all(isinstance(reason, str) for reason in callback_reasons)):
                        raise OperationError('INVALID_RESULT',
                            'Availability callback must return a list of reason strings or None')
                    reasons.extend(callback_reasons)
        except KeyError as error:
            # Discovery may omit target fields, while invocation validates the
            # complete schema before reaching a resolver. Internal lookup errors
            # remain explicit failures rather than being called missing input.
            field = error.args[0] if error.args else None
            if (isinstance(field, str) and field in operation.input_schema.get('properties', {})
                    and field not in parameters):
                reasons.append('Provide frontend target parameter: ' + field)
            else:
                reasons.append('Availability check failed: missing field ' + str(field))
        except TypeError as error:
            reasons.append('Availability check failed: ' + str(error))
        except (ValueError, OSError, RuntimeError, OperationError) as error:
            reasons.append(str(error))
        return {'available': not reasons, 'reasons': reasons, 'prerequisites': list(operation.preconditions)}

    def describe_operation(self, operation_id, context=None, parameters=None):
        operation = self.operations.get(operation_id)
        if not operation:
            raise FileNotFoundError('Operation does not exist')
        context = context or InvocationContext()
        params = dict(parameters or {})
        if context.project_id:
            params.setdefault('project_id', context.project_id)
        return {**operation.describe(), 'availability': self.availability(operation, context, params)}

    def list_operations(self, context=None, module='', keyword='', parameters=None, available_only=False):
        context = context or InvocationContext()
        result = []
        for operation_id, operation in sorted(self.operations.items()):
            if module and module != operation.module:
                continue
            if keyword and keyword.casefold() not in ' '.join((operation_id, operation.name, operation.description, *operation.tags)).casefold():
                continue
            entry = self.describe_operation(operation_id, context, parameters)
            if not available_only or entry['availability']['available']:
                result.append(entry)
        return result

    def list_operations_page(self, context=None, module='', keyword='', parameters=None,
                             cursor=None, limit=DISCOVERY_PAGE_ITEMS):
        """Byte-bounded summaries for model/MCP discovery; full schemas stay separate.

        A cursor binds the ordered catalog and caller/filter, not availability:
        prerequisites and permissions are checked again on each returned page.
        Changing the item limit is safe; catalog or caller changes require restart.
        """
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Discovery limit must be an integer from 1 to 100')
        if any(not isinstance(value,str) or len(value.encode())>1024 for value in (module,keyword)):
            raise ValueError('Discovery filters must contain at most 1024 UTF-8 bytes')
        if cursor is not None and (not isinstance(cursor,str) or
                not re.fullmatch(r'[0-9a-f]{64}\.[0-9]{1,9}',cursor)):
            raise ValueError('Invalid discovery cursor; restart without a cursor')
        context=context or InvocationContext()
        parameters=dict(parameters or {})
        if context.project_id:
            parameters.setdefault('project_id',context.project_id)
        catalog=sorted(self.operations.items())
        # No output schemas, source reads or changing prerequisite values need to
        # be materialized for the whole catalog just to return one short page.
        metadata=[(identifier,operation.version,operation.name,operation.description,
                   operation.module,operation.kind,operation.permissions,operation.tags)
                  for identifier,operation in catalog]
        identity={'catalog':metadata,'module':module,'keyword':keyword,'parameters':parameters,
                  'context':{**vars(context),'permissions':sorted(context.permissions)}}
        encode=lambda value:json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
        fingerprint=hashlib.sha256(encode(identity)).hexdigest()
        offset=0
        if cursor is not None:
            previous,position=cursor.split('.')
            if previous!=fingerprint:
                raise ValueError('Discovery cursor does not match this catalog, filter or caller; restart without a cursor')
            offset=int(position)
        entries=[(identifier,operation) for identifier,operation in catalog
                 if (not module or module==operation.module) and
                 (not keyword or keyword.casefold() in ' '.join((identifier,operation.name,
                     operation.description,*operation.tags)).casefold())]
        if offset and offset>=len(entries):
            raise ValueError('Discovery cursor is outside the catalog; restart without a cursor')

        def clip(value,maximum):
            data=value.encode()
            return value if len(data)<=maximum else data[:maximum].decode(errors='ignore')+'…'

        result={'operations':[],'total':len(entries),'offset':offset,'returned':0,
                'next_cursor':None,'complete':offset==len(entries),
                'details':'Follow next_cursor with the same filters until complete. Availability is refreshed per page; use describe_operation for full schemas and unabridged metadata.'}
        for identifier,operation in entries[offset:offset+limit]:
            availability=self.availability(operation,context,parameters)
            reasons=availability['reasons'];short_reasons=[clip(str(reason),512) for reason in reasons[:4]]
            name,description=clip(operation.name,160),clip(operation.description,240)
            summary={'operation_id':identifier,'name':name,'description':description,
                     'module':operation.module,'kind':operation.kind,'permissions':list(operation.permissions),
                     'availability':{'available':availability['available'],'reasons':short_reasons,'reason_count':len(reasons)},
                     'summary_truncated':name!=operation.name or description!=operation.description or short_reasons!=reasons}
            result['operations'].append(summary)
            position=offset+len(result['operations'])
            result.update(returned=len(result['operations']),complete=position==len(entries),
                          next_cursor=None if position==len(entries) else f'{fingerprint}.{position}')
            if len(encode(result))>DISCOVERY_PAGE_BYTES:
                result['operations'].pop()
                if not result['operations']:
                    raise ValueError('Operation summary exceeds discovery page byte limit; inspect the catalog metadata')
                position=offset+len(result['operations'])
                result.update(returned=len(result['operations']),complete=False,next_cursor=f'{fingerprint}.{position}')
                break
        return result

    def version(self, project_id=None):
        digest = hashlib.sha256()
        if project_id:
            root = self.workspace.projects.path(project_id)
            for entry in self.workspace.projects.files(project_id):
                path = root / entry['path']
                stat = path.stat()
                digest.update(entry['path'].encode())
                digest.update(f'{stat.st_ino}:{stat.st_mtime_ns}:{stat.st_size}'.encode())
                with path.open('rb') as stream:
                    while chunk := stream.read(256 * 1024):
                        digest.update(chunk)
            # Include editable session metadata, never streaming events/status:
            # an approval's own status transitions must not invalidate its source token.
            for path in sorted((root / '.af/agent-sessions').glob('*.json')):
                if not path.is_symlink():
                    digest.update(path.name.encode())
                    record = json.loads(path.read_text())
                    digest.update(json.dumps({key: record.get(key) for key in
                        ('id', 'title', 'backend', 'mode', 'model', 'fast_mode', 'effort')}, sort_keys=True).encode())
        else:
            digest.update(json.dumps(self.workspace.settings(), sort_keys=True, allow_nan=False).encode())
            credential = self.workspace.root / 'settings.json'
            if credential.is_file():
                stat = credential.stat()
                digest.update(f'{stat.st_ino}:{stat.st_mtime_ns}'.encode())
            # Workspace-level import/list versions include membership, names and
            # tombstones. Source edits remain scoped to the owning project token.
            for project in self.workspace.projects.list(include_trashed=True):
                digest.update(json.dumps(project, sort_keys=True, allow_nan=False).encode())
        return digest.hexdigest()

    def issue_context_token(self, context, callback=None):
        """Mint a turn-scoped credential; never persist or expose it in events."""
        token = secrets.token_urlsafe(32)
        with self.capability_lock:
            self.capabilities[token] = {'context': context, 'callback': callback}
        return token

    def resolve_context_token(self, token):
        with self.capability_lock:
            record = self.capabilities.get(token)
            return record['context'] if record else None

    def invoke_agent_tool(self, token, name, arguments):
        with self.capability_lock:
            record = self.capabilities.get(token)
        if not record or not record['callback']:
            raise PermissionError('Agent turn credential is invalid or expired')
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise ValueError('Agent tool name and argument object are required')
        return record['callback'](name, arguments)

    def revoke_context_token(self, token):
        with self.capability_lock:
            self.capabilities.pop(token, None)

    @staticmethod
    def _progress(record):
        if not isinstance(record, dict):
            return None
        if record.get('kind') == 'tuning':
            return {'completed_iterations': len(record.get('iterations', [])),
                    'active_iteration': record.get('active_iteration'),
                    'iteration_limit': record.get('workflow', {}).get('limits', {}).get('iterations'),
                    'active_node': record.get('active_node')}
        if record.get('kind') == 'evaluation':
            return {'completed_episodes': record.get('completed_episodes', len(record.get('iterations', [])))}
        return None

    def _event(self, operation_id, project_id, trace_id, status, version=None, task_id=None, client_id=None, progress=None):
        with self.event_condition:
            try:
                item = self.journal.append_event(dict(operation_id=operation_id, project_id=project_id,
                    trace_id=trace_id, status=status, version=version, task_id=task_id, client_id=client_id, progress=progress))
            except OSError:
                self.journal_error = 'Operation events could not be durably stored; read the authoritative task record'
                raise
            self.journal_error = None
            self.cursor = item['sequence']
            self.events.append(item)
            self.event_condition.notify_all()

    def subscribe_events(self, after=0, wait=0, context=None):
        if type(after) is not int or after < 0 or not isinstance(wait, (float, int)) or not 0 <= wait <= 25:
            raise ValueError('Event cursor must be nonnegative; wait must be 0–25 seconds')
        context = context or InvocationContext()
        if 'read' not in context.permissions:
            raise PermissionError('Reading events requires read permission')
        with self.event_condition:
            if self.cursor <= after and wait:
                self.event_condition.wait(wait)
            return {'events': [copy.deepcopy(e) for e in self.events if e['sequence'] > after and
                               (not context.project_id or e['project_id'] == context.project_id)],
                    'cursor': self.cursor, 'reset': bool(self.events and after < self.events[0]['sequence'] - 1) or after > self.cursor,
                    **({'storage_error': self.journal_error} if self.journal_error else {})}

    @staticmethod
    def _error_code(error):
        if isinstance(error, OperationError):
            return error.code
        if isinstance(error, PermissionError):
            return 'PERMISSION_DENIED'
        if isinstance(error, FileNotFoundError):
            return 'FRONTEND_DISCONNECTED' if 'frontend' in str(error).lower() else 'NOT_FOUND'
        if isinstance(error, TimeoutError):
            return 'TIMEOUT'
        if isinstance(error, (ValueError, TypeError, KeyError, UnicodeError)):
            return 'INVALID_ARGUMENT'
        if isinstance(error, RuntimeError):
            return 'VERSION_CONFLICT' if 'version conflict' in str(error).lower() else 'EXECUTION_FAILED'
        if isinstance(error, OSError):
            return 'EXECUTION_FAILED'
        return 'INTERNAL_ERROR'

    def invoke(self, operation_id, parameters=None, context=None, *, expected_version=None, idempotency_key=None):
        context = context or InvocationContext()
        trace_id = uuid.uuid4().hex
        envelope = dict(ok=False, operation_id=operation_id, trace_id=trace_id, data=None, error=None,
                        task_id=None, status='failed', progress=None, artifacts=[], version=None)
        key, claimed, handler_started = None, False, False
        secrets_in_request = ()
        try:
            operation = self.operations.get(operation_id)
            if not operation:
                raise FileNotFoundError('Operation does not exist')
            self._allowed(operation, context)
            params = dict(parameters) if isinstance(parameters, dict) else parameters
            if params is None:
                params = {}
            if context.project_id and 'project_id' in operation.input_schema.get('properties', {}):
                params.setdefault('project_id', context.project_id)
            params = validate(params, operation.input_schema)
            fingerprint_params = copy.deepcopy(params)
            pid = self._scope(context, params)
            if operation.kind == 'frontend' and (operation.frontend_adapter != 'browser'
                    or context.project_id or 'client_id' in operation.input_schema.get('properties', {})):
                # Browser ownership/version binding remains atomic under its
                # session lock; other adapters own their live target resolution.
                self._frontend_target(operation, context, params, bind=True)
            if operation.confirmation == 'agent' and context.source in ('agent', 'external-agent') and not context.confirmed:
                raise OperationError('CONFIRMATION_REQUIRED', 'This mutation requires an explicit approval')
            if idempotency_key is not None and (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200):
                raise ValueError('idempotency_key must contain 1–200 characters')
            if idempotency_key is not None and operation.volatile_response:
                raise ValueError('This operation returns volatile private output and does not accept idempotency keys')
            # A key belongs to one principal; changing its permission/project
            # binding conflicts rather than returning another scope's receipt.
            fingerprint = hashlib.sha256(json.dumps([operation_id, fingerprint_params,
                context.project_id, pid, sorted(context.permissions), context.source],
                sort_keys=True, allow_nan=False).encode()).hexdigest()
            key = hashlib.sha256(json.dumps([context.principal, idempotency_key]).encode()).hexdigest() if idempotency_key else None
            secrets_in_request = secret_values(params)
            if key:
                with self.request_condition:
                    pending = self.pending_requests.get(key)
                    if pending is not None:
                        if pending['fingerprint'] != fingerprint:
                            raise OperationError('IDEMPOTENCY_CONFLICT', 'This request key is executing different parameters')
                        while not pending['done']:
                            self.request_condition.wait()
                        if pid:
                            self.workspace.projects.path(pid)
                            self._project_state_allowed(operation_id, pid)
                        # Waiters already joined this invocation. They share its
                        # live result even when privacy/retention prevents replay.
                        return copy.deepcopy(pending['response'])
                    try:
                        reservation = self.journal.reserve(key, fingerprint, envelope)
                    except OSError:
                        raise OperationError('UNAVAILABLE', 'Operation journal unavailable; no operation was started') from None
                    if not reservation['acquired']:
                        if reservation['fingerprint'] != fingerprint:
                            raise OperationError('IDEMPOTENCY_CONFLICT', 'This request key was used for different parameters')
                        # Source and lifecycle checks still apply to historical
                        # receipts. expected_version is deliberately not checked:
                        # a successful original mutation changed that version.
                        if pid:
                            self.workspace.projects.path(pid)
                            self._project_state_allowed(operation_id, pid)
                        return copy.deepcopy(reservation['response'])
                    self.pending_requests[key] = {'fingerprint': fingerprint, 'done': False}
                    claimed = True
            # Never hold the global mutation lock while waiting for a frontend:
            # its command may invoke business operations before acknowledging.
            guard = self.lock if operation.kind != 'frontend' else threading.RLock()
            with guard:
                if operation.versioned or expected_version is not None:
                    current = self.version(pid)
                    if expected_version is None and context.source != 'legacy':
                        raise OperationError('VERSION_REQUIRED', 'Read current state and supply its version as expected_version')
                    if expected_version is not None and expected_version != current:
                        raise OperationError('VERSION_CONFLICT', 'State changed; read the latest state before applying this change')
                # Services remain authoritative about runtime capability checks.
                if pid:
                    self.workspace.projects.path(pid)
                    self._project_state_allowed(operation_id, pid)
                handler_started = True
                data = operation.handler(context, params)
                if operation.kind == 'frontend' and context.project_id:
                    if not isinstance(data, dict) or not self._frontend_scope_allowed(context, data.get('state', {})):
                        raise PermissionError('Frontend project changed during command execution; read the current session again')
                try:
                    data = validate(data, operation.output_schema, 'result')
                    json.dumps(data, allow_nan=False)
                except (ValueError, TypeError):
                    raise OperationError('INVALID_RESULT', 'Operation handler returned a result that violates its output schema') from None
                status = data.get('status') if isinstance(data, dict) else None
                failed = status in ('unavailable', 'failed', 'timed_out', 'interrupted') and operation.kind != 'query'
                envelope.update(ok=not failed, data=data, status=status or 'succeeded',
                                version=self.version(pid) if operation.source_revision else None)
                if pid and operation.source_revision:
                    previous = self.watched_versions.get(pid)
                    if previous and previous != envelope['version'] and operation.kind == 'query':
                        self._event('project.detail', pid, uuid.uuid4().hex, 'changed', envelope['version'])
                    self.watched_versions[pid] = envelope['version']
                if isinstance(data, dict):
                    envelope['progress'] = self._progress(data)
                if failed:
                    envelope['error'] = {'code': 'UNAVAILABLE' if status == 'unavailable' else 'EXECUTION_FAILED',
                                         'message': data.get('error') or data.get('diagnostic') or 'Execution did not succeed'}
                if operation.task_kind and isinstance(data, dict) and data.get('id'):
                    task_id = f'{operation.task_kind}:{pid}:{data["id"]}'
                    if operation.task_kind == 'agent' and data.get('turns'):
                        task_id += ':' + data['turns'][-1]['id']
                    envelope['task_id'] = task_id
                    if operation.task_kind == 'run':
                        envelope['artifacts'] = [
                            {'operation_id': 'run.get', 'parameters': {'project_id': pid, 'run_id': data['id']}},
                            {'operation_id': 'run.log', 'parameters': {'project_id': pid, 'run_id': data['id']}}]
                    self.tasks[task_id] = {'operation_id': operation_id, 'trace_id': trace_id,
                                           'principal': context.principal, 'cancellable': operation.cancellable}
                    try:
                        self.journal.save_task(task_id, self.tasks[task_id])
                    except OSError:
                        raise OperationError('OUTCOME_UNKNOWN', 'Task started but its trace receipt could not be stored') from None
                    if status not in TERMINAL:
                        threading.Thread(target=self._watch_task, args=(task_id,), daemon=True).start()
                if operation.kind != 'query':
                    try:
                        self._event(operation_id, pid, trace_id, envelope['status'], envelope['version'], envelope['task_id'], context.client_id, envelope['progress'])
                    except OSError:
                        raise OperationError('OUTCOME_UNKNOWN', 'Operation returned but its event receipt could not be stored') from None
            return envelope
        except Exception as error:
            code = self._error_code(error)
            # Do not echo untrusted exception payloads on sensitive mutations.
            sensitive = operation_id in ('settings.update', 'project.configure')
            message = ('Configuration rejected; check declared fields and values' if sensitive else
                       str(error) if code != 'INTERNAL_ERROR' else 'Unexpected operation handler failure')
            if sensitive:
                message = {'VERSION_CONFLICT': 'State changed; reopen the configuration form or read the latest version before saving',
                           'VERSION_REQUIRED': 'Read current state and supply its version before saving configuration',
                           'PERMISSION_DENIED': 'This caller cannot change configuration',
                           'CONFIRMATION_REQUIRED': 'Configuration changes require the applicable human confirmation'}.get(code, message)
            if code == 'OUTCOME_UNKNOWN':
                envelope.update(unknown_response(envelope, message))
            else:
                envelope.update(ok=False, error={'code': code, 'message': message})
            return envelope
        except BaseException:
            if handler_started:
                envelope.update(unknown_response(envelope, 'Operation interrupted while its handler was running'))
            raise
        finally:
            if claimed:
                with self.request_condition:
                    try:
                        self.journal.finalize(key, fingerprint, envelope, secrets=secrets_in_request)
                    except OSError:
                        if handler_started:
                            envelope.update(unknown_response(envelope, 'The handler ran but its result could not be durably stored'))
                        else:
                            envelope.update(ok=False, error={'code': 'UNAVAILABLE',
                                'message': 'Operation journal unavailable; no operation was started'})
                    finally:
                        pending = self.pending_requests.pop(key, None)
                        if pending is not None:
                            pending.update(done=True, response=copy.deepcopy(envelope))
                        self.request_condition.notify_all()

    def _task_record(self, task_id, context=None):
        context = context or InvocationContext()
        if 'read' not in context.permissions:
            raise PermissionError('Reading tasks requires read permission')
        parts = str(task_id).split(':')
        if len(parts) not in (3, 4) or parts[0] not in ('run', 'job', 'agent', 'terminal') or (len(parts) == 4 and parts[0] != 'agent'):
            raise ValueError('task_id must be kind:project_id:record_id')
        kind, pid, rid = parts[:3]
        self._scope(context, {'project_id': pid})
        w = self.workspace
        if kind == 'run':
            return w.runner.get(w.run_root(pid, rid), rid)
        if kind == 'job':
            return w.jobs.get(w.projects.path(pid), rid)
        if kind == 'terminal':
            return w.terminals.get(pid, rid)
        session = w.agents.get(pid, rid)
        if len(parts) == 4:
            turn = next((item for item in session['turns'] if item['id'] == parts[3]), None)
            if not turn:
                raise FileNotFoundError('Agent turn does not exist in this session')
            session['status'] = turn['status']
            if session['turns'][-1]['id'] != turn['id']:
                session['pending_approval'] = None
            session['turns'] = [turn]
        return session

    def get_operation_status(self, task_id, context=None, *, view='full'):
        trace = self.tasks.get(task_id, {})
        try:
            if view not in ('full', 'summary'):
                raise ValueError('Status view must be full or summary')
            record = self._task_record(task_id, context)
            status = record.get('status', 'unknown')
            kind = task_id.split(':')[0]
            inferred = ('native.terminal.start' if kind == 'terminal' else 'agent.session.send' if kind == 'agent' else
                        'evaluation.run' if kind == 'job' and record.get('kind') == 'evaluation' else
                        'workflow.run' if kind == 'job' else 'run.retry' if record.get('parent_id') else 'run.start')
            progress = self._progress(record)
            artifacts = record.get('artifacts', []) or [
                            {'operation_id': 'run.artifact', 'parameters': {'project_id': task_id.split(':')[1],
                             'run_id': record['id'], 'path': path}} for path in record.get('artifact_hashes', {})]
            if view == 'summary':
                from .operation_summary import summarize_record
                record = summarize_record(record, task_id.split(':')[1], kind, progress)
                progress, artifacts = record['progress'], record['artifact_operations']
            return dict(ok=True, operation_id=trace.get('operation_id', inferred), trace_id=trace.get('trace_id') or uuid.uuid4().hex,
                        task_id=task_id, status=status, progress=progress, data=record, error=None, artifacts=artifacts)
        except Exception as error:
            return dict(ok=False, operation_id=trace.get('operation_id'), trace_id=trace.get('trace_id'),
                        task_id=task_id, status='failed', data=None, error={'code': self._error_code(error), 'message': str(error)})

    def cancel_operation(self, task_id, context=None):
        context = context or InvocationContext()
        try:
            with self.lock:
                record = self._task_record(task_id, context)
                if 'execute' not in context.permissions:
                    raise PermissionError('Cancelling tasks requires execute permission')
                if record.get('status') in TERMINAL:
                    return self.get_operation_status(task_id, context)
                kind, pid, rid = task_id.split(':')[:3]
                operation_id = {'run': 'run.cancel', 'job': 'job.cancel', 'agent': 'agent.session.cancel', 'terminal': 'native.terminal.stop'}[kind]
                key = {'run': 'run_id', 'job': 'job_id', 'agent': 'session_id', 'terminal': 'terminal_id'}[kind]
                return self.invoke(operation_id, {'project_id': pid, key: rid}, context=context)
        except Exception as error:
            return {'ok': False, 'task_id': task_id, 'error': {'code': self._error_code(error), 'message': str(error)}}

    def _watch_task(self, task_id):
        previous = None
        while not self.closed.wait(.25):
            result = self.get_operation_status(task_id)
            signature = json.dumps([result.get('status'), result.get('progress'), result.get('data', {}).get('updated_at') if result.get('data') else None], sort_keys=True)
            if signature != previous:
                trace = self.tasks.get(task_id, {})
                try:
                    with self.event_condition:
                        if self.closed.is_set():
                            return
                        self._event(trace.get('operation_id'), task_id.split(':')[1], trace.get('trace_id'), result['status'], task_id=task_id, progress=result.get('progress'))
                except OSError:
                    if not self.workspace.root.exists():
                        return
                    continue
                previous = signature
            if not result['ok'] or result['status'] in TERMINAL or result['status'] == 'idle':
                return

    def poll_external_changes(self):
        """Notice source writes by native EDA editors without overwriting UI drafts."""
        with self.lock:
            for pid, previous in list(self.watched_versions.items()):
                try:
                    current = self.version(pid)
                except (OSError, ValueError):
                    continue
                if current != previous:
                    self.watched_versions[pid] = current
                    self._event('project.detail', pid, uuid.uuid4().hex, 'changed', current)

    def start_monitor(self):
        with self.capability_lock:
            if self.monitor_started:
                return
            self.monitor_started = True
        def monitor():
            while not self.closed.wait(2):
                try:
                    self.poll_external_changes()
                except OSError:
                    if not self.workspace.root.exists():
                        return
        threading.Thread(target=monitor, name='af-source-events', daemon=True).start()

    def close(self):
        # Drain any journal write already in progress before closing. A task
        # observer may have passed its wait before shutdown began.
        with self.event_condition:
            self.closed.set()
        cli_updates = getattr(self.workspace, 'cli_updates', None)
        if cli_updates:
            cli_updates.close()
        native_canvas = getattr(self.workspace, 'native_canvas', None)
        if native_canvas:
            native_canvas.close()
        terminals = getattr(self.workspace, 'terminals', None)
        if terminals:
            terminals.close()
        transfers = getattr(self.workspace, 'transfers', None)
        if transfers:
            transfers.close()
