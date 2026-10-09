"""Managed native commands must depend on actual owned acknowledgments."""
import tempfile
import shutil
import unittest
import subprocess
from pathlib import Path


class NativeTransportTests(unittest.TestCase):
    def test_catalog_native_frontend_permissions_and_explicit_targets(self):
        from analog_factory.service import Workspace
        from analog_factory.operation_native_canvas import register_native_canvas
        from analog_factory.operations import InvocationContext
        with tempfile.TemporaryDirectory() as directory:
            workspace=Workspace(Path(directory));registry=workspace.operations
            self.addCleanup(registry.close);self.addCleanup(workspace.agents.close)
            if 'native.canvas.start' not in registry.operations:register_native_canvas(registry)
            operation=registry.operations['native.canvas.move']
            self.assertEqual(operation.kind,'frontend')
            self.assertEqual(operation.frontend_adapter,'native')
            self.assertIn('write',operation.permissions)
            self.assertFalse(operation.retryable)
            self.assertIn('expected_native_revision',operation.input_schema['required'])
            params={'project_id':'a'*32,'session_id':'b'*32,'run_id':'c'*32,'expected_native_revision':1,'instance_id':'R1','direction':'right','grid_steps':1}
            result=registry.invoke('native.canvas.move',params,InvocationContext(permissions=frozenset({'read'})))
            self.assertEqual(result['error']['code'],'PERMISSION_DENIED')

    def test_browser_controller_blocks_unknown_retries_until_explicit_inspection(self):
        import shutil
        if not shutil.which('node'):self.skipTest('Node is required for the portable controller check')
        controller=Path(__file__).resolve().parents[1]/'analog_factory/static/native-canvas.js'
        script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
global.window={};global.crypto=require('crypto').webcrypto;
const dialog={open:false,innerHTML:'',setAttribute(){},addEventListener(){},showModal(){this.open=true},close(){this.open=false}};
global.document={createElement(){return dialog},body:{append(){}}};
const native={project_id:'p',run_id:'r',session_id:'s',tool:'xschem',source:'schematics/top.sch',native_revision:7,modified:true,instances:[{id:'R1',pins:['P','M']}],selected:[],box:[],box_token:'',layers:[],connected:true,source_sha256:'a'.repeat(64)};
let calls=[],unknown=true;
window.AFOperations={async invoke(name,p){calls.push([name,p]);if(name==='project.detail')return {data:{},version:'opened'};if(name==='native.canvas.start')return {data:{id:'r',native_session_id:'s'},version:'opened'};if(name==='native.canvas.get')return {data:{state:native},version:'opened'};if(name==='native.canvas.move'&&unknown){let e=new Error('unknown native outcome');e.code='OUTCOME_UNKNOWN';throw e}return {data:{state:{...native,native_revision:8},acknowledged:true},version:'opened'}}};
vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));
(async()=>{const state={project:{id:'p'},dirty:false};const ui=window.AFNativeCanvas.create({state});ui.mount({innerHTML:'',addEventListener(){},querySelector(){return {disabled:false}}});await ui.commands.open();await ui.commands.set_options({tool:'xschem',source:'schematics/top.sch'});assert.equal((await ui.commands.start()).busy,false);await assert.rejects(ui.commands.move({instance_id:'R1',direction:'right',grid_steps:1}),/unknown/);const length=calls.length;await assert.rejects(ui.commands.move({instance_id:'R1',direction:'right',grid_steps:1}),/inspect/i);assert.equal(calls.length,length);assert.equal(ui.snapshot().unknown_outcome,true);unknown=false;assert.equal((await ui.commands.refresh()).busy,false);assert.equal((await ui.commands.move({instance_id:'R1',direction:'right',grid_steps:1})).busy,false);state.project={id:'other'};assert.equal(ui.snapshot().native,null);await assert.rejects(ui.commands.refresh(),/project/i);})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result=subprocess.run(['node','-e',script,str(controller)],text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
    def test_session_authority_is_memory_owned_and_bound_to_project_run(self):
        from analog_factory.native_canvas import NativeCanvas
        from analog_factory.operations import OperationError
        class Workspace:root=Path('/tmp')
        manager=NativeCanvas(Workspace())
        with self.assertRaises(OperationError) as error:manager.resolve('a'*32,'b'*32,'c'*32)
        self.assertEqual(error.exception.code,'FRONTEND_DISCONNECTED')
        manager.sessions['b'*32]={'project_id':'a'*32,'run_id':'c'*32}
        with self.assertRaises(OperationError) as error:manager.resolve('d'*32,'b'*32,'c'*32)
        self.assertEqual(error.exception.code,'NOT_FOUND')
        with self.assertRaises(OperationError):manager.resolve('a'*32,'b'*32,'d'*32)

    def test_native_authority_is_absent_from_run_metadata_and_project_export_tree(self):
        from analog_factory.service import Workspace
        from analog_factory.native_canvas import NativeCanvas
        from unittest.mock import patch
        import json
        with tempfile.TemporaryDirectory() as directory:
            workspace=Workspace(Path(directory));self.addCleanup(workspace.agents.close)
            pid=workspace.projects.create('Private native authority')['id']
            workspace.projects.write(pid,'schematics/native.sch','v {xschem version=3.4.7 file_version=1.2}\n')
            prepared={'command':['/absolute/xschem','source.sch'],'timeout':30,'env':{},'metadata':{'generated_files':{}}}
            manager=NativeCanvas(workspace)
            with patch('analog_factory.eda.prepare',return_value=prepared),patch.object(workspace,'run_path',return_value=workspace.projects.path(pid)/'.af/runs'/('a'*32)),patch.object(workspace.runner,'start',return_value={'id':'a'*32,'status':'queued'}) as launch:
                run=manager.start(pid,'xschem','schematics/native.sch',30)
            session=manager.sessions[run['native_session_id']]
            metadata=launch.call_args.kwargs['metadata']
            self.assertFalse(session['nonce'] in json.dumps(metadata),'Native nonce leaked into run metadata')
            self.assertFalse(session['directory'].is_relative_to(workspace.projects.path(pid)))
            self.assertEqual((session['directory']/'nonce').read_text().strip(),session['nonce'])
    def test_fixed_tcl_transport_never_evaluates_decoded_values(self):
        from analog_factory.native_canvas import encode_packet
        interpreter=shutil.which('tclsh8.6') or shutil.which('tclsh')
        if not interpreter:self.skipTest('A Tcl interpreter is required for the literal native transport check')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            value='[exec touch '+str(root/'INJECTED')+'];$env(HOME)'
            packet=encode_packet({'instance_id':value})
            (root/'packet').write_bytes(packet)
            adapter=Path(__file__).resolve().parents[1]/'analog_factory/native_canvas_common.tcl'
            script=f'set af_root {{{root}}}\nsource {{{adapter}}}\nset p [afcanvas::decode [afcanvas::read_file {{{root/"packet"}}} 131072]]\nputs [dict get $p instance_id]\n'
            result=subprocess.run([interpreter],input=script,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stderr,'')
            self.assertEqual(result.stdout.strip(),value,result.stderr)
            self.assertFalse((root/'INJECTED').exists())
    def test_packets_are_literal_bounded_data_and_reject_duplicate_or_unknown_fields(self):
        from analog_factory.native_canvas import encode_packet, decode_packet
        value={'nonce':'a'*64,'command_id':'b'*32,'verb':'inspect','expected_revision':'0','instance_id':'R[exec BAD];$env(HOME)'}
        encoded=encode_packet(value)
        self.assertNotIn(b'exec',encoded)
        self.assertEqual(decode_packet(encoded),value)
        with self.assertRaises(ValueError): decode_packet(encoded+b'verb=00\n')
        with self.assertRaises(ValueError): decode_packet(b'unknown=61\n')
        with self.assertRaises(ValueError): decode_packet(b'verb=zz\n')
        with self.assertRaises(ValueError): decode_packet(b'verb='+b'61'*65537+b'\n')

    def test_private_reads_reject_symlinks_and_oversized_output(self):
        from analog_factory.native_canvas import read_private
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'data';target.write_bytes(b'hello')
            self.assertEqual(read_private(target,6),b'hello')
            with self.assertRaises(ValueError):read_private(target,4)
            alias=root/'alias';alias.symlink_to(target)
            with self.assertRaises(ValueError):read_private(alias,6)

    def test_magic_archive_extracts_only_bound_source_and_rejects_multiple_or_incomplete_cells(self):
        from analog_factory.native_canvas import extract_magic_document
        text='file /project/layout/top\nmagic\ntech sky130A\ntimestamp 0\n<< metal1 >>\nrect 0 0 1 1\n<< end >>\nend top\n'
        expected='magic\ntech sky130A\ntimestamp 0\n<< metal1 >>\nrect 0 0 1 1\n<< end >>\n'
        self.assertEqual(extract_magic_document(text,Path('/project/layout/top.mag')),expected)
        with self.assertRaises(ValueError):extract_magic_document(text,Path('/project/layout/other.mag'))
        with self.assertRaises(ValueError):extract_magic_document(text.replace('<< end >>',''),Path('/project/layout/top.mag'))
        with self.assertRaises(ValueError):extract_magic_document(text.replace('end top','file /project/layout/child\nmagic\n<< end >>\nend top'),Path('/project/layout/top.mag'))

    def test_save_keeps_actual_write_receipt_after_native_hold_expiry_or_uncertain_write(self):
        from analog_factory.service import Workspace
        from analog_factory.native_canvas import NativeCanvas, encode_packet
        from analog_factory.operations import InvocationContext
        from unittest.mock import patch
        from dataclasses import replace
        import hashlib, threading
        for failure_mode in ('expired', 'post_commit_error'):
            with self.subTest(failure_mode=failure_mode), tempfile.TemporaryDirectory() as directory:
                workspace=Workspace(Path(directory));registry=workspace.operations
                self.addCleanup(registry.close);self.addCleanup(workspace.agents.close)
                pid=workspace.projects.create('Save receipt safety')['id']
                relative='schematics/native.sch';before='original native source\n';after='actual exported native snapshot\n'
                workspace.projects.write(pid,relative,before)
                target=workspace.projects.path(pid)/relative;private=Path(directory)/'private';private.mkdir()
                sid='a'*32;rid='b'*32;nonce='c'*64
                session={'id':sid,'project_id':pid,'run_id':rid,'tool':'xschem','source':relative,'target':target,
                    'directory':private,'nonce':nonce,'commands':0,'lock':threading.Lock()}
                manager=NativeCanvas(workspace)
                prepared={'nonce':nonce,'revision':'3','source':str(target),'modified':'1','instances':'[]','selected':'[]','box':'[]','layers':'[]','complete':'1','ok':'1','commit':'prepared'}
                def response(session,command_id,deadline,phase=None):
                    packet={**prepared,'command_id':command_id,'document':command_id+'.document'}
                    if phase=={'prepared'}:
                        (private/packet['document']).write_text(after)
                        (private/(command_id+'.response')).write_bytes(encode_packet(packet))
                        return packet
                    return {**packet,'commit':'expired'}
                original=registry.operations['file.write']
                if failure_mode=='post_commit_error':
                    def write_then_error(context,params):
                        original.handler(context,params)
                        raise RuntimeError('deliberate post-commit receipt failure')
                    registry.operations['file.write']=replace(original,handler=write_then_error)
                with patch.object(manager,'resolve',return_value=session),patch.object(manager,'_response',side_effect=response):
                    result=manager.save(InvocationContext(),pid,sid,rid,3,hashlib.sha256(before.encode()).hexdigest())
                self.assertEqual(target.read_text(),after)
                self.assertTrue(workspace.projects.history(pid,relative))
                self.assertFalse(result['acknowledged'])
                self.assertTrue(result['state']['modified'])
                self.assertEqual(result['status'],'interrupted')
                self.assertIn('write_receipt',result)
                self.assertEqual(result['write_receipt']['ok'],failure_mode=='expired')
                self.assertEqual(result['saved'],True if failure_mode=='expired' else None)
                self.assertEqual(session['commands'],1,'uncertain saves must never retry')

    def test_discovery_without_native_target_reports_unavailability_without_consuming_commands(self):
        from analog_factory.service import Workspace
        from analog_factory.operations import InvocationContext, OperationError
        with tempfile.TemporaryDirectory() as directory:
            workspace=Workspace(Path(directory));registry=workspace.operations
            self.addCleanup(registry.close);self.addCleanup(workspace.agents.close)
            context=InvocationContext()
            for name in ('native.canvas.select','native.canvas.move','native.canvas.connect_pins','native.canvas.paint_box','native.canvas.save'):
                with self.subTest(name=name), self.assertRaises(OperationError) as error:
                    registry.operations[name].frontend_target(context,{})
                self.assertEqual(error.exception.code,'FRONTEND_DISCONNECTED')
            available=registry.operations['native.canvas.get'].availability(context,{})
            self.assertTrue(available)
            self.assertEqual(workspace.native_canvas.sessions,{})

    def test_registry_discovery_native_availability_uses_reason_list_contract(self):
        from analog_factory.service import Workspace
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            workspace=Workspace(Path(directory));registry=workspace.operations
            self.addCleanup(registry.close);self.addCleanup(workspace.agents.close)
            pid=workspace.projects.create('Native discovery')['id']
            params={'project_id':pid,'session_id':'a'*32,'run_id':'b'*32,'tool':'xschem','source':'schematics/native.sch'}
            live={'state':{'project_id':pid,'session_id':params['session_id'],'run_id':params['run_id'],'tool':'xschem','connected':True}}
            with patch.object(workspace.native_canvas,'live_target',return_value=live),patch.object(workspace,'run_availability',return_value={'available':True,'reasons':[]}),patch('analog_factory.operation_native_canvas.eda.prepare',return_value={}):
                for name in ('native.canvas.get','native.canvas.start'):
                    result=registry.describe_operation(name,parameters=params)
                    self.assertTrue(result['availability']['available'],result['availability'])
                result=registry.list_operations(module='native',parameters=params,available_only=True)
                self.assertIn('native.canvas.get',[item['operation_id'] for item in result])
                self.assertIn('native.canvas.start',[item['operation_id'] for item in result])
            missing=registry.describe_operation('native.canvas.get',parameters={'project_id':pid})
            self.assertFalse(missing['availability']['available'])
            self.assertFalse(any('Availability callback' in reason for reason in missing['availability']['reasons']))

    def test_controller_manual_field_bounds_and_wrong_target_ack(self):
        import shutil
        if not shutil.which('node'):self.skipTest('Node is required')
        controller=Path(__file__).resolve().parents[1]/'analog_factory/static/native-canvas.js'
        script=r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
global.window={};global.crypto=require('crypto').webcrypto;
const handlers={},dialog={open:false,innerHTML:'',setAttribute(){},addEventListener(n,f){handlers[n]=f},showModal(){this.open=true},close(){this.open=false}};
global.document={createElement(){return dialog},body:{append(){}}};
const native={project_id:'p',run_id:'r',session_id:'s',tool:'xschem',source:'schematics/native.sch',native_revision:7,modified:true,instances:[{id:'R1',pins:['P','M']}],selected:[],box:[],layers:[],connected:true,source_sha256:'a'.repeat(64)};
let wrong=false;
window.AFOperations={async invoke(name,p){if(name==='project.detail')return {data:{},version:'v'};return {data:{state:{...native,session_id:wrong?'other':p.session_id}},version:'v'}}};
vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));
(async()=>{const ui=window.AFNativeCanvas.create({state:{project:{id:'p'},dirty:false},t:v=>'translated:'+v});ui.mount({innerHTML:'',addEventListener(){},querySelector(){return {disabled:false}}});await ui.commands.open();
for(const [key,value] of [['source','x'.repeat(2049)],['tool','invalid'],['direction','diagonal'],['route','invalid'],['grid_steps','1.5'],['grid_steps','NaN']]){
 const old=ui.snapshot().options[key];handlers.change({target:{dataset:{nativeField:key},value}});assert.equal(ui.snapshot().options[key],old);assert.ok(ui.snapshot().error);
}
handlers.change({target:{dataset:{nativeField:'source'},value:'x'.repeat(2048)}});assert.equal(ui.snapshot().options.source.length,2048);
await assert.rejects(Promise.resolve().then(()=>ui.commands.set_options({source:'x'.repeat(2049)})),/length/);
await ui.commands.open({session_id:'s',run_id:'r'});assert.ok(dialog.innerHTML.includes('translated:right'));assert.ok(dialog.innerHTML.includes('translated:horizontal_first'));
wrong=true;await assert.rejects(ui.commands.refresh(),/acknowledgment.*target/i);assert.equal(ui.snapshot().needs_inspection,true);await assert.rejects(ui.commands.move(),/inspect/i);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
        result=subprocess.run(['node','-e',script,str(controller)],text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
