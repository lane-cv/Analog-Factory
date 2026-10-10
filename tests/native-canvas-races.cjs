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
function fixture(invoke,options={}){
  const listeners={};
  const dialog={open:false,innerHTML:'',setAttribute(){},addEventListener(name,handler){listeners[name]=handler;},showModal(){this.open=true;},close(){this.open=false;}};
  const context={window:{AFOperations:{invoke}},crypto:require('node:crypto').webcrypto,
    setTimeout(handler){handler();},
    document:{createElement(){return dialog;},body:{append(){}}}};
  vm.createContext(context);vm.runInContext(source,context);
  const state={project:{id:'p'},dirty:false};
  const ui=context.window.AFNativeCanvas.create({state,...options});
  ui.mount({innerHTML:'',addEventListener(){},querySelector(){return {disabled:false};}});
  return {ui,state,listeners,dialog};
}
function native(session_id,run_id){return {project_id:'p',session_id,run_id,tool:'xschem',
  source:`schematics/${session_id}.sch`,native_revision:7,modified:true,
  instances:[{id:'R1',pins:['P','M']}],selected:[],box:[],box_token:'',layers:[],connected:true,source_sha256:'e'.repeat(64)};}
function launchFailure(code,extra={}){return Object.assign(new Error(`Launch rejected: ${code}`),{code,
  envelope:{ok:false,operation_id:'native.canvas.start',data:null,task_id:null,status:'failed',version:null,...extra}});}

test('a proven launch rejection requires a project re-read and a new explicit start',async()=>{
  for(const code of ['VERSION_CONFLICT','CONFIRMATION_REQUIRED']){
    let version='v1';const launches=[];
    const {ui,dialog}=fixture(async(name,p,options)=>{
      if(name==='project.detail')return {data:{},version};
      if(name==='native.canvas.start'){
        launches.push(options);
        if(launches.length===1)throw launchFailure(code);
        return {data:{id:newRun,native_session_id:newSession},version};
      }
      if(name==='native.canvas.get')return {data:{state:native(p.session_id,p.run_id)},version};
      throw new Error(`Unexpected operation ${name}`);
    });
    await ui.commands.open();
    await assert.rejects(ui.commands.start(),error=>error.code===code);
    assert.equal(ui.snapshot().needs_inspection,true);
    assert.equal(ui.snapshot().unknown_outcome,false);
    assert.match(dialog.innerHTML,/Read latest project state/);
    await assert.rejects(ui.commands.start(),/inspect/i);
    assert.equal(launches.length,1,'A rejected launch is never automatically retried');
    version='v2';ui.commands.close();
    const reread=await ui.commands.open();
    assert.equal(reread.source_version,'v2');
    assert.equal(reread.needs_inspection,false,'Reopening after a proven rejection reads the current project version');
    assert.equal(launches.length,1,'Reopening performs no launch');
    const started=await ui.commands.start();
    assert.equal(launches[1].expected_version,'v2');
    assert.notEqual(launches[0].idempotency_key,launches[1].idempotency_key);
    assert.equal(started.session_id,newSession);
  }
});

test('the registered refresh and manual refresh both recover only by reading latest project state',async()=>{
  for(const manual of [false,true]){
    let reads=0,launches=0;
    const {ui,listeners,dialog}=fixture(async name=>{
      if(name==='project.detail')return {data:{},version:++reads===1?'v1':'v2'};
      if(name==='native.canvas.start'){launches++;throw launchFailure('VERSION_CONFLICT');}
      throw new Error(`Unexpected operation ${name}`);
    });
    await ui.commands.open();await assert.rejects(ui.commands.start(),/rejected/);
    assert.match(dialog.innerHTML,/<button[^>]*data-native-action="refresh"\s*>/,
      'Manual recovery must be available without an uninspectable run');
    if(manual){
      listeners.click({target:{closest(){return {disabled:false,dataset:{nativeAction:'refresh'}};}}});
      for(let turn=0;turn<20;turn++)await Promise.resolve();
    }else await ui.commands.refresh();
    const view=ui.snapshot();
    assert.equal(view.source_version,'v2');assert.equal(view.needs_inspection,false);
    assert.equal(view.unknown_outcome,false);assert.equal(view.busy,false);
    assert.equal(reads,2);assert.equal(launches,1,'A recovery action may not start the native process');
  }
});

test('a failed project re-read keeps a rejected launch locked until a successful explicit read',async()=>{
  let reads=0,failRead=false,launches=0;
  const {ui}=fixture(async name=>{
    if(name==='project.detail'){
      reads++;
      if(failRead)throw new Error('Project read failed');
      return {data:{},version:reads===1?'v1':'v2'};
    }
    if(name==='native.canvas.start'){launches++;throw launchFailure('VERSION_CONFLICT');}
  });
  await ui.commands.open();await assert.rejects(ui.commands.start(),/rejected/);
  failRead=true;
  await assert.rejects(ui.commands.refresh(),/Project read failed/);
  assert.equal(ui.snapshot().needs_inspection,true);assert.equal(ui.snapshot().source_version,'v1');
  await assert.rejects(ui.commands.start(),/inspect/i);assert.equal(launches,1);
  failRead=false;await ui.commands.refresh();
  assert.equal(ui.snapshot().needs_inspection,false);assert.equal(ui.snapshot().source_version,'v2');
});

test('unproven launch failures cannot be cleared by reopening or a generic project read',async()=>{
  for(const failure of [new Error('Lost connection'),launchFailure('OUTCOME_UNKNOWN'),
    launchFailure('INVALID_ARGUMENT'),launchFailure('PERMISSION_DENIED'),launchFailure('UNAVAILABLE'),
    Object.assign(new Error('Unproven version rejection'),{code:'VERSION_CONFLICT'}),
    launchFailure('VERSION_CONFLICT',{operation_id:'native.canvas.get'}),
    launchFailure('VERSION_CONFLICT',{data:{id:oldRun,native_session_id:oldSession}}),
    launchFailure('VERSION_CONFLICT',{task_id:`run:p:${oldRun}`})]){
    let launches=0;
    const {ui,dialog}=fixture(async name=>{
      if(name==='project.detail')return {data:{},version:'v2'};
      if(name==='native.canvas.start'){launches++;throw failure;}
    });
    await ui.commands.open();await assert.rejects(ui.commands.start());
    assert.equal(ui.snapshot().needs_inspection,true);assert.equal(ui.snapshot().unknown_outcome,true);
    ui.commands.close();await ui.commands.open();
    assert.equal(ui.snapshot().unknown_outcome,true,'A generic read cannot establish a launch outcome');
    await assert.rejects(ui.commands.start(),/inspect/i);
    await assert.rejects(ui.commands.refresh(),/actual recorded native run/i);
    assert.equal(launches,1);assert.doesNotMatch(dialog.innerHTML,/Read latest project state/);
  }
});

test('a failed generic project read cannot downgrade an unknown launch outcome',async()=>{
  let failRead=false;
  const {ui}=fixture(async name=>{
    if(name==='project.detail'){
      if(failRead)throw launchFailure('VERSION_CONFLICT');
      return {data:{},version:'v2'};
    }
    if(name==='native.canvas.start')throw launchFailure('OUTCOME_UNKNOWN');
  });
  await ui.commands.open();await assert.rejects(ui.commands.start());ui.commands.close();
  failRead=true;await assert.rejects(ui.commands.open());
  assert.equal(ui.snapshot().unknown_outcome,true);
  failRead=false;await ui.commands.open();
  assert.equal(ui.snapshot().unknown_outcome,true);await assert.rejects(ui.commands.start(),/inspect/i);
});

test('closing during a rejected-launch project re-read cannot accept the delayed version',async()=>{
  const read=deferred();let reads=0;
  const {ui}=fixture(async name=>{
    if(name==='project.detail')return ++reads===2?read.promise:{data:{},version:reads===1?'v1':'v3'};
    if(name==='native.canvas.start')throw launchFailure('VERSION_CONFLICT');
  });
  await ui.commands.open();await assert.rejects(ui.commands.start());
  const refreshing=ui.commands.refresh();ui.commands.close();
  read.resolve({data:{},version:'v2'});await assert.rejects(refreshing,/changed/i);
  assert.equal(ui.snapshot().open,false);assert.equal(ui.snapshot().source_version,'v1');
  assert.equal(ui.snapshot().needs_inspection,true);
  await ui.commands.open();assert.equal(ui.snapshot().source_version,'v3');
  assert.equal(ui.snapshot().needs_inspection,false);
});

test('a delayed launch response after close remains bounded by its actual delivery outcome',async()=>{
  for(const outcome of ['rejected','unknown','launched']){
    const pending=deferred();let launches=0;
    const {ui}=fixture(async(name,p)=>{
      if(name==='project.detail')return {data:{},version:'v2'};
      if(name==='native.canvas.start'){launches++;return pending.promise;}
      if(name==='native.canvas.get')return {data:{state:native(p.session_id,p.run_id)},version:'v2'};
    });
    await ui.commands.open();
    const starting=ui.commands.start();ui.commands.close();
    if(outcome==='launched')pending.resolve({data:{id:newRun,native_session_id:newSession},version:'v2'});
    else pending.resolve(Promise.reject(outcome==='rejected'?launchFailure('VERSION_CONFLICT'):launchFailure('OUTCOME_UNKNOWN')));
    await assert.rejects(starting);
    await ui.commands.open();
    assert.equal(launches,1,'Close and reopen never automatically deliver a second launch');
    if(outcome==='rejected')assert.equal(ui.snapshot().needs_inspection,false);
    else{
      assert.equal(ui.snapshot().needs_inspection,true);assert.equal(ui.snapshot().unknown_outcome,true);
      await assert.rejects(ui.commands.start(),/inspect/i);
      if(outcome==='launched'){
        assert.equal(ui.snapshot().run_id,newRun,'A known launched target remains available for inspection');
        await ui.commands.refresh();assert.equal(ui.snapshot().needs_inspection,false);
      }
    }
  }
});

test('existing targets and errors after a launch receipt always require native inspection',async()=>{
  for(const stage of ['existing','update','inspect'])for(const code of ['VERSION_CONFLICT','CONFIRMATION_REQUIRED','INVALID_ARGUMENT','PERMISSION_DENIED','UNAVAILABLE']){
    let failInspection=false;
    const {ui}=fixture(async(name,p)=>{
      if(name==='project.detail')return {data:{},version:'v2'};
      if(name==='native.canvas.start'){
        if(stage==='existing')throw launchFailure(code);
        failInspection=stage==='inspect';return {data:{id:newRun,native_session_id:newSession},version:'v2'};
      }
      if(name==='native.canvas.get'){
        if(failInspection)throw launchFailure(code);
        return {data:{state:native(p.session_id,p.run_id)},version:'v2'};
      }
    },{onUpdate(){if(stage==='update')throw launchFailure(code);}});
    await ui.commands.open(stage==='existing'?{session_id:oldSession,run_id:oldRun}:{});
    await assert.rejects(ui.commands.start());
    ui.commands.close();await ui.commands.open();
    assert.equal(ui.snapshot().needs_inspection,true);
    assert.equal(ui.snapshot().run_id,stage==='existing'?oldRun:newRun);
    await assert.rejects(ui.commands.start(),/inspect/i);
    failInspection=false;await ui.commands.refresh();assert.equal(ui.snapshot().needs_inspection,false);
  }
});

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
