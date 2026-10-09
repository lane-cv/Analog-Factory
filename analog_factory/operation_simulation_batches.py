"""Structured registry adapters for advanced simulator scenarios."""
from .operation_catalog import JOB, OPEN_OBJECT, _project
from .operations import Operation
from .simulation_batches import BATCH_SCHEMA, QUALIFICATION_SCHEMA, capabilities, preview, start_batch, start_qualification


def register_simulation_batches(registry):
    workspace = registry.workspace
    registry.register(Operation(operation_id='simulation.statistics.qualify',name='Qualify statistical model behavior',
        description='Run five real matched-device probes to check SKY130 seed repeatability and pair correlation using installed semantics or explicitly selected experimental caching of global FET draws.',
        module='simulation',input_schema=_project(QUALIFICATION_SCHEMA['properties'],QUALIFICATION_SCHEMA['required']),
        output_schema=JOB,handler=lambda c,p:start_qualification(workspace,p['project_id'],{k:v for k,v in p.items() if k != 'project_id'}),
        permissions=('execute',),confirmation='agent',versioned=True,asynchronous=True,cancellable=True,task_kind='job',
        preconditions=('Configured SKY130 library with explicit MC process/mismatch switches','No active orchestration job'),
        side_effects=('Creates a durable qualification job and five real simulator snapshots; explicit experimental semantics bundle adapted models into snapshots without modifying the installed PDK',),
        usage='Installed semantics are the default. Explicit model_semantics=cache_global_fet_draws supports only global_process/mc and requires the same explicit selection in batch.statistics. A successful job qualifies its exact simulator, source/model/generated hashes and tested nfet_01v8/pfet_01v8 families for this server session; it does not validate foundry distributions.',
        examples=({'parameters':{'project_id':'0123456789abcdef0123456789abcdef','library':'/pdk/sky130A/libs.tech/combined/sky130.lib.spice','kind':'global_process','section':'mc'}},)))
    sample = {'project_id':'0123456789abcdef0123456789abcdef','spec':{
        'source':'testbenches/pre.cir','analysis':{'type':'tran','parameters':{'step':'2u','stop':'5m'}},
        'axes':[{'parameter':'RD','values':[2000,3300,5000]}],
        'measurements':[{'kind':'swing','name':'swing_vpp','output':'out','start_s':.001,'stop_s':.005}]}}
    registry.register(Operation(operation_id='simulation.batch.capabilities',name='Advanced simulation capabilities',
        description='Discover the real simulator, configured model library sections, and statistical qualification requirements.',
        module='simulation',input_schema=_project(),output_schema=OPEN_OBJECT,
        handler=lambda c,p:capabilities(workspace,p['project_id']),kind='query',permissions=('read',),idempotent=True,
        examples=({'parameters':{'project_id':sample['project_id']}},)))
    registry.register(Operation(operation_id='simulation.batch.preview',name='Preview advanced simulation batch',
        description='Validate bounded scenario products, declared parameters, model sections and measurement directives without editing sources or running tools.',
        module='simulation',input_schema=_project({'spec':BATCH_SCHEMA},['spec']),output_schema=OPEN_OBJECT,
        handler=lambda c,p:preview(workspace,p['project_id'],p['spec']),kind='query',permissions=('read',),
        idempotent=True,examples=({'parameters':sample},)))
    registry.register(Operation(operation_id='simulation.batch.start',name='Run advanced simulation batch',
        description='Run bounded parameter/voltage/temperature/model scenarios using immutable generated snapshots and real ngspice measurements.',
        module='simulation',input_schema=_project({'spec':BATCH_SCHEMA},['spec']),output_schema=JOB,
        handler=lambda c,p:start_batch(workspace,p['project_id'],p['spec']),permissions=('execute',),
        confirmation='agent',versioned=True,asynchronous=True,cancellable=True,task_kind='job',
        preconditions=('No active orchestration job in this project','Measurements and requested model sections are supported and validated'),
        side_effects=('Creates a durable simulation-batch job and real run evidence through registered run operations; original source files remain unchanged',),
        usage='Preview with simulation.batch.preview, supply expected_version to start, then inspect/cancel via job.get and job.cancel. Statistical parameter variation is not a foundry model.',
        examples=({'parameters':sample},)))
