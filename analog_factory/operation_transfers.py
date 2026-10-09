"""Registry adapters for bounded artifacts and portable project ZIP streams."""
from .operation_catalog import ID,PATH,TEXT,PROJECT,object_schema as obj,_project
from .operations import Operation
from .project_lifecycle import CHUNK_BYTES,TRANSFER_LIMITS
from .transfers import Transfers,artifact_stat,artifact_read

INT={'type':'integer','minimum':0}
NULL_ID={'oneOf':[ID,{'type':'null'}]}
ROW=obj({'id':ID,'project_id':NULL_ID,'kind':TEXT,'status':TEXT,'size':INT,'received':INT,
    'filename':TEXT,'content_type':TEXT,'chunk_bytes':INT,'expires_in':INT},
    ('id','project_id','kind','status','size','received','filename','content_type','chunk_bytes','expires_in'))
READ=obj({'content_base64':TEXT,'offset':INT,'next_offset':INT,'size':INT,'eof':{'type':'boolean'},'artifact_version':TEXT},
    ('content_base64','offset','next_offset','size','eof'))
CHUNK={'offset':{**INT,'default':0},'limit':{'type':'integer','minimum':1,'maximum':CHUNK_BYTES,'default':CHUNK_BYTES}}


def register_transfers(registry):
    workspace=registry.workspace
    workspace.transfers=manager=Transfers(workspace)
    def add(name,description,schema,output,handler,permissions=('read',),**options):
        if options.get('kind') != 'query':
            options.setdefault('side_effects', ('Creates, updates or removes bounded private transfer state; import finish atomically publishes validated project files',))
        registry.register(Operation(operation_id=name,name=name.replace('.',' ').capitalize(),description=description,
            module=name.split('.')[0],input_schema=schema,output_schema=output,handler=handler,permissions=permissions,**options))
    def handle(extra=None,required=()):return obj({'transfer_id':ID,'project_id':ID,**(extra or {})},('transfer_id',*required))
    add('project.archive.prepare','Prepare a disk-backed source export or full backup including inert training evidence; at most 1 GiB ZIP, 2 GiB expanded, 512 MiB/member. Use transfer.read or the guarded streaming download route.',
        _project({'mode':{'type':'string','enum':['backup','export'],'default':'backup'}}),ROW,
        lambda c,p:manager.prepare(c,p['project_id'],p['mode']),preconditions=('No active project execution','Available bounded transfer disk quota'))
    add('project.import.begin','Reserve a private bounded ZIP upload; expires after 30 minutes idle, owner and project scope bound.',
        obj({'size':{'type':'integer','minimum':1,'maximum':TRANSFER_LIMITS.compressed_bytes}},('size',)),ROW,
        lambda c,p:manager.begin(c,p['size']),permissions=('write',),confirmation='agent')
    add('project.import.append','Append one sequential 256 KiB ZIP chunk; refuses offset replay, overflow and other callers.',
        handle({'offset':INT,'content_base64':{'type':'string','minLength':1,'maxLength':4*((CHUNK_BYTES+2)//3)}},('offset','content_base64')),
        ROW,manager.append,permissions=('write',),confirmation='agent')
    add('project.import.finish','Validate uploaded ZIP and atomically create a project. Nonempty configuration requires configure permission; imported training/checkpoints remain inert and cannot resume or evaluate.',
        handle({'name':{'type':'string','minLength':1,'maxLength':100}}),PROJECT,manager.finish,
        permissions=('write',),confirmation='agent',versioned=True)
    add('transfer.get','Read transfer metadata without exposing any filesystem path.',handle(),ROW,manager.get,kind='query',idempotent=True,source_revision=False)
    add('transfer.read','Read one bounded prepared ZIP chunk; uses the caller and project scope of archive preparation.',
        handle(CHUNK),READ,manager.read,kind='query',idempotent=True,volatile_response=True,source_revision=False)
    add('transfer.release','Delete this caller’s temporary transfer file and release its quota.',handle(),
        obj({'id':ID,'released':{'type':'boolean'}},('id','released')),manager.release,permissions=('read',))
    artifact={'kind':{'type':'string','enum':['run','training','file']},'path':PATH,'run_id':ID,'job_id':ID}
    add('artifact.stat','Inspect one confined regular run, training or visible-file artifact; returns size and a change token without loading content.',
        _project(artifact,('kind','path')),obj({'filename':TEXT,'size':INT,'artifact_version':TEXT,'content_type':TEXT,'chunk_bytes':INT},
        ('filename','size','artifact_version','content_type','chunk_bytes')),lambda c,p:artifact_stat(workspace,p),kind='query',idempotent=True,source_revision=False)
    add('artifact.read','Read at most 256 KiB of a confined artifact, rejecting stale file identity/size/timestamps; editor limits remain unchanged.',
        _project({**artifact,**CHUNK,'artifact_version':{'type':'string','pattern':'^[a-f0-9]{64}$'}},('kind','path','artifact_version')),
        READ,lambda c,p:artifact_read(workspace,p),kind='query',idempotent=True,volatile_response=True,source_revision=False)
