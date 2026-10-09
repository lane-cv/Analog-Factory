// Opt-in actual prepared-SKY130 browser acceptance; uses a dedicated workspace.
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const base = process.env.AF_URL || 'http://127.0.0.1:8765';
(async()=>{
 const browser=await chromium.launch({channel:'chromium',headless:true,args:['--no-sandbox']});
 try {
  const page=await browser.newPage({viewport:{width:1450,height:1000}}), errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base);await page.waitForFunction(()=>window.AFOperations);
  const invoke=(operation_id,parameters={},options={})=>page.evaluate(({operation_id,parameters,options})=>AFOperations.invoke(operation_id,parameters,options),{operation_id,parameters,options});
  await page.locator('body:not([inert])').waitFor();
  const previous=await page.evaluate(()=>AFOperations.snapshot().workspace.project_id);
  await page.locator('#new-project').click();
  const form=page.locator('#new-project-form');
  await form.locator('[name=name]').fill('Actual browser OTA reference');
  await form.locator('[name=template]').selectOption('ota5');
  await form.locator('[type=submit]').click();
  await page.waitForFunction(previous=>AFOperations.snapshot().workspace.project_id && AFOperations.snapshot().workspace.project_id!==previous,previous);
  const pid=await page.evaluate(()=>AFOperations.snapshot().workspace.project_id);
  const detail=(await invoke('project.detail',{project_id:pid})).data;
  assert.equal(detail.project.template,'ota5');
  assert.equal(detail.runs.length,0);
  await page.evaluate(()=>AFOperations.dispatch('ui.workspace.navigate',{tab:'verification'}));
  assert.equal(await page.locator('#verification-layout').inputValue(),'layout/ota5.mag');
  assert.equal(await page.locator('#verification-extracted').inputValue(),'netlists/extracted-lvs.spice');
  assert.equal(await page.locator('#verification-cell').inputValue(),'ota5');
  const waitRun=async()=>{
   const id=await page.evaluate(()=>AFOperations.snapshot().runs.selected);
   let run;
   const deadline=Date.now()+210000;
   while(Date.now()<deadline){
    run=(await invoke('run.get',{project_id:pid,run_id:id})).data;
    if(!['queued','starting','running'].includes(run.status))break;
    await page.waitForTimeout(400);
   }
   assert.equal(run.status,'succeeded',JSON.stringify(run));
   await page.evaluate(id=>AFOperations.dispatch('ui.runs.select',{run_id:id}),id);
   await page.evaluate(()=>AFOperations.dispatch('ui.workspace.navigate',{tab:'verification'}));
   return run;
  };
  await page.locator('[data-action=reference-netlist]').click();
  await page.waitForFunction(()=>AFOperations.snapshot().runs.selected);
  const schematic=await waitRun();
  assert.equal(schematic.metadata.sources.schematic,'schematics/ota5.sch');
  assert.equal(await page.evaluate(()=>AFOperations.snapshot().workspace.tab),'verification');
  // Acknowledged remote publication executes the same semantic handler as the button.
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
  assert.ok(ready,'Current browser state must be acknowledged');
  const published=await invoke('ui.runs.derive_reference',{client_id:ready.client_id,ui_version:ready.ui_version,run_id:schematic.id},{expected_version:ready.version});
  assert.equal(published.data.data.run_id,schematic.id);
  assert.ok(published.data.data.outputs['netlists/schematic.spice']);
  await page.locator('[data-run-kind=extract]').click();
  await page.waitForFunction(id=>AFOperations.snapshot().runs.selected!==id,schematic.id);
  const extracted=await waitRun();
  assert.equal(extracted.metadata.sources.layout,'layout/ota5.mag');
  await page.locator('[data-action=reference-publish]').click();
  await page.waitForFunction(()=>AFOperations.snapshot().workspace.files.includes('netlists/extracted.spice'));
  await page.locator('[data-run-kind=lvs]').click();
  await page.waitForFunction(id=>AFOperations.snapshot().runs.selected!==id,extracted.id);
  const lvs=await waitRun();assert.equal(lvs.verification.status,'pass');
  await page.locator('[data-action=reference-post]').click();
  await page.waitForFunction(id=>AFOperations.snapshot().runs.selected!==id,lvs.id);
  const post=await waitRun();assert.ok(post.metrics.gain_db>30);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({project_id:pid,template_create:true,no_supplied_results:true,default_sources:true,headless_netlisting:true,
    acknowledged_publication:true,actual_extraction:true,actual_lvs:true,post_gain_db:post.metrics.gain_db}));
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
