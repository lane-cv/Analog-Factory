"""Progress must reflect current evidence, never executable availability alone."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from analog_factory import design_progress


class DesignProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = {'template': 'ota5', 'design': {'schematic': 'schematics/ota5.sch',
            'layout': 'layout/ota5.mag', 'netlist': 'testbenches/pre.cir',
            'post_netlist': 'testbenches/post.cir', 'cell': 'ota5'}}
        for name in ['specifications.json', 'schematics/ota5.sch', 'layout/ota5.mag',
                     'testbenches/pre.cir', 'testbenches/post.cir']:
            self.write(name, 'original')
        self.write('project.json', json.dumps(self.project))

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return hashlib.sha256(content.encode()).hexdigest()

    def progress(self, runs=(), config=None):
        files = [{'path': p.relative_to(self.root).as_posix()} for p in self.root.rglob('*') if p.is_file()]
        args = {} if config is None else {'config': config}
        return {row['id']: row for row in design_progress.build(self.root, self.project, files, list(runs), **args)}

    def record(self, kind, **extra):
        sources = {'xschem': {'schematic': 'schematics/ota5.sch'},
                   'drc': {'layout': 'layout/ota5.mag'}, 'extract': {'layout': 'layout/ota5.mag'},
                   'lvs': {'schematic_netlist': 'netlists/schematic.spice',
                           'layout_netlist': 'netlists/extracted.spice'}}.get(kind, {})
        return {'id': kind, 'kind': kind, 'status': 'succeeded', 'stale': False,
                'metadata': {'sources': sources},
                'created_at': '2026-10-10T12:00:00Z', **extra}

    def publish(self, kind='xschem'):
        snapshot = {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in self.root.rglob('*') if p.is_file() and not any(
                        part.startswith('.') for part in p.relative_to(self.root).parts)}
        run_id = ('a' if kind == 'xschem' else 'b') * 32
        text = (Path(__file__).resolve().parents[1] / 'analog_factory/assets/ota5/netlists/ota5.spice').read_text()
        if kind == 'extract':
            text = text.replace('.ends ota5', 'C0 out vss 2f\n.ends ota5')
        artifact = 'ota5.spice' if kind == 'xschem' else 'extracted.spice'
        artifact_hash = self.write(f'.af/runs/{run_id}/output/{artifact}', text)
        outputs = {'netlists/schematic.spice': text} if kind == 'xschem' else {
            'netlists/extracted.spice': text,
            'netlists/extracted-lvs.spice': text.replace('C0 out vss 2f\n', '')}
        receipt = {'run_id': run_id, 'kind': kind, 'artifact': artifact,
                   'artifact_sha256': artifact_hash,
                   'outputs': {name: self.write(name, content) for name, content in outputs.items()}}
        self.write(f'reports/ota5-{kind}-derivation.json', json.dumps(receipt))
        return self.record(kind, id=run_id, stale=True, owner_identity='local-owner',
                           source_hashes=snapshot, artifact_hashes={artifact: artifact_hash}), receipt

    def test_template_files_are_ready_not_completed_design_evidence(self):
        stages = self.progress()
        self.assertEqual(stages['specification']['status'], 'ready')
        self.assertEqual(stages['schematic']['status'], 'not_run')
        self.assertEqual(stages['pre']['status'], 'not_run')
        self.assertEqual(stages['post']['status'], 'blocked')

    def test_verification_needs_actual_pass_and_current_sources(self):
        for verification, stale, expected in [({'status': 'unknown'}, False, 'unknown'),
                ({'status': 'fail'}, False, 'failed'), ({'status': 'pass'}, True, 'stale'),
                ({'status': 'pass'}, False, 'passed')]:
            with self.subTest(verification=verification, stale=stale):
                stage = self.progress([self.record('drc', verification=verification, stale=stale)])['drc']
                self.assertEqual(stage['status'], expected)

    def test_latest_failed_attempt_does_not_reuse_old_pass(self):
        old = self.record('drc', verification={'status': 'pass'})
        new = self.record('drc', id='new', status='failed', created_at='2026-10-10T13:00:00Z')
        self.assertEqual(self.progress([old, new])['drc']['status'], 'failed')

    def test_pre_and_post_runs_are_matched_by_testbench_and_keep_unknowns(self):
        pre = self.record('simulate', metadata={'sources': {'netlist': 'testbenches/pre.cir'}})
        other = self.record('simulate', id='other', metadata={'sources': {'netlist': 'testbenches/other.cir'}})
        stages = self.progress([pre, other])
        self.assertEqual(stages['pre']['status'], 'completed')
        self.assertNotEqual(stages['post']['status'], 'completed')
        pre.pop('stale')
        self.assertEqual(self.progress([pre])['pre']['status'], 'unknown')

    def test_publication_checks_outputs_and_native_sources_not_report_existence(self):
        record, receipt = self.publish()
        self.assertEqual(self.progress([record])['schematic']['status'], 'published')
        original = (self.root / 'netlists/schematic.spice').read_text()
        self.write('netlists/schematic.spice', 'manual change')
        self.assertEqual(self.progress([record])['schematic']['status'], 'stale')
        self.write('netlists/schematic.spice', original)
        self.write('schematics/ota5.sch', 'edited schematic')
        self.assertEqual(self.progress([record])['schematic']['status'], 'stale')

    def test_receipt_and_netlist_cannot_be_changed_together_to_forge_publication(self):
        record, receipt = self.publish()
        receipt['outputs']['netlists/schematic.spice'] = self.write('netlists/schematic.spice', 'forged output')
        self.write('reports/ota5-xschem-derivation.json', json.dumps(receipt))
        self.assertNotEqual(self.progress([record])['schematic']['status'], 'published')

    def test_publication_requires_actual_unchanged_artifact_and_local_run(self):
        for change in ('missing', 'changed', 'imported', 'ownerless'):
            with self.subTest(change=change):
                record, _ = self.publish()
                artifact = self.root / f'.af/runs/{record["id"]}/output/ota5.spice'
                if change == 'missing':
                    artifact.unlink()
                elif change == 'changed':
                    artifact.write_text('changed artifact')
                elif change == 'imported':
                    record['imported_evidence'] = True
                else:
                    record.pop('owner_identity')
                self.assertNotEqual(self.progress([record])['schematic']['status'], 'published')

    def test_publication_exemption_preserves_all_other_snapshot_inputs(self):
        for name in ('project.json', 'scripts/netlisting.tcl', 'schematics/child.sym'):
            with self.subTest(name=name):
                self.write(name, 'snapshot source')
                record, _ = self.publish()
                self.write(name, 'changed source')
                self.assertEqual(self.progress([record])['schematic']['status'], 'stale')

    def test_new_native_dependency_invalidates_old_publication(self):
        record, _ = self.publish()
        self.write('schematics/new-child.sym', 'new dependency')
        self.assertEqual(self.progress([record])['schematic']['status'], 'stale')

    def test_both_verified_publications_survive_the_normal_pipeline(self):
        schematic, _ = self.publish()
        extraction, _ = self.publish('extract')
        stages = self.progress([schematic, extraction])
        self.assertEqual(stages['schematic']['status'], 'published')
        self.assertEqual(stages['extract']['status'], 'published')

    def test_other_publication_is_exempt_only_if_its_actual_derivation_is_verified(self):
        schematic, _ = self.publish()
        extraction, receipt = self.publish('extract')
        receipt['outputs']['netlists/extracted.spice'] = self.write('netlists/extracted.spice', 'forged PEX')
        self.write('reports/ota5-extract-derivation.json', json.dumps(receipt))
        stages = self.progress([schematic, extraction])
        self.assertNotEqual(stages['schematic']['status'], 'published')
        self.assertNotEqual(stages['extract']['status'], 'published')

    def test_deleted_external_dependency_invalidates_publication(self):
        record, _ = self.publish()
        path = self.root / '.pdk-dependency'
        digest = self.write('.pdk-dependency', 'external dependency')
        record['external_dependencies'] = {str(path): digest}
        path.unlink()
        self.assertNotEqual(self.progress([record])['schematic']['status'], 'published')

    def test_oversized_external_dependency_yields_unknown_without_unbounded_read(self):
        record, _ = self.publish()
        path = self.root / '.oversized-dependency'
        with path.open('wb') as stream:
            stream.truncate(64 * 1024**2 + 1)
        record['external_dependencies'] = {str(path): 'not-current'}
        self.assertEqual(self.progress([record])['schematic']['status'], 'unknown')

    def test_changed_workspace_pdk_does_not_reuse_old_publication(self):
        record, _ = self.publish()
        record['pdk'] = {'root': '/old/pdk', 'variant': 'sky130A'}
        self.assertEqual(self.progress([record], config={'pdk_root': '/new/pdk'})['schematic']['status'], 'stale')

    def test_report_symlink_is_unknown_instead_of_breaking_project_detail(self):
        report = self.root / 'reports/ota5-xschem-derivation.json'
        report.parent.mkdir(exist_ok=True)
        report.symlink_to(self.root / 'missing-report')
        self.assertEqual(self.progress()['schematic']['status'], 'unknown')

    def test_other_layout_run_does_not_satisfy_current_design_milestone(self):
        record = self.record('drc', verification={'status': 'pass'},
                             metadata={'sources': {'layout': 'layout/other.mag'}})
        self.assertNotEqual(self.progress([record])['drc']['status'], 'passed')

    def test_lvs_other_sources_do_not_satisfy_current_design_milestone(self):
        record = self.record('lvs', verification={'status': 'pass'}, metadata={'sources': {
            'layout_netlist': 'netlists/other.spice', 'schematic_netlist': 'netlists/schematic.spice'}})
        self.assertNotEqual(self.progress([record])['lvs']['status'], 'passed')

    def test_lvs_for_other_top_cell_does_not_satisfy_current_design(self):
        record = self.record('lvs', verification={'status': 'pass'})
        record['metadata']['options'] = {'cell': 'another_cell'}
        self.assertNotEqual(self.progress([record])['lvs']['status'], 'passed')

    def test_published_stage_links_its_receipt_run_rather_than_a_newer_failed_attempt(self):
        published, _ = self.publish()
        newer = self.record('xschem', id='c' * 32, status='failed', created_at='2026-10-10T13:00:00Z')
        stage = self.progress([published, newer])['schematic']
        self.assertEqual(stage['status'], 'published')
        self.assertEqual(stage['run_id'], published['id'])

    def test_successful_extraction_still_needs_publication_before_post(self):
        stages = self.progress([self.record('extract')])
        self.assertEqual(stages['extract']['status'], 'publication_required')
        self.assertEqual(stages['post']['status'], 'blocked')
        self.assertEqual(stages['extract']['run_id'], 'extract')

    def test_imported_evidence_does_not_mark_current_stage_passed(self):
        record = self.record('drc', imported_evidence=True, verification={'status': 'pass'})
        self.assertEqual(self.progress([record])['drc']['status'], 'stale')

    def test_imported_evidence_cannot_claim_a_live_tool_is_running(self):
        record = self.record('drc', status='running', imported_evidence=True)
        self.assertEqual(self.progress([record])['drc']['status'], 'stale')

    def test_corrupt_publication_report_is_unknown_and_does_not_break_project(self):
        self.write('reports/ota5-xschem-derivation.json', '[]')
        self.assertEqual(self.progress()['schematic']['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
