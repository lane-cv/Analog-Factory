"""Human-only native terminal byte controls behind the shared operation policy."""
from .native_terminals import NativeTerminals, capabilities
from .operation_catalog import ID, OPEN_OBJECT, _project
from .operations import Operation


def register_terminals(registry):
    workspace=registry.workspace
    if not hasattr(workspace,'terminals'):workspace.terminals=NativeTerminals(workspace)
    manager=workspace.terminals
    def stop(c,p):
        record=manager.stop(p['project_id'],p['terminal_id'])
        return {'id':record['id'],'status':'completed','terminal':record}
    terminal=lambda properties=None,required=():_project({'terminal_id':ID,**(properties or {})},['terminal_id',*required])
    def add(name,description,schema,handler,permissions=('read',),**options):
        registry.register(Operation(operation_id='native.terminal.'+name,name='Native terminal '+name,
            description=description,module='agent',input_schema=schema,output_schema=OPEN_OBJECT,
            handler=handler,permissions=permissions,**options))
    add('capabilities','Inspect installed interactive native CLI flags and the human terminal permission boundary.',
        _project(),lambda c,p:capabilities(workspace,p['project_id']),kind='query',idempotent=True)
    add('list','List project terminal status metadata without exposing terminal bytes.',_project(),
        lambda c,p:{'terminals':manager.list(p['project_id'])},kind='query',idempotent=True)
    add('get','Read native terminal status; abandoned or imported records never restore process authority.',terminal(),
        lambda c,p:manager.get(p['project_id'],p['terminal_id']),kind='query',idempotent=True)
    add('start','Open a human-controlled native CLI PTY in the selected project with native human approval prompts.',
        _project({'backend':{'type':'string','enum':['codex','claude']},'mode':{'type':'string','enum':['read-only','workspace-write'],'default':'read-only'},
                  'model':{'type':'string','maxLength':256,'default':''},'cols':{'type':'integer','minimum':20,'maximum':240,'default':100},
                  'rows':{'type':'integer','minimum':5,'maximum':100,'default':30},
                  'runtime_seconds':{'type':'number','minimum':1,'maximum':14400,'default':3600}},['backend']),
        lambda c,p:manager.start(p['project_id'],**{k:v for k,v in p.items() if k!='project_id'}),
        permissions=('execute','approve'),confirmation='none',versioned=True,asynchronous=True,cancellable=True,task_kind='terminal',
        preconditions=('One active native terminal per project','Installed CLI exposes required interactive flags','Human approve-capable caller'),
        side_effects=('Starts a native CLI with its existing user-owned authentication and configuration; retains bounded output in memory only',),
        usage='Poll native.terminal.get/read and cancel with native.terminal.stop. This human terminal is not an agent automation escape hatch.',
        examples=({'parameters':{'project_id':'0123456789abcdef0123456789abcdef','backend':'codex','mode':'read-only'}},))
    add('read','Read bounded in-memory terminal output; raw native output is available only to a human approve-capable caller.',
        terminal({'after':{'type':'integer','minimum':0,'default':0}}),
        lambda c,p:manager.read(p['project_id'],p['terminal_id'],p['after']),permissions=('read','approve'),kind='query',idempotent=True,volatile_response=True)
    add('input','Send literal human keystrokes to the owned PTY; never interpret bytes as registry approval or persist them.',
        terminal({'text':{'type':'string','minLength':1,'maxLength':8192}},['text']),
        lambda c,p:manager.input(p['project_id'],p['terminal_id'],p['text']),permissions=('execute','approve'),
        side_effects=('Sends human input to the native CLI, including its own interactive approval prompts',))
    add('resize','Resize the active owned native terminal.',terminal({'cols':{'type':'integer','minimum':20,'maximum':240},
        'rows':{'type':'integer','minimum':5,'maximum':100}},['cols','rows']),
        lambda c,p:manager.resize(p['project_id'],p['terminal_id'],p['cols'],p['rows']),permissions=('execute','approve'),
        side_effects=('Changes the owned PTY dimensions and persists its updated terminal status metadata',))
    add('stop','Stop the owned native terminal process group; never signal a process from imported or persisted metadata.',terminal(),
        stop,permissions=('execute','approve'),idempotent=True,
        side_effects=('Terminates the live owned native CLI process group and records terminal status',))
