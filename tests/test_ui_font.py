"""The bundled offline font manifests must cover authored Chinese UI strings."""
import hashlib
import json
import unittest

from scripts.update_ui_font import DESTINATION, interface_characters


class InterfaceFontTests(unittest.TestCase):
    def test_local_subsets_cover_interface_text_and_match_manifest(self):
        coverage = set()
        for manifest in ['source.json', 'cjk-supplement-source.json']:
            with self.subTest(manifest=manifest):
                metadata = json.loads((DESTINATION / manifest).read_text(encoding='utf-8'))
                data = (DESTINATION / metadata['file']).read_bytes()
                self.assertEqual(data[:4], b'wOF2')
                self.assertEqual(hashlib.sha256(data).hexdigest(), metadata['sha256'])
                self.assertEqual(len(data), metadata['bytes'])
                self.assertEqual(len(metadata['characters']), metadata['character_count'])
                coverage.update(metadata['characters'])
        self.assertFalse(set(interface_characters()) - coverage,
                         'Refresh local font subsets after adding interface translations')


if __name__ == '__main__':
    unittest.main()
