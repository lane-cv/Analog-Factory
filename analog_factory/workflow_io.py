"""Bounded JSON/YAML interchange for the executable workflow schemas.

YAML is an optional serialization dependency. Anchors, aliases, explicit tags,
duplicate keys and non-JSON values are deliberately outside the accepted subset.
"""
import importlib
import json
import math
import re

MAX_BYTES = 2 * 1024 * 1024
MAX_DEPTH = 64
MAX_NODES = 50000


def yaml_module():
    try:
        return importlib.import_module('yaml')
    except ImportError:
        raise RuntimeError('YAML interchange requires the optional yaml extra: pip install "analog-factory[yaml]"') from None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str):
            raise ValueError('Workflow mapping keys must be strings')
        if key in result:
            raise ValueError(f'Duplicate workflow mapping key: {key}')
        result[key] = value
    return result


def _json_values(value):
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise ValueError('Workflow document exceeds the nesting or item limit')
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError('Workflow mapping keys must be strings')
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif item is None or isinstance(item, (str, bool, int)):
            pass
        elif isinstance(item, float) and math.isfinite(item):
            pass
        else:
            raise ValueError('Workflow documents must contain only finite JSON values')


def _validated(value):
    from .operation_catalog import WORKFLOW
    from .operation_schema import validate
    from .workflows import validate_workflow
    _json_values(value)
    validate(value, WORKFLOW, 'workflow')
    return validate_workflow(value)


def _yaml_load(content):
    yaml = yaml_module()
    depth = documents = count = 0
    try:
        # Inspect events before constructing objects; this prevents alias graphs
        # or deeply nested collections from reaching the recursive constructor.
        for event in yaml.parse(content, Loader=yaml.SafeLoader):
            count += 1
            if count > MAX_NODES:
                raise ValueError('Workflow YAML exceeds the item limit')
            if isinstance(event, yaml.events.AliasEvent) or getattr(event, 'anchor', None):
                raise ValueError('Workflow YAML anchors and aliases are not supported')
            if getattr(event, 'tag', None):
                raise ValueError('Workflow YAML explicit tags are not supported')
            if isinstance(event, yaml.events.DocumentStartEvent):
                documents += 1
                if documents > 1:
                    raise ValueError('Import exactly one workflow YAML document')
            if isinstance(event, (yaml.events.MappingStartEvent, yaml.events.SequenceStartEvent)):
                depth += 1
                if depth > MAX_DEPTH:
                    raise ValueError('Workflow YAML exceeds the nesting limit')
            elif isinstance(event, (yaml.events.MappingEndEvent, yaml.events.SequenceEndEvent)):
                depth -= 1

        class WorkflowLoader(yaml.SafeLoader):
            pass

        # Keep graph edge keys such as `on` as strings. Only true/false are
        # booleans, and date-looking input values remain ordinary strings.
        WorkflowLoader.yaml_implicit_resolvers = {
            key: [(tag, expression) for tag, expression in values
                  if tag not in ('tag:yaml.org,2002:bool', 'tag:yaml.org,2002:timestamp')]
            for key, values in yaml.SafeLoader.yaml_implicit_resolvers.items()}
        WorkflowLoader.add_implicit_resolver('tag:yaml.org,2002:bool',
                                            re.compile(r'^(?:true|false)$', re.I), list('tTfF'))

        def mapping(loader, node):
            return _pairs((loader.construct_object(key, deep=True), loader.construct_object(value, deep=True))
                          for key, value in node.value)

        WorkflowLoader.add_constructor('tag:yaml.org,2002:map', mapping)
        return yaml.load(content, Loader=WorkflowLoader)
    except yaml.YAMLError as error:
        raise ValueError(f'Invalid workflow YAML: {error}') from None


def parse_document(content, format='json'):
    if not isinstance(content, str) or len(content.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Workflow document must be text no larger than 2 MiB')
    if format == 'yaml':
        value = _yaml_load(content)
    elif format == 'json':
        def nonfinite(_):
            raise ValueError('Workflow JSON numbers must be finite')
        try:
            value = json.loads(content, object_pairs_hook=_pairs, parse_constant=nonfinite)
        except (RecursionError, json.JSONDecodeError) as error:
            raise ValueError(f'Invalid workflow JSON: {error}') from None
    else:
        raise ValueError('Workflow format must be json or yaml')
    return {'workflow': _validated(value), 'format': format}


def export_document(workflow, format='json'):
    flow = _validated(workflow)
    if format == 'yaml':
        content = yaml_module().safe_dump(flow, allow_unicode=True, sort_keys=False)
        content_type = 'application/yaml'
    elif format == 'json':
        content = json.dumps(flow, indent=2, ensure_ascii=False, allow_nan=False) + '\n'
        content_type = 'application/json'
    else:
        raise ValueError('Workflow format must be json or yaml')
    if len(content.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Exported workflow exceeds 2 MiB')
    return {'content': content, 'content_type': content_type, 'filename': f'analog-factory-workflow.{format}'}
