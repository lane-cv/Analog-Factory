"""One-use business permits derived from live official Codex Auto-review events.

The official CLI makes the approval decision. This ledger only binds its receipt
to one exact MCP invocation and the project/browser state present at review
start. Business permissions, file identity and final version checks still belong
to agent_api and the operation registry.
"""
import json
import threading
import time

from .agent_api import _arguments, _json, _redact
from .operation_mcp import NAMES


MAX_ITEMS = 128
MAX_ARGUMENT_BYTES = 1024 * 1024
_METHODS = frozenset({'item/started', 'item/completed',
                     'item/autoApprovalReview/started', 'item/autoApprovalReview/completed'})
_DECISIONS = {'inProgress': 'reviewing', 'approved': 'approve', 'denied': 'reject',
              'timedOut': 'manual', 'aborted': 'cancelled'}


def _identifier(value):
    return isinstance(value, str) and 0 < len(value) <= 512


def _key(name, arguments):
    if name not in NAMES or not isinstance(arguments, dict):
        raise ValueError('Unsupported official approval tool')
    arguments = _arguments(name, _json(_redact(arguments)))
    return name, json.dumps(arguments, sort_keys=True, allow_nan=False, separators=(',', ':'))


class OfficialApprovalLedger:
    def __init__(self, workspace, project_id, emit, cancel, *, receipt_timeout=5):
        self.workspace, self.project_id = workspace, project_id
        self.emit, self.cancel = emit, cancel
        self.receipt_timeout = receipt_timeout
        self.condition = threading.Condition(threading.RLock())
        self.thread_id = self.turn_id = None
        self.items, self.reviews = {}, {}
        self.argument_bytes = 0
        self.closed = False

    def bind(self, thread_id, turn_id):
        with self.condition:
            if self.closed or not _identifier(thread_id) or not _identifier(turn_id):
                raise ValueError('Official approval requires a live native thread and turn')
            if self.thread_id is not None:
                raise ValueError('Official approval cannot be rebound to another turn')
            self.thread_id, self.turn_id = thread_id, turn_id

    def _snapshot(self):
        registry = self.workspace.operations
        return {'version': registry.version(self.project_id),
                'browsers': {item['client_id']: item['version'] for item in registry.sessions.list()}}

    def accept(self, notification):
        """Consume live JSON-RPC notifications; historical/foreign events grant nothing."""
        if not isinstance(notification, dict) or notification.get('method') not in _METHODS:
            return
        params = notification.get('params')
        if not isinstance(params, dict):
            return
        event = None
        with self.condition:
            if (self.closed or self.cancel.is_set() or self.thread_id is None
                    or params.get('threadId') != self.thread_id or params.get('turnId') != self.turn_id):
                return
            method = notification['method']
            if method in ('item/started', 'item/completed'):
                item = params.get('item')
                if (not isinstance(item, dict) or item.get('type') != 'mcpToolCall'
                        or item.get('server') != 'analog_factory' or not _identifier(item.get('id'))):
                    return
                identifier = item['id']
                if method == 'item/completed':
                    if identifier not in self.items:
                        if len(self.items) >= MAX_ITEMS:
                            raise RuntimeError('Official approval item limit exceeded')
                        self.items[identifier] = {'key': None, 'finished': True}
                    self.items[identifier]['finished'] = True
                    for review in self.reviews.values():
                        if review['target'] == identifier:
                            review['used'] = True
                else:
                    key = _key(item.get('tool'), item.get('arguments'))
                    if identifier in self.items:
                        if self.items[identifier]['key'] != key:
                            self.items[identifier]['finished'] = True
                    else:
                        size = len(key[1].encode())
                        if len(self.items) >= MAX_ITEMS or self.argument_bytes + size > MAX_ARGUMENT_BYTES:
                            raise RuntimeError('Official approval item limit exceeded')
                        self.items[identifier] = {'key': key, 'finished': False}
                        self.argument_bytes += size
            else:
                review_id, target = params.get('reviewId'), params.get('targetItemId')
                action, review = params.get('action'), params.get('review')
                if not _identifier(review_id):
                    return
                valid = (isinstance(action, dict) and action.get('type') == 'mcpToolCall'
                         and action.get('server') == 'analog_factory' and action.get('toolName') in NAMES
                         and _identifier(target) and isinstance(review, dict))
                previous = self.reviews.get(review_id)
                if not valid:
                    if previous:
                        previous['used'] = True
                    return
                action_key = (action['server'], action['toolName'])
                if method.endswith('/started'):
                    # A duplicate start never refreshes the reviewed source version.
                    if previous is not None:
                        if previous['target'] != target or previous['action'] != action_key:
                            previous['used'] = True
                        return
                    if len(self.reviews) >= MAX_ITEMS:
                        raise RuntimeError('Official approval review limit exceeded')
                    item = self.items.get(target)
                    if (not item or item['finished'] or item['key'][0] != action['toolName']
                            or review.get('status') != 'inProgress'):
                        # Native stdout is ordered: item-start must precede its
                        # review. An orphan cannot gain authority from a later item.
                        self.reviews[review_id] = {'target': target, 'action': action_key,
                                                  'status': 'invalid', 'used': True}
                        return
                    previous = {'target': target, 'action': action_key, 'status': 'inProgress', 'published': False,
                                'used': False, **self._snapshot()}
                    self.reviews[review_id] = previous
                else:
                    if previous is None:
                        # Remember the orphan so a late start cannot resurrect it.
                        if len(self.reviews) >= MAX_ITEMS:
                            raise RuntimeError('Official approval review limit exceeded')
                        self.reviews[review_id] = {'target': target, 'action': action_key,
                                                  'status': 'invalid', 'used': True}
                        return
                    if previous['status'] != 'inProgress' or previous['used']:
                        return
                    if (previous['target'] != target or previous['action'] != action_key
                            or params.get('decisionSource') != 'agent'
                            or review.get('status') not in ('approved', 'denied', 'timedOut', 'aborted')):
                        previous.update(status='invalid', used=True)
                        self.condition.notify_all()
                        return
                    previous.update(status=review['status'], published=False)
                reason = review.get('rationale')
                if reason is not None and not isinstance(reason, str):
                    previous.update(status='invalid', used=True)
                    return
                if reason and len(reason.encode()) > 16000:
                    raise RuntimeError('Official approval rationale limit exceeded')
                event = {'type': 'approval_review', 'approval_id': review_id, 'tool_call_id': target,
                         'source': 'codex_official', 'decision': _DECISIONS.get(previous['status'], 'manual'),
                         'reason': reason or '', 'model': ''}
                for source, output in (('riskLevel', 'risk_level'), ('userAuthorization', 'user_authorization')):
                    value = review.get(source)
                    if isinstance(value, str) and len(value) <= 100:
                        event[output] = value
            self.condition.notify_all()
        # Manager emission acquires a different lock; never call it while holding
        # the ledger lock, since Stop closes the ledger under the manager lock.
        if event is not None:
            self.emit(event)
            with self.condition:
                if not self.closed and not self.cancel.is_set():
                    previous['published'] = True
                    self.condition.notify_all()

    def consume(self, name, arguments, proposal_name, proposal):
        """Release exactly one validated business callback, or decline it."""
        key = _key(name, arguments)
        if proposal_name != name:
            return False
        deadline = time.monotonic() + self.receipt_timeout
        with self.condition:
            while True:
                if self.closed or self.cancel.is_set():
                    raise InterruptedError('Official approval turn ended')
                matching = []
                for review in self.reviews.values():
                    item = self.items.get(review['target'])
                    if (not review['used'] and item and not item['finished'] and item['key'] == key
                            and review['action'] == ('analog_factory', name)):
                        matching.append(review)
                accepted = next((review for review in matching if review['status'] == 'approved'
                                 and review.get('published')), None)
                if accepted:
                    accepted['used'] = True
                    # A tool item represents one invocation even if the native
                    # stream contains multiple review IDs for that same item.
                    self.items[accepted['target']]['finished'] = True
                    current = self._snapshot()
                    if (current['version'] != accepted['version']
                            or proposal.get('expected_version', accepted['version']) != accepted['version']):
                        raise ValueError('Project version changed during official review; request a new review')
                    if name == 'invoke_operation':
                        operation = self.workspace.operations.operations.get(arguments['operation_id'])
                        if (operation and operation.kind == 'frontend'
                                and operation.frontend_adapter == 'browser'):
                            params = proposal.get('parameters', {})
                            client = params.get('client_id')
                            expected = accepted['browsers'].get(client)
                            if (expected is None or current['browsers'].get(client) != expected
                                    or params.get('ui_version') != expected):
                                raise ValueError('Frontend version changed during official review; request a new review')
                    return True
                # Native approval finishes before MCP execution, but its stdout
                # notification can race the HTTP callback. Wait a bounded interval
                # rather than interpreting callback arrival as approval evidence.
                remaining = deadline - time.monotonic()
                if remaining <= 0 or matching and all(review['status'] != 'inProgress'
                        and (review['status'] != 'approved' or review.get('published')) for review in matching):
                    return False
                self.condition.wait(min(remaining, .05))

    def close(self):
        with self.condition:
            self.closed = True
            self.items.clear()
            self.reviews.clear()
            self.condition.notify_all()
