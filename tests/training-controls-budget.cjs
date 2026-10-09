'use strict';
// Controller contracts only: no trainer, model inference or download is invoked.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const clone=value=>JSON.parse(JSON.stringify(value));
function fixture(){
 const dialogs=[],calls=[],jobs={legacy:{id:'legacy',status:'succeeded',kind:'training',created_at:'2026-01-03',options:{taskset_id:'educational-amplifier-v1'}},
  tuned:{id:'tuned',status:'succeeded',kind:'training',created_at:'2026-01-02',options:{taskset_id:'educational-amplifier-v1',max_completion_length:512},policy_limits:{completion_tokens:512,tool_rounds:6},curves:[{step:1,num_tokens:301},{step:2,reward:.4}]},
  running:{id:'running',status:'running',kind:'training',created_at:'2026-01-01',options:{max_completion_length:2048}}};
 let delay;
 const api={invoke:async(id,params={})=>{calls.push({id,params:clone(params)});
  if(id==='training.resources')return {data:{frameworks:{trl_cpu:{available:false}}}};
  if(id==='training.taskset.list')return {data:{tasksets:[{id:'educational-amplifier-v1',counts:{}}],defaults:{amplifier:{family:'amplifier'}}}};
  if(id==='training.list')return {data:{jobs:clone(Object.values(jobs))}};
  if(id==='training.get'){if(delay){const pending=delay;delay=null;return pending;}return {data:clone(jobs[params.job_id])};}
  if(id==='training.evaluate')return {data:{id:'resume',status:'queued',kind:'training_evaluation',options:{max_completion_length:512}}};
  throw new Error('Unexpected operation '+id);
 }};
 const element=()=>({innerHTML:'',open:false,disabled:false,addEventListener(){},setAttribute(){},querySelector(){return element();},showModal(){this.open=true;},close(){this.open=false;}});
 const window={AFOperations:api},sandbox={window,document:{createElement(){const item=element();dialogs.push(item);return item;},body:{append(){}}},setTimeout:()=>1,clearTimeout(){},crypto:{randomUUID:()=> 'test'}};
 vm.runInNewContext(fs.readFileSync('analog_factory/static/training-controls.js','utf8'),sandbox);
 const ui=window.AFTrainingControls.create({state:{project:{id:'p'}}});ui.mount(element());
 return {ui,jobs,calls,dialog:dialogs[0],delay:promise=>{delay=promise;}};
}
(async()=>{
 const f=fixture();await f.ui.commands.open();assert.equal(f.ui.snapshot().options.max_completion_length,96,'historical absent budget defaults to 96');
 assert.match(f.dialog.innerHTML,/data-training-option="max_completion_length"/,'actual manual budget control exists');
 await f.ui.commands.select_job({job_id:'tuned'});assert.equal(f.ui.snapshot().options.max_completion_length,512,'pristine selection hydrates recorded budget');
 assert.match(f.dialog.innerHTML,/completion_tokens/);assert.match(f.dialog.innerHTML,/301/,'token-only curve rows remain visible');
 for(const value of [95,2049,96.5,'512',NaN,Infinity])assert.throws(()=>f.ui.commands.set_options({options:{max_completion_length:value}}),/range/);
 f.ui.commands.set_options({options:{max_completion_length:2048}});assert.equal(f.ui.snapshot().options.max_completion_length,2048);
 await f.ui.commands.refresh();assert.equal(f.ui.snapshot().options.max_completion_length,2048,'refresh never replaces an edited budget');
 await f.ui.commands.select_job({job_id:'legacy'});assert.equal(f.ui.snapshot().options.max_completion_length,2048,'historical evidence selection preserves edited draft');
 const delayed=fixture();await delayed.ui.commands.open();let release;delayed.delay(new Promise(resolve=>{release=resolve;}));
 const selecting=delayed.ui.commands.select_job({job_id:'tuned'});delayed.ui.commands.set_options({options:{max_completion_length:1024}});
 release({data:clone(delayed.jobs.tuned)});await selecting;
 assert.equal(delayed.ui.snapshot().options.max_completion_length,1024,'typing during selected-job read cannot be overwritten');
 const raced=fixture();await raced.ui.commands.open();let releaseOld;raced.delay(new Promise(resolve=>{releaseOld=resolve;}));
 const oldSelection=raced.ui.commands.select_job({job_id:'legacy'});await raced.ui.commands.select_job({job_id:'tuned'});
 releaseOld({data:clone(raced.jobs.legacy)});await assert.rejects(oldSelection,/changed/);
 assert.equal(raced.ui.snapshot().job_id,'tuned','a late historical read cannot replace the latest selected job');
 assert.equal(raced.ui.snapshot().options.max_completion_length,512);
 const active=fixture();await active.ui.commands.open();await active.ui.commands.select_job({job_id:'running'});
 assert.equal(active.ui.snapshot().options.max_completion_length,2048);
 assert.throws(()=>active.ui.commands.set_options({options:{max_completion_length:96}}),/cancel/,'running jobs remain immutable');
 const resumed=fixture();resumed.jobs.tuned.kind='training_evaluation';resumed.jobs.tuned.status='failed';resumed.jobs.tuned.parent_id='checkpoint';
 await resumed.ui.commands.open();await resumed.ui.commands.select_job({job_id:'tuned'});
 resumed.ui.commands.set_options({options:{max_completion_length:2048,wall_seconds:300}});await resumed.ui.commands.resume_evaluation();
 assert.deepEqual(resumed.calls.at(-1).params.options,{wall_seconds:300},'resume only forwards wall time and cannot replace the original completion budget');
 assert.ok(f.calls.every(call=>!['training.start','training.evaluate','training.replay'].includes(call.id)));
 console.log('Training completion budget: integer bounds/default, manual field, recorded policy/token evidence, pristine historical hydration, edited and delayed draft preservation, active lock PASS.');
})().catch(error=>{console.error(error);process.exit(1);});
