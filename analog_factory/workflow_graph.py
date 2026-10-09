"""Independent, typed schema-v2 graphs executed through registered operations.

Graphs are JSON data, never Python or shell programs. Control graphs are acyclic;
explicit loop nodes own bounded nested graphs and share their parent's budgets.
Model sessions are read-only; explicit source patches require human review.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import operator
import re
import secrets
import threading
import time

from .operation_schema import validate as validate_value
from .operations import InvocationContext

VALUE_TYPES = ('any', 'object', 'array', 'string', 'number', 'integer', 'boolean', 'null')
NODE_PORTS = {
    'input': ({}, {'value': 'any'}),
    'specification': ({'value': 'object'}, {'value': 'object'}),
    'parameter': ({'value': 'number'}, {'value': 'number'}),
    'project': ({'path': 'string', 'run_id': 'string'}, {'content': 'string', 'data': 'object', 'sha256': 'string'}),
    'netlist': ({'content': 'string', 'value': 'number'}, {'content': 'string'}),
    'simulate': ({'content': 'string', 'netlist': 'string'}, {'result': 'object', 'metrics': 'object', 'run_id': 'string', 'success': 'boolean'}),
    'drc': ({}, {'result': 'object', 'metrics': 'object', 'run_id': 'string', 'success': 'boolean'}),
    'lvs': ({}, {'result': 'object', 'metrics': 'object', 'run_id': 'string', 'success': 'boolean'}),
    'extract': ({}, {'result': 'object', 'metrics': 'object', 'run_id': 'string', 'success': 'boolean'}),
    'measure': ({'result': 'object', 'metrics': 'object', 'run_id': 'string', 'metric': 'string',
                 'target': 'number', 'tolerance': 'number'}, {'value': 'number', 'satisfied': 'boolean'}),
    'condition': ({'left': 'any', 'right': 'any'}, {'result': 'boolean'}),
    'loop': ({'state': 'any', 'values': 'array'}, {'iterations': 'array', 'result': 'object', 'satisfied': 'boolean'}),
    'human_review': ({'value': 'any'}, {'approved': 'boolean', 'value': 'any'}),
    'source_patch': ({'content': 'string', 'expected_sha256': 'string'},
                     {'approved': 'boolean', 'applied': 'boolean', 'outcome_unknown': 'boolean',
                      'path': 'string', 'before_sha256': 'string', 'after_sha256': 'string',
                      'project_version': 'string', 'proposal': 'object', 'receipt': 'object'}),
    'model': ({'prompt': 'string', 'context': 'any'}, {'reply': 'string', 'usage': 'any', 'cost_usd': 'null', 'session_id': 'string'}),
    'report': ({}, {'report': 'object'}),
}
COMPARISONS = {'eq': operator.eq, 'ne': operator.ne, 'lt': operator.lt,
               'lte': operator.le, 'gt': operator.gt, 'gte': operator.ge,
               'contains': lambda left, right: right in left}
TERMINAL = {'succeeded', 'failed', 'skipped', 'cancelled', 'timed_out', 'unavailable', 'interrupted'}
_ID = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,79}$')


def _typed(value, kind, name):
    if kind not in VALUE_TYPES:
        raise ValueError(f'{name} has unknown type {kind}')
    return validate_value(value, {} if kind == 'any' else {'type': kind}, name)


def _number(value, name, minimum, maximum, integer=False):
    kind = 'integer' if integer else 'number'
    return validate_value(value, {'type': kind, 'minimum': minimum, 'maximum': maximum}, name)


def _port_types(node):
    result = {**NODE_PORTS[node['type']][1], 'error': 'string'}
    if node['type'] == 'input':
        result['value'] = node['config'].get('type', 'any')
    for key, mapping in node.get('outputs', {}).items():
        result[key] = mapping['type']
    return result


def _mapping(mapping, by_id, name, expected='any'):
    if not isinstance(mapping, dict):
        raise ValueError(f'{name} requires a literal or node/port mapping')
    if 'value' in mapping:
        if set(mapping) != {'value'}:
            raise ValueError(f'{name} literal mapping only accepts value')
        _typed(mapping['value'], expected, name)
        return None
    if set(mapping) - {'node', 'port', 'default'} or not {'node', 'port'} <= set(mapping):
        raise ValueError(f'{name} requires node and port')
    if not isinstance(mapping['node'], str) or not isinstance(mapping['port'], str):
        raise ValueError(f'{name} node and port must be strings')
    source = by_id.get(mapping['node'])
    if source is None:
        raise ValueError(f'{name} references an unknown node')
    types = _port_types(source)
    if mapping['port'] not in types:
        raise ValueError(f'{name} references unknown output port {mapping["port"]}')
    actual = types[mapping['port']]
    if expected != 'any' and actual not in (expected, 'any') and not (expected == 'number' and actual == 'integer'):
        raise ValueError(f'{name} type mismatch: {actual} cannot feed {expected}')
    if 'default' in mapping:
        _typed(mapping['default'], expected, name + '.default')
    return mapping['node']


def validate_graph(workflow, *, _depth=0):
    """Validate and normalize v2 JSON, including typed ports and dependency cycles."""
    if not isinstance(workflow, dict) or workflow.get('schema_version') != 2:
        raise ValueError('Workflow schema_version must be 2')
    if _depth > 3:
        raise ValueError('Loop nesting cannot exceed three levels')
    try:
        encoded = json.dumps(workflow, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError('Workflow must contain finite JSON values') from None
    if len(encoded.encode()) > 2 * 1024 * 1024:
        raise ValueError('Workflow exceeds the 2 MiB limit')
    flow = copy.deepcopy(workflow)
    nodes, edges = flow.get('nodes'), flow.get('edges', [])
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 200 or not isinstance(edges, list) or len(edges) > 2000:
        raise ValueError('Graph requires 1–200 nodes and at most 2000 edges')
    flow['edges'] = edges
    by_id = {}
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get('id'), str) or not _ID.fullmatch(node['id']):
            raise ValueError('Node identifiers must start with a letter and contain letters, digits, _ or -')
        if node['id'] in by_id:
            raise ValueError('Node identifiers must be unique')
        if not isinstance(node.get('type'), str) or node['type'] not in NODE_PORTS:
            raise ValueError('Unknown executable node type')
        by_id[node['id']] = node
        for field in ('config', 'inputs', 'outputs'):
            node.setdefault(field, {})
            if not isinstance(node[field], dict):
                raise ValueError(f'Node {field} must be an object')
        _number(node.setdefault('retries', 0), 'Node retries', 0, 5, True)
        _number(node.setdefault('timeout_seconds', 60), 'Node timeout', .1, 3600)
        if node.get('on_error', 'fail') not in ('fail', 'continue'):
            raise ValueError('Node on_error must be fail or continue')
        for alias, mapping in node['outputs'].items():
            if alias in NODE_PORTS[node['type']][1] or alias == 'error':
                raise ValueError(f'Output alias {alias} is reserved for the authoritative node result')
            if not _ID.fullmatch(alias) or not isinstance(mapping, dict) or set(mapping) != {'path', 'type'}:
                raise ValueError('Output mappings require a named path and type')
            if not isinstance(mapping['path'], str) or not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*', mapping['path']):
                raise ValueError('Output mapping path must be a dot-separated JSON path')
            if mapping['type'] not in VALUE_TYPES:
                raise ValueError('Output mapping has an unknown type')
        config = node['config']
        kind = node['type']
        if kind == 'input' and config.get('type', 'any') not in VALUE_TYPES:
            raise ValueError('Input node has an unknown type')
        if kind == 'condition' and config.get('operator', 'eq') not in COMPARISONS:
            raise ValueError('Unknown condition operator')
        if kind == 'source_patch':
            if node['retries']:
                raise ValueError('Source patch retries are forbidden')
            if not isinstance(config.get('path'), str) or not {'content', 'expected_sha256'} <= set(node['inputs']):
                raise ValueError('Source patch requires a fixed configured path, content and expected_sha256 inputs')
            if set(node['inputs']) - {'content', 'expected_sha256'}:
                raise ValueError('Source patch accepts only content and expected_sha256 inputs')
        if kind == 'loop':
            _number(config.get('max_iterations'), 'Loop max_iterations', 1, 100, True)
            config['body'] = validate_graph(config.get('body'), _depth=_depth + 1)
            if 'values' in config and not isinstance(config['values'], list):
                raise ValueError('Loop values must be an array')
        if kind == 'parameter' and 'value' in config:
            _typed(config['value'], 'number', 'Parameter value')
        if kind == 'specification' and 'value' in config:
            _typed(config['value'], 'object', 'Specification value')
        if kind == 'netlist' and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', str(config.get('parameter', ''))):
            raise ValueError('Netlist processing requires a SPICE parameter name')
        if kind == 'model' and config.get('backend', 'api') not in ('api', 'codex', 'claude'):
            raise ValueError('Unsupported model backend')
    dependencies = {key: set() for key in by_id}
    seen_edges = set()
    for edge in edges:
        if not isinstance(edge, dict) or set(edge) - {'source', 'target', 'when', 'on'}:
            raise ValueError('Edges accept source, target, when and on')
        source, target = edge.get('source'), edge.get('target')
        if not isinstance(source, str) or not isinstance(target, str) or source not in by_id or target not in by_id:
            raise ValueError('Edge references unknown node')
        signature = json.dumps(edge, sort_keys=True)
        if signature in seen_edges:
            raise ValueError('Duplicate edge')
        seen_edges.add(signature)
        if edge.get('on', 'success') not in ('success', 'failure', 'always'):
            raise ValueError('Edge on must be success, failure or always')
        if 'when' in edge and (type(edge['when']) is not bool or by_id[source]['type'] not in ('condition', 'human_review', 'measure')):
            raise ValueError('Boolean branch edges require a condition, measure or review node')
        dependencies[target].add(source)
    for node in nodes:
        expected = NODE_PORTS[node['type']][0]
        for key, mapping in node['inputs'].items():
            if node['type'] != 'report' and key not in expected:
                raise ValueError(f'Unknown input port {node["id"]}.{key}')
            source = _mapping(mapping, by_id, f'{node["id"]}.{key}', expected.get(key, 'any'))
            if source:
                dependencies[node['id']].add(source)
    visited = set()
    while len(visited) < len(nodes):
        ready = [key for key, parents in dependencies.items() if key not in visited and parents <= visited]
        if not ready:
            raise ValueError('Unbounded graph cycle; use an explicit bounded loop node')
        visited.update(ready)
    limits = flow.setdefault('limits', {})
    if not isinstance(limits, dict) or set(limits) - {'runtime_seconds', 'max_node_executions', 'max_iterations', 'cost_usd'}:
        raise ValueError('Invalid graph limits')
    _number(limits.setdefault('runtime_seconds', 120), 'Runtime budget', .1, 3600)
    _number(limits.setdefault('max_node_executions', 1000), 'Node execution budget', 1, 10000, True)
    _number(limits.setdefault('max_iterations', 100), 'Iteration budget', 1, 1000, True)
    if 'cost_usd' in limits:
        _number(limits['cost_usd'], 'Cost budget', 0, 1000000)
    for field in ('inputs', 'outputs', 'debug'):
        flow.setdefault(field, {})
        if not isinstance(flow[field], dict):
            raise ValueError(f'Graph {field} must be an object')
    if set(flow['debug']) - {'step'} or ('step' in flow['debug'] and type(flow['debug']['step']) is not bool):
        raise ValueError('Debug accepts a boolean step field')
    for key, mapping in flow['outputs'].items():
        _mapping(mapping, by_id, 'Graph output ' + key)
    return flow


def graph_catalog():
    """Public typed node catalog for editor and registry adapters."""
    return {'schema_version': 2, 'node_types': [
        {'type': kind, 'inputs': inputs, 'outputs': {**outputs, 'error': 'string'}}
        for kind, (inputs, outputs) in NODE_PORTS.items()],
        'limits': {'nodes': 200, 'edges': 2000, 'loop_depth': 3, 'retries': 5,
                   'source_patch_retries': 0, 'source_patch_file_bytes': 64000,
                   'source_proposal_bytes': 256 * 1024, 'source_proposals': 20,
                   'total_source_proposal_bytes': 2 * 1024 * 1024},
        'cost_accounting': 'Model monetary cost is unavailable; a required cost budget blocks model execution.'}


class GraphControl:
    """Live job controls. Only the user-facing operation may submit review decisions."""
    def __init__(self, step=False):
        self.lock = threading.RLock()
        self.paused = bool(step)
        self.steps = 0
        self.decisions = {}
        self.pending_review = None
        self.review_token = None
        self.review_binding = None
        self.review_decided = False

    def command(self, action, node_id=None, approved=None, review_token=None):
        with self.lock:
            if action == 'step':
                self.paused = True
                self.steps += 1
            elif action == 'continue':
                self.paused = False
                self.steps = 0
            elif action == 'pause':
                self.paused = True
                self.steps = 0
            elif action == 'review':
                if type(approved) is not bool or not isinstance(node_id, str):
                    raise ValueError('Review requires node_id and a boolean decision')
                if self.pending_review != node_id:
                    raise ValueError('This node does not have a pending human review')
                if node_id in self.decisions or self.review_decided:
                    raise ValueError('This review decision has already been submitted')
                if self.review_token is not None and (not isinstance(review_token, str)
                        or not secrets.compare_digest(self.review_token, review_token)):
                    raise ValueError('Source patch review requires its exact current review token')
                if self.review_token is None and review_token is not None:
                    raise ValueError('This review does not accept a source patch token')
                self.decisions[node_id] = approved
                self.review_decided = True
            else:
                raise ValueError('Control action must be step, continue, pause or review')
            return {'action': action, 'paused': self.paused, 'pending_review': self.pending_review}

    def permits_step(self):
        with self.lock:
            if not self.paused:
                return True
            if self.steps:
                self.steps -= 1
                return True
            return False


class GraphStop(Exception):
    def __init__(self, termination, message, status='failed'):
        super().__init__(message)
        self.termination, self.status = termination, status


class NodeFailure(ValueError):
    def __init__(self, message, outputs=None):
        super().__init__(message)
        self.outputs = outputs or {}


class _Execution:
    def __init__(self, workspace, project_id, flow, job, cancel, update, control):
        self.workspace, self.pid, self.flow = workspace, project_id, flow
        self.job, self.cancel, self.update = job, cancel, update
        self.control = control or GraphControl(flow['debug'].get('step', False))
        self.started = time.monotonic()
        self.deadline = self.started + flow['limits']['runtime_seconds']
        self.executions = 0
        self.iterations = 0
        self.cost_unknown = False
        self.records = {}
        self.reports = []
        self.scopes = []
        self.context = InvocationContext(principal='workflow-engine', source='workflow', project_id=project_id,
                                         permissions=frozenset({'read', 'write', 'execute'}), confirmed=True)
        self.sources = self.source_hashes()
        self.source_generations = [{'index': 0, 'source_hashes': copy.deepcopy(self.sources)}]
        self.proposal_count = self.proposal_size = 0

    def source_hashes(self):
        from .storage import confined
        root = self.workspace.projects.path(self.pid)
        return {f['path']: hashlib.sha256(confined(root, f['path']).read_bytes()).hexdigest()
                for f in self.workspace.projects.files(self.pid)}

    def check(self, deadline=None):
        if self.cancel.is_set():
            raise GraphStop('cancelled', 'Workflow cancelled', 'cancelled')
        if time.monotonic() >= self.deadline:
            raise GraphStop('runtime_limit', 'Workflow runtime budget exhausted', 'timed_out')
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError('Node timeout exhausted')
        if any(time.monotonic() >= scope['deadline'] for scope in self.scopes):
            raise TimeoutError('Nested graph runtime budget exhausted')

    def sources_unchanged(self):
        if self.source_hashes() != self.sources:
            raise GraphStop('source_conflict', 'Project sources changed during execution; start a new workflow')

    def publish(self, **extra):
        self.update(nodes=copy.deepcopy(self.records), node_executions=self.executions,
                    loop_iterations=self.iterations, elapsed_seconds=time.monotonic() - self.started, **extra)

    def invoke(self, operation_id, parameters=None):
        result = self.workspace.operations.invoke(operation_id, {'project_id': self.pid, **(parameters or {})}, context=self.context)
        child_record = (operation_id in ('run.start', 'run.get', 'run.cancel', 'agent.session.send',
                                        'agent.session.get', 'agent.session.cancel') and
                        isinstance(result.get('data'), dict) and result['data'].get('id'))
        if not result['ok'] and not child_record:
            raise ValueError(result['error']['message'])
        return result['data']

    def resolve(self, mapping, records):
        if 'value' in mapping:
            return copy.deepcopy(mapping['value'])
        outputs = records[mapping['node']]['outputs']
        if mapping['port'] in outputs:
            return copy.deepcopy(outputs[mapping['port']])
        if 'default' in mapping:
            return copy.deepcopy(mapping['default'])
        raise ValueError(f'Mapped output {mapping["node"]}.{mapping["port"]} is unavailable')

    def wait_child(self, kind, child, deadline, full_id):
        key = 'run_id' if kind == 'run' else 'session_id'
        operation = 'run' if kind == 'run' else 'agent.session'
        self.records[full_id]['child'] = {'kind': kind, 'id': child['id']}
        last_logs = None
        try:
            self.publish(**{'active_run' if kind == 'run' else 'active_session': child['id']})
            while True:
                self.check(deadline)
                record = self.invoke(operation + '.get', {key: child['id']})
                if record['status'] in TERMINAL:
                    return record
                if kind == 'run':
                    content = self.invoke('run.log', {key: child['id']})['content'][-16000:]
                else:
                    content = json.dumps(record.get('turns', [{}])[-1].get('events', [])[-10:])[-16000:]
                if content != last_logs:
                    self.records[full_id]['live_log'] = content
                    self.publish()
                    last_logs = content
                time.sleep(.025)
        except BaseException:
            self.invoke(operation + '.cancel', {key: child['id']})
            # EDA cancellation owns a real process group; drain it before a job
            # becomes terminal. API workers retain their slot until transport drains.
            drain_until = time.monotonic() + 5
            while time.monotonic() < drain_until:
                current = self.invoke(operation + '.get', {key: child['id']})
                if current['status'] in TERMINAL:
                    break
                time.sleep(.025)
            raise

    def checkpoint(self, full_id, deadline=None):
        announced = False
        while not self.control.permits_step():
            self.check(deadline)
            if not announced:
                self.publish(debug_state='paused', active_node=full_id)
                announced = True
            time.sleep(.025)
        self.publish(debug_state='running', active_node=full_id)

    def source_patch(self, node, inputs, deadline, full_id):
        from .file_changes import prepare_change, verify_change, proposal_bytes
        with self.workspace.projects.lock:
            self.check(deadline)
            self.sources_unchanged()
            proposal, identity = prepare_change(self.workspace, self.pid, node['config']['path'],
                                                inputs['content'], inputs['expected_sha256'])
            if proposal['current_sha256'] == proposal['candidate_sha256']:
                raise ValueError('Source patch must change the current content')
            size = proposal_bytes(proposal)
            if self.proposal_count >= 20 or self.proposal_size + size > 2 * 1024 * 1024:
                raise GraphStop('proposal_limit', 'Shared source proposal count or byte budget exhausted')
            self.proposal_count += 1
            self.proposal_size += size
            binding = {'project_id': self.pid, 'job_id': self.job['id'], 'node_id': full_id,
                       'attempt': self.records[full_id]['attempts'], 'proposal_sha256': proposal['proposal_sha256'],
                       'project_version': proposal['project_version']}
            token = secrets.token_hex(32)
            binding = {**binding, 'review_token': token}
        outputs = {'approved': False, 'applied': False, 'outcome_unknown': False,
                   'path': proposal['path'], 'before_sha256': proposal['current_sha256'],
                   'after_sha256': proposal['candidate_sha256'], 'project_version': proposal['project_version'],
                   'proposal': proposal, 'receipt': {}}
        record = self.records[full_id]
        record['outputs'] = copy.deepcopy(outputs)
        record['review'] = copy.deepcopy(binding)
        with self.control.lock:
            self.control.pending_review = full_id
            self.control.review_token = token
            self.control.review_binding = copy.deepcopy(binding)
            self.control.review_decided = False
        try:
            self.publish(pending_review={**binding, 'kind': 'source_patch',
                         'prompt': node['config'].get('prompt', 'Review and apply this exact source change'),
                         'value': proposal}, proposal_count=self.proposal_count, proposal_bytes=self.proposal_size)
            while True:
                self.check(deadline)
                with self.control.lock:
                    if full_id in self.control.decisions:
                        approved = self.control.decisions.pop(full_id)
                        if self.control.review_binding != binding:
                            raise ValueError('Source patch review binding changed')
                        break
                time.sleep(.025)
            outputs['approved'] = approved
            record['outputs'] = copy.deepcopy(outputs)
            record['review']['approved'] = approved
            if not approved:
                raise NodeFailure('Human rejected the source patch', outputs)
            with self.workspace.projects.lock:
                self.check(deadline)
                self.sources_unchanged()
                verify_change(self.workspace, self.pid, proposal, identity)
                self.check(deadline)
                context = InvocationContext(principal='workflow-engine', source='workflow', project_id=self.pid,
                                            permissions=frozenset({'read', 'write'}), confirmed=True)
                outputs['outcome_unknown'] = True
                record['outputs'] = copy.deepcopy(outputs)
                receipt = self.workspace.operations.invoke('file.write',
                    {'project_id': self.pid, 'path': proposal['path'], 'content': proposal['candidate_content']},
                    context=context, expected_version=proposal['project_version'])
                outputs['receipt'] = receipt
                record['outputs'] = copy.deepcopy(outputs)
                expected = {**self.sources, proposal['path']: proposal['candidate_sha256']}
                try:
                    actual = self.source_hashes()
                    if not receipt['ok'] or actual != expected or receipt['data']['sha256'] != proposal['candidate_sha256']:
                        raise ValueError('Registered write or resulting source map could not be verified')
                except Exception as error:
                    outputs['outcome_unknown'] = True
                    record['outputs'] = copy.deepcopy(outputs)
                    raise GraphStop('patch_outcome_unknown', f'Source patch write outcome requires inspection: {error}') from error
                outputs.update(applied=True, outcome_unknown=False, project_version=receipt['version'])
                record['outputs'] = copy.deepcopy(outputs)
                self.sources = expected
                self.source_generations.append({'index': len(self.source_generations), 'node_id': full_id,
                    'source_hashes': copy.deepcopy(expected), 'project_version': receipt['version'],
                    'proposal_sha256': proposal['proposal_sha256'], 'trace_id': receipt['trace_id']})
                self.publish(source_generations=copy.deepcopy(self.source_generations))
            return outputs
        finally:
            with self.control.lock:
                self.control.pending_review = None
                self.control.review_token = self.control.review_binding = None
                self.control.decisions.pop(full_id, None)
            self.publish(pending_review=None)

    def run_node(self, node, inputs, deadline, full_id, graph_inputs):
        kind, config = node['type'], node['config']
        if kind == 'source_patch':
            return self.source_patch(node, inputs, deadline, full_id)
        if kind == 'input':
            name = config.get('name', node['id'])
            if name not in graph_inputs and 'default' not in config:
                raise ValueError(f'Missing workflow input {name}')
            return {'value': _typed(graph_inputs.get(name, config.get('default')), config.get('type', 'any'), name)}
        if kind == 'specification':
            return {'value': _typed(inputs.get('value', config.get('value', {})), 'object', 'Specification')}
        if kind == 'parameter':
            return {'value': _typed(inputs.get('value', config.get('value')), 'number', 'Parameter')}
        if kind == 'project':
            if 'run_id' in inputs or 'run_id' in config:
                rid = inputs.get('run_id', config.get('run_id'))
                record = self.invoke('run.get', {'run_id': rid})
                content = self.invoke('run.log', {'run_id': rid})['content']
                return {'content': content, 'data': record}
            path = inputs.get('path', config.get('path'))
            if path:
                record = self.invoke('file.read', {'path': path})
                data = json.loads(record['content']) if config.get('parse_json') else record
                return {'content': record['content'], 'data': data,
                        'sha256': hashlib.sha256(record['content'].encode()).hexdigest()}
            return {'data': self.invoke('project.detail'), 'content': ''}
        if kind == 'netlist':
            from .workflows import replace_parameter
            return {'content': replace_parameter(inputs['content'], config['parameter'], inputs['value'])}
        if kind in ('simulate', 'drc', 'lvs', 'extract'):
            options = copy.deepcopy(config.get('options', {}))
            if kind == 'simulate':
                options['netlist'] = inputs.get('netlist', options.get('netlist', 'testbenches/pre.cir'))
            metadata = {'workflow_id': self.job['id'], 'source_generation': len(self.source_generations) - 1}
            if 'content' in inputs:
                metadata['generated_files'] = {options['netlist']: inputs['content']}
            if 'conditions' in config:
                metadata['conditions'] = config['conditions']
            self.sources_unchanged()
            remaining = min(deadline, self.deadline) - time.monotonic()
            self.check(deadline)
            run = self.invoke('run.start', {'kind': kind, 'options': options,
                                          'timeout': max(.1, remaining), 'metadata': metadata})
            result = self.wait_child('run', run, deadline, full_id)
            output = {'result': result, 'metrics': result.get('metrics', {}), 'run_id': result['id'],
                      'success': result['status'] == 'succeeded' and not result.get('stale', False)}
            verification = result.get('verification', {})
            if kind in ('drc', 'lvs'):
                output['success'] = output['success'] and verification.get('status') == 'pass'
            if result['status'] == 'timed_out':
                raise TimeoutError(result.get('error') or 'EDA run timed out')
            if not output['success']:
                diagnostic = (f'verification {verification.get("status", "unknown")}: {verification.get("reason", "missing report")}'
                              if kind in ('drc', 'lvs') else result.get('error') or 'inspect run evidence')
                raise NodeFailure(f'{kind} run {result["id"]} {result["status"]}: {diagnostic}', output)
            return output
        if kind == 'measure':
            if 'signal' in config:
                plot = self.invoke('run.waveform_stats', {'run_id': inputs['run_id'], 'plot_index': config.get('plot_index', 0)})
                index = next((i for i, var in enumerate(plot['variables']) if var['name'] == config['signal']), None)
                if index is None:
                    raise ValueError('Waveform signal is unavailable')
                statistics = plot['statistics'][index]
                if not statistics['count']:
                    raise ValueError('Waveform has no samples')
                method = config.get('statistic', 'mean')
                if method not in ('min', 'max', 'mean', 'rms', 'peak_to_peak'):
                    raise ValueError('Unknown waveform statistic')
                value = statistics[method]
            else:
                metric = inputs.get('metric', config.get('metric'))
                value = inputs.get('metrics', inputs.get('result', {}).get('metrics', {})).get(metric)
                if value is None:
                    raise ValueError(f'Measurement {metric} is missing')
            value = _typed(value, 'number', 'Measurement')
            target = inputs.get('target', config.get('target'))
            tolerance = _number(inputs.get('tolerance', config.get('tolerance', 0)), 'Measurement tolerance', 0, 1e100)
            satisfied = abs(value - _typed(target, 'number', 'Measurement target')) <= tolerance if target is not None else True
            return {'value': value, 'satisfied': satisfied}
        if kind == 'condition':
            return {'result': bool(COMPARISONS[config.get('operator', 'eq')](inputs['left'], inputs['right']))}
        if kind == 'loop':
            results, state = [], inputs.get('state')
            values = inputs.get('values', config.get('values'))
            count = config['max_iterations'] if values is None else min(config['max_iterations'], len(values))
            satisfied = False
            for index in range(count):
                self.check(deadline)
                if any(self.iterations - scope['iterations'] >= scope['limits']['max_iterations'] for scope in self.scopes):
                    raise GraphStop('iteration_limit', 'Shared loop iteration budget exhausted')
                self.iterations += 1
                body_inputs = {**config['body']['inputs'], 'index': index, 'state': state}
                if values is not None:
                    body_inputs['item'] = values[index]
                attempt = self.records[full_id]['attempts']
                child_prefix = f'{full_id}' + (f'@{attempt}' if attempt > 1 else '') + f'[{index}]/'
                try:
                    result = self.run_graph(config['body'], child_prefix, body_inputs, deadline)
                except GraphStop as error:
                    if error.termination not in ('node_failed', 'node_timeout'):
                        raise
                    if error.termination == 'node_timeout':
                        raise TimeoutError(str(error)) from error
                    raise NodeFailure(str(error), {'iterations': results, 'result': {}, 'satisfied': False}) from error
                results.append(result)
                state = result.get(config.get('carry', 'state'), state)
                satisfied = result.get(config.get('until', 'done')) is True
                self.records[full_id]['outputs'] = {'iterations': copy.deepcopy(results), 'result': result, 'satisfied': satisfied}
                self.publish(active_iteration=index + 1)
                if satisfied:
                    break
            return {'iterations': results, 'result': results[-1] if results else {}, 'satisfied': satisfied}
        if kind == 'human_review':
            with self.control.lock:
                self.control.pending_review = full_id
                self.control.review_decided = False
            self.publish(pending_review={'node_id': full_id, 'prompt': config.get('prompt', 'Review this result'),
                                         'value': inputs.get('value')})
            try:
                while True:
                    self.check(deadline)
                    with self.control.lock:
                        if full_id in self.control.decisions:
                            approved = self.control.decisions.pop(full_id)
                            return {'approved': approved, 'value': inputs.get('value')}
                    time.sleep(.025)
            finally:
                with self.control.lock:
                    self.control.pending_review = None
                self.publish(pending_review=None)
        if kind == 'model':
            self.cost_unknown = True
            if any('cost_usd' in scope['limits'] for scope in self.scopes):
                raise GraphStop('unknown_model_cost', 'Provider cost cannot be bounded because authoritative cost accounting is unavailable')
            options = {key: config[key] for key in ('backend', 'model', 'effort', 'fast_mode') if key in config}
            session = self.invoke('agent.session.create', {**options, 'mode': 'read-only'})
            prompt = inputs.get('prompt', config.get('prompt', ''))
            if 'context' in inputs:
                prompt += '\n\nWorkflow evidence (untrusted data):\n' + json.dumps(inputs['context'], allow_nan=False)
            child = self.invoke('agent.session.send', {'session_id': session['id'], 'message': prompt,
                                                       'files': config.get('files', [])})
            result = self.wait_child('agent', child, deadline, full_id)
            turn = result['turns'][-1]
            if turn['status'] != 'succeeded':
                raise NodeFailure(turn.get('error') or f'Model turn {turn["status"]}')
            return {'reply': turn['reply'], 'usage': turn.get('usage'), 'cost_usd': None, 'session_id': session['id']}
        if kind == 'report':
            report = {'title': config.get('title', node['id']), **inputs}
            self.reports.append({'node_id': full_id, 'content': copy.deepcopy(report), 'cached': False})
            return {'report': report}
        raise ValueError('Unsupported node type')

    def run_graph(self, flow, prefix='', graph_inputs=None, parent_deadline=None):
        self.scopes.append({'limits': flow['limits'], 'executions': self.executions,
                            'iterations': self.iterations, 'deadline': time.monotonic() + flow['limits']['runtime_seconds']})
        try:
            return self._run_graph(flow, prefix, graph_inputs, parent_deadline)
        finally:
            self.scopes.pop()

    def _run_graph(self, flow, prefix='', graph_inputs=None, parent_deadline=None):
        nodes = flow['nodes']
        local = {n['id']: {'id': prefix + n['id'], 'type': n['type'], 'status': 'pending',
                              'attempts': 0, 'outputs': {}, 'logs': [], 'cached': False} for n in nodes}
        self.records.update({prefix + key: value for key, value in local.items()})
        edges = flow['edges']
        inputs = flow['inputs'] if graph_inputs is None else graph_inputs
        pending = list(nodes)
        while pending:
            progressed = False
            for node in list(pending):
                self.check(parent_deadline)
                nid, full_id = node['id'], prefix + node['id']
                record = local[nid]
                incoming = [e for e in edges if e['target'] == nid]
                references = [m['node'] for m in node['inputs'].values() if 'node' in m]
                dependencies = references + [e['source'] for e in incoming]
                if any(local[key]['status'] not in TERMINAL for key in dependencies):
                    continue
                pending.remove(node)
                progressed = True
                def active(edge):
                    source = local[edge['source']]
                    state = source['status']
                    expected = edge.get('on', 'success')
                    if state == 'skipped' or (expected == 'success' and state != 'succeeded') or (expected == 'failure' and state not in ('failed', 'timed_out')):
                        return False
                    if 'when' in edge:
                        output = source['outputs']
                        branch_port = {'condition': 'result', 'human_review': 'approved', 'measure': 'satisfied'}[source['type']]
                        condition = output.get(branch_port)
                        return condition is edge['when']
                    return True
                skipped_data = any(local[m['node']]['status'] == 'skipped' and 'default' not in m for m in node['inputs'].values() if 'node' in m)
                if (incoming and not any(active(e) for e in incoming)) or skipped_data:
                    record.update(status='skipped', skip_reason='Inactive branch or skipped input')
                    self.publish()
                    continue
                self.checkpoint(full_id, parent_deadline)
                self.sources_unchanged()
                record.update(status='running', started_seconds=time.monotonic() - self.started)
                record['source_generation'] = len(self.source_generations) - 1
                self.publish(active_node=full_id)
                deadline = min(self.deadline, parent_deadline or self.deadline, time.monotonic() + node['timeout_seconds'])
                for attempt in range(node['retries'] + 1):
                    self.check(deadline)
                    if any(self.executions - scope['executions'] >= scope['limits']['max_node_executions'] for scope in self.scopes):
                        raise GraphStop('node_execution_limit', 'Shared node execution budget exhausted')
                    self.executions += 1
                    record['attempts'] = attempt + 1
                    try:
                        resolved = {key: self.resolve(mapping, local) for key, mapping in node['inputs'].items()}
                        expected = NODE_PORTS[node['type']][0]
                        for key, value in resolved.items():
                            _typed(value, expected.get(key, 'any'), full_id + '.' + key)
                        record['inputs'] = copy.deepcopy(resolved)
                        output = self.run_node(node, resolved, deadline, full_id, inputs)
                        self.check(deadline)
                        for port, value in output.items():
                            _typed(value, NODE_PORTS[node['type']][1].get(port, 'any'), full_id + '.' + port)
                        for alias, mapping in node['outputs'].items():
                            value = output
                            for path in mapping['path'].split('.'):
                                value = value[int(path)] if isinstance(value, list) else value[path]
                            output[alias] = _typed(value, mapping['type'], full_id + '.' + alias)
                        record.update(status='succeeded', outputs=output)
                        break
                    except GraphStop:
                        raise
                    except Exception as error:
                        if node['type'] == 'source_patch' and (record['outputs'].get('applied') or record['outputs'].get('outcome_unknown')):
                            termination = 'patch_outcome_unknown' if record['outputs'].get('outcome_unknown') else 'patch_post_commit_failure'
                            raise GraphStop(termination, f'Source patch write requires inspection; execution stopped: {error}') from error
                        record['logs'].append({'attempt': attempt + 1, 'error': str(error)})
                        retained = record['outputs'] if node['type'] == 'source_patch' else {}
                        record['outputs'] = {**retained, **getattr(error, 'outputs', {}), 'error': str(error)}
                        if attempt == node['retries'] or isinstance(error, TimeoutError):
                            record.update(status='timed_out' if isinstance(error, TimeoutError) else 'failed', error=str(error))
                            handled = node.get('on_error') == 'continue' or any(
                                e['source'] == nid and e.get('on') in ('failure', 'always') and active(e) for e in edges)
                            if not handled:
                                self.publish()
                                raise GraphStop('node_timeout' if isinstance(error, TimeoutError) else 'node_failed',
                                                f'Node {full_id}: {error}', 'timed_out' if isinstance(error, TimeoutError) else 'failed') from error
                            break
                        self.publish()
                record['duration_seconds'] = time.monotonic() - self.started - record['started_seconds']
                self.publish()
            if not progressed:
                raise GraphStop('invalid_graph', 'No executable nodes remain')
        return {key: self.resolve(mapping, local) for key, mapping in flow['outputs'].items()}


def execute_graph(workspace, project_id, workflow, job, cancel, update, control=None):
    """Execute a validated v2 graph as a Jobs callback; return evidence and status."""
    flow = validate_graph(workflow)
    execution = _Execution(workspace, project_id, flow, job, cancel, update, control)
    status, termination, error, outputs = 'succeeded', 'completed', None, {}
    try:
        outputs = execution.run_graph(flow)
        execution.sources_unchanged()
    except GraphStop as stopped:
        status, termination, error = stopped.status, stopped.termination, str(stopped)
    except Exception as stopped:
        status, termination, error = 'failed', 'node_failed', str(stopped)
    for record in execution.records.values():
        if record['status'] == 'running':
            record.update(status=status if status in TERMINAL else 'failed', error=error)
        elif record['status'] == 'pending':
            record.update(status='skipped', skip_reason='Workflow terminated')
    result = {'schema_version': 2, 'workflow': flow, 'status': status, 'termination': termination,
              'nodes': execution.records, 'outputs': outputs, 'reports': execution.reports,
              'node_executions': execution.executions, 'loop_iterations': execution.iterations,
              'duration_seconds': time.monotonic() - execution.started,
              'cost_usd': None if execution.cost_unknown else 0,
              'cost_accounting': 'unavailable' if execution.cost_unknown else 'no_model_calls',
              'cached': False, 'model_weights_updated': False}
    result.update(source_generations=execution.source_generations,
                  proposal_count=execution.proposal_count, proposal_bytes=execution.proposal_size)
    if error:
        result['error'] = error
    return result
