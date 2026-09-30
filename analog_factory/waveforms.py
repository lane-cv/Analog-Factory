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
import re

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


def parse_raw(path: Path) -> dict:
    """Return title, plot, variables, and numeric points; reject truncated data."""
    try:
        content = Path(path).read_text(encoding='utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError('Binary rawfiles are unsupported; request ASCII ngspice output') from exc
    if re.search(r'^Binary:', content, re.M):
        raise ValueError('Binary rawfiles are unsupported; request ASCII ngspice output')
    lines = iter(content.splitlines())
    plots = []
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
        if variable_count < 1 or point_count < 0:
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
            points, complex_points = [], []
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
                        row.append(pair[0] if variable == 0 else math.hypot(*pair))
                    else:
                        row.append(_number(line))
                points.append(row)
                if complex_mode:
                    complex_points.append(components)
        except StopIteration as exc:
            raise ValueError('Truncated waveform data') from exc
        plot = {'title': headers.get('title', ''), 'plot': headers.get('plotname', ''),
                'variables': variables, 'points': points}
        if complex_mode:
            plot['complex_points'] = complex_points
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
