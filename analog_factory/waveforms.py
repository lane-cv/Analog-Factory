"""Read ngspice ASCII rawfiles without numerical dependencies.

For complex plots, ``points`` contains magnitudes (the scale keeps its real
component) and ``complex_points`` retains [real, imaginary] for every value.
Multiple plots are retained in ``plots``; top-level fields describe the first.
Binary rawfiles are rejected; the EDA adapter requests ASCII output explicitly.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

_UNITS = {'time': 's', 'frequency': 'Hz', 'voltage': 'V', 'current': 'A',
          'resistance': 'Ω', 'capacitance': 'F', 'temperature': '°C'}


def _number(value):
    try:
        number = float(value.replace('D', 'e').replace('d', 'e'))
    except ValueError as exc:
        raise ValueError(f'Invalid waveform value: {value!r}') from exc
    if not math.isfinite(number):
        raise ValueError('Waveform contains nonfinite values')
    return number


def parse_raw(path: Path, *, max_points=20000) -> dict:
    """Stream validated ASCII data into a bounded display representation.

    Large plots retain bucket extrema and endpoints; original artifacts remain
    authoritative for measurements and full-resolution export.
    """
    if type(max_points) is not int or not 8 <= max_points <= 20000:
        raise ValueError('Display point limit must be between 8 and 20000')
    return _read_raw(path, max_points)


def raw_statistics(path: Path) -> dict:
    """Compute statistics from every original sample with bounded memory.

These are sample-weighted statistics, not time integrals. Complex signal values
use magnitude; the complex scale uses its real component, matching parse_raw.
"""
    return _read_raw(path, 20000, statistics_only=True)


def _read_raw(path, max_points, statistics_only=False):
    if Path(path).stat().st_size > 512 * 1024 * 1024:
        raise ValueError('Rawfile exceeds the 512 MiB display limit; download the original artifact')
    try:
        with Path(path).open(encoding='utf-8') as stream:
            return _parse_lines(_bounded_lines(stream), max_points, statistics_only)
    except UnicodeDecodeError as exc:
        raise ValueError('Binary rawfiles are unsupported; request ASCII ngspice output') from exc


def _bounded_lines(stream):
    while line := stream.readline(65537):
        if len(line) > 65536:
            raise ValueError('Rawfile line exceeds the display parser limit')
        if line.strip().lower() == 'binary:':
            raise ValueError('Binary rawfiles are unsupported; request ASCII ngspice output')
        yield line


class _DisplaySamples:
    def __init__(self, count, width, complex_mode, capacity):
        self.points, self.components, self.indices = [], [], []
        self.sampled = count > capacity
        self.complex_mode = complex_mode
        self.minimum, self.maximum, self.bucket, self.last = [], [], None, None
        channels = max(1, width - 1) * (3 if complex_mode else 1)
        buckets = (capacity - 2) // (2 * channels)
        if self.sampled and buckets < 1:
            raise ValueError('Too many waveform variables/plots for the display budget; download the original artifact')
        self.bucket_size = max(1, math.ceil(count / max(1, buckets)))

    def append(self, record):
        index, row, components = record
        if self.indices and index == self.indices[-1]:
            return
        self.indices.append(index)
        self.points.append(row)
        if self.complex_mode:
            self.components.append(components)

    def flush(self):
        records = {record[0]: record for _, record in self.minimum + self.maximum}
        for index in sorted(records):
            self.append(records[index])

    def feed(self, index, row, components):
        record = (index, row, components)
        self.last = record
        if not self.sampled:
            self.append(record)
            return
        if index == 0:
            self.append(record)
        bucket = index // self.bucket_size
        values = row[1:] or row
        if self.complex_mode:
            values = values + [value for pair in (components[1:] or components) for value in pair]
        if bucket != self.bucket:
            self.flush()
            self.bucket = bucket
            self.minimum = [(value, record) for value in values]
            self.maximum = self.minimum.copy()
        else:
            for channel, value in enumerate(values):
                if value < self.minimum[channel][0]:
                    self.minimum[channel] = (value, record)
                if value > self.maximum[channel][0]:
                    self.maximum[channel] = (value, record)

    def finish(self):
        if self.sampled:
            self.flush()
            self.append(self.last)


class _Statistics:
    def __init__(self, count, width):
        self.count = count
        self.values = [{'min': math.inf, 'max': -math.inf, 'sum': 0., 'correction': 0.,
                        'scale': 0., 'squares': 0.} for _ in range(width)]

    def feed(self, row):
        for state, value in zip(self.values, row):
            state['min'] = min(state['min'], value)
            state['max'] = max(state['max'], value)
            # Sum values/count with compensated addition so repeated finite
            # values cannot overflow merely because the rawfile has many rows.
            increment = value / self.count - state['correction']
            updated = state['sum'] + increment
            state['correction'] = (updated - state['sum']) - increment
            state['sum'] = updated
            magnitude = abs(value)
            if magnitude > state['scale']:
                state['squares'] = 1 + state['squares'] * (state['scale'] / magnitude) ** 2
                state['scale'] = magnitude
            elif magnitude:
                state['squares'] += (magnitude / state['scale']) ** 2

    def finish(self):
        result = []
        for state in self.values:
            item = {'count': self.count, **{name: None for name in ('min', 'max', 'mean', 'rms', 'peak_to_peak')}}
            if self.count:
                item.update(min=state['min'], max=state['max'], mean=state['sum'],
                    rms=state['scale'] * math.sqrt(min(1., state['squares'] / self.count)),
                    peak_to_peak=state['max'] - state['min'])
                for key in ('mean', 'rms', 'peak_to_peak'):
                    if not math.isfinite(item[key]): item[key] = None
            result.append(item)
        return result


def _parse_lines(lines, max_points, statistics_only=False):
    plots = []
    remaining_values = 1000000
    while True:
        headers = {}
        for line in lines:
            key, separator, value = line.partition(':')
            if separator:
                headers[key.lower().strip()] = value.strip()
            if key.lower().strip() == 'variables' and separator:
                break
        else:
            if headers:
                raise ValueError('Rawfile has no variable table')
            break
        try:
            variable_count = int(headers['no. variables'])
            point_count = int(headers['no. points'])
        except (KeyError, ValueError) as exc:
            raise ValueError('Rawfile is missing valid variable/point counts') from exc
        if not 1 <= variable_count <= 256 or not 0 <= point_count <= 10000000 or len(plots) >= 16:
            raise ValueError('Invalid rawfile dimensions')
        variables = []
        complex_mode = 'complex' in headers.get('flags', '').lower()
        try:
            for index in range(variable_count):
                parts = next(lines).split()
                if len(parts) < 3 or int(parts[0]) != index:
                    raise ValueError('Invalid rawfile variable table')
                entry = {'name': parts[1], 'unit': _UNITS.get(parts[2], parts[2]), 'type': parts[2]}
                if complex_mode:
                    entry['component'] = 'real' if index == 0 else 'magnitude'
                variables.append(entry)
            marker = next(lines)
            while not marker.strip():
                marker = next(lines)
            if marker.strip().lower() != 'values:':
                raise ValueError('Expected ASCII Values section')
            weight = variable_count * (3 if complex_mode else 1) + 1
            capacity = min(max_points, remaining_values // weight)
            samples = None if statistics_only else _DisplaySamples(point_count, variable_count, complex_mode, capacity)
            statistics = _Statistics(point_count, variable_count) if statistics_only else None
            for index in range(point_count):
                row, components = [], []
                for variable in range(variable_count):
                    line = next(lines).strip()
                    while not line:
                        line = next(lines).strip()
                    if variable == 0:
                        point_index, line = line.split(None, 1)
                        if int(point_index) != index:
                            raise ValueError('Unexpected point index in rawfile')
                    if complex_mode:
                        real, imaginary = line.split(',', 1)
                        pair = [_number(real.strip()), _number(imaginary.strip())]
                        components.append(pair)
                        magnitude = pair[0] if variable == 0 else math.hypot(*pair)
                        if not math.isfinite(magnitude):
                            raise ValueError('Waveform contains nonfinite magnitude')
                        row.append(magnitude)
                    else:
                        row.append(_number(line))
                if statistics:
                    statistics.feed(row)
                else:
                    samples.feed(index, row, components)
            if samples:
                samples.finish()
        except StopIteration as exc:
            raise ValueError('Truncated waveform data') from exc
        plot = {'title': headers.get('title', ''), 'plot': headers.get('plotname', ''),
                'variables': variables, 'original_point_count': point_count}
        if statistics_only:
            plot.update(statistics=statistics.finish(), definition='Every original raw sample; sample-weighted, not time-weighted. Complex signals use magnitude; scale uses real component.')
            plots.append(plot)
            continue
        plot['points'] = samples.points
        remaining_values -= len(samples.points) * weight
        if samples.sampled:
            plot['sample_indices'] = samples.indices
            plot['display_sampling'] = {'method': 'bucket_extrema', 'returned_points': len(samples.points),
                'original_points': point_count,
                'description': 'Endpoints and bucket extrema of magnitude/real/imaginary traces; phase and ratios can miss intermediate extrema. Display CSV includes retained samples only.'}
        if complex_mode:
            plot['complex_points'] = samples.components
        plots.append(plot)
    if not plots:
        raise ValueError('Rawfile has no plots')
    result = dict(plots[0])
    if len(plots) > 1:
        result['plots'] = plots
    return result


def parse_csv(path: Path) -> dict:
    """Read a header row and numeric CSV columns; units are unknown unless named."""
    with Path(path).open(newline='') as stream:
        rows = csv.reader(stream)
        try:
            names = next(rows)
        except StopIteration as exc:
            raise ValueError('Empty waveform CSV') from exc
        points = []
        for row in rows:
            if not row:
                continue
            if len(row) != len(names):
                raise ValueError('Inconsistent CSV column count')
            points.append([_number(value) for value in row])
    return {'title': Path(path).stem, 'plot': 'CSV',
            'variables': [{'name': name, 'unit': _UNITS.get(name.lower(), '')} for name in names], 'points': points}
