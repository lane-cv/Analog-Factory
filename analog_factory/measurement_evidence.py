"""Conservative ordinary .measure evidence from the effective input closure.

This is deliberately not a SPICE interpreter. Unsupported directives, scoped or
expression-based models, and mutable external includes cannot establish ordinary
comparison evidence. No source is changed. Runtime temperature is read from the
simulator report, including when the deck leaves it at the simulator default.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
from pathlib import Path
import re
import shlex

_NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
_SCALES = {'':1, 't':1e12, 'g':1e9, 'meg':1e6, 'k':1e3, 'm':1e-3, 'u':1e-6, 'n':1e-9, 'p':1e-12, 'f':1e-15}


def simulator_identity(executable):
    """Hash the actual executable, following only transparent static launchers."""
    binaries = {}
    for _ in range(4):
        executable = Path(executable).resolve()
        data = executable.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        binaries[str(executable)] = digest
        if not data.startswith(b'#!'):
            return {'sha256':digest, 'binaries':binaries}
        lines = [line.strip() for line in data.decode().splitlines()
                 if line.strip() and not line.lstrip().startswith('#')]
        words = shlex.split(lines[0]) if len(lines) == 1 else []
        if (len(words) != 3 or words[0] != 'exec' or words[2] != '$@'
                or not Path(words[1]).is_absolute() or any(char in words[1] for char in '$`{}')):
            raise ValueError('A transparent launcher must exec one explicit absolute target with "$@"')
        executable = words[1]
    raise ValueError('Simulator launcher chain is too deep')


def _number(value, parameters=None):
    value = value.strip().lower()
    if re.fullmatch(r'\{[a-z_]\w*\}', value):
        return (parameters or {})[value[1:-1]]
    match = re.fullmatch(f'({_NUMBER})(meg|[tgkmunpf])?', value)
    if not match:
        raise ValueError('Not a literal SPICE number')
    result = float(match[1]) * _SCALES[match[2] or '']
    if not math.isfinite(result):
        raise ValueError('Nonfinite number')
    return result


def _lines(data):
    logical = []
    for number, line in enumerate(data.splitlines(), 1):
        text = line.strip()
        if text.startswith('+') and logical:
            logical[-1][1] += ' ' + text[1:].strip()
        else:
            logical.append([number, text])
    return logical


def _canonical(text):
    return re.sub(r'\s+', ' ', text.strip()).lower()


def _definition_signature(definition):
    # A direct dependency signature composes earlier dependency signatures,
    # retaining transitive semantics without expanding a shared measurement DAG.
    semantic = {key:value for key,value in definition.items()
                if key not in {'path', 'line', 'limitations'}}
    return hashlib.sha256(json.dumps(semantic, sort_keys=True).encode()).hexdigest()


def _unit_expression(expression, definitions):
    """Only dimensionally unambiguous products/ratios/sums of known measures.

    Numeric scaling is left unknown: a naked coefficient can carry a physical
    unit that cannot be recovered from arbitrary user text.
    """
    tree = ast.parse(expression, mode='eval')
    def visit(node):
        if isinstance(node, ast.Name):
            unit = definitions[node.id]['units']
            return {'V':(1,0), 'A':(0,1), 'W':(1,1), '1':(0,0)}[unit]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return visit(node.operand)
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Mult):
                return tuple(a+b for a,b in zip(left,right))
            if isinstance(node.op, ast.Div):
                return tuple(a-b for a,b in zip(left,right))
            if isinstance(node.op, (ast.Add, ast.Sub)) and left == right:
                return left
        raise ValueError('Unqualified expression')
    return {(1,0):'V', (0,1):'A', (1,1):'W', (0,0):'1'}[visit(tree.body)]


def collect(snapshot: Path, netlist: str, dependencies: dict) -> dict:
    """Called after overrides/aliases, before launching the process."""
    evidence = {'schema_version':1, 'complete':False, 'limitations':[], 'conditions':{},
                'definitions':{}, 'source_hashes':dict(dependencies['provenance'].get('input_files', {}))}
    limitations = evidence['limitations']
    if not dependencies['provenance'].get('complete'):
        limitations.append('The executed SPICE dependency closure is incomplete.')
    if dependencies['provenance'].get('repeated_dependencies'):
        limitations.append('Repeated or recursive ordinary includes cannot be qualified.')
    rows = []
    for path, digest in evidence['source_hashes'].items():
        data = (snapshot / path).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            limitations.append('An executed source changed during evidence collection.')
        ended = False
        for line, text in _lines(data.decode(errors='replace')):
            if not text or text.startswith('*') or (path == netlist and line == 1):
                continue
            # Inline SPICE comments are removed; semicolon dialects stay unsupported.
            text = re.split(r'\s+\$', text, maxsplit=1)[0].strip()
            if ended or (text.lower() == '.end' and path != netlist):
                limitations.append('Deck end boundaries cannot be resolved unambiguously.')
            if text.lower() == '.end':
                ended = True
            rows.append((path, line, text))
    parameters, models, sources, analyses, temperatures, nominals, devices = {}, {}, {}, {}, [], [], set()
    measurements = []
    for path, line, text in rows:
        if not text.lower().startswith('.param '):
            continue
        try:
            assignments = re.findall(r'([a-z_]\w*)\s*=\s*(\S+)', text[7:], re.I)
            rest = re.sub(r'([a-z_]\w*)\s*=\s*(\S+)', '', text[7:], flags=re.I).strip()
            if not assignments or rest:
                raise ValueError()
            for name, raw in assignments:
                name = name.lower()
                if name in parameters:
                    raise ValueError()
                parameters[name] = _number(raw)
        except (ValueError, KeyError):
            limitations.append('Parameter definitions are conflicting or cannot be resolved statically.')
    for path, line, text in rows:
        normalized = _canonical(text)
        words = normalized.split()
        first = words[0]
        try:
            if first in {'.include','.inc','.end','.param','.save'}:
                continue
            if first in {'.temp','.option','.options'}:
                if first == '.temp':
                    if len(words) != 2:
                        raise ValueError()
                    temperatures.append(_number(words[1], parameters))
                else:
                    assignments = re.findall(r'(temp|tnom)\s*=\s*(\S+)', normalized[len(first):])
                    rest = re.sub(r'(temp|tnom)\s*=\s*(\S+)', '', normalized[len(first):]).strip()
                    if not assignments or rest:
                        raise ValueError()
                    for name, raw in assignments:
                        (temperatures if name == 'temp' else nominals).append(_number(raw, parameters))
            elif first == '.model':
                match = re.fullmatch(r'\.model\s+(\w+)\s+(nmos|pmos)\s*\(([^()]+)\)', normalized)
                if not match or match[1] in models:
                    raise ValueError()
                values = {}
                for assignment in match[3].split():
                    name, raw = assignment.split('=')
                    if name in values:
                        raise ValueError()
                    values[name] = _number(raw)  # model expressions/parameters intentionally unsupported
                if values.get('level') != 1:
                    raise ValueError()
                models[match[1]] = {'type':match[2], 'parameters':values}
            elif first in {'.tran','.ac','.dc','.op'}:
                analysis = first[1:]
                if analysis in analyses:
                    raise ValueError()
                args = words[1:]
                if analysis == 'tran' and 2 <= len(args) <= 4:
                    values = [_number(raw, parameters) for raw in args]
                elif analysis == 'ac' and len(args) == 4 and args[0] in {'dec','oct','lin'}:
                    values = [args[0], *[_number(raw, parameters) for raw in args[1:]]]
                elif analysis == 'dc' and len(args) == 4:
                    values = [args[0], *[_number(raw, parameters) for raw in args[1:]]]
                elif analysis == 'op' and not args:
                    values = []
                else:
                    raise ValueError()
                analyses[analysis] = {'type':analysis, 'parameters':{'arguments':values}}
            elif first in {'.meas','.measure'}:
                match = re.fullmatch(r'\.meas(?:ure)?\s+(tran|ac|dc|op)\s+(\w+)\s+(.+)', normalized)
                if not match:
                    raise ValueError()
                measurements.append((match[2], {'path':path,'line':line,'text':normalized,
                                                'analysis':match[1]}, match[3]))
            elif first.startswith('.'):
                raise ValueError()
            else:
                if first in devices:
                    raise ValueError()
                devices.add(first)
                if first[0] in {'v','i'} and len(words) >= 4:
                    raw = ' '.join(words[3:])
                    # Resolve scalar parameter substitutions before recording source semantics.
                    raw = re.sub(r'\{([a-z_]\w*)\}', lambda m: str(parameters[m[1]]), raw)
                    pattern = rf'(?:dc\s+)?({_NUMBER}(?:meg|[tgkmunpf])?)(?:\s+(sin|pulse)\(([^()]+)\))?(?:\s+ac\s+({_NUMBER}(?:meg|[tgkmunpf])?)(?:\s+({_NUMBER}))?)?'
                    match = re.fullmatch(pattern, raw)
                    if not match:
                        raise ValueError()
                    waveform = None
                    if match[2]:
                        values = [_number(part) for part in re.split(r'[ ,]+',match[3].strip())]
                        if not (3 <= len(values) <= 6 if match[2] == 'sin' else len(values) == 7):
                            raise ValueError()
                        waveform = {'type':match[2], 'arguments':values}
                    sources[first] = {'nodes':words[1:3], 'dc':_number(match[1]), 'waveform':waveform,
                                      'ac':[_number(match[4]),_number(match[5] or '0')] if match[4] else None}
                    if first in {'vdd','vcc','vsupply'} and waveform:
                        raise ValueError()
                elif first[0] in {'r','c','l'} and len(words) == 4:
                    _number(words[3], parameters)
                elif first[0] == 'm' and len(words) >= 8:
                    # Only ordinary four-terminal devices using the validated inline model.
                    for assignment in words[6:]:
                        name, raw = assignment.split('=')
                        if name not in {'w','l','m','ad','as','pd','ps','nrd','nrs'}:
                            raise ValueError()
                        _number(raw, parameters)
                else:
                    raise ValueError()
        except (ValueError, KeyError, OverflowError):
            limitations.append(f'Unsupported or conflicting SPICE evidence at {path}:{line}.')
    for path, line, text in rows:
        words = text.lower().split()
        if words[0].startswith('m') and (len(words) < 6 or words[5] not in models):
            limitations.append('A device model cannot be resolved to a supported inline model.')
    if len(temperatures) > 1 or len(nominals) > 1:
        limitations.append('Temperature directives are conflicting or repeated.')
    if len(analyses) != 1:
        limitations.append('A unique analysis configuration is required.')
    if temperatures:
        evidence['declared_temperature_c'] = temperatures[0]
    if nominals:
        evidence['declared_nominal_temperature_c'] = nominals[0]
    static_voltages = {name:source['dc'] for name,source in sources.items()
                       if name.startswith('v') and source['nodes'][1] == '0' and source['waveform'] is None}
    conditions = evidence['conditions']
    conditions.update(dc_sources=static_voltages, sources=sources,
                      model_library={'kind':'inline_level_1' if models else 'no_semiconductor_models',
                                     'sha256':hashlib.sha256(json.dumps(models,sort_keys=True).encode()).hexdigest()},
                      corner=None)
    evidence['corner_provenance'] = {'status':'not_applicable',
        'reason':'The fully inspected deck uses inline level-1 models or passive devices; no process library or corner is selected.'}
    supplies = [static_voltages[name] for name in ('vdd','vcc','vsupply') if name in static_voltages]
    if len(supplies) == 1:
        conditions['supply_v'] = supplies[0]
    if not static_voltages:
        limitations.append('No unambiguous static voltage source establishes supply evidence.')
    if len(analyses) == 1:
        conditions['analysis'] = next(iter(analyses.values()))
    definitions = evidence['definitions']
    expressions, duplicate_names = [], set()
    for name, definition, body in measurements:
        if name in definitions:
            limitations.append('Measurement names are conflicting or repeated.')
            duplicate_names.add(name)
        if name in parameters:
            limitations.append('Parameter and measurement names conflict.')
        definitions[name] = definition
        if definition['analysis'] not in analyses:
            limitations.append('A measurement has no matching analysis configuration.')
        direct = re.fullmatch(r'(avg|pp|min|max|rms|find)\s+([vi])\(([a-z0-9_.,+!:-]+)\)(.*)',body)
        expression = re.fullmatch(r"param\s*=\s*['\"]([^'\"]+)['\"]", body)
        if direct:
            tail = direct[4].strip()
            arguments = re.findall(r'(from|to|at)\s*=\s*(\S+)', tail)
            rest = re.sub(r'(from|to|at)\s*=\s*(\S+)', '', tail).strip()
            try:
                if rest or len({key for key,_ in arguments}) != len(arguments):
                    raise ValueError()
                resolved_arguments = {key:_number(raw, parameters) for key,raw in arguments}
                if direct[1] == 'find' and [key for key,_ in arguments] != ['at']:
                    raise ValueError()
                if direct[1] != 'find' and any(key == 'at' for key,_ in arguments):
                    raise ValueError()
                definition['units'] = 'V' if direct[2] == 'v' else 'A'
                definition['operation'] = direct[1]
                definition['vector'] = direct[2] + '(' + direct[3] + ')'
                definition['arguments'] = resolved_arguments
            except (ValueError, KeyError):
                pass
        elif expression:
            expressions.append((name, expression[1]))
    for name, expression in expressions:
        if name in duplicate_names:
            continue
        try:
            definitions[name]['units'] = _unit_expression(expression,definitions)
            # Referenced measures are part of the derived measurement definition.
            definitions[name]['dependencies'] = {node.id:_definition_signature(definitions[node.id]) for node in ast.walk(ast.parse(expression)) if isinstance(node,ast.Name)}
        except (ValueError, KeyError, SyntaxError, RecursionError):
            pass
    # Shipped educational power is a documented voltage coefficient, not a rule
    # for arbitrary numeric-current products. Verify the actual template measure
    # contract and inline model, plus the actual constant source voltage.
    from .templates import PRE
    standard = {match[1]:_canonical(match[0]) for match in re.finditer(r'^\.meas\s+tran\s+(\w+)\s+.*$',PRE,re.M)}
    if (all(definitions.get(name,{}).get('text') == text for name,text in standard.items())
            and models.get('afnmos',{}).get('parameters',{}).get('level') == 1
            and static_voltages.get('vdd') == 3.3):
        definitions['power']['units'] = 'W'
        definitions['power']['contract'] = 'educational_constant_supply_mean_power_v1'
        definitions['power']['dependencies'] = {'supply_current':_definition_signature(definitions['supply_current'])}
    for definition in definitions.values():
        if 'units' not in definition:
            definition['limitations'] = ['Measurement units or expression semantics cannot be established from the executed source.']
    evidence['limitations'] = list(dict.fromkeys(limitations))
    evidence['complete'] = not evidence['limitations']
    if not evidence['complete']:
        evidence['corner_provenance'] = {'status':'unknown', 'reason':'The executed source closure is not fully qualified.'}
        conditions.pop('corner', None)
    return evidence


def finalize(evidence, snapshot, log, declared_conditions):
    """Confirm snapshot integrity and actual finite runtime TEMP/TNOM evidence."""
    limitations = evidence['limitations']
    if evidence.get('simulator_path'):
        try:
            identity = simulator_identity(evidence['simulator_path'])
        except (OSError, ValueError, UnicodeError):
            identity = None
        if identity != evidence['conditions'].get('simulator_identity'):
            limitations.append('The simulator executable changed during execution.')
    for path, digest in evidence['source_hashes'].items():
        try:
            current = hashlib.sha256((snapshot / path).read_bytes()).hexdigest()
        except OSError:
            current = None
        if current != digest:
            limitations.append('An executed source changed after evidence collection.')
    matches = re.findall(r'^\s*Doing analysis at TEMP\s*=\s*(' + _NUMBER + r')\s+and TNOM\s*=\s*(' + _NUMBER + r')\s*$',log,re.M | re.I)
    temperatures = {(float(a),float(b)) for a,b in matches}
    reports = re.findall(r'^\s*Doing analysis at TEMP\b.*$', log, re.M | re.I)
    if len(reports) == len(matches) and len(temperatures) == 1 and all(math.isfinite(value) for pair in temperatures for value in pair):
        temperature, nominal = next(iter(temperatures))
        evidence['conditions'].update(temperature_c=temperature, nominal_temperature_c=nominal)
        evidence['temperature_provenance'] = {'source':'ngspice.log runtime TEMP/TNOM report', 'reports':len(matches)}
        for key, actual in [('declared_temperature_c',temperature),('declared_nominal_temperature_c',nominal)]:
            if key in evidence and evidence[key] != actual:
                limitations.append('Runtime temperature conflicts with the executed source.')
    else:
        limitations.append('Unique finite runtime temperature evidence is missing.')
    for key, value in declared_conditions.items():
        if key in evidence['conditions'] and value != evidence['conditions'][key]:
            limitations.append('Declared simulation conditions conflict with executed source evidence.')
    evidence['limitations'] = list(dict.fromkeys(limitations))
    evidence['complete'] = not evidence['limitations']
