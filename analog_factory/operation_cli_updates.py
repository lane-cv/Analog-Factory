"""Project CLI health and anonymous fixed official release observations."""
from .cli_updates import CliUpdates
from .operation_catalog import ID, object_schema as obj
from .operations import Operation
from uuid import uuid4

TEXT={'type':'string','maxLength':8000}
NULL_TEXT={'anyOf':[TEXT,{'type':'null'}]}
BACKEND={'type':'string','enum':['codex','claude']}
SUCCESS=obj({'version':TEXT,'tag':TEXT,'url':TEXT,'published_at':TEXT,'checked_at':TEXT,'notes':TEXT,'notes_truncated':{'type':'boolean'}},('version','tag','url','published_at','checked_at','notes','notes_truncated'))
ATTEMPT=obj({'checked_at':TEXT,'status':{'type':'string','enum':['succeeded','failed']},'error':NULL_TEXT},('checked_at','status','error'))
ROW=obj({'backend':BACKEND,'source_url':TEXT,'documentation_url':TEXT,'label':{'type':'string','const':'latest published release'},
    'latest_success':{'anyOf':[SUCCESS,{'type':'null'}]},'latest_attempt':{'anyOf':[ATTEMPT,{'type':'null'}]},'stale':{'type':'boolean'}},
    ('backend','source_url','documentation_url','label','latest_success','latest_attempt','stale'))
AUTOMATIC=obj({'enabled':{'type':'boolean'},'interval_hours':{'type':'integer','minimum':6,'maximum':168},'server_running_only':{'type':'boolean','const':True}},('enabled','interval_hours','server_running_only'))
RELEASES=obj({'backends':{'type':'array','items':ROW,'minItems':2,'maxItems':2},'automatic':AUTOMATIC},('backends','automatic'))
TOOL=obj({'backend':BACKEND,'available':{'type':'boolean'},'path':NULL_TEXT,'configured_path':NULL_TEXT,
    'config_source':{'type':'string','enum':['project','workspace','PATH']},'version':NULL_TEXT,'login':{'type':'string','const':'not_checked'},'diagnostic':TEXT,
    'update_status':{'type':'string','enum':['unknown','current','update_available','ahead']}},
    ('backend','available','path','configured_path','config_source','version','login','diagnostic','update_status'))
STATUS=obj({'project_id':{'anyOf':[ID,{'type':'null'}]},'tools':{'type':'array','items':TOOL,'minItems':2,'maxItems':2},'releases':RELEASES},('project_id','tools','releases'))
CHECK=obj({**RELEASES['properties'],'checked_backends':{'type':'array','items':BACKEND,'maxItems':2},'cache_hits':{'type':'array','items':BACKEND,'maxItems':2}},('backends','automatic','checked_backends','cache_hits'))


def register_cli_updates(registry):
    workspace=registry.workspace
    manager=getattr(workspace,'cli_updates',None)
    if manager is None:manager=workspace.cli_updates=CliUpdates(workspace)
    def add(name,description,schema,output,handler,**kwargs):
        examples=({'backends':['codex','claude'],'force':False},) if name=='agent.cli.check_updates' else ({},)
        registry.register(Operation(name,name.replace('.',' ').title(),description,'agents',schema,output,handler,
            kind='query',permissions=('read',),confirmation='none',tags=('cli','updates'),examples=examples,**kwargs))
    def check(context,parameters):
        return manager.check_updates(parameters.get('backends'),parameters.get('force',False),
            on_change=lambda:registry._event('agent.cli.releases',None,uuid4().hex,'changed',None,client_id=context.client_id))
    add('agent.cli.status','Inspect effective project/workspace/PATH CLI executables and strict installed versions, without inference or credential/login reads. Authentication remains not_checked.',
        obj({'project_id':ID}),STATUS,lambda c,p:manager.status(p.get('project_id')))
    add('agent.cli.releases','Read cached fixed official latest published release observations, freshness and previous check failure. No network request.',
        obj(),RELEASES,lambda c,p:manager.releases())
    add('agent.cli.check_updates','Make bounded anonymous HTTPS requests to fixed official GitHub releases/latest sources. This is latest published release, not a delayed stable channel. Never installs or updates CLIs.',
        obj({'backends':{'type':'array','items':BACKEND,'minItems':1,'maxItems':2,'uniqueItems':True,'default':['codex','claude']},'force':{'type':'boolean','default':False}}),CHECK,
        check,retryable=False,
        side_effects=('Anonymous bounded official HTTP GET and persistence of public release observations; force still observes a 60-second cooldown',))
    manager.attach_registry(registry)
