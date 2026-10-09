"""Full-resolution numerical measurements, independent of display sampling."""
from .operations import Operation
from .operation_catalog import _run, object_schema, array_schema, TEXT, ID
from .waveforms import raw_statistics

INTEGER = {'type': 'integer', 'minimum': 0}


def register_operations(registry):
    nullable = {'oneOf': [{'type': 'number'}, {'type': 'null'}]}
    statistic = object_schema({'count': INTEGER, **{name: nullable for name in ('min', 'max', 'mean', 'rms', 'peak_to_peak')}},
                              ['count', 'min', 'max', 'mean', 'rms', 'peak_to_peak'])
    variable = object_schema({'name': TEXT, 'unit': TEXT, 'type': TEXT, 'component': TEXT}, ['name', 'unit', 'type'])
    def measure(context, parameters):
        data = raw_statistics(registry.workspace.artifact(parameters['project_id'], parameters['run_id'], 'waveforms.raw'))
        plots = data.get('plots') or [data]
        index = parameters['plot_index']
        if index >= len(plots): raise ValueError('Waveform plot index is unavailable')
        return {**plots[index], 'run_id': parameters['run_id'], 'plot_index': index, 'artifact': 'waveforms.raw'}
    registry.register(Operation('run.waveform_stats', 'Measure every raw waveform sample',
        'Stream the original ASCII rawfile to compute full-sample min, max, mean, RMS and peak-to-peak; display downsampling never changes measurements. Empty or unrepresentable statistics are null.',
        'run', _run({'plot_index': {'type': 'integer', 'minimum': 0, 'maximum': 15, 'default': 0}}),
        object_schema({'run_id': ID, 'plot_index': INTEGER, 'artifact': TEXT, 'title': TEXT, 'plot': TEXT,
            'variables': array_schema(variable), 'statistics': array_schema(statistic), 'original_point_count': INTEGER,
            'definition': TEXT}, ['run_id', 'plot_index', 'artifact', 'variables', 'statistics', 'original_point_count', 'definition']),
        measure, kind='query', idempotent=True, tags=('run', 'waveform'),
        preconditions=('Recorded ASCII waveforms.raw artifact is within the 512 MiB parser limit',),
        examples=({'project_id': '0'*32, 'run_id': '1'*32},)))
