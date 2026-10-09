"""Unified OTA characterization operation."""
from .operation_catalog import JOB, _project
from .operations import Operation
from .characterization import SCHEMA,start,prepare


def register_characterization(registry):
    def available(context,parameters):
        try:
            prepare(registry.workspace,parameters['project_id'],parameters.get('spec',{}))
            if any(job['status'] in ('queued','running') for job in registry.workspace.jobs.list(registry.workspace.projects.path(parameters['project_id']))):
                return ['Another orchestration job is active in this project']
        except (ValueError,RuntimeError,OSError) as error:
            return [str(error)]
        return []
    registry.register(Operation(operation_id='reference.characterize',name='Characterize OTA across PVT',
        description='Run actual immutable pre/post OTA PVT AC, operating point, offset sweep, transient step and output-noise characterization. Observations and explicit target verdicts remain distinct.',
        module='verification',availability=available,input_schema=_project({'spec':{**SCHEMA,'default':{}}}),output_schema=JOB,
        handler=lambda c,p:start(registry.workspace,p['project_id'],p['spec']),permissions=('execute',),
        confirmation='agent',versioned=True,asynchronous=True,cancellable=True,task_kind='job',
        preconditions=('OTA reference sources and configured SKY130 models','Closed source/model include closure without executable control or analysis overrides','Post-layout view requires extracted parasitic capacitors','No active orchestration job'),
        side_effects=('Creates a bounded characterization job and actual simulator runs; design sources remain unchanged',),
        usage='Supply expected_version; inspect results with job.get and raw evidence with run.get/run.log/run.waveforms. Deterministic PVT is not Monte Carlo or foundry signoff.',
        examples=({'parameters':{'project_id':'0123456789abcdef0123456789abcdef','spec':{'views':['pre'],'analyses':['ac','op'],'cases':[{'corner':'tt','supply_v':1.8,'temperature_c':27}],'targets':{'gain_db_min':30}}}},)))
