"""Schema-bound commands acknowledged by an owned native editor, not a browser."""
from .native_canvas import NativeCanvas
from .operation_catalog import ID,PATH,RUN,TEXT,OPEN_OBJECT,_project,object_schema as obj
from .operations import Operation,OperationError
from . import eda

IDENTIFIER={'type':'string','pattern':'^[A-Za-z_][A-Za-z0-9_.:/+-]{0,127}$','maxLength':128}
REVISION={'type':'integer','minimum':1,'maximum':999999999999}
TARGET={'session_id':ID,'run_id':ID}
NUMBER={'type':'number','minimum':-1000000000,'maximum':1000000000}
INSTANCE=obj({'id':IDENTIFIER,'symbol':{'type':'string','maxLength':4096},'type':{'type':'string','maxLength':128},
    'x':NUMBER,'y':NUMBER,'rotation':{'type':'integer','minimum':0,'maximum':3},'flip':{'type':'integer','minimum':0,'maximum':1},
    'pins':{'type':'array','items':IDENTIFIER,'maxItems':128}},
    ('id','symbol','type','x','y','rotation','flip','pins'))
STATE=obj({'project_id':ID,'run_id':ID,'session_id':ID,'tool':{'type':'string','enum':['xschem','magic']},'source':PATH,
    'native_revision':REVISION,'modified':{'type':'boolean'},'instances':{'type':'array','items':INSTANCE,'maxItems':512},
    'selected':{'type':'array','items':{'type':'string','maxLength':4096},'maxItems':4096},
    'box':{'type':'array','items':NUMBER,'maxItems':4},'box_token':{'type':'string','maxLength':256},
    'layers':{'type':'array','items':{'type':'string','maxLength':128},'maxItems':512},'connected':{'type':'boolean'},
    'source_sha256':{'type':'string','pattern':'^[a-f0-9]{64}$'}},
    ('project_id','run_id','session_id','tool','source','native_revision','modified','instances','selected','box','box_token','layers','connected','source_sha256'))
RESULT=obj({'state':STATE,'command_id':ID,'acknowledged':{'type':'boolean'},'run_id':ID,'session_id':ID},
    ('state','command_id','acknowledged','run_id','session_id'),extra=True)


def register_native_canvas(registry):
    workspace=registry.workspace
    manager=getattr(workspace,'native_canvas',None)
    if manager is None:manager=workspace.native_canvas=NativeCanvas(workspace)

    def get(context,p):return manager.get(p['project_id'],p['session_id'],p['run_id'])
    def target(context,p):
        if not all(p.get(key) for key in ('project_id','session_id','run_id')):
            raise OperationError('FRONTEND_DISCONNECTED','Choose a project, managed native session and run')
        return manager.live_target(p['project_id'],p['session_id'],p['run_id'])
    def get_available(context,p):
        try:manager.live_target(p.get('project_id'),p.get('session_id'),p.get('run_id'));return []
        except (OperationError,ValueError,OSError) as error:return [str(error)]
    def start_available(context,p):
        reasons=[]
        pid=p.get('project_id');tool=p.get('tool')
        if not pid:reasons.append('Select a project')
        if tool not in ('xschem','magic'):reasons.append('Choose the native tool and source')
        if pid and tool in ('xschem','magic'):
            try:
                options={'schematic' if tool=='xschem' else 'layout':p.get('source','')}
                availability=workspace.run_availability(pid,tool,options)
                reasons.extend(availability.get('reasons',[]))
                if not reasons:eda.prepare(workspace.projects.path(pid),tool,options,workspace.config(pid))
            except (ValueError,RuntimeError,OSError) as error:reasons.append(str(error))
        return reasons
    def start(context,p):
        try:return manager.start(p['project_id'],p['tool'],p['source'],p['timeout'])
        except RuntimeError as error:raise OperationError('UNAVAILABLE',str(error)) from error

    registry.register(Operation(operation_id='native.canvas.start',name='Open managed native canvas',
        description='Launch a new supervised Xschem or Magic window with fixed native semantic adapters. Inspect waits for actual native readiness; existing unmanaged editors are never taken over.',
        module='native',input_schema=_project({'tool':{'type':'string','enum':['xschem','magic']},'source':PATH,
            'timeout':{'type':'number','minimum':1,'maximum':3600,'default':900}},('tool','source')),
        output_schema=RUN,handler=start,permissions=('read','execute','ui'),confirmation='agent',versioned=True,
        asynchronous=True,cancellable=True,task_kind='run',
        preconditions=('Available native display and installed tool','Existing visible .sch/.mag source','At most eight live managed canvases'),
        side_effects=('Starts an independent native GUI on the selected source and bounded private transport; no reload or disk edit',),
        tags=('native-session','actual-acknowledgment'),availability=start_available))
    registry.register(Operation(operation_id='native.canvas.get',name='Inspect actual managed native state',
        description='Query native unsaved document, selected instances or box, current source and revision through a matching owned process acknowledgment. Historical run files grant no session authority.',
        module='native',input_schema=_project(TARGET,('session_id','run_id')),output_schema=RESULT,
        handler=get,kind='query',permissions=('read','ui'),tags=('native-session','actual-acknowledgment'),availability=get_available))

    def add(name,description,fields,verb,tool=None,write=True):
        schema=_project({**TARGET,'expected_native_revision':REVISION,**fields},('session_id','run_id','expected_native_revision',*fields))
        def owned_target(context,p):
            value=target(context,p)
            if tool and value['state']['tool']!=tool:raise OperationError('UNAVAILABLE',f'This semantic action is qualified only for {tool}')
            return value
        def execute(context,p):
            if name=='save':return manager.save(context,p['project_id'],p['session_id'],p['run_id'],p['expected_native_revision'],p['expected_source_sha256'])
            return manager.command(p['project_id'],p['session_id'],p['run_id'],verb,p['expected_native_revision'],{key:p[key] for key in fields})
        registry.register(Operation(operation_id='native.canvas.'+name,name='Native canvas '+name,
            description=description,module='native',input_schema=schema,output_schema=RESULT,handler=execute,
            kind='frontend',frontend_adapter='native',frontend_target=owned_target,
            permissions=('read','execute','ui',*(('write',) if write else ())),confirmation='agent',
            versioned=name=='save',retryable=False,
            preconditions=('Live session owned by this server/project/run','Expected native revision matches actual unsaved state',
                'Native editor is not busy and its state can be exported without loss'),
            side_effects=('Executes a fixed native semantic action only after native revision validation; edits remain unsaved until explicit save',),
            tags=('native-session','actual-acknowledgment'),
            usage='Inspect native.canvas.get first. Timeout after possible delivery is unknown and must not be automatically retried. run.cancel closes the managed GUI.'))
    add('select','Select one actual named Xschem instance without reloading or saving its native document.',{'instance_id':IDENTIFIER},'select','xschem',False)
    add('move','Move one named Xschem instance by bounded native grid steps and stretch attached nets; native geometry acknowledges the result.',
        {'instance_id':IDENTIFIER,'direction':{'type':'string','enum':['left','right','up','down']},'grid_steps':{'type':'integer','minimum':1,'maximum':999}},'move','xschem')
    add('connect_pins','Connect actual named Xschem pins using a bounded orthogonal route derived from their native pin coordinates.',
        {'from_instance':IDENTIFIER,'from_pin':IDENTIFIER,'to_instance':IDENTIFIER,'to_pin':IDENTIFIER,
         'route':{'type':'string','enum':['horizontal_first','vertical_first']}},'connect','xschem')
    add('paint_box','Paint a discovered Magic layer within the previously acknowledged native cursor box; arbitrary screen coordinates are not accepted.',
        {'box_token':{'type':'string','pattern':'^-?[0-9]+(?:\\.[0-9]+)?(?:,-?[0-9]+(?:\\.[0-9]+)?){3}$','maxLength':256},'layer':IDENTIFIER},'paint','magic')
    add('save','Export the current native document inside a bounded native event hold and save it through registered file.write with project history. Native dirty flags remain explicit; no reload or second native write occurs.',
        {'expected_source_sha256':{'type':'string','pattern':'^[a-f0-9]{64}$'}},'save_prepare')
