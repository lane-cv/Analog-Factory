"""Registry contracts for optional, locally supervised language-model training."""
from .operations import Operation
from .training import manager, task_catalog


def obj(properties=None, required=(), extra=False):
    return {'type': 'object', 'properties': properties or {}, 'required': list(required),
            'additionalProperties': extra}


ID = {'type': 'string', 'pattern': '^[a-f0-9]{32}$'}
TEXT = {'type': 'string'}
OPEN = obj(extra=True)
OPTIONS = obj({'framework': {'type': 'string', 'enum': ['trl_cpu']},
    'taskset_id': {'type': 'string', 'pattern': '^(educational-amplifier-v1|taskset-[a-f0-9]{24})$'},
    'steps': {'type': 'integer', 'minimum': 1, 'maximum': 128},
    'train_tasks': {'type': 'integer', 'minimum': 2, 'maximum': 64},
    'eval_tasks': {'type': 'integer', 'minimum': 1, 'maximum': 32},
    'seed': {'type': 'integer', 'minimum': 0, 'maximum': 2**31-1},
    'wall_seconds': {'type': 'integer', 'minimum': 60, 'maximum': 14400},
    'max_completion_length': {'type':'integer','minimum':96,'maximum':2048,
        'description':'Total completion token budget including policy output, tool observations and framing. New jobs default to 96; partial patches and resume requests preserve an omitted value.'}})
JOB = obj({'id': ID, 'kind': TEXT, 'status': TEXT, 'classification': TEXT,
           'model_weights_updated': {'type': 'boolean'}, 'phase': TEXT, 'curves': {'type': 'array', 'items': OPEN},
           'checkpoint': OPEN, 'evaluation': OPEN}, ['id', 'kind', 'status'], extra=True)
TASKSET_ID = {'type': 'string', 'pattern': '^taskset-[a-f0-9]{24}$'}
SPECIFICATION = obj({'name': {'type': 'string', 'minLength': 1, 'maxLength': 80},
    'family': {'type': 'string', 'enum': ['amplifier', 'current_mirror']},
    'model': {'type': 'string', 'enum': [task_catalog.MODEL_ID]},
    'seed': {'type': 'integer', 'minimum': 0, 'maximum': 2**31-1},
    'max_actions': {'type': 'integer', 'minimum': 1, 'maximum': 6},
    **{key: OPEN for key in ('counts', 'conditions', 'targets', 'constraints', 'actions', 'scoring', 'cost', 'robustness')}},
    ['name', 'family', 'model', 'seed', 'max_actions', 'counts', 'conditions', 'targets', 'constraints', 'actions', 'scoring', 'cost', 'robustness'])
TASKSET = obj({'id': TASKSET_ID, 'schema_version': {'type': 'integer'}, 'evaluator_version': TEXT,
               'specification': SPECIFICATION, 'splits': OPEN, 'hashes': OPEN},
              ['id', 'schema_version', 'evaluator_version', 'specification', 'splits', 'hashes'])


def register_operations(registry):
    workspace = registry.workspace
    def add(name, description, schema, output, handler, *, execute=False, task=False):
        registry.register(Operation(name, name.replace('.', ' ').title(), description, 'training', schema, output,
            handler, kind='business' if execute else 'query', permissions=('execute',) if execute else ('read',),
            confirmation='agent' if execute else 'none', asynchronous=task, cancellable=task,
            task_kind='job' if task else None, idempotent=not execute, tags=('training',),
            side_effects=('Creates isolated training/evaluation evidence and bounded local processes',) if execute else (),
            examples=({},) if not schema['required'] else ()))
    add('training.resources', 'Inspect optional local trainer, model, resources and qualification; no ML import.',
        obj(), obj({'frameworks': OPEN, 'host': OPEN, 'model': OPEN, 'qualification': OPEN, 'runtime': OPEN},
                   ['frameworks', 'host', 'model', 'qualification', 'runtime']), lambda c,p: manager.resources())
    add('training.tasksets', 'Inspect fixed train/validation/test splits, evaluator scope and action bounds.',
        obj(), obj({'taskset_id': TEXT, 'counts': OPEN, 'hashes': OPEN, 'bounds': OPEN,
                    'max_actions': {'type': 'integer'}, 'conditions_per_action': {'type': 'integer'},
                    'coverage': TEXT, 'evaluation_specification': TEXT},
                   ['taskset_id', 'counts', 'hashes', 'bounds', 'max_actions', 'conditions_per_action', 'coverage', 'evaluation_specification']),
        lambda c,p: manager.taskset_summary())
    add('training.taskset.validate', 'Validate and deterministically build a bounded taskset; accepts data only, never scoring code.',
        obj({'specification': SPECIFICATION}, ['specification']), TASKSET,
        lambda c,p: task_catalog.build(p['specification']))
    add('training.taskset.get', 'Read an immutable project taskset after verifying its complete content hash.',
        obj({'project_id': ID, 'taskset_id': TASKSET_ID}, ['project_id', 'taskset_id']), TASKSET,
        lambda c,p: task_catalog.get(workspace, p['project_id'], p['taskset_id']))
    add('training.taskset.list', 'List the original reference and configured tasksets, with trusted family defaults.',
        obj({'project_id': ID}, ['project_id']), obj({'tasksets': {'type': 'array', 'items': OPEN}, 'defaults': OPEN}, ['tasksets', 'defaults']),
        lambda c,p: {'tasksets': task_catalog.list_tasksets(workspace, p['project_id']),
                    'defaults': {family: task_catalog.defaults(family) for family in ('amplifier', 'current_mirror')}})
    registry.register(Operation('training.taskset.create', 'Publish training taskset',
        'Publish immutable circuit, constraints, objectives and split data. Scoring executables remain trusted application code.',
        'training', obj({'project_id': ID, 'specification': SPECIFICATION}, ['project_id', 'specification']), TASKSET,
        lambda c,p: task_catalog.create(workspace, p['project_id'], p['specification']),
        kind='business', permissions=('configure',), confirmation='agent', idempotent=True,
        side_effects=('Publishes a content-addressed taskset; existing tasksets and runs remain unchanged',), tags=('training',)))
    add('training.start', 'Train pretrained language-model LoRA weights with TRL GRPO and independent real ngspice rewards.',
        obj({'project_id': ID, 'options': OPTIONS}, ['project_id']), JOB,
        lambda c,p: manager.start(workspace, p['project_id'], p.get('options')), execute=True, task=True)
    add('training.evaluate', 'Reload a verified local checkpoint and evaluate the fixed held-out tasks.',
        obj({'project_id': ID, 'checkpoint_job_id': ID, 'resume_job_id': ID, 'options': OPTIONS}, ['project_id', 'checkpoint_job_id']), JOB,
        lambda c,p: manager.start(workspace, p['project_id'], p.get('options'), checkpoint_job_id=p['checkpoint_job_id'], resume_job_id=p.get('resume_job_id')),
        execute=True, task=True)
    add('training.get', 'Inspect training job, measured resources, curves, checkpoint, comparisons and bounded log tail.',
        obj({'project_id': ID, 'job_id': ID}, ['project_id', 'job_id']), JOB,
        lambda c,p: manager.get(workspace, p['project_id'], p['job_id']))
    add('training.list', 'List local training, evaluation and replay jobs.',
        obj({'project_id': ID}, ['project_id']), obj({'jobs': {'type': 'array', 'items': JOB}}, ['jobs']),
        lambda c,p: {'jobs': [j for j in workspace.jobs.list(workspace.projects.path(p['project_id']))
                             if j['kind'] in ('training', 'training_evaluation', 'training_replay')]})
    def cancel(c,p):
        manager.get(workspace, p['project_id'], p['job_id'])
        return workspace.jobs.cancel(workspace.projects.path(p['project_id']), p['job_id'])
    add('training.cancel', 'Cancel a supervised trainer and its active simulator children.',
        obj({'project_id': ID, 'job_id': ID}, ['project_id', 'job_id']), JOB, cancel, execute=True)
    add('training.replay', 'Replay bounded recorded actions against the fixed task and real ngspice, without model training.',
        obj({'project_id': ID, 'job_id': ID, 'episode': {'type': 'integer', 'minimum': 0, 'maximum': 100000}},
            ['project_id', 'job_id', 'episode']), JOB,
        lambda c,p: manager.replay(workspace, p['project_id'], p['job_id'], p['episode']), execute=True, task=True)
