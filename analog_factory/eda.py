"""Prepare reproducible tool invocations for :class:`execution.RunManager`.

``prepare(project, kind, options, config)`` returns start() keyword arguments.
Invalid user paths/options raise ValueError; unavailable tools, PDK assets, or
desktop sessions raise RuntimeError. Commands use argv lists, and generated Tcl
scripts are written only into run snapshots through metadata.generated_files.
GUI editors intentionally open the original project source for normal editing.
"""

from __future__ import annotations

import math
import hashlib
from pathlib import Path
import re
import shlex
from string import Template

from .toolchain import detect


def input_dependencies(snapshot: Path, netlist: str, environment: dict) -> dict:
    """Hash the static SPICE include/library closure, including external models.

    External files remain external; this fact and unresolved/dynamic references
    explicitly make provenance incomplete. This is dependency accounting, not a
    sandbox or a complete interpreter of arbitrary SPICE control programs.
    """
    snapshot = Path(snapshot).resolve()
    external, visited, limitations = {}, set(), []
    pending = [snapshot / netlist]
    while pending:
        path = pending.pop().resolve()
        if path in visited:
            continue
        visited.add(path)
        if len(visited) > 10000:
            limitations.append('Dependency scan exceeded 10,000 files')
            break
        try:
            data = path.read_bytes()
        except OSError:
            if not path.is_relative_to(snapshot):
                external[str(path)] = None
            limitations.append(f'Missing or unreadable dependency: {path}')
            continue
        if not path.is_relative_to(snapshot):
            external[str(path)] = hashlib.sha256(data).hexdigest()
        logical = []
        for line in data.decode(errors='replace').splitlines():
            if line.lstrip().startswith('+') and logical:
                logical[-1] += ' ' + line.lstrip()[1:]
            else:
                logical.append(line)
        for line in logical:
            if re.match(r'^\s*\.control\b', line, re.I):
                limitations.append(f'User control block can access additional files: {path}')
            if not re.match(r'^\s*\.(?:include|inc|lib)\b', line, re.I):
                continue
            try:
                words = shlex.split(line, comments=False)
            except ValueError:
                limitations.append(f'Unparseable dependency directive: {path}: {line.strip()}')
                continue
            if len(words) < 2 or (words[0].lower() == '.lib' and len(words) == 2):
                continue  # .lib tt begins a library section, not a file reference.
            raw = words[1]
            try:
                expanded = Template(raw).safe_substitute(environment)
            except ValueError:
                expanded = raw
            if any(character in expanded for character in '$`{}'):
                limitations.append(f'Dynamic dependency cannot be resolved statically: {raw}')
                continue
            dependency = Path(expanded).expanduser()
            if dependency.is_absolute():
                pending.append(dependency)
            else:
                # Both ngspice's working directory and the containing library
                # directory can resolve includes. Record both on ambiguity.
                candidates = list(dict.fromkeys([(snapshot / dependency).resolve(), (path.parent / dependency).resolve()]))
                existing = [candidate for candidate in candidates if candidate.is_file()]
                if len(existing) > 1:
                    limitations.append(f'Ambiguous include search path: {raw} in {path}')
                pending.extend(existing or [candidates[-1]])
    if external:
        limitations.append('External model files are hashed but are not included in the immutable input snapshot')
    return {'external_dependencies': external,
            'provenance': {'complete': not limitations, 'coverage': 'Static SPICE .include/.inc/.lib dependency closure',
                           'limitations': list(dict.fromkeys(limitations))}}


def _source(project, value, *, allow_missing=False):
    relative = Path(value)
    if relative.is_absolute() or not relative.parts or any(part == '..' or part.startswith('.') for part in relative.parts):
        raise ValueError('Source path must be a visible project-relative path')
    target = project / relative
    if any((project / Path(*relative.parts[:index])).is_symlink() for index in range(1, len(relative.parts) + 1)):
        raise ValueError('Symbolic links cannot be used as run inputs')
    if not target.is_file() and not (allow_missing and not target.exists()):
        raise ValueError(f'Source file does not exist: {relative.as_posix()}')
    return relative.as_posix()


def _tcl(value):
    """Quote one Tcl word without substitutions, including unusual filenames."""
    value = str(value)
    return '"' + ''.join({'\\': '\\\\', '"': '\\"', '$': '\\$', '[': '\\[', ']': '\\]',
                          '\n': '\\n', '\r': '\\r'}.get(char, char) for char in value) + '"'


def _script(body):
    return 'if {[catch {\n' + body + '\n} af_error]} {\n  puts stderr "AF_ERROR: $af_error"\n  exit 1\n}\nquit -noprompt\n'


def prepare(project: Path, kind: str, options: dict, config: dict) -> dict:
    """Support simulate, xschem (GUI or netlist_only), magic, drc, lvs, extract.

    Extraction produces capacitance-inclusive SPICE via ext2spice cthresh 0;
    distributed resistance extraction and foundry signoff are not claimed.
    All relative includes in SPICE decks resolve from the snapshot root.
    """
    project = Path(project).resolve()
    from .storage import read_json
    design = read_json(project / 'project.json', {}).get('design', {})
    if not isinstance(design, dict):
        raise ValueError('Project design defaults must be an object')
    options = {**{key: value for key, value in design.items() if key in
                  ('netlist', 'schematic', 'layout', 'layout_netlist', 'schematic_netlist', 'cell')}, **(options or {})}
    gui = kind == 'magic' or (kind == 'xschem' and not options.get('netlist_only'))
    create_missing = gui and options.get('create_missing') is True
    sources = {}
    if kind == 'simulate':
        sources['netlist'] = _source(project, options.get('netlist', 'testbenches/pre.cir'))
    elif kind == 'xschem':
        sources['schematic'] = _source(project, options.get('schematic', 'schematics/amplifier.sch'), allow_missing=create_missing)
    elif kind in {'magic', 'drc', 'extract'}:
        sources['layout'] = _source(project, options.get('layout', 'layout/amplifier.mag'), allow_missing=create_missing)
    elif kind == 'lvs':
        sources['layout_netlist'] = _source(project, options.get('layout_netlist', 'netlists/extracted.spice'))
        sources['schematic_netlist'] = _source(project, options.get('schematic_netlist', 'netlists/schematic.spice'))
    else:
        raise ValueError(f'Unsupported EDA task: {kind}')
    if create_missing:
        source = next(iter(sources.values()))
        suffix = '.sch' if kind == 'xschem' else '.mag'
        if Path(source).suffix != suffix:
            raise ValueError(f'The editor source must be a {suffix} file')
    detected = detect(config)
    tool_name = {'simulate': 'ngspice', 'drc': 'magic', 'extract': 'magic', 'lvs': 'netgen'}.get(kind, kind)
    tool = detected['tools'][tool_name]
    if not tool['available']:
        raise RuntimeError(tool['diagnostic'])
    pdk = detected['pdk']
    env = {'SPICE_ASCIIRAWFILE': '1'}
    if pdk['root']:
        env.update(PDK_ROOT=pdk['root'], PDK=pdk['variant'], PDKPATH=str(Path(pdk['root']) / pdk['variant']))
    metadata = {'tool_versions': {tool_name: tool['version']}, 'pdk': pdk,
                'options': options, 'sources': sources,
                'conditions': options.get('conditions', {}), 'generated_files': {}}
    timeout = float(options.get('timeout', 86400 if gui else 60))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Timeout must be positive and finite')
    command = [tool['path']]
    if kind == 'simulate':
        # ngspice disables .meas with -b -r. A control driver keeps measurement
        # evaluation and rawfile writing in the same actual simulation.
        # A safe alias avoids ngspice's source-command quoting limitations and
        # is copied after generated parameter overrides have been applied.
        metadata['input_aliases'] = {'af_scripts/circuit.cir': sources['netlist']}
        script_name = 'af_scripts/simulate.cir'
        metadata['generated_files'][script_name] = (
            '* Analog-factory simulation control driver\n.control\nset filetype=ascii\n'
            'source af_scripts/circuit.cir\nrun\nwrite ../output/waveforms.raw all\nquit\n.endc\n.end\n')
        command += ['-n', '-b', '-o', '{output}/ngspice.log', script_name]
    elif kind == 'xschem':
        rcfile = pdk['paths']['xschem']
        if rcfile and Path(rcfile).is_file():
            command += ['--rcfile', rcfile]
        if options.get('netlist_only'):
            command += ['-n', '-q', '-x', '-s', '-o', '{output}', '{input}/' + sources['schematic']]
        else:
            command.append(str(project / sources['schematic']))
    elif kind in {'magic', 'drc', 'extract'}:
        rcfile = pdk['paths']['magic']
        if not rcfile or not Path(rcfile).is_file():
            raise RuntimeError('Magic requires a prepared PDK magicrc: ' + pdk['diagnostic'])
        command += ['-rcfile', rcfile]
        if kind == 'magic':
            command.append(str(project / sources['layout']))
        else:
            body = f'load {_tcl(sources["layout"])}\nselect top cell\nexpand\nset af_out [file normalize [file join [pwd] .. output]]\n'
            if kind == 'drc':
                body += ('drc on\ndrc ignore none\ndrc check\ndrc catchup\n'
                         'set af_count [drc listall count total]\n'
                         'set af_report [open [file join $af_out drc.txt] w]\n'
                         'puts $af_report "AF_DRC_COUNT=$af_count"\n'
                         'puts $af_report "DRC_STYLE=[drc list style]"\n'
                         'puts $af_report [drc listall why]\n'
                         'puts $af_report "AF_DRC_COMPLETE=1"\nclose $af_report')
            else:
                # Magic 8.3 supports local extraction into the current directory;
                # "extract path" is unsupported and can silently leave .ext files
                # beside the layout inside the input snapshot.
                body += ('extract do local\ncd $af_out\nextract all\next2spice lvs\n'
                         # Flatten extracted hierarchy while retaining the top
                         # interface. Magic 8.3.684's legacy-tech hierarchical
                         # conversion omits node-to-substrate capacitance.
                         'ext2spice hierarchy off\next2spice subcircuit top on\n'
                         'ext2spice cthresh 0\next2spice rthresh 0\n'
                         'ext2spice -p . -o extracted.spice\n'
                         'if {![file exists [file join $af_out extracted.spice]]} {error "Extraction produced no SPICE file"}\n'
                         'set af_report [open [file join $af_out extraction.txt] w]\n'
                         'puts $af_report "Flattened capacitance-inclusive extraction with top-level ports. Distributed resistance extraction and signoff are not validated."\n'
                         'close $af_report')
                metadata['extraction_coverage'] = 'Flattened capacitance-inclusive ext2spice with top-level ports; distributed resistance and signoff unvalidated'
            script_name = 'af_scripts/' + kind + '.tcl'
            metadata['generated_files'][script_name] = _script(body)
            command += ['-dnull', '-noconsole', script_name]
    elif kind == 'lvs':
        setup = pdk['paths']['netgen']
        if not setup or not Path(setup).is_file():
            raise RuntimeError('LVS requires a prepared PDK Netgen setup file: ' + pdk['diagnostic'])
        cell = str(options.get('cell', 'amplifier'))
        if not cell or any(character in cell for character in '\0\r\n'):
            raise ValueError('A valid LVS top-level cell name is required')
        script_name = 'af_scripts/lvs.tcl'
        body = (f'lvs [list {_tcl(sources["layout_netlist"])} {_tcl(cell)}] '
                f'[list {_tcl(sources["schematic_netlist"])} {_tcl(cell)}] '
                f'{_tcl(setup)} [file normalize [file join [pwd] .. output lvs.txt]]')
        metadata['generated_files'][script_name] = 'if {[catch {\n' + body + '\n} af_error]} {puts stderr "AF_ERROR: $af_error"; exit 1}\n'
        command += ['-batch', 'source', script_name]
    if gui:
        if not detected['desktop']['available']:
            raise RuntimeError(detected['desktop']['diagnostic'])
        metadata['gui_edits_original'] = True
        if create_missing and not (project / source).exists():
            content = ('v {xschem version=3.4.7 file_version=1.2}\nG {}\nK {}\nV {}\nS {}\nE {}\n'
                       if kind == 'xschem' else f'magic\ntech {pdk["variant"]}\ntimestamp 0\n<< end >>\n')
            metadata['initial_editor_source'] = {'path': source, 'content': content}
    return {'command': command, 'env': env, 'metadata': metadata, 'timeout': timeout}
