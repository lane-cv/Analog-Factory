"""Version dispatch and backwards-compatible schema-v1 real-simulation tuning."""
import copy
import hashlib
import math
import re
import time

SUPPORTED_CHAIN = ['specification', 'parameter', 'simulate', 'measure', 'condition', 'report']


def finite(value, name):
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise ValueError(f'{name} must be a finite number') from None
    if not math.isfinite(number):
        raise ValueError(f'{name} must be a finite number')
    return number


def validate_workflow(workflow: dict) -> dict:
    if isinstance(workflow, dict) and workflow.get('schema_version') == 2:
        from .workflow_graph import validate_graph
        return validate_graph(workflow)
    if not isinstance(workflow, dict) or workflow.get('schema_version') != 1:
        raise ValueError('Workflow schema_version must be 1')
    flow = copy.deepcopy(workflow)
    nodes, edges = flow.get('nodes', []), flow.get('edges', [])
    if len(nodes) != 6 or len(edges) != 5:
        raise ValueError('Executable workflow requires six connected tuning nodes; other graphs are planned')
    by_id = {n['id']: n for n in nodes}
    if len(by_id) != len(nodes):
        raise ValueError('Node identifiers must be unique')
    incoming, outgoing = {}, {}
    for edge in edges:
        a, b = edge.get('source'), edge.get('target')
        if a not in by_id or b not in by_id or a in outgoing or b in incoming:
            raise ValueError('Tuning graph must be a single connected chain')
        incoming[b], outgoing[a] = a, b
    roots = [n for n in by_id if n not in incoming]
    if len(roots) != 1:
        raise ValueError('Tuning graph requires one start and no unbounded cycles')
    sequence, current = [], roots[0]
    while current is not None:
        if current in sequence:
            raise ValueError('Unbounded graph cycle')
        sequence.append(current)
        current = outgoing.get(current)
    if len(sequence) != len(nodes) or [by_id[i]['type'] for i in sequence] != SUPPORTED_CHAIN:
        raise ValueError('Supported execution order: ' + ' → '.join(SUPPORTED_CHAIN))
    limits = flow.get('limits', {})
    count = limits.get('iterations', 8)
    if isinstance(count, bool) or not isinstance(count, int) or not 3 <= count <= 100:
        raise ValueError('Iteration limit must be an integer from 3 to 100')
    runtime = finite(limits.get('runtime_seconds', 120), 'Runtime')
    if not 1 <= runtime <= 3600:
        raise ValueError('Runtime budget must be 1–3600 seconds')
    tuning = flow.get('tuning', {})
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', str(tuning.get('parameter', ''))):
        raise ValueError('Invalid SPICE parameter name')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', str(tuning.get('metric', ''))):
        raise ValueError('Invalid measurement name')
    low, high = finite(tuning.get('minimum'), 'Minimum'), finite(tuning.get('maximum'), 'Maximum')
    target, tolerance = finite(tuning.get('target'), 'Target'), finite(tuning.get('tolerance'), 'Tolerance')
    if low >= high or tolerance <= 0:
        raise ValueError('Parameter minimum must be below maximum and tolerance must be positive')
    flow['limits'] = {'iterations': count, 'runtime_seconds': runtime}
    flow['tuning'].update(minimum=low, maximum=high, target=target, tolerance=tolerance)
    return flow


def replace_parameter(content: str, name: str, value: float) -> str:
    value = finite(value, 'Parameter value')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', name):
        raise ValueError('Invalid parameter name')
    pattern = re.compile(r'(?im)^(\s*\.param\s+[^\n]*?\b' + re.escape(name) + r'\s*=\s*)(\{[^}]*\}|\S+)')
    matches = list(pattern.finditer(content))
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one .param definition for {name} in the selected testbench')
    return pattern.sub(lambda match: match[1] + format(value, '.12g'), content)


def invoke_engine(workspace, project_id, source, operation_id, parameters=None):
    """A confirmed parent job delegates its EDA work through the shared policy."""
    from .operations import InvocationContext
    result = workspace.operations.invoke(operation_id, {'project_id': project_id, **(parameters or {})},
        context=InvocationContext(principal=source + '-engine', source=source, project_id=project_id,
                                  permissions=frozenset({'read', 'execute'}), confirmed=True))
    if not result['ok'] and not (result.get('task_id') and isinstance(result.get('data'), dict)):
        raise ValueError(result['error']['message'])
    return result['data']


def execute_tuning(workspace, project_id, workflow, job, cancel, update):
    if isinstance(workflow, dict) and workflow.get('schema_version') == 2:
        from .workflow_graph import execute_graph
        return execute_graph(workspace, project_id, workflow, job, cancel, update)
    from .evaluation import evaluate_metric
    flow = validate_workflow(workflow)
    tuning, limits = flow['tuning'], flow['limits']
    netlist = tuning.get('netlist', 'testbenches/pre.cir')
    source = invoke_engine(workspace, project_id, 'workflow', 'file.read', {'path': netlist})['content']
    project = workspace.projects.path(project_id)
    original = {f['path']: hashlib.sha256((project / f['path']).read_bytes()).hexdigest()
                for f in workspace.projects.files(project_id)}
    start = time.monotonic()
    deadline = start + limits['runtime_seconds']
    low, high = tuning['minimum'], tuning['maximum']
    low_error = high_error = None
    iterations = []
    best = None
    termination = 'iteration_limit'
    for index in range(limits['iterations']):
        if cancel.is_set():
            termination = 'cancelled'
            break
        if time.monotonic() >= deadline:
            termination = 'runtime_limit'
            break
        current = {f['path']: hashlib.sha256((project / f['path']).read_bytes()).hexdigest()
                   for f in workspace.projects.files(project_id)}
        if current != original:
            raise ValueError('Project sources changed during tuning; start a new workflow')
        value = low if index == 0 else high if index == 1 else (low + high) / 2
        deck = replace_parameter(source, tuning['parameter'], value)
        update(active_node='simulate', active_iteration=index + 1)
        run = invoke_engine(workspace, project_id, 'workflow', 'run.start', {
            'kind': 'simulate', 'options': {'netlist': netlist},
            'timeout': min(60, max(.1, deadline - time.monotonic())),
            'metadata': {'generated_files': {netlist: deck}, 'workflow_id': job['id'],
                        'conditions': {'parameter': tuning['parameter'], 'value': value}, 'iteration': index + 1}})
        while True:
            result = invoke_engine(workspace, project_id, 'workflow', 'run.get', {'run_id': run['id']})
            if result['status'] not in ('queued', 'running'):
                break
            if cancel.is_set() or time.monotonic() >= deadline:
                invoke_engine(workspace, project_id, 'workflow', 'run.cancel', {'run_id': run['id']})
            time.sleep(.025)
        if cancel.is_set():
            termination = 'cancelled'
            break
        metric = result.get('metrics', {}).get(tuning['metric'])
        score = evaluate_metric(metric, tuning)
        item = {'iteration': index + 1, 'run_id': run['id'], 'parameter': tuning['parameter'],
                'value': value, 'metric': metric, 'metric_name': tuning['metric'],
                'score': score, 'status': result['status']}
        iterations.append(item)
        update(iterations=iterations, active_run=run['id'], active_node='condition')
        if result['status'] != 'succeeded' or metric is None or result.get('stale'):
            raise ValueError(f'Iteration {index + 1} has no valid measurement; inspect run {run["id"]}')
        if best is None or score['absolute_error'] < best['score']['absolute_error']:
            best = item
        if score['satisfied']:
            termination = 'specification_satisfied'
            break
        error = metric - tuning['target']
        if index == 0:
            low_error = error
        elif index == 1:
            high_error = error
            if low_error * high_error > 0:
                termination = 'target_not_bracketed'
                break
        elif error * low_error > 0:
            low, low_error = value, error
        else:
            high, high_error = value, error
    return {'iterations': iterations, 'best': best, 'satisfied': bool(best and best['score']['satisfied']),
            'termination': termination, 'duration_seconds': time.monotonic() - start,
            'active_node': 'report', 'classification': 'circuit_parameter_optimization',
            'workflow': flow, 'model_weights_updated': False}
