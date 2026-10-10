"""Read-only design milestones derived from recorded tools and published outputs."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

from .storage import confined


class _Hashes:
    """Bound work added to a detail read, including external model dependencies."""
    def __init__(self):
        self.cache = {}
        self.bytes = 0

    def digest(self, path):
        key = str(path)
        if key in self.cache:
            return self.cache[key]
        if len(self.cache) >= 4096 or not path.is_file():
            raise ValueError('Progress hash budget exceeded or input is not a regular file')
        size = path.stat().st_size
        if size > 64 * 1024**2 or self.bytes + size > 128 * 1024**2:
            raise ValueError('Progress hash byte budget exceeded')
        digest = hashlib.sha256()
        read = 0
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(65536), b''):
                read += len(block)
                if read > size:
                    raise ValueError('Input changed during progress inspection')
                digest.update(block)
        self.bytes += read
        self.cache[key] = digest.hexdigest()
        return self.cache[key]


def _object(value):
    return value if isinstance(value, dict) else {}


def _environment(record, config, hashes):
    """Compare external identity without launching tools or trusting imported paths."""
    if record.get('imported_evidence'):
        return 'stale'
    if config is not None:
        pdk = _object(record.get('pdk'))
        if not pdk:
            return 'unknown'
        root = config.get('pdk_root') or os.environ.get('PDK_ROOT')
        current_root = str(Path(root).expanduser().resolve()) if root else None
        old_root = str(Path(pdk['root']).expanduser().resolve()) if pdk.get('root') else None
        variant = config.get('pdk_variant') or os.environ.get('PDK') or 'sky130A'
        if current_root != old_root or variant != pdk.get('variant'):
            return 'stale'
        tool = {'simulate': 'ngspice', 'drc': 'magic', 'extract': 'magic', 'lvs': 'netgen'}.get(
            record.get('kind'), record.get('kind'))
        candidate = _object(config.get('tools')).get(tool) or tool
        command = record.get('command')
        current_tool = shutil.which(candidate) if isinstance(candidate, str) else None
        if not current_tool or not isinstance(command, list) or not command or not isinstance(command[0], str):
            return 'unknown'
        if Path(current_tool).resolve() != Path(command[0]).resolve():
            return 'stale'
    dependencies = _object(record.get('external_dependencies')).copy()
    pdk = _object(record.get('pdk'))
    for key, digest in _object(pdk.get('configuration_hashes')).items():
        path = _object(pdk.get('paths')).get(key)
        if not isinstance(path, str):
            return 'unknown'
        dependencies[path] = digest
    for name, digest in dependencies.items():
        if not isinstance(name, str) or not Path(name).is_absolute():
            return 'unknown'
        if hashes.digest(Path(name)) != digest:
            return 'stale'
    return None


def _publication(root, kind, records, native_paths, paths, config, hashes, *, check_snapshot=True,
                 published_records=None):
    """A derivation receipt alone is insufficient: verify its run and file hashes."""
    try:
        report_name = f'reports/ota5-{kind}-derivation.json'
        report = confined(root, report_name)
        if not report.exists():
            return None
        if report.stat().st_size > 65536:
            return 'unknown'
        with report.open('rb') as stream:
            report_bytes = stream.read(65537)
        if len(report_bytes) > 65536:
            return 'unknown'
        receipt = json.loads(report_bytes)
        if not isinstance(receipt, dict):
            return 'unknown'
        record = next((r for r in records if r.get('id') == receipt.get('run_id')), None)
        artifact = 'ota5.spice' if kind == 'xschem' else 'extracted.spice'
        expected_outputs = {'netlists/schematic.spice'} if kind == 'xschem' else {
            'netlists/extracted.spice', 'netlists/extracted-lvs.spice'}
        outputs = receipt.get('outputs')
        if (not record or record.get('kind') != kind or record.get('status') != 'succeeded'
                or record.get('imported_evidence') or not record.get('owner_identity') or receipt.get('kind') != kind
                or receipt.get('artifact') != artifact
                or not receipt.get('artifact_sha256')
                or _object(record.get('artifact_hashes')).get(artifact) != receipt['artifact_sha256']
                or not isinstance(outputs, dict) or set(outputs) != expected_outputs):
            return 'unknown'
        sources = _object(record.get('source_hashes'))
        selected_sources = _object(_object(record.get('metadata')).get('sources'))
        native_path = native_paths[kind]
        if (not sources.get(native_path) or
                selected_sources.get('schematic' if kind == 'xschem' else 'layout') != native_path):
            return 'unknown'
        run_id = record.get('id')
        if not isinstance(run_id, str) or not re.fullmatch(r'[a-f0-9]{32}', run_id):
            return 'unknown'
        actual = confined(root, f'.af/runs/{run_id}/output/{artifact}', hidden=True)
        if actual.stat().st_size > 2 * 1024**2 or hashes.digest(actual) != receipt['artifact_sha256']:
            return 'unknown'
        from .ota5_reference import canonical_subcircuit, without_capacitors
        with actual.open('rb') as stream:
            content = stream.read(2 * 1024**2 + 1)
        if len(content) > 2 * 1024**2:
            return 'unknown'
        canonical = canonical_subcircuit(content.decode())
        derived = {'netlists/schematic.spice': canonical} if kind == 'xschem' else {
            'netlists/extracted.spice': canonical,
            'netlists/extracted-lvs.spice': without_capacitors(canonical)[0]}
        expected = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in derived.items()}
        if outputs != expected:
            return 'unknown'
        if any(hashes.digest(confined(root, name)) != digest for name, digest in expected.items()):
            return 'stale'
        if hashes.digest(confined(root, native_path)) != sources[native_path]:
            return 'stale'
        environment_status = _environment(record, config, hashes)
        if environment_status:
            return environment_status
        if not check_snapshot:
            return 'published'
        # The two fixed template derivations may advance in sequence. Exempt the
        # other derivation only after independently proving its local artifact,
        # native source and deterministic output chain. Never exclude arbitrary
        # reports, configuration, scripts, or newly added hierarchy inputs.
        excluded = expected_outputs | {report_name}
        other_kind = 'extract' if kind == 'xschem' else 'xschem'
        if _publication(root, other_kind, records, native_paths, paths, config, hashes,
                        check_snapshot=False) == 'published':
            excluded |= {'reports/ota5-' + other_kind + '-derivation.json'}
            excluded |= {'netlists/schematic.spice'} if other_kind == 'xschem' else {
                'netlists/extracted.spice', 'netlists/extracted-lvs.spice'}
        snapshot = {name: digest for name, digest in sources.items() if name not in excluded}
        if set(snapshot) != paths - excluded:
            return 'stale'
        if any(hashes.digest(confined(root, name)) != digest for name, digest in snapshot.items()):
            return 'stale'
        if published_records is not None:
            published_records[kind] = record
        return 'published'
    except (OSError, ValueError, TypeError, KeyError):
        return 'unknown'


def _status(record, verify=False, config=None, hashes=None):
    if record is None:
        return 'not_run'
    if record.get('imported_evidence'):
        return 'stale'
    if record.get('status') in ('queued', 'starting', 'running', 'pending'):
        return 'running'
    if record.get('stale') is True:
        return 'stale'
    if record.get('status') not in ('succeeded', 'completed', 'success', 'passed'):
        return 'failed'
    if record.get('stale') is not False:
        return 'unknown'
    try:
        environment_status = _environment(record, config, hashes or _Hashes())
        if environment_status:
            return environment_status
    except (OSError, ValueError, TypeError):
        return 'unknown'
    if verify:
        return {'pass': 'passed', 'fail': 'failed'}.get(_object(record.get('verification')).get('status'), 'unknown')
    return 'completed'


def build(root, project, files, runs, *, config=None):
    """Return observable milestones; file presence only means ready for inspection."""
    paths = {item['path'] for item in files if isinstance(item.get('path'), str) and
             not any(part.startswith('.') for part in Path(item['path']).parts)}
    design = {'schematic': 'schematics/amplifier.sch', 'layout': 'layout/amplifier.mag',
              'netlist': 'testbenches/pre.cir', 'post_netlist': 'testbenches/post.cir',
              'schematic_netlist': 'netlists/schematic.spice', 'layout_netlist': 'netlists/extracted.spice',
              **_object(project.get('design'))}
    records = sorted((r for r in runs if isinstance(r, dict)),
                     key=lambda r: str(r.get('created_at') or r.get('started_at') or ''), reverse=True)
    hashes = _Hashes()
    published_records = {}
    native_paths = {'xschem': design['schematic'], 'extract': design['layout']}
    def status(record, verify=False):
        return _status(record, verify, config, hashes)
    ota = project.get('template') == 'ota5'
    def latest(kind, **sources):
        return next((r for r in records if r.get('kind') == kind and all(
            _object(_object(r.get('metadata')).get('sources')).get(key) == value
            for key, value in sources.items()) and (kind != 'lvs' or
            _object(_object(r.get('metadata')).get('options')).get('cell', 'amplifier') ==
            design.get('cell', 'amplifier'))), None)
    stages = []
    def add(key, title, status, target, reason, record=None):
        stages.append({'id': key, 'title': title, 'status': status, 'target': target,
                       'reason': reason, 'run_id': record.get('id') if record else None})
    add('specification', 'Specification', 'ready' if 'specifications.json' in paths else 'missing',
        'editor', 'Review the design targets before running tools.')
    schematic = latest('xschem', schematic=design['schematic'])
    published_schematic = _publication(root, 'xschem', records, native_paths, paths, config, hashes,
                                      published_records=published_records) if ota else None
    schematic_status = published_schematic or status(schematic) if ota else ('ready' if design['schematic'] in paths else 'missing')
    if ota and schematic_status == 'completed':
        schematic_status = 'publication_required'
    add('schematic', 'Schematic', schematic_status, 'verification' if ota else 'desktop',
        'Netlist the schematic, then publish its actual output.' if ota else 'Inspect and save the native schematic.',
        published_records.get('xschem', schematic))
    pre = latest('simulate', netlist=design['netlist'])
    add('pre', 'Pre-layout simulation', status(pre), 'simulation',
        'Run the saved pre-layout testbench; completion does not establish target compliance.', pre)
    add('layout', 'Layout', 'ready' if design['layout'] in paths else 'missing', 'desktop',
        'Inspect and save the native layout before verification.')
    drc = latest('drc', layout=design['layout'])
    add('drc', 'Design rule check', status(drc, True), 'verification',
        'A current passing DRC report is required.', drc)
    extraction = latest('extract', layout=design['layout'])
    published_extract = _publication(root, 'extract', records, native_paths, paths, config, hashes,
                                    published_records=published_records) if ota else None
    extraction_status = published_extract or status(extraction)
    if ota and extraction_status == 'completed':
        extraction_status = 'publication_required'
    add('extract', 'Extraction', extraction_status, 'verification',
        'Extract the layout, then publish the actual parasitic and LVS netlists.' if ota else
        'Inspect the extracted netlist and its coverage.', published_records.get('extract', extraction))
    lvs = latest('lvs', layout_netlist=design['layout_netlist'], schematic_netlist=design['schematic_netlist'])
    lvs_status = status(lvs, True)
    if ota and lvs_status == 'not_run' and (published_schematic != 'published' or published_extract != 'published'):
        lvs_status = 'blocked'
    add('lvs', 'Layout versus schematic', lvs_status, 'verification',
        'Publish both schematic and extracted netlists before LVS.' if ota else 'A current passing LVS report is required.', lvs)
    post = latest('simulate', netlist=design['post_netlist'])
    post_status = status(post)
    if post_status == 'not_run' and (design['post_netlist'] not in paths or (ota and published_extract != 'published')):
        post_status = 'blocked'
    add('post', 'Post-layout comparison', post_status, 'simulation',
        'Run the extracted testbench, then compare measurements under matching conditions.', post)
    add('archive', 'Archive', 'ready', 'export', 'Export the design and its recorded evidence.')
    return stages
