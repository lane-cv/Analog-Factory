#!/usr/bin/env python3
"""Investigate random-parameter evaluation; never authorizes statistical batches.

All model changes occur in a new evidence directory. The installed PDK is read
only. The optional full-library comparison runs one process sample per variant;
the small dependency subset runs the five existing qualification conditions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analog_factory.simulation_batches import _probe_deck, _qualification_checks


PARAMETERS = 'continuous/parameters_fet_tt.spice'
SUBSET = (
    PARAMETERS,
    'continuous/parameters_res_nom.spice',
    'continuous/parameters_cap_nom.spice',
    'continuous/models_global.spice',
    'continuous/models_fet/sky130_fd_pr__nfet_01v8.spice',
    'continuous/models_fet/sky130_fd_pr__pfet_01v8.spice',
)
WRAPPERS = ('.func af_cached_agauss(a,b,c) {agauss(a,b,c)}\n'
            '.func af_cached_gauss(a,b,c) {gauss(a,b,c)}\n')
METRIC = re.compile(r'^\s*(n1|n2|p1|p2|va|vb)\s*=\s*([-+0-9.eE]+)\s*$', re.M)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def included_paths(path):
    for line in path.read_text().splitlines():
        if re.match(r'^\s*\.(?:include|inc)\s+', line, re.I):
            words = shlex.split(line)
            yield (path.parent / words[1]).resolve(strict=True)


def source_closure(paths, pdk):
    pending, found, total = list(paths), set(), 0
    while pending:
        path = pending.pop().resolve(strict=True)
        if path in found:
            continue
        if not path.is_relative_to(pdk):
            raise ValueError('Model include escapes the supplied SKY130 root')
        found.add(path)
        total += path.stat().st_size
        if len(found) > 10000 or total > 64 * 1024 * 1024:
            raise ValueError('Model source closure exceeds diagnostic bounds')
        pending.extend(included_paths(path))
    return sorted(found)


def cache_global_draws(text):
    """Hide only top-level FET random calls from ngspice's .param→.func pass.

    This experimental transformation is restricted to the known flat parameter
    file. Device mismatch calls in model subcircuits remain byte-identical.
    """
    if re.search(r'^\s*\.(?:subckt|include|inc|lib)\b', text, re.I | re.M):
        raise ValueError('Expected a flat top-level FET parameter file')
    lines, count = [], 0
    for line in text.splitlines():
        if re.match(r'^\s*\.param\b', line, re.I) and re.search(r'\b(?:agauss|gauss)\s*\(', line, re.I):
            lhs, rhs = line.split('=', 1)
            if ';' in rhs or '$' in rhs or '=' in rhs:
                raise ValueError('Unsupported parameter expression')
            rhs = rhs.replace('{', '(').replace('}', ')')
            rhs = re.sub(r'\b(agauss|gauss)\s*\(',
                         lambda match: 'af_cached_' + match[1].lower() + '(', rhs, flags=re.I)
            # ngspice expects a single brace-delimited expression when a macro
            # appears alongside the original corner term.
            line = lhs + '= {' + rhs.strip() + '}'
            count += 1
        lines.append(line)
    if not count:
        raise ValueError('No top-level random FET parameters found')
    return WRAPPERS + '\n'.join(lines) + '\n', count


def run_probe(directory, simulator, deck, seed, timeout):
    directory.mkdir()
    circuit = directory / 'circuit.cir'
    circuit.write_text(deck)
    commands = (['let va = v(a)', 'let vb = v(b)', 'print va', 'print vb']
                if 'V1 a 0' in deck else
                ['let n1 = -i(VDN1)', 'let n2 = -i(VDN2)',
                 'let p1 = i(VDP1)', 'let p2 = i(VDP2)',
                 'print n1', 'print n2', 'print p1', 'print p2'])
    driver = directory / 'driver.cir'
    driver.write_text('* seed before reading models\n.control\nset numdgt=15\n'
                      f'setseed {seed}\nsource circuit.cir\nrun\n'
                      + '\n'.join(commands) + '\nquit\n.endc\n.end\n')
    command = [str(simulator), '-n', '-b', 'driver.cir']
    started = time.monotonic()
    with (directory / 'simulator.log').open('w') as output:
        try:
            process = subprocess.run(command, cwd=directory, stdout=output,
                                     stderr=subprocess.STDOUT, timeout=timeout, check=False)
            status = 'succeeded' if process.returncode == 0 else 'failed'
            code = process.returncode
        except subprocess.TimeoutExpired:
            status, code = 'timed_out', None
    log = (directory / 'simulator.log').read_text()
    metrics = {match[1]: float(match[2]) for match in METRIC.finditer(log)}
    required = {'va', 'vb'} if 'V1 a 0' in deck else {'n1', 'n2', 'p1', 'p2'}
    if status == 'succeeded' and (set(metrics) != required or not all(map(math.isfinite, metrics.values()))):
        status = 'missing_or_invalid_measurements'
    return {'status': status, 'exit_code': code, 'seed': seed, 'metrics': metrics,
            'elapsed_seconds': round(time.monotonic() - started, 6), 'command': command,
            'directory': str(directory), 'circuit_sha256': digest(circuit),
            'driver_sha256': digest(driver), 'log_sha256': digest(directory / 'simulator.log')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--simulator', type=Path, required=True)
    parser.add_argument('--binary', type=Path, required=True,
                        help='Actual launcher target, retained and hashed separately')
    parser.add_argument('--output', type=Path, required=True,
                        help='New disposable evidence directory; existing paths are rejected')
    parser.add_argument('--full-library', action='store_true',
                        help='Also compare one mc sample of each full library (can take minutes)')
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 180:
        parser.error('timeout must be between 1 and 180 seconds per process')
    library, simulator, binary = (path.resolve(strict=True)
                                  for path in (args.library, args.simulator, args.binary))
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    source_root = library.parent
    parameter_text = (source_root / PARAMETERS).read_text()
    cached_text, count = cache_global_draws(parameter_text)
    cached = output / 'cached-parameters.spice'
    cached.write_text(cached_text)
    source_files = sorted(source_root.rglob('*.spice')) if args.full_library else [source_root / item for item in SUBSET]
    source_hashes = {str(path): digest(path) for path in source_closure(source_files, source_root.parents[1])}
    source_hashes[str(library)] = digest(library)
    record = {'purpose': 'controlled parser/model diagnosis; no product qualification certificate',
              'scope': '27 C, 1.8 V, two SKY130 01v8 families at fixed probe dimensions',
              'source_sha256': source_hashes, 'script_sha256': digest(__file__),
              'qualification_logic_sha256': digest(Path(__file__).resolve().parents[1] / 'analog_factory/simulation_batches.py'),
              'simulator': {'launcher': str(simulator), 'launcher_sha256': digest(simulator),
                            'binary': str(binary), 'binary_sha256': digest(binary)},
              'cached_parameter_count': count, 'cached_parameter_sha256': digest(cached),
              'cases': {}, 'checks': {}}
    version = subprocess.run([str(simulator), '-n', '--version'], capture_output=True,
                             text=True, timeout=10, check=False)
    record['simulator']['version'] = version.stdout + version.stderr
    # This independent two-source reproducer separates simulator behavior from
    # transistor physics and library switching.
    for variant in ('direct', 'cached'):
        expression = 'agauss(1,.1,1)' if variant == 'direct' else 'af_cached_agauss(1,.1,1)'
        semantic = ('* independent random reuse probe\n' + (WRAPPERS if variant == 'cached' else '')
                    + f'.param global_draw = {expression}\nV1 a 0 {{global_draw}}\nV2 b 0 {{global_draw}}\n.op\n.end\n')
        rows = [run_probe(output / f'semantics-{variant}-{index}', simulator, semantic, seed, args.timeout)
                for index, seed in enumerate((41, 41, 42))]
        record['cases']['semantics_' + variant] = rows
    for variant in ('direct', 'cached'):
        rows = []
        for index, (section, seed) in enumerate((('tt', 41), ('tt', 42), ('mc', 41), ('mc', 41), ('mc', 42))):
            switches = ('.param MC_MM_SWITCH=0 MC_PR_SWITCH=' + ('0' if section == 'tt' else '1')
                        + ' corner_factor=1 process_mc_factor=1 mismatch_factor=1\n')
            includes = ''.join(f'.include "{cached if variant == "cached" and item == PARAMETERS else source_root / item}"\n'
                               for item in SUBSET)
            lines = _probe_deck(str(library), section).splitlines(keepends=True)
            deck = lines[0] + switches + includes + ''.join(lines[2:])
            rows.append(run_probe(output / f'subset-{variant}-{index}', simulator, deck, seed, args.timeout))
        record['cases']['subset_' + variant] = rows
        record['checks']['subset_' + variant] = _qualification_checks(rows, 'global_process')
    if args.full_library:
        copied = output / 'experimental-combined'
        shutil.copytree(source_root, copied, symlinks=False)
        (copied / PARAMETERS).write_text(cached_text)
        # Legacy models include libs.ref outside combined/. Retain their exact
        # installed contents and redirect only these relocated include paths.
        redirected = {}
        for original in source_files:
            path = copied / original.relative_to(source_root)
            lines, changed = [], False
            for line in path.read_text().splitlines(keepends=True):
                if re.match(r'^\s*\.(?:include|inc)\s+', line, re.I):
                    words = shlex.split(line)
                    dependency = (original.parent / words[1]).resolve(strict=True)
                    if not dependency.is_relative_to(source_root):
                        line = f'.include "{dependency}"\n'
                        changed = True
                lines.append(line)
            if changed:
                path.write_text(''.join(lines))
                redirected[str(path.relative_to(copied))] = digest(path)
        record['relocated_external_include_files_sha256'] = redirected
        for variant, selected in (('direct', library), ('cached', copied / library.name)):
            record['cases']['full_' + variant] = [run_probe(output / f'full-{variant}', simulator,
                                                             _probe_deck(str(selected), 'mc'), 41, args.timeout)]
    record['source_unchanged'] = all(digest(path) == value for path, value in source_hashes.items())
    record['all_processes_succeeded'] = all(row['status'] == 'succeeded' for rows in record['cases'].values() for row in rows)
    target = output / 'evidence.json'
    target.write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'evidence': str(target), 'source_unchanged': record['source_unchanged'],
                      'all_processes_succeeded': record['all_processes_succeeded'], 'checks': record['checks']}, indent=2))
    return 0 if record['source_unchanged'] and record['all_processes_succeeded'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
