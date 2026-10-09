// Actual registry/job evidence; full weight training is separately opt-in.
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const base = process.env.AF_URL || 'http://127.0.0.1:8765';
(async()=>{
 const browser=await chromium.launch({channel:'chromium',headless:true,args:['--no-sandbox']});
 try {
  const page=await browser.newPage({viewport:{width:1450,height:1000}}), errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base);await page.locator('body:not([inert])').waitFor();
  await page.waitForFunction(()=>window.AFTrainingControls && window.AFOperations);
  const invoke=(operation_id,parameters={})=>page.evaluate(({operation_id,parameters})=>AFOperations.invoke(operation_id,parameters),{operation_id,parameters});
  const project=process.env.AF_TRAINING_PROJECT || (await invoke('project.create',{name:'Training browser qualification'})).data.id;
  await page.evaluate(async project=>{
   await AFOperations.dispatch('ui.workspace.select_project',{project_id:project});
   if(!AFOperations.names().includes('ui.training.open'))throw new Error('Training controller is not registered in the main app');
  },project);
  const command=(name,parameters={})=>page.evaluate(({name,parameters})=>AFOperations.dispatch('ui.training.'+name,parameters),{name,parameters});
  await command('open');
  const dialog=page.locator('#training-controls-dialog');
  await dialog.waitFor();
  assert.match(await dialog.innerText(),/Task set|任务/);
  assert.match(await dialog.innerText(),/Agent Lightning/);
  assert.match(await dialog.innerText(),/not integrated|尚未集成|unavailable/i);
  await dialog.locator('[data-training-option=steps]').fill('3');
  await dialog.locator('[data-training-option=steps]').dispatchEvent('change');
  const snapshot=()=>page.evaluate(()=>AFOperations.snapshot().training);
  assert.equal((await snapshot()).options.steps,3);
  const completion=dialog.locator('[data-training-option=max_completion_length]');
  assert.equal(await completion.getAttribute('min'),'96');assert.equal(await completion.getAttribute('max'),'2048');
  assert.equal(Number(await completion.inputValue()),(await snapshot()).options.max_completion_length);
  if(!process.env.AF_TRAINING_PROJECT)assert.equal((await snapshot()).options.max_completion_length,96);
  assert.match(await dialog.innerText(),/tool observations and framing|工具观测和格式文本/);
  await completion.fill('512');await completion.dispatchEvent('change');
  await page.waitForFunction(()=>AFOperations.snapshot().training.options.max_completion_length===512);
  for(const value of [95,2049,96.5,'512']){
   const rejected=await page.evaluate(async value=>{try{await AFOperations.dispatch('ui.training.set_options',{options:{max_completion_length:value}});return false;}catch{return true;}},value);
   assert.equal(rejected,true);assert.equal((await snapshot()).options.max_completion_length,512);
  }
  await command('set_options',{options:{steps:2,eval_tasks:2}});
  assert.equal((await snapshot()).options.max_completion_length,512,'manual partial options preserve the selected budget');
  assert.equal(await dialog.locator('[data-training-option=max_completion_length]').inputValue(),'512');
  const bad=await page.evaluate(async()=>{try{await (AFOperations.dispatch('ui.training.set_options',{options:{model:'/tmp/model'}}));return null;}catch(e){return e.message;}});
  assert.ok(bad);
  const defaults=(await invoke('training.taskset.list',{project_id:project})).data.defaults;
  await command('set_taskset_draft',{specification:{...defaults.current_mirror,name:'Browser current mirror qualification'}});
  await command('validate_taskset');
  const preview=(await snapshot()).taskset_preview_id;
  assert.match(preview,/^taskset-[a-f0-9]{24}$/);
  await command('create_taskset');
  assert.equal((await snapshot()).options.taskset_id,preview);
  assert.ok(await dialog.locator(`[data-training-taskset] option[value="${preview}"]`).count());
  await command('set_options',{options:{taskset_id:'educational-amplifier-v1'}});
  const jobs=(await invoke('training.list',{project_id:project})).data.jobs;
  const checkpoint=jobs.find(job=>job.model_weights_updated && job.status==='succeeded');
  if(process.env.AF_TRAINING_PROJECT) assert.ok(checkpoint, 'Requested project must contain a verified local checkpoint');
  let replayed=false;
  if(checkpoint){
   await command('select_job',{job_id:checkpoint.id});
   assert.match(await dialog.innerText(),/Checkpoint|检查点/);
   assert.match(await dialog.innerText(),/0\.016|Weight update|权重/);
   assert.equal((await snapshot()).job_id,checkpoint.id);
   assert.ok(await dialog.locator('[data-training-curve]').count());
   const result=checkpoint.evaluation?.random?.episodes?.find(e=>e.results?.length);
   if(result){
    await command('replay',{episode:result.episode});
    await page.waitForFunction(()=>{
     const x=AFOperations.snapshot().training;
     return x.status==='succeeded';
    },null,{timeout:180000});
    assert.match(await dialog.innerText(),/Replay|重放/);
    replayed=true;
   }
  }
  // Closing preserves the running job; no hidden automatic cancellation.
  await command('close');assert.equal((await snapshot()).open,false);
  // Acknowledged semantic commands target the same live controller.
  await page.waitForFunction(()=>Boolean(AFOperations.status().client_id));
  const remote=async(operation_id,parameters={})=>{
   let ready;
   for(let attempt=0;attempt<100;attempt++){
    ready=await page.evaluate(async()=>{
     const result=await AFOperations.invoke('ui.sessions');
     const session=result.data.sessions.find(item=>item.client_id===AFOperations.status().client_id);
     return session && JSON.stringify(session.state)===JSON.stringify(AFOperations.snapshot()) ?
      {client_id:session.client_id,ui_version:session.version,version:result.version}:null;
    });
    if(ready)break;await page.waitForTimeout(100);
   }
   if(!ready){const diagnostic=await page.evaluate(async()=>{
    const result=await AFOperations.invoke('ui.sessions'),current=AFOperations.snapshot();
    const session=result.data.sessions.find(item=>item.client_id===AFOperations.status().client_id);
    return {bridge:AFOperations.status(),different_modules:Object.keys(current).filter(key=>JSON.stringify(current[key])!==JSON.stringify(session?.state?.[key]))};
   });assert.fail('Browser must acknowledge the current training state: '+JSON.stringify(diagnostic));}
   return page.evaluate(({operation_id,parameters,ready})=>AFOperations.invoke(operation_id,
    {client_id:ready.client_id,ui_version:ready.ui_version,...parameters},
    operation_id==='ui.training.create_taskset'?{expected_version:ready.version}:{}),{operation_id,parameters,ready});
  };
  await remote('ui.training.open');
  await remote('ui.training.set_options',{options:{steps:3,max_completion_length:2048}});
  assert.equal((await snapshot()).options.steps,3);
  assert.equal((await snapshot()).options.max_completion_length,2048);
  assert.equal(await dialog.locator('[data-training-option=max_completion_length]').inputValue(),'2048');
  await remote('ui.training.set_options',{options:{max_completion_length:768}});
  await dialog.locator('[data-training-option=steps]').fill('4');
  await dialog.locator('[data-training-option=steps]').dispatchEvent('change');
  await page.waitForFunction(()=>AFOperations.snapshot().training.options.steps===4);
  assert.equal((await snapshot()).options.max_completion_length,768,'manual steps-only edit preserves budget');
  assert.equal(await dialog.locator('[data-training-option=max_completion_length]').inputValue(),'768');
  await remote('ui.training.set_options',{options:{steps:3}});
  assert.equal((await snapshot()).options.steps,3);
  assert.equal((await snapshot()).options.max_completion_length,768,'remote partial patch must not inject the default budget');
  assert.equal(await dialog.locator('[data-training-option=max_completion_length]').inputValue(),'768','remote and manual controls retain the same budget');
  await remote('ui.training.set_options',{options:{max_completion_length:96}});
  assert.equal((await snapshot()).options.max_completion_length,96);
  const invalidRemote=await page.evaluate(async()=>{try{await AFOperations.invoke('ui.training.set_options',
   {client_id:AFOperations.status().client_id,options:{max_completion_length:95}});return false;}catch{return true;}});
  assert.equal(invalidRemote,true);assert.equal((await snapshot()).options.max_completion_length,96);
  await remote('ui.training.set_taskset_draft',{specification:{...defaults.amplifier,name:'Acknowledged amplifier taskset'}});
  await remote('ui.training.validate_taskset');
  const remotePreview=(await snapshot()).taskset_preview_id;
  await remote('ui.training.create_taskset');
  assert.equal((await snapshot()).options.taskset_id,remotePreview);
  await remote('ui.training.close');
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({resources:true,bounded_options:true,completion_budget_manual_and_acknowledged:true,immutable_taskset_publication:true,unavailable_framework_reasons:true,
    checkpoint_and_curves:!!checkpoint,actual_simulator_replay:replayed,acknowledged_ui_parity:true}));
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
