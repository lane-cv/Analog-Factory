from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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

    def test_streamed_display_keeps_narrow_extrema_and_endpoint_without_read_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'large.raw'
            with path.open('w') as stream:
                stream.write('Title: narrow pulse\nFlags: real\nNo. Variables: 2\nNo. Points: 10001\nVariables:\n0 time time\n1 v(out) voltage\nValues:\n')
                for index in range(10001):
                    value = 100 if index == 1237 else -20 if index == 4211 else 0
                    stream.write(f'{index} {index * .001}\n {value}\n')
            with patch.object(Path, 'read_text', side_effect=AssertionError('Do not materialize rawfiles')):
                data = parse_raw(path, max_points=100)
            self.assertLessEqual(len(data['points']), 100)
            self.assertEqual(data['original_point_count'], 10001)
            self.assertEqual(data['sample_indices'][0], 0)
            self.assertEqual(data['sample_indices'][-1], 10000)
            self.assertIn(100, [row[1] for row in data['points']])
            self.assertIn(-20, [row[1] for row in data['points']])
            self.assertEqual(data['display_sampling']['method'], 'bucket_extrema')

    def test_sampling_still_validates_dropped_rows_and_dimension_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'large.raw'
            text = 'Flags: real\nNo. Variables: 2\nNo. Points: 100\nVariables:\n0 time time\n1 v(out) voltage\nValues:\n'
            text += ''.join(f'{i} {i}\n {"nan" if i == 51 else 0}\n' for i in range(100))
            path.write_text(text)
            with self.assertRaisesRegex(ValueError, 'nonfinite'):
                parse_raw(path, max_points=10)
            path.write_text('No. Variables: 1000000000\nNo. Points: 1\nVariables:\n')
            with self.assertRaisesRegex(ValueError, 'dimensions'):
                parse_raw(path)

    def test_complex_display_samples_keep_components_aligned_and_validate_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'complex.raw'
            text = 'Flags: complex\nNo. Variables: 2\nNo. Points: 100\nVariables:\n0 frequency frequency\n1 v(out) voltage\nValues:\n'
            text += ''.join(f'{i} {i+1},0\n {30 if i == 51 else 0},{-40 if i == 27 else 0}\n' for i in range(100))
            path.write_text(text)
            data = parse_raw(path, max_points=12)
            self.assertLessEqual(len(data['points']), 12)
            self.assertIn(51, data['sample_indices'])
            self.assertIn(27, data['sample_indices'])
            for index, row, components in zip(data['sample_indices'], data['points'], data['complex_points']):
                self.assertEqual(row[0], index + 1)
                self.assertEqual(components[0], [index + 1, 0])
                self.assertEqual(row[1], abs(complex(*components[1])))
            path.write_text(text.rsplit('\n', 2)[0])
            with self.assertRaisesRegex(ValueError, 'Truncated'):
                parse_raw(path, max_points=12)


if __name__ == '__main__':
    unittest.main()
