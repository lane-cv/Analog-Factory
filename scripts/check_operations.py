#!/usr/bin/env python3
"""Validate the registry and generate/check its public reference and tool schemas.

This detects transport/business bypasses in application entry points and records
UI action declarations. Dynamic DOM behavior and embedded native tools require
browser/integration tests; this checker does not claim semantic completeness.
"""
import argparse
import ast
import hashlib
import inspect
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from analog_factory.service import Workspace
from analog_factory.operation_catalog import LEGACY_ROUTES
from analog_factory.operation_schema import validate


# Adding a first-party controller requires declaring its owner here and reviewing
# the generated inventory. This is a boundary declaration, not a UI test waiver.
FRONTEND_BOUNDARIES = {
    'agent-cli-controls.js': 'Project CLI configuration drafts, guarded settings and official release observations',
    'app.js': 'Workspace selection, editor, workflow and provider forms',
    'agents.js': 'Agent conversations, stream display, attachments and human scope drafts',
    'agent-content.js': 'Escaped conversation rendering helper',
    'drc-navigation.js': 'Registered DRC report selection and human native Magic focus controls',
    'characterization.js': 'Registered OTA characterization drafts, PVT execution and raw measurement evidence',
    'editor-recovery.js': 'Browser-owned draft recovery and version-pinned conflict inspection; registered file writer remains authoritative',
    'native-canvas.js': 'Acknowledged semantic commands for owned Xschem and Magic sessions',
    'graph-controls.js': 'Graph node forms and registered live execution controls',
    'layout.js': 'Local layout geometry and acknowledged semantic layout commands',
    'native-terminal.js': 'Human-only registered PTY byte and lifecycle controls',
    'operations.js': 'Shared browser HTTP transport, discovery and acknowledgement owner',
    'preferences.js': 'Local theme/language controller and shared settings transport',
    'project-controls.js': 'Project/file lifecycle forms and registered lifecycle operations',
    'provider-fields.js': 'Capability-aware provider form helper; no independent transport',
    'simulation-batches.js': 'Bounded scenario, qualification and evidence controller',
    'training-controls.js': 'Taskset, training, evaluation, resume and replay controller',
    'waveform-view.js': 'Waveform numerical/display helper; no business transport',
    'i18n.js': 'Translation data and text rendering; not an operation controller',
    'xterm.js': 'Pinned third-party terminal renderer; source hashes verified separately',
}
SOURCE_READS = {'workspace.projects.path': 'Confined project root for internal snapshots',
                'workspace.projects.files': 'Source enumeration for version/conflict checks'}
JOB_SERVICE = {'workspace.config': 'Effective configuration for capability or conflict checks',
               'workspace.jobs.get': 'Owned orchestration record inspection',
               'workspace.jobs.list': 'Prevent concurrent jobs within the project',
               'workspace.jobs.start': 'Own job supervision; child business actions still use registry'}
PYTHON_ENTRY_POINTS = {
    'server.py': {'workspace._operations.close': 'Service shutdown', 'workspace.agents.close': 'Service shutdown'},
    'operation_http.py': {}, 'agent_api.py': SOURCE_READS,
    '__main__.py': {'workspace._operations.close': 'CLI shutdown', 'workspace.agents.close': 'CLI shutdown'},
    'workflows.py': SOURCE_READS, 'workflow_graph.py': SOURCE_READS,
    'evaluation.py': SOURCE_READS,
    'simulation_batches.py': {**SOURCE_READS, **JOB_SERVICE},
    'training/environment.py': SOURCE_READS,
    'training/configurable_environment.py': SOURCE_READS,
    'training/manager.py': {**SOURCE_READS, **JOB_SERVICE,
                            'workspace.runs': 'Inspect owned trainer subprocesses for cancellation'},
    'training/resume.py': SOURCE_READS,
}
CLIENT_CALL = re.compile(r'\b(?:window\.)?(?:AFOperations|operations|api)\.(?:invoke|dispatch|describe)\(\s*')


def declarations():
    result = {}
    for path in sorted((ROOT / 'analog_factory/static').glob('*.js')):
        if path.name in ('i18n.js', 'xterm.js'):
            continue
        text = path.read_text()
        # Action attributes may appear in templates; only their semantic names are
        # tracked. Browser tests verify controller effects, not this text scan.
        namespaces = {}
        for attribute, name in re.findall(r'(data-(?:[a-z-]+-)?action)=[\\"\']?([a-z][a-z0-9_-]+)', text):
            namespaces.setdefault(attribute, set()).add(name)
        names = sorted(set().union(*namespaces.values())) if namespaces else []
        cases = sorted(set(re.findall(r"case\s+['\"]([^'\"]+)['\"]\s*:", text)))
        literals, dynamic = set(), 0
        for match in CLIENT_CALL.finditer(text):
            value = re.match(r"['\"]([a-z][a-z0-9_.]+)['\"]", text[match.end():])
            if value:
                literals.add(value[1])
            else:
                dynamic += 1
        modules = sorted(set(re.findall(r"\b(?:window\.)?(?:AFOperations|operations)\.register\(\s*['\"]([a-z_]+)['\"]", text)))
        result[path.name] = {'owner': FRONTEND_BOUNDARIES.get(path.name, 'UNCLASSIFIED'),
            'action_attributes': names, 'action_namespaces': {key:sorted(values) for key,values in sorted(namespaces.items())},
            'data_attributes': sorted(set(re.findall(r'\bdata-[a-z][a-z0-9-]*', text))),
            'event_types': sorted(set(re.findall(r"\.addEventListener\(\s*['\"]([^'\"]+)['\"]", text))),
            'switch_cases': cases, 'literal_operations': sorted(literals),
            'dynamic_operation_calls': dynamic, 'registered_modules': modules}
    return result


def check_entry_points():
    problems = []
    for path in (ROOT / 'analog_factory/static').glob('*.js'):
        if path.name not in FRONTEND_BOUNDARIES:
            problems.append(f'{path.name}: classify the new frontend module and review its operation coverage')
        if path.name == 'xterm.js':
            manifest = path.with_name('xterm-source.json')
            expected = json.loads(manifest.read_text()).get('files', {}) if manifest.is_file() else {}
            if expected.get(path.name) != hashlib.sha256(path.read_bytes()).hexdigest():
                problems.append('xterm.js: vendored renderer hash differs from its source declaration')
            continue
        if path.name != 'operations.js' and re.search(r'\b(?:fetch|XMLHttpRequest|WebSocket)\s*\(', path.read_text()):
            problems.append(f'{path.name}: use AFOperations.request/invoke for application transport')
    # Shared services legitimately call their stores. These adapters must not.
    # The remaining direct calls are lifecycle cleanup or source confinement/
    # snapshot enumeration inside existing engines, never user business dispatch.
    for relative, allowed in PYTHON_ENTRY_POINTS.items():
        path = ROOT / 'analog_factory' / relative
        if not path.is_file():
            problems.append(f'{relative}: declared operation boundary file is missing')
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            callee = ast.unparse(node.func)
            if callee.startswith('self.workspace.'):
                callee = callee.removeprefix('self.')
            # Calls on a returned dict/path are not workspace service calls;
            # their inner workspace call is inspected separately by ast.walk.
            if '(' in callee:
                continue
            if (callee.startswith('workspace.') or callee.startswith('self.workspace.')) and not (
                callee.startswith('workspace.operations.') or callee in allowed):
                problems.append(f'{relative}:{node.lineno}: unregistered business invocation {callee}')
    return problems


def check_client_operations(registry):
    problems = []
    for filename, record in declarations().items():
        for operation_id in record['literal_operations']:
            if operation_id not in registry.operations:
                problems.append(f'{filename}: literal operation is not registered: {operation_id}')
    return problems


def boundary_inventory(registry):
    handlers = []
    for operation_id, operation in sorted(registry.operations.items()):
        source = inspect.getsourcefile(operation.handler)
        handlers.append({'operation_id': operation_id, 'kind': operation.kind,
            'handler_file': Path(source).relative_to(ROOT).as_posix() if source and Path(source).is_relative_to(ROOT) else source,
            'handler_line': inspect.getsourcelines(operation.handler)[1] if source else None})
    return {'static_proof': 'declarations_only',
        'limitations': ['Lexical JavaScript scans do not resolve aliases, templates or dynamically chosen operation IDs',
                       'Python AST checks cover named workspace call chains in declared entry points, not whole-program data flow',
                       'Action declarations do not prove behavior; browser registry/handler parity and real integration tests are required',
                       'Third-party terminal rendering and native EDA canvas interactions are outside semantic operation coverage'],
        'frontend': dict(sorted(FRONTEND_BOUNDARIES.items())),
        'python_entry_points': {name: {'allowed_service_calls': calls} for name,calls in sorted(PYTHON_ENTRY_POINTS.items())},
        'registered_handlers': handlers}


def generated(registry):
    records = [op.describe() for _, op in sorted(registry.operations.items())]
    tools = [{'type': 'function', 'function': {'name': item['operation_id'].replace('.', '__'),
             'description': item['description'], 'parameters': item['input_schema']}} for item in records]
    reference = '# Operation reference\n\nGenerated by `python3 scripts/check_operations.py --write`. Do not edit by hand.\n\n'
    reference += '| Operation | Kind / owner | Permissions | Execution | Revision required |\n|---|---|---|---|---|\n'
    for op in records:
        owner = 'target browser' if op['kind'] == 'frontend' else 'Python service'
        reference += f"| `{op['operation_id']}` | {op['kind']} / {owner} | {', '.join(op['permissions'])} | {op['execution']} | {'yes' if op['versioned'] else 'no'} |\n"
    reference += '\nSee [registry.json](registry.json) for exact schemas, defaults, units, permissions, errors, side effects, and usage. '
    reference += '[agent-tools.json](agent-tools.json) contains generated function descriptions. Runtime discovery adds current availability.\n'
    return {'reference.md': reference,
            'registry.json': json.dumps(records, indent=2, ensure_ascii=False) + '\n',
            'agent-tools.json': json.dumps(tools, indent=2, ensure_ascii=False) + '\n',
            'ui-actions.json': json.dumps(declarations(), indent=2, ensure_ascii=False) + '\n',
            'boundary-inventory.json': json.dumps(boundary_inventory(registry), indent=2, ensure_ascii=False) + '\n'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='Update generated reference files after an intentional operation change')
    args = parser.parse_args()
    problems = check_entry_points()
    with tempfile.TemporaryDirectory() as directory:
        workspace = Workspace(Path(directory))
        registry = workspace.operations  # Registration validates IDs, schemas and declarations.
        problems.extend(check_client_operations(registry))
        for operation in registry.operations.values():
            try:
                inspect.signature(operation.handler).bind(None, {})
            except TypeError:
                problems.append(operation.operation_id + ': handler must accept (context, parameters)')
            if operation.kind == 'business' and set(operation.permissions) != {'read'} and not operation.side_effects:
                problems.append(operation.operation_id + ': declare mutation side effects')
            for example in operation.examples:
                try:
                    validate(example.get('parameters', example), operation.input_schema)
                except (ValueError, TypeError) as error:
                    problems.append(operation.operation_id + ': invalid usage example: ' + str(error))
        seen = set()
        for route in LEGACY_ROUTES:
            key = route[:2]
            if key in seen:
                problems.append('Duplicate compatibility route: ' + str(key))
            seen.add(key)
            if route[2] not in registry.operations:
                problems.append('Unregistered compatibility route: ' + route[2])
        for name, content in generated(registry).items():
            target = ROOT / 'docs/operations' / name
            if args.write:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            elif not target.is_file() or target.read_text() != content:
                problems.append(f'{target.relative_to(ROOT)} is stale; run python3 scripts/check_operations.py --write and review coverage changes')
        registry.close()
        workspace.agents.close()
        count = len(registry.operations)
    if problems:
        print('\n'.join(problems), file=sys.stderr)
        return 1
    print(f'Validated {count} registered operations, {len(seen)} compatibility routes, generated schemas, and adapter boundaries.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
