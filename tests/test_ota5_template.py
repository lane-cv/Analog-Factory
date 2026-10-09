"""User-created OTA references use native sources and truthful prerequisites."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

from analog_factory.service import Workspace


class OtaTemplateTests(unittest.TestCase):
    def pdk(self, root):
        pdk = root/'pdk'
        for name in ('libs.tech/combined/sky130.lib.spice', 'libs.tech/xschem/xschemrc',
                     'libs.tech/magic/sky130A.magicrc', 'libs.tech/netgen/sky130A_setup.tcl'):
            path = pdk/'sky130A'/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('* local qualification fixture\n')
        return {'pdk_root': str(pdk), 'pdk_variant': 'sky130A'}

    def test_missing_pdk_fails_before_project_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(Path(temporary))
            reply = workspace.operations.invoke('project.create', {'name':'OTA','template':'ota5'})
            self.assertFalse(reply['ok'])
            self.assertIn('PDK', reply['error']['message'])
            self.assertEqual(workspace.projects.list(), [])

    def test_create_has_native_sources_and_defaults_without_saved_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(Path(temporary)/'workspace')
            config = self.pdk(Path(temporary))
            workspace.save_settings(config)
            reply = workspace.operations.invoke('project.create', {'name':'OTA','template':'ota5'})
            self.assertTrue(reply['ok'], reply.get('error'))
            project = reply['data']; root = workspace.projects.path(project['id'])
            self.assertEqual(project['design']['cell'], 'ota5')
            self.assertEqual(project['design']['layout'], 'layout/ota5.mag')
            self.assertTrue((root/'schematics/ota5.sch').is_file())
            self.assertTrue((root/'layout/ota5.mag').is_file())
            self.assertIn(config['pdk_root'], (root/'testbenches/pre.cir').read_text())
            self.assertNotIn('@MODEL_LIBRARY@', (root/'testbenches/pre.cir').read_text())
            self.assertFalse((root/'netlists/extracted.spice').exists())
            self.assertEqual(workspace.runs(project['id']), [])

    def test_eda_uses_project_design_defaults_and_explicit_overrides(self):
        from analog_factory import eda
        from analog_factory.storage import atomic_json
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'schematics').mkdir()
            (root/'schematics/ota5.sch').write_text('native schematic')
            (root/'schematics/other.sch').write_text('other native schematic')
            atomic_json(root/'project.json', {'design':{'schematic':'schematics/ota5.sch','cell':'ota5'}})
            detected = {'tools':{'xschem':{'available':True,'path':'/bin/true','version':'fixture'}},
                        'pdk':{'root':None,'paths':{'xschem':None}},'desktop':{'available':False}}
            with patch.object(eda, 'detect', return_value=detected):
                result = eda.prepare(root,'xschem',{'netlist_only':True},{})
                self.assertEqual(result['metadata']['sources']['schematic'],'schematics/ota5.sch')
                result = eda.prepare(root,'xschem',{'netlist_only':True,'schematic':'schematics/other.sch'},{})
                self.assertEqual(result['metadata']['sources']['schematic'],'schematics/other.sch')

    def test_packaged_assets_match_qualified_portable_sources(self):
        from analog_factory.ota5_reference import ASSETS
        root = Path(__file__).resolve().parents[1]
        for name in ASSETS:
            self.assertEqual((root/'analog_factory/assets/ota5'/name).read_bytes(),
                             (root/'examples/ota5'/name).read_bytes())

    def test_archive_roundtrips_keep_design_defaults_and_require_fresh_local_derivation(self):
        from analog_factory import eda
        from analog_factory.ota5_reference import DESIGN, derive_netlists
        from analog_factory.project_lifecycle import ProjectLifecycle
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(Path(temporary)/'workspace')
            self.addCleanup(workspace.operations.close)
            config = self.pdk(Path(temporary))
            project = workspace.projects.create('OTA', 'ota5', config=config)
            root = workspace.projects.path(project['id'])
            # This local Python fixture copies the supplied electrical source;
            # it does not claim actual native-tool or reference qualification.
            code = 'import pathlib,sys;pathlib.Path(sys.argv[1]).write_bytes(pathlib.Path(sys.argv[2]).read_bytes())'
            run = workspace.runner.start(root, 'xschem', [sys.executable, '-c', code,
                '{output}/ota5.spice', str(root/'netlists/ota5.spice')],
                metadata={'sources': {'schematic': DESIGN['schematic']}})
            self.assertEqual(workspace.runner.wait(root, run['id'])['status'], 'succeeded')
            lifecycle = ProjectLifecycle(workspace.projects, workspace)
            export_path = lifecycle.backup_to(project['id'], Path(temporary)/'export.zip', mode='export')
            archives = {'backup': lifecycle.backup(project['id']),
                        'legacy export': workspace.projects.export(project['id']),
                        'disk export': export_path}
            pdk = Path(config['pdk_root'])/'sky130A'
            detected = {'tools': {name: {'available': True, 'path': '/bin/true', 'version': 'fixture'}
                                  for name in ('ngspice', 'xschem', 'magic', 'netgen')},
                        'pdk': {'root': config['pdk_root'], 'variant': 'sky130A', 'paths': {
                            'xschem': str(pdk/'libs.tech/xschem/xschemrc'),
                            'magic': str(pdk/'libs.tech/magic/sky130A.magicrc'),
                            'netgen': str(pdk/'libs.tech/netgen/sky130A_setup.tcl')}},
                        'desktop': {'available': False}}
            for label, archive in archives.items():
                with self.subTest(archive=label):
                    restored = lifecycle.import_archive(archive)
                    self.assertEqual(restored['design'], DESIGN)
                    self.assertNotEqual(restored['id'], project['id'])
                    target = workspace.projects.path(restored['id'])
                    self.assertEqual((target/'specifications.json').read_bytes(),
                                     (root/'specifications.json').read_bytes())
                    with patch.object(eda, 'detect', return_value=detected):
                        for kind, role, options in [('simulate', 'netlist', {}),
                                ('xschem', 'schematic', {'netlist_only': True}),
                                ('drc', 'layout', {}), ('extract', 'layout', {})]:
                            prepared = eda.prepare(target, kind, options, config)
                            self.assertEqual(prepared['metadata']['sources'][role], DESIGN[role])
                        # An archive supplies source defaults, never a completed
                        # extraction. LVS still requires its actual derived file.
                        with self.assertRaisesRegex(ValueError, 'Source file does not exist'):
                            eda.prepare(target, 'lvs', {}, config)
                        workspace.projects.write(restored['id'], DESIGN['layout_netlist'], '* fixture only\n')
                        prepared = eda.prepare(target, 'lvs', {}, config)
                        self.assertEqual(prepared['metadata']['options']['cell'], 'ota5')
                        self.assertEqual(prepared['metadata']['sources'], {
                            key: DESIGN[key] for key in ('layout_netlist', 'schematic_netlist')})
                    if label == 'backup':
                        imported = workspace.runner.get(target, run['id'])
                        self.assertTrue(imported['imported_evidence'])
                        self.assertIsNone(imported['owner_identity'])
                        with self.assertRaisesRegex(ValueError, 'local'):
                            derive_netlists(workspace, restored['id'], run['id'])
                    else:
                        self.assertEqual(workspace.runs(restored['id']), [])

    def test_derived_outputs_require_current_local_hashed_run_and_five_devices(self):
        from analog_factory.ota5_reference import derive_netlists, canonical_subcircuit
        from analog_factory.storage import atomic_json
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(Path(temporary)/'workspace')
            project = workspace.projects.create('OTA','ota5',config=self.pdk(Path(temporary)))
            root = workspace.projects.path(project['id'])
            code = 'import pathlib,sys;pathlib.Path(sys.argv[1]).write_text(pathlib.Path(sys.argv[2]).read_text())'
            run = workspace.runner.start(root,'xschem',[sys.executable,'-c',code,'{output}/ota5.spice',str(root/'netlists/ota5.spice')],
                metadata={'sources':{'schematic':'schematics/ota5.sch'}})
            run = workspace.runner.wait(root,run['id'])
            manifest = root/'.af/runs'/run['id']/'manifest.json'
            saved = json.loads(manifest.read_text())
            atomic_json(manifest,{**saved,'imported_evidence':True})
            with self.assertRaisesRegex(ValueError,'local'):
                derive_netlists(workspace,project['id'],run['id'])
            atomic_json(manifest,saved)
            output = root/'.af/runs'/run['id']/'output/ota5.spice'
            content = output.read_text();output.write_text(content+'* tampering\n')
            with self.assertRaisesRegex(ValueError,'changed'):
                derive_netlists(workspace,project['id'],run['id'])
            output.write_text(content)
            result = derive_netlists(workspace,project['id'],run['id'])
            self.assertIn('netlists/schematic.spice',result['outputs'])
            self.assertEqual((root/'netlists/schematic.spice').read_text(),content)
            with self.assertRaises(ValueError):
                canonical_subcircuit(content.replace('.ends ota5','Mhidden a b c d MODEL\n.ends ota5'))


if __name__ == '__main__':
    unittest.main()
