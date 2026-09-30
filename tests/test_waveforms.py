from pathlib import Path
import tempfile
import unittest

from analog_factory.waveforms import parse_raw


class WaveformTests(unittest.TestCase):
    def parse(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'wave.raw'
            path.write_text(text)
            return parse_raw(path)

    def test_real_ascii_and_units(self):
        data = self.parse('Title: RC\nPlotname: Transient Analysis\nFlags: real\nNo. Variables: 2\nNo. Points: 2\nVariables:\n0 time time\n1 v(out) voltage\nValues:\n0 0\n 1.0\n1 0.001\n 2.0\n')
        self.assertEqual(data['title'], 'RC')
        self.assertEqual(data['points'], [[0.0, 1.0], [0.001, 2.0]])
        self.assertEqual(data['variables'][1]['unit'], 'V')

    def test_complex_preserves_components_and_reports_magnitude(self):
        data = self.parse('Title: AC\nPlotname: AC Analysis\nFlags: complex\nNo. Variables: 2\nNo. Points: 1\nVariables:\n0 frequency frequency\n1 v(out) voltage\nValues:\n0 1e3,0\n 3,4\n')
        self.assertEqual(data['points'], [[1000.0, 5.0]])
        self.assertEqual(data['complex_points'][0][1], [3.0, 4.0])
        self.assertEqual(data['variables'][1]['component'], 'magnitude')

    def test_truncated_and_binary_files_are_rejected(self):
        for text in ['Title: x\nBinary:\nabc', 'No. Variables: 2\nNo. Points: 1\nVariables:\n0 time time\n1 v(x) voltage\nValues:\n0 0\n']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.parse(text)


if __name__ == '__main__':
    unittest.main()
