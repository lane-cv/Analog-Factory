"""Explicit experimental SKY130 global-draw adaptation for immutable run inputs.

Preparation creates text in memory, never an installed-library mutation. Its
identity is evidence only: five successful registered simulator probes are still
required before a statistical batch can use it.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat


SEMANTICS = {'type': 'string', 'enum': ['installed', 'cache_global_fet_draws'],
             'default': 'installed',
             'description': 'Explicit experimental caching of global SKY130 FET draws; original installed semantics remain the default'}
MAX_FILES = 512
MAX_BYTES = 16 * 1024 * 1024
PREFIX = 'af_scripts/statistical_models'
WRAPPERS = ('.func af_cached_agauss(a,b,c) {agauss(a,b,c)}\n'
            '.func af_cached_gauss(a,b,c) {gauss(a,b,c)}\n')


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _read(path, root, maximum_bytes):
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError('Adapted models cannot follow symbolic links')
    path = path.resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError('Adapted model dependency is outside the configured PDK root')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum_bytes:
            raise ValueError('Adapted model dependency must be a bounded regular file')
        data = stream.read(maximum_bytes + 1)
    if len(data) > maximum_bytes:
        raise ValueError('Adapted model dependency exceeds the size limit')
    return path, data


def _directives(text):
    for index, line in enumerate(text.splitlines(keepends=True)):
        if re.match(r'^\s*\.(?:control|osdi|hdl)\b', line, re.I):
            raise ValueError('Adapted models cannot contain external code or control commands')
        if not re.match(r'^\s*\.(?:include|inc|lib)\b', line, re.I):
            continue
        words = shlex.split(line)
        if words[0].lower() == '.lib' and len(words) == 2:
            continue
        if len(words) not in ((2, 3) if words[0].lower() == '.lib' else (2,)):
            raise ValueError('Adapted models require literal single-line include paths')
        if any(char in words[1] for char in '$`{}\n\r\x00'):
            raise ValueError('Adapted model include path is dynamic or unsupported')
        yield index, words


def _cache_parameters(text):
    if re.search(r'^\s*\.(?:subckt|ends|include|inc|lib|control|func)\b', text, re.I | re.M):
        raise ValueError('Global FET parameters must be a flat parameter file')
    if 'af_cached_' in text.lower():
        raise ValueError('Global draw wrapper name conflicts with an existing parameter')
    lines, count = [], 0
    for line in text.splitlines():
        if re.match(r'^\s*\.param\b', line, re.I) and re.search(r'\b(?:agauss|gauss)\s*\(', line, re.I):
            lhs, rhs = line.split('=', 1)
            if ';' in rhs or '$' in rhs or '=' in rhs:
                raise ValueError('Unsupported global random parameter expression')
            if re.search(r'\b(?:MC_MM_SWITCH|mismatch_factor)\b',rhs,re.I) or re.search(r'\bsw_mm_',lhs,re.I):
                raise ValueError('Global draw adaptation cannot rewrite mismatch expressions')
            rhs = rhs.replace('{', '(').replace('}', ')')
            rhs = re.sub(r'\b(agauss|gauss)\s*\(', lambda match: 'af_cached_' + match[1].lower() + '(', rhs, flags=re.I)
            line = lhs + '= {' + rhs.strip() + '}'
            count += 1
        lines.append(line)
    if not count:
        raise ValueError('No supported top-level global FET random parameters found')
    return WRAPPERS + '\n'.join(lines) + '\n', count


def prepare_global_models(library, pdk_root, *, maximum_bytes=MAX_BYTES):
    root = Path(pdk_root).expanduser().resolve(strict=True)
    library = Path(library)
    parameter_file = (library.parent / 'continuous/parameters_fet_tt.spice').resolve(strict=True)
    pending, sources, directives, total = [library], {}, {}, 0
    while pending:
        original = pending.pop()
        path, data = _read(original, root, maximum_bytes)
        if path in sources:
            continue
        total += len(data)
        if len(sources) >= MAX_FILES or total > maximum_bytes:
            raise ValueError('Adapted model closure exceeds its file or byte limit')
        try:
            text = data.decode('utf-8')
        except UnicodeError as error:
            raise ValueError('Adapted model dependency must be UTF-8 SPICE text') from error
        entries = []
        for index, words in _directives(text):
            target = Path(words[1])
            target = target if target.is_absolute() else path.parent / target
            _read(target, root, maximum_bytes)  # Reject unsafe/missing references before returning any bundle.
            pending.append(target)
            entries.append((index, words, target.resolve(strict=True)))
        sources[path] = data
        directives[path] = entries
    library = library.resolve(strict=True)
    if parameter_file not in sources:
        raise ValueError('Library does not include the supported global FET parameter file')
    paths = {path: f'{PREFIX}/{index:04d}.spice' for index, path in enumerate(sorted(sources))}
    files, count = {}, 0
    for path, data in sources.items():
        text = data.decode('utf-8')
        if path == parameter_file:
            text, count = _cache_parameters(text)
        if directives[path]:
            lines = text.splitlines(keepends=True)
            for index, words, target in directives[path]:
                relative = os.path.relpath(paths[target], Path(paths[path]).parent)
                suffix = ' ' + words[2] if len(words) == 3 else ''
                lines[index] = f'{words[0]} "{relative}"{suffix}\n'
            text = ''.join(lines)
        files[paths[path]] = text
    if sum(len(text.encode()) for text in files.values()) > maximum_bytes:
        raise ValueError('Adapted generated models exceed the byte limit')
    identity = {'semantics': 'cache_global_fet_draws', 'experimental': True,
                'algorithm': 'sky130-global-fet-cache-v1', 'implementation_sha256': _hash(Path(__file__).read_bytes()),
                'source_dependencies': {str(path): _hash(data) for path, data in sorted(sources.items())},
                'generated_hashes': {name: _hash(text.encode()) for name, text in sorted(files.items())},
                'cached_parameter_count': count, 'library': paths[library]}
    identity['sha256'] = _hash(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode())
    return {'library': paths[library], 'generated_files': files, 'identity': identity}
