'use strict';
// Controller acceptance must bind each delayed receipt to its inspected target.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../analog_factory/static/native-canvas.js'),'utf8');
const oldSession='a'.repeat(32),oldRun='b'.repeat(32),newSession='c'.repeat(32),newRun='d'.repeat(32);

function deferred(){let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};}
function fixture(invoke){
  const listeners={};
  const dialog={open:false,innerHTML:'',setAttribute(){},addEventListener(name,handler){listeners[name]=handler;},showModal(){this.open=true;},close(){this.open=false;}};
  const context={window:{AFOperations:{invoke}},crypto:require('node:crypto').webcrypto,
    setTimeout(handler){handler();},
    document:{createElement(){return dialog;},body:{append(){}}}};
  vm.createContext(context);vm.runInContext(source,context);
  const state={project:{id:'p'},dirty:false};
  const ui=context.window.AFNativeCanvas.create({state});
  ui.mount({innerHTML:'',addEventListener(){},querySelector(){return {disabled:false};}});
  return {ui,state,listeners,dialog};
}
function native(session_id,run_id){return {project_id:'p',session_id,run_id,tool:'xschem',
  source:`schematics/${session_id}.sch`,native_revision:7,modified:true,
  instances:[{id:'R1',pins:['P','M']}],selected:[],box:[],box_token:'',layers:[],connected:true,source_sha256:'e'.repeat(64)};}

test('failed inspection of a new target cannot authorize edits using the old target state',async()=>{
  const calls=[];
  const {ui}=fixture(async(name,p)=>{
    calls.push([name,p]);
    if(name==='project.detail')return {data:{},version:'v'};
    if(name==='native.canvas.get'){
      if(p.session_id===newSession)throw Object.assign(new Error('Inspection timed out'),{code:'OUTCOME_UNKNOWN'});
      return {data:{state:native(p.session_id,p.run_id)},version:'v'};
    }
    return {data:{acknowledged:true,state:native(p.session_id,p.run_id)},version:'v'};
  });
  await ui.commands.open({session_id:oldSession,run_id:oldRun});
  await assert.rejects(ui.commands.open({session_id:newSession,run_id:newRun}),/timed out/i);
  await assert.rejects(ui.commands.move({instance_id:'R1',direction:'right',grid_steps:1}),/inspect|connected|target/i);
  assert.equal(calls.filter(([name])=>name==='native.canvas.move').length,0);
  const view=ui.snapshot();
  assert.ok(!view.native||view.native.session_id===view.session_id,'A snapshot cannot combine different targets');
});

test('a delayed stop cannot disconnect a newly inspected target',async()=>{
  const cancelled=deferred();let cancelling=false;
  const {ui}=fixture(async(name,p)=>{
    if(name==='project.detail')return {data:{},version:'v'};
    if(name==='run.cancel'){assert.equal(p.run_id,oldRun);cancelling=true;return cancelled.promise;}
    return {data:{state:native(p.session_id,p.run_id)},version:'v'};
  });
  await ui.commands.open({session_id:oldSession,run_id:oldRun});
  const stopping=ui.commands.stop();
  while(!cancelling)await Promise.resolve();
  let reopened=false;
  try{await ui.commands.open({session_id:newSession,run_id:newRun});reopened=true;}
  catch(error){assert.match(error.message,/wait|current request|busy/i);}
  cancelled.resolve({data:{id:oldRun,status:'cancelled'}});await stopping;
  if(!reopened)await ui.commands.open({session_id:newSession,run_id:newRun});
  assert.equal(ui.snapshot().native.session_id,newSession);
  assert.equal(ui.snapshot().native.connected,true,'The old stop receipt only belongs to the old target');
});

test('closing controls during launch inspection invalidates the delayed success receipt',async()=>{
  const inspected=deferred();let inspecting=false;
  const {ui}=fixture(async(name)=>{
    if(name==='project.detail')return {data:{},version:'v'};
    if(name==='native.canvas.start')return {data:{id:oldRun,native_session_id:oldSession},version:'v'};
    if(name==='native.canvas.get'){inspecting=true;return inspected.promise;}
  });
  await ui.commands.open();
  const starting=ui.commands.start();
  while(!inspecting)await Promise.resolve();
  ui.commands.close();
  inspected.resolve({data:{state:native(oldSession,oldRun)},version:'v'});
  await assert.rejects(starting,/project|closed|changed|inspect/i);
  assert.equal(ui.snapshot().open,false);
});

test('manual invalid grid drafts cannot make published native state violate its schema',async()=>{
  const {ui,listeners}=fixture(async()=>({data:{},version:'v'}));
  await ui.commands.open();
  for(const value of ['0','','1000','invalid']){
    listeners.change({target:{dataset:{nativeField:'grid_steps'},value}});
    const steps=ui.snapshot().options.grid_steps;
    assert.ok(Number.isInteger(steps)&&steps>=1&&steps<=999,
      `Published grid steps must remain valid after a manual draft of ${JSON.stringify(value)}`);
  }
});

test('Escape during a delivered native edit requires inspection before any retry',async()=>{
  const edited=deferred();let editing=false,editCalls=0,inspectCalls=0;
  const {ui,listeners,dialog}=fixture(async(name,p)=>{
    if(name==='project.detail')return {data:{},version:'v'};
    if(name==='native.canvas.get'){inspectCalls++;return {data:{state:native(p.session_id,p.run_id)},version:'v'};}
    if(name==='native.canvas.move'){
      editCalls++;
      if(!editing){editing=true;return edited.promise;}
      return {data:{acknowledged:true,state:native(p.session_id,p.run_id)},version:'v'};
    }
  });
  await ui.commands.open({session_id:oldSession,run_id:oldRun});
  const moving=ui.commands.move({instance_id:'R1',direction:'right',grid_steps:1});
  while(!editing)await Promise.resolve();
  let prevented=false;
  listeners.cancel({preventDefault(){prevented=true;}});
  if(!prevented)dialog.open=false; // Native Escape closes a non-cancelled dialog.
  edited.resolve({data:{acknowledged:true,state:native(oldSession,oldRun)},version:'v'});
  await assert.rejects(moving,/changed|inspect/i);
  await ui.commands.open();
  await assert.rejects(ui.commands.move({instance_id:'R1',direction:'right',grid_steps:1}),/inspect/i);
  assert.equal(editCalls,1,'No second edit may be delivered before inspection');
  assert.equal(inspectCalls,1,'Reopening alone is not a new native inspection');
});

test('only a confirmed stopped target allows stop-close-reopen and a fresh native launch',async()=>{
  for(const outcome of ['confirmed','pending_then_confirmed','unknown','pending']){
    const calls=[];
    const {ui}=fixture(async(name,p)=>{
      calls.push([name,p]);
      if(name==='project.detail')return {data:{},version:'v'};
      if(name==='native.canvas.get')return {data:{state:native(p.session_id,p.run_id)},version:'v'};
      if(name==='run.cancel'){
        if(outcome==='unknown')throw Object.assign(new Error('Unknown stop outcome'),{code:'OUTCOME_UNKNOWN'});
        return {data:{id:oldRun,status:outcome==='confirmed'?'cancelled':'running'}};
      }
      if(name==='run.get')return {data:{id:oldRun,status:outcome==='pending_then_confirmed'?'cancelled':'running'}};
      if(name==='native.canvas.start')return {data:{id:newRun,native_session_id:newSession},version:'v'};
      throw new Error(`Unexpected operation ${name}`);
    });
    await ui.commands.open({session_id:oldSession,run_id:oldRun});
    if(outcome==='confirmed'||outcome==='pending_then_confirmed'){
      await ui.commands.stop();
      if(outcome==='pending_then_confirmed')assert.ok(calls.some(([name])=>name==='run.get'),
        'Cancellation request must be confirmed terminal');
      ui.commands.close();await ui.commands.open();
      await ui.commands.set_options({tool:'magic',source:'layout/new.mag'});
      const started=await ui.commands.start();
      assert.equal(started.run_id,newRun);
      assert.equal(started.needs_inspection,false);
      assert.equal(calls.filter(([name])=>name==='native.canvas.start').length,1);
      const launch=calls.find(([name])=>name==='native.canvas.start')[1];
      assert.equal(launch.tool,'magic');assert.equal(launch.source,'layout/new.mag');
    }else{
      await assert.rejects(ui.commands.stop(),/unknown|inspect/i);
      await assert.rejects(ui.commands.start(),/inspect/i,'An unconfirmed stop must not authorize a fresh launch');
      ui.commands.close();await ui.commands.open();
      await assert.rejects(ui.commands.start(),/inspect/i);
      assert.equal(calls.filter(([name])=>name==='native.canvas.start').length,0);
    }
  }
});

test('manual and remote native control drafts share one bounded operation without native effects',async()=>{
  function controls(){
    const calls=[];
    return {...fixture(async(name,p)=>{
      calls.push(name);
      if(name==='project.detail')return {data:{},version:'v'};
      if(name==='native.canvas.get')return {data:{state:{...native(p.session_id,p.run_id),
        instances:[{id:'R1',pins:['P','M']},{id:'R2',pins:['A','B']}],layers:['metal2']}},version:'v'};
      throw new Error(`Draft editing must not invoke ${name}`);
    }),calls};
  }
  const manual=controls(),remote=controls();
  await manual.ui.commands.open();await remote.ui.commands.open();
  for(const tool of ['magic','xschem']){
    manual.listeners.change({target:{dataset:{nativeField:'tool'},value:tool}});
    remote.ui.commands.set_options({tool});
    assert.equal(JSON.stringify(manual.ui.snapshot().options),JSON.stringify(remote.ui.snapshot().options));
    assert.equal(remote.ui.snapshot().options.source,tool==='magic'?'layout/amplifier.mag':'schematics/amplifier.sch');
  }
  await manual.ui.commands.open({session_id:oldSession,run_id:oldRun});
  await remote.ui.commands.open({session_id:oldSession,run_id:oldRun});
  manual.calls.length=0;remote.calls.length=0;
  const changes={instance_id:'R1',direction:'left',grid_steps:3,from_instance:'R1',from_pin:'M',
    to_instance:'R1',to_pin:'P',route:'vertical_first',layer:'metal2'};
  for(const [key,value] of Object.entries(changes))manual.listeners.change({target:{dataset:{nativeField:key},value:String(value)}});
  const acknowledged=remote.ui.commands.set_options(changes);
  assert.equal(JSON.stringify(manual.ui.snapshot().options),JSON.stringify(acknowledged.options));
  assert.equal(acknowledged.native.native_revision,7);
  assert.equal(manual.ui.snapshot().native.native_revision,7);
  manual.listeners.change({target:{dataset:{nativeField:'from_instance'},value:'R2'}});
  remote.ui.commands.set_options({from_instance:'R2'});
  assert.equal(remote.ui.snapshot().options.from_pin,'A','Changing an instance selects its first pin in the shared draft');
  assert.equal(JSON.stringify(manual.ui.snapshot().options),JSON.stringify(remote.ui.snapshot().options));
  remote.ui.commands.set_options({to_instance:'R2',to_pin:'B'});
  manual.listeners.change({target:{dataset:{nativeField:'to_instance'},value:'R2'}});
  manual.listeners.change({target:{dataset:{nativeField:'to_pin'},value:'B'}});
  assert.equal(JSON.stringify(manual.ui.snapshot().options),JSON.stringify(remote.ui.snapshot().options));
  const before=JSON.stringify(remote.ui.snapshot().options);
  for(const invalid of [{instance_id:'missing'},{from_instance:'missing'},
    {from_pin:'P'},{to_instance:'R1',to_pin:'B'},{layer:'missing'}]){
    assert.throws(()=>remote.ui.commands.set_options(invalid),/listed native option/i);
    assert.equal(JSON.stringify(remote.ui.snapshot().options),before,'Unknown native enum values must preserve the whole draft');
  }
  for(const [key,value] of [['instance_id','missing'],['from_pin','P'],['layer','missing']]){
    manual.listeners.change({target:{dataset:{nativeField:key},value}});
    assert.equal(JSON.stringify(manual.ui.snapshot().options),before);
  }
  assert.throws(()=>remote.ui.commands.set_options({direction:'right',grid_steps:0}),/grid/i);
  assert.equal(JSON.stringify(remote.ui.snapshot().options),before,'Invalid remote changes are atomic');
  manual.listeners.change({target:{dataset:{nativeField:'grid_steps'},value:'0'}});
  assert.equal(JSON.stringify(manual.ui.snapshot().options),before,'Invalid manual changes preserve the same draft');
  for(const changes of [{tool:'magic'},{source:'layout/new.mag'}]){
    assert.throws(()=>remote.ui.commands.set_options(changes),/launch.*connected/i);
    const [key,value]=Object.entries(changes)[0];
    manual.listeners.change({target:{dataset:{nativeField:key},value}});
    assert.equal(JSON.stringify(manual.ui.snapshot().options),before);
  }
  assert.equal(manual.calls.length,0);assert.equal(remote.calls.length,0);
  remote.ui.commands.set_options({instance_id:'',from_instance:'',to_instance:'',from_pin:'',to_pin:'',layer:''});
  assert.equal(remote.ui.snapshot().options.from_pin,'','Empty select values remain valid');
});
