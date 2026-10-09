"""Small, explicit JSON Schema subset; no optional validation dependency."""
import copy
import math
import re

TYPES = {'object', 'array', 'string', 'integer', 'number', 'boolean', 'null'}
KEYS = {'type', 'properties', 'required', 'additionalProperties', 'items', 'enum', 'const',
        'default', 'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'minLength',
        'maxLength', 'pattern', 'minItems', 'maxItems', 'uniqueItems', 'anyOf', 'oneOf',
        'description', 'title', 'examples', 'format', 'units', 'unit', 'writeOnly', 'readOnly',
        '$schema', '$id'}


def check_schema(schema):
    if not isinstance(schema, dict) or set(schema) - KEYS:
        raise ValueError('Unsupported schema declaration')
    kind = schema.get('type')
    if kind is not None and (not isinstance(kind, str) or kind not in TYPES):
        raise ValueError('Schema type must be one supported JSON type')
    for rule in schema.get('properties', {}).values():
        check_schema(rule)
    if set(schema.get('required', [])) - set(schema.get('properties', {})):
        raise ValueError('Required property has no schema')
    if 'items' in schema:
        check_schema(schema['items'])
    extra = schema.get('additionalProperties')
    if isinstance(extra, dict):
        check_schema(extra)
    for key in ('anyOf', 'oneOf'):
        if key in schema:
            if not isinstance(schema[key], list) or not schema[key]:
                raise ValueError('Schema alternatives must be nonempty')
            for rule in schema[key]:
                check_schema(rule)
    if 'pattern' in schema:
        re.compile(schema['pattern'])
    if 'default' in schema:
        validate(schema['default'], {k: v for k, v in schema.items() if k != 'default'})


def validate(value, schema, path='parameters'):
    value = copy.deepcopy(value)
    for key in ('anyOf', 'oneOf'):
        if key in schema:
            successes = []
            for rule in schema[key]:
                try:
                    successes.append(validate(value, rule, path))
                except ValueError:
                    pass
            if not successes or (key == 'oneOf' and len(successes) != 1):
                raise ValueError(f'{path} does not match {key}')
            value = successes[0]
    kind = schema.get('type')
    valid = {'object': isinstance(value, dict), 'array': isinstance(value, list),
             'string': isinstance(value, str), 'integer': type(value) is int,
             'number': type(value) in (int, float) and math.isfinite(value),
             'boolean': type(value) is bool, 'null': value is None}
    if kind and not valid[kind]:
        raise ValueError(f'{path} must be {kind}')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(f'{path} must be one of the declared values')
    if 'const' in schema and value != schema['const']:
        raise ValueError(f'{path} must equal its declared constant')
    if isinstance(value, dict):
        props = schema.get('properties', {})
        for key, rule in props.items():
            if key not in value and 'default' in rule:
                value[key] = copy.deepcopy(rule['default'])
        if any(key not in value for key in schema.get('required', [])):
            raise ValueError(f'{path} missing required fields: ' + ', '.join(k for k in schema['required'] if k not in value))
        for key in value:
            if key in props:
                value[key] = validate(value[key], props[key], f'{path}.{key}')
            elif schema.get('additionalProperties') is False:
                raise ValueError(f'{path} contains undeclared field: {key}')
            elif isinstance(schema.get('additionalProperties'), dict):
                value[key] = validate(value[key], schema['additionalProperties'], f'{path}.{key}')
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', math.inf):
            raise ValueError(f'{path} has invalid item count')
        if schema.get('uniqueItems') and any(item in value[:i] for i, item in enumerate(value)):
            raise ValueError(f'{path} must contain unique items')
        value = [validate(item, schema.get('items', {}), f'{path}[{i}]') for i, item in enumerate(value)]
    if isinstance(value, str):
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', math.inf):
            raise ValueError(f'{path} has invalid length')
        if 'pattern' in schema and not re.search(schema['pattern'], value):
            raise ValueError(f'{path} has invalid format')
    if type(value) in (float, int):
        if not math.isfinite(value) or value < schema.get('minimum', -math.inf) or value > schema.get('maximum', math.inf):
            raise ValueError(f'{path} is outside the allowed range')
        if value <= schema.get('exclusiveMinimum', -math.inf) or value >= schema.get('exclusiveMaximum', math.inf):
            raise ValueError(f'{path} is outside the allowed range')
    return value
