"""New-project checks must match creation, rather than the generic PDK probe."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from analog_factory.ota5_reference import materialize
from analog_factory.toolchain import detect


class ProjectReadinessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = {'pdk_root': str(self.root), 'pdk_variant': 'sky130A'}
        # Probe missing executables without relying on the developer machine's tools.
        self.config['tools'] = {name: str(self.root / 'absent' / name)
                                for name in ('ngspice', 'magic', 'xschem', 'netgen', 'codex', 'claude')}

    def prepare(self, *, combined=True, generic=True):
        paths = ['libs.tech/xschem/xschemrc', 'libs.tech/magic/sky130A.magicrc',
                 'libs.tech/netgen/sky130A_setup.tcl']
        if combined:
            paths.append('libs.tech/combined/sky130.lib.spice')
        if generic:
            paths.append('libs.tech/ngspice/sky130.lib.spice')
        for relative in paths:
            path = self.root / 'sky130A' / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('* local test asset\n')

    def ota_status(self, status):
        self.assertIn('ota5', status['pdk'], 'Status must qualify the OTA creation assets separately')
        return status['pdk']['ota5']

    def test_generic_pdk_does_not_qualify_missing_ota_combined_library(self):
        self.prepare(combined=False)
        status = detect(self.config)
        self.assertTrue(status['pdk']['available'])
        self.assertFalse(self.ota_status(status)['available'])
        self.assertIn('libs.tech/combined/sky130.lib.spice', self.ota_status(status)['diagnostic'])
        with self.assertRaisesRegex(ValueError, 'combined'):
            materialize(self.config)

    def test_ota_creation_and_status_accept_combined_assets_without_generic_library_or_executables(self):
        self.prepare(generic=False)
        status = detect(self.config)
        self.assertFalse(status['pdk']['available'])
        self.assertTrue(self.ota_status(status)['available'])
        self.assertEqual(self.ota_status(status)['paths']['models'],
                         str(self.root / 'sky130A/libs.tech/combined/sky130.lib.spice'))
        self.assertIn('testbenches/pre.cir', materialize(self.config))
        self.assertFalse(status['tools']['ngspice']['available'])

    def test_environment_pdk_fallback_does_not_claim_creation_can_use_unconfigured_root(self):
        self.prepare()
        with patch.dict(os.environ, {'PDK_ROOT': str(self.root), 'PDK': 'sky130A'}):
            status = detect({'tools': self.config['tools']})
        self.assertTrue(status['pdk']['available'])
        self.assertFalse(self.ota_status(status)['available'])
        with self.assertRaisesRegex(ValueError, 'configured'):
            materialize({})

    def test_unusable_variant_remains_unavailable_in_status_and_creation(self):
        self.prepare()
        config = dict(self.config, pdk_variant='sky130B')
        self.assertFalse(self.ota_status(detect(config))['available'])
        with self.assertRaisesRegex(ValueError, 'sky130A'):
            materialize(config)

    def test_invalid_quoted_model_root_is_rejected_before_creation(self):
        self.root = self.root / 'quoted"root'
        self.config['pdk_root'] = str(self.root)
        self.prepare()
        self.assertFalse(self.ota_status(detect(self.config))['available'])
        with self.assertRaisesRegex(ValueError, 'quotes'):
            materialize(self.config)


if __name__ == '__main__':
    unittest.main()
