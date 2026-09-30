"""Real Magic smoke checks using bundled generic scmos, without a foundry PDK."""

import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from analog_factory import eda
from analog_factory.execution import RunManager


_LOCAL_MAGIC = Path(__file__).resolve().parents[1] / '.tools/eda/bin/magic'
_MAGIC = str(_LOCAL_MAGIC) if os.access(_LOCAL_MAGIC, os.X_OK) else shutil.which('magic')

# One generic scmos NFET with source/drain/gate labels. This is a runtime
# integration fixture, not a SKY130 design or a foundry-qualified layout.
_GENERIC_LAYOUT = '''magic
tech scmos
timestamp 1
<< polysilicon >>
rect 8 8 10 12
rect 8 -4 10 0
<< ndiffusion >>
rect 0 0 8 8
rect 10 0 20 8
<< ntransistor >>
rect 8 0 10 8
<< labels >>
rlabel ndiffusion 2 2 2 2 0 source
rlabel ndiffusion 16 2 16 2 0 drain
rlabel polysilicon 9 10 9 10 0 gate
<< end >>
'''


@unittest.skipUnless(_MAGIC, 'Magic is not installed')
class MagicIntegrationTests(unittest.TestCase):
    def test_extract_generic_layout_keeps_artifacts_outside_input_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / 'project with spaces'
            layout = project / 'layout/generic.mag'
            layout.parent.mkdir(parents=True)
            layout.write_text(_GENERIC_LAYOUT)
            pdk_root = root / 'generic technology config'
            magicrc = pdk_root / 'generic/libs.tech/magic/generic.magicrc'
            magicrc.parent.mkdir(parents=True)
            magicrc.write_text('# Generic bundled scmos only; no foundry PDK.\ntech load scmos\n')
            config = {'tools': {'magic': _MAGIC}, 'pdk_root': str(pdk_root),
                      'pdk_variant': 'generic'}

            prepared = eda.prepare(project, 'extract', {'layout': 'layout/generic.mag'}, config)
            manager = RunManager()
            started = manager.start(project, 'extract', **prepared)
            result = manager.wait(project, started['id'], timeout=20)
            run = project / '.af/runs' / result['id']
            log = (run / 'output/process.log').read_text()
            self.assertEqual(result['status'], 'succeeded', log)
            extracted = run / 'output/extracted.spice'
            self.assertTrue(extracted.is_file(), log)
            spice = extracted.read_text()
            self.assertRegex(spice, r'(?m)^M\S+\s+drain\s+gate\s+source\s+Gnd\s+nfet\b')
            self.assertRegex(spice, r'(?m)^C\S+\s+gate\s+Gnd\s+\S+')
            self.assertIn('extracted.spice', result['artifact_hashes'])
            self.assertTrue(any((run / 'output').glob('*.ext')), log)
            self.assertEqual(list((run / 'input').rglob('*.ext')), [])
            self.assertEqual(layout.read_text(), _GENERIC_LAYOUT)
            source_hash = hashlib.sha256(_GENERIC_LAYOUT.encode()).hexdigest()
            self.assertEqual(result['source_hashes']['layout/generic.mag'], source_hash)
            self.assertEqual(result['input_hashes']['layout/generic.mag'], source_hash)
            self.assertFalse(result['pdk']['available'])  # No complete foundry PDK was supplied.


if __name__ == '__main__':
    unittest.main()
