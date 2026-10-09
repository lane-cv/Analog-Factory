"""Managed native Magic navigation with an actual Tcl acknowledgment."""
import hashlib
import math
import re
import uuid

from . import eda
from .operation_catalog import ID,RUN,TEXT,_project,object_schema as obj
from .operations import Operation,OperationError
from .storage import confined


def register_verification(registry):
    workspace=registry.workspace
    certificates={}

    def source(pid,rid,index):
        record=workspace.runner.get(workspace.projects.path(pid),rid)
        if record['kind']!='drc' or record['status']!='succeeded' or record.get('imported_evidence') or not record.get('owner_identity'):
            raise ValueError('Choose a completed local DRC execution; imported evidence cannot navigate a native editor')
        if record.get('stale'):
            raise OperationError('VERSION_CONFLICT','DRC source files changed; run DRC again before navigating errors')
        count=record.get('verification',{}).get('error_count')
        if type(count) is not int or count<1 or record['verification'].get('status')!='fail':
            raise ValueError('The selected DRC execution has no verified error locations')
        if index>count:raise ValueError('DRC error index exceeds the recorded error count')
        report=workspace.artifact(pid,rid,'drc.txt')
        if report.stat().st_size>2*1024*1024:raise ValueError('DRC navigation report exceeds its 2 MiB validation limit')
        data=report.read_bytes()
        if hashlib.sha256(data).hexdigest()!=record.get('artifact_hashes',{}).get('drc.txt'):
            raise ValueError('DRC report changed after execution')
        text=data.decode(errors='replace')
        measured=re.search(r'^AF_DRC_COUNT=([0-9]+)\s*$',text,re.M)
        if not measured or int(measured[1])!=count or not re.search(r'^AF_DRC_COMPLETE=1\s*$',text,re.M):
            raise ValueError('DRC report is incomplete or inconsistent')
        return record,count

    def focus(context,parameters):
        pid,rid,index=parameters['project_id'],parameters['run_id'],parameters['error_index']
        record,count=source(pid,rid,index)
        layout=record.get('metadata',{}).get('sources',{}).get('layout')
        if not isinstance(layout,str):raise ValueError('DRC record does not identify its layout source')
        root=workspace.projects.path(pid);target=confined(root,layout)
        try:
            prepared=eda.prepare(root,'magic',{'layout':layout,'timeout':parameters['timeout']},workspace.config(pid))
        except RuntimeError as error:
            raise OperationError('UNAVAILABLE',str(error)) from error
        if (prepared['metadata'].get('pdk')!=record.get('pdk') or
                prepared['metadata'].get('tool_versions')!=record.get('tool_versions')):
            raise OperationError('VERSION_CONFLICT','Magic or prepared PDK identity changed; run DRC again before navigating')
        nonce=uuid.uuid4().hex;revision=registry.version(pid)
        body=(f'set af_nonce "{nonce}"\nset af_index {index}\n'
              f'load {eda._tcl(target)}\nselect top cell\nexpand\n'
              'drc on\ndrc ignore none\ndrc check\ndrc catchup\n'
              'set af_count [drc listall count total]\n'
              f'if {{$af_count != {count}}} {{error "DRC error count changed; run DRC again"}}\n'
              'if {$af_index > $af_count} {error "Requested DRC error is not available"}\n'
              'drc find $af_index\nfindbox zoom\nupdate idletasks\n'
              'set af_box [box values]\n'
              'if {[llength $af_box] != 4} {error "Magic did not return an error box"}\n'
              'set af_out [file normalize [file join [pwd] .. output drc-focus.txt]]\n'
              'set af_report [open $af_out w]\n'
              'puts $af_report "AF_DRC_FOCUS_NONCE=$af_nonce"\n'
              'puts $af_report "AF_DRC_FOCUS_INDEX=$af_index"\n'
              'puts $af_report "AF_DRC_FOCUS_COUNT=$af_count"\n'
              'puts $af_report "AF_DRC_FOCUS_BOX=$af_box"\n'
              'puts $af_report "AF_DRC_FOCUS_COMPLETE=1"\nclose $af_report\n')
        script='if {[catch {\n'+body+'} af_error]} {puts stderr "AF_ERROR: $af_error"; exit 1}\n'
        relative='af_scripts/drc-focus.tcl'
        prepared['metadata']['generated_files'][relative]=script
        prepared['metadata']['drc_focus']={'source_run_id':rid,'error_index':index,'source_version':revision,
            'acknowledgment':'drc-focus.txt','meaning':'Magic drc find and findbox zoom completed; coordinates come from Magic box values'}
        prepared['command'][-1]=relative
        row=workspace.runner.start(root,'magic',**prepared)
        certificates[(pid,row['id'])]={'nonce':nonce,'source_run_id':rid,'error_index':index,'source_version':revision}
        if len(certificates)>256:certificates.pop(next(iter(certificates)))
        return row

    def status(context,parameters):
        pid,rid=parameters['project_id'],parameters['run_id']
        record=workspace.runner.get(workspace.projects.path(pid),rid)
        result={'run_id':rid,'status':record['status'],'focused':False,'source_run_id':None,'error_index':None,
                'box':None,'reason':'No verified focus acknowledgment from this server'}
        certificate=certificates.get((pid,rid))
        if not certificate or record.get('imported_evidence'):return result
        result.update(source_run_id=certificate['source_run_id'],error_index=certificate['error_index'])
        if registry.version(pid)!=certificate['source_version']:
            result['reason']='Project changed after focus was requested; run DRC again';return result
        path=confined(workspace.run_path(pid,rid)/'output','drc-focus.txt')
        if not path.is_file() or path.stat().st_size>4096:return result
        lines=dict(line.split('=',1) for line in path.read_text().splitlines() if '=' in line)
        if lines.get('AF_DRC_FOCUS_NONCE')!=certificate['nonce'] or lines.get('AF_DRC_FOCUS_COMPLETE')!='1':return result
        try:
            box=[float(value) for value in lines['AF_DRC_FOCUS_BOX'].split()]
            if int(lines['AF_DRC_FOCUS_INDEX'])!=certificate['error_index'] or len(box)!=4 or not all(math.isfinite(value) for value in box):return result
        except (ValueError,KeyError):return result
        result.update(focused=True,box=box,reason=('Magic acknowledged drc find and findbox zoom for this error index'
            if record['status'] in ('queued','running') else
            'Magic acknowledged this error earlier; the navigation window is no longer running'))
        return result

    registry.register(Operation(operation_id='verification.drc.focus',name='Find DRC error in Magic',
        description='Revalidate a completed local DRC report and open a managed Magic GUI at its selected error index. Focus is confirmed only by a matching Tcl acknowledgment.',
        module='verification',input_schema=_project({'run_id':ID,'error_index':{'type':'integer','minimum':1,'maximum':100000},
            'timeout':{'type':'number','minimum':1,'maximum':3600,'default':900}},('run_id','error_index')),
        output_schema=RUN,handler=focus,permissions=('execute',),confirmation='agent',versioned=True,
        asynchronous=True,cancellable=True,task_kind='run',
        preconditions=('Completed local nonstale DRC report with errors','Same Magic and prepared PDK identity','Available native GUI display'),
        side_effects=('Starts a managed Magic GUI on the original layout and writes a bounded Tcl focus acknowledgment in run output',),
        usage='Poll verification.drc.focus_status; run.cancel stops the native GUI. An index refers to Magic error tiles, not an invented screen coordinate.'))
    registry.register(Operation(operation_id='verification.drc.focus_status',name='Read Magic DRC focus acknowledgment',
        description='Inspect the actual matching native Tcl acknowledgment; imported or previous-server records never authorize a focus claim.',
        module='verification',input_schema=_project({'run_id':ID},('run_id',)),output_schema=obj({
            'run_id':ID,'status':TEXT,'focused':{'type':'boolean'},'source_run_id':{'oneOf':[ID,{'type':'null'}]},
            'error_index':{'oneOf':[{'type':'integer','minimum':1},{'type':'null'}]},
            'box':{'oneOf':[{'type':'array','items':{'type':'number'},'minItems':4,'maxItems':4},{'type':'null'}]},
            'reason':TEXT},('run_id','status','focused','source_run_id','error_index','box','reason')),
        handler=status,kind='query',permissions=('read',),idempotent=True))
