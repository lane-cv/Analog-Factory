#!/usr/bin/env python3
"""Explicit actual Xvfb acceptance, using only an isolated output workspace.

Run inside the prepared Ubuntu24 acceptance image under xvfb-run. This driver
never attaches to an existing display/editor or imports historical authority.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import io
import zipfile
from unittest.mock import patch
from dataclasses import replace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from analog_factory.service import Workspace
from analog_factory.native_canvas import NativeCanvas,read_private,decode_packet
from analog_factory.operation_native_canvas import register_native_canvas


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--pdk-root',type=Path,default=Path('/pdk'))
    args=parser.parse_args()
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    Path(os.environ['HOME']).mkdir(parents=True,exist_ok=True)
    workspace=Workspace(output/'workspace');registry=workspace.operations
    if 'native.canvas.start' not in registry.operations:register_native_canvas(registry)
    config={'tools':{'xschem':'/opt/eda/bin/xschem','magic':'/opt/eda/bin/magic','ngspice':'/opt/eda/bin/ngspice-sky130'},
        'pdk_root':str(args.pdk_root),'pdk_variant':'sky130A'}
    result=registry.invoke('settings.update',{'settings':config},expected_version=registry.version())
    if not result['ok']:raise RuntimeError(result)
    pid=workspace.projects.create('Actual native canvas acceptance')['id']
    schematic='v {xschem version=3.4.7 file_version=1.2}\nG {}\nK {}\nV {}\nS {.param untouched=17}\nE {}\nC {/opt/eda/share/xschem/xschem_library/devices/res.sym} 100 100 0 0 {name=R1 value=1k}\nC {/opt/eda/share/xschem/xschem_library/devices/res.sym} 300 100 0 0 {name=R2 value=2k}\n'
    layout='magic\ntech sky130A\ntimestamp 0\n<< metal1 >>\nrect 0 0 10 10\n<< end >>\n'
    workspace.projects.write(pid,'schematics/native.sch',schematic)
    workspace.projects.write(pid,'layout/native.mag',layout)
    manager=workspace.native_canvas;report={'status':'failed','checks':[],'limits':'Managed allowlisted verbs only; no arbitrary native-menu coverage or unmanaged-window takeover'}
    started=[]
    original_read_text=Path.read_text
    # Acceptance-only native callback simulates a second human edit while already
    # modified. It is not reachable through the production command/schema.
    human_fixture=r"""
proc af_acceptance_human {} {
    set marker [file join $::af_root human-edit]
    if {[file exists $marker]} {
        file delete $marker
        xschem move_instance R2 350 100 0 0
        xschem set_modify 1
        set f [open [file join $::af_root human-done] w];puts $f done;close $f
    }
    after 20 af_acceptance_human
}
after 20 af_acceptance_human
"""
    def fixture_read(path,*args,**kwargs):
        text=original_read_text(path,*args,**kwargs)
        return text+human_fixture if path.name=='native_canvas_xschem.tcl' else text
    def trigger_human(sid):
        private=manager.sessions[sid]['directory']
        (private/'human-done').unlink(missing_ok=True)
        (private/'human-edit').write_text('test-only native edit')
        deadline=time.monotonic()+4
        while not (private/'human-done').exists():
            if time.monotonic()>deadline:raise RuntimeError('Native human-edit fixture did not acknowledge')
            time.sleep(.02)

    def invoke(name,parameters):
        result=registry.invoke(name,parameters,expected_version=registry.version(parameters.get('project_id')))
        if not result['ok']:raise RuntimeError(result)
        return result['data']
    try:
        for tool,source in (('xschem','schematics/native.sch'),('magic','layout/native.mag')):
            with patch.object(Path,'read_text',fixture_read):
                run=invoke('native.canvas.start',{'project_id':pid,'tool':tool,'source':source,'timeout':90})
            started.append(run)
            sid=run['native_session_id'];rid=run['id']
            base={'project_id':pid,'session_id':sid,'run_id':rid}
            initial=invoke('native.canvas.get',base)
            nonce=manager.sessions[sid]['nonce']
            public_run=invoke('run.get',{'project_id':pid,'run_id':rid})
            assert nonce not in json.dumps(public_run),'Native authority leaked through run.get'
            assert manager.sessions[sid]['directory'].is_relative_to(workspace.root) and not manager.sessions[sid]['directory'].is_relative_to(workspace.projects.path(pid))
            report['checks'].append({'tool':tool,'run_id':rid,'initial':initial})
            if tool=='xschem':
                revision=initial['state']['native_revision']
                selected=invoke('native.canvas.select',{**base,'expected_native_revision':revision,'instance_id':'R1'})
                moved=invoke('native.canvas.move',{**base,'expected_native_revision':selected['state']['native_revision'],'instance_id':'R1','direction':'right','grid_steps':1})
                r1=next(item for item in moved['state']['instances'] if item['id']=='R1')
                assert r1['x']>100 and moved['state']['modified'],moved
                connected=invoke('native.canvas.connect_pins',{**base,'expected_native_revision':moved['state']['native_revision'],'from_instance':'R1','from_pin':'P','to_instance':'R2','to_pin':'P','route':'horizontal_first'})
                assert connected['state']['modified'],connected
                packet=decode_packet(read_private(manager.sessions[sid]['directory']/f'{connected["command_id"]}.response',131072))
                exported=read_private(manager.sessions[sid]['directory']/packet['document'],2097152).decode()
                assert '.param untouched=17' in exported and 'N ' in exported,exported
                assert workspace.projects.read(pid,source)['content']==schematic
                report['checks'][-1].update(selected=selected,moved=moved,connected=connected,unsaved_source_preserved=True)
                trigger_human(sid)
                stale=registry.invoke('native.canvas.move',{**base,'expected_native_revision':connected['state']['native_revision'],'instance_id':'R1','direction':'right','grid_steps':1})
                assert not stale['ok'] and stale['error']['code']=='VERSION_CONFLICT',stale
                inspected=invoke('native.canvas.get',base)
                assert inspected['state']['modified'] and inspected['state']['native_revision']>connected['state']['native_revision'],inspected
                assert next(item for item in inspected['state']['instances'] if item['id']=='R2')['x']==350,inspected
                report['checks'][-1]['already_modified_human_edit_rejects_stale_command']=True
                connected=invoke('native.canvas.connect_pins',{**base,'expected_native_revision':inspected['state']['native_revision'],'from_instance':'R1','from_pin':'P','to_instance':'R2','to_pin':'P','route':'horizontal_first'})
                saved=registry.invoke('native.canvas.save' ,{'project_id':pid,'session_id':sid,'run_id':rid,
                    'expected_native_revision':connected['state']['native_revision'],
                    'expected_source_sha256':connected['state']['source_sha256']},expected_version=registry.version(pid))
                assert saved['ok'],saved
                assert saved['data']['write_receipt']['ok'] and saved['data']['state']['modified'],saved
                assert workspace.projects.history(pid,source),saved
                report['checks'][-1]['saved']=saved
                native_netlist=invoke('run.start',{'project_id':pid,'kind':'xschem','options':{'schematic':source,'netlist_only':True},'timeout':20})
                netlist=workspace.runner.wait(workspace.projects.path(pid),native_netlist['id'],timeout=25)
                assert netlist['status']=='succeeded',netlist
                files=list((workspace.run_path(pid,netlist['id'])/'output').glob('*.spice'))
                assert len(files)==1,files
                lines=[line.split() for line in files[0].read_text().splitlines() if line.startswith(('R1 ','R2 '))]
                assert len(lines)==2 and lines[0][1]==lines[1][1],lines
                report['checks'][-1]['actual_netlist']={'run_id':netlist['id'],'resistors':lines,'common_pin_verified':True}
                # Let the actual native hold expire while the registered write is
                # slow. The real receipt must survive, with dirty state retained.
                current=invoke('native.canvas.get',base)
                moved_again=invoke('native.canvas.move',{**base,'expected_native_revision':current['state']['native_revision'],'instance_id':'R1','direction':'right','grid_steps':1})
                original_write=registry.operations['file.write']
                def slow_write(context,parameters):
                    time.sleep(2.3)
                    return original_write.handler(context,parameters)
                registry.operations['file.write']=replace(original_write,handler=slow_write)
                try:
                    uncertain=registry.invoke('native.canvas.save',{**base,'expected_native_revision':moved_again['state']['native_revision'],'expected_source_sha256':moved_again['state']['source_sha256']},expected_version=registry.version(pid))
                finally:registry.operations['file.write']=original_write
                assert not uncertain['ok'] and uncertain['data']['saved'] is True and uncertain['data']['write_receipt']['ok'],uncertain
                assert uncertain['data']['acknowledged'] is False and uncertain['data']['state']['modified'],uncertain
                final_native=invoke('native.canvas.get',base)
                assert final_native['state']['modified'],final_native
                report['checks'][-1]['slow_registered_write_retained_after_native_hold_expiry']=uncertain

            else:
                assert 'metal1' in initial['state']['layers'],initial
                painted=invoke('native.canvas.paint_box',{**base,'expected_native_revision':initial['state']['native_revision'],
                    'box_token':initial['state']['box_token'],'layer':'metal2'})
                assert painted['state']['modified'],painted
                saved=invoke('native.canvas.save',{**base,'expected_native_revision':painted['state']['native_revision'],
                    'expected_source_sha256':painted['state']['source_sha256']})
                actual=workspace.projects.read(pid,source)['content']
                assert '<< metal2 >>' in actual and 'rect 0 0 10 10' in actual,actual
                report['checks'][-1].update(painted=painted,saved=saved,original_geometry_preserved=True)
                drc_run=invoke('run.start',{'project_id':pid,'kind':'drc','options':{'layout':source},'timeout':20})
                drc=workspace.runner.wait(workspace.projects.path(pid),drc_run['id'],timeout=25)
                assert drc['status']=='succeeded' and drc['verification']['status'] in ('pass','fail'),drc
                report['checks'][-1]['actual_drc']={'run_id':drc['id'],'verification':drc['verification']}
            exported=workspace.projects.export(pid)
            with zipfile.ZipFile(io.BytesIO(exported)) as archive:
                for name in archive.namelist():
                    assert not name.endswith(('.request','.response','.commit','.document')) and 'native-canvas/' not in name,name
                    assert nonce.encode() not in archive.read(name),'Native authority leaked through project export'
            report['checks'][-1]['private_authority_absent_from_run_and_export']=True
        report['status']='passed'
    finally:
        for run in started:workspace.runner.cancel(workspace.projects.path(pid),run['id'])
        registry.close();workspace.agents.close()
        (output/'native-canvas-validation.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'report':str(output/'native-canvas-validation.json')}))


if __name__=='__main__':main()
