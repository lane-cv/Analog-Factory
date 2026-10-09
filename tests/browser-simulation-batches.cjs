// Real UI, registry, scenario snapshots and ngspice evidence in a disposable server.
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const base = process.env.AF_URL || 'http://127.0.0.1:8765';
const source = `Browser RC batch
.param R=1000 VDD=1.8
VIN in 0 AC 2 SIN(0 0.1 1k)
VDD vdd 0 {VDD}
RPOWER vdd 0 1000
R1 in out {R}
C1 out 0 1u
.end
`;
(async () => {
  const browser=await chromium.launch({channel:'chromium',headless:true,args:['--no-sandbox']});
  try {
    const page=await browser.newPage({viewport:{width:1500,height:1100}}),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(base);await page.locator('body:not([inert])').waitFor();
    await page.waitForFunction(()=>window.AFOperations?.names().includes('ui.simulation_batch.open'));
    const invoke=async(operation_id,parameters={},options={})=>{
      const result=await page.evaluate(async({operation_id,parameters,options})=>{
        try{return {value:await AFOperations.invoke(operation_id,parameters,options)};}
        catch(error){return {error:{code:error.code,message:error.message}};}
      },{operation_id,parameters,options});
      if(result.error)throw Object.assign(new Error(result.error.message),result.error);
      return result.value;
    };
    const dispatch=(name,parameters={})=>page.evaluate(({name,parameters})=>AFOperations.dispatch(name,parameters),{name,parameters});
    const mutate=async(operation,parameters)=>{
      const read=await invoke('project.detail',{project_id:parameters.project_id});
      return invoke(operation,parameters,{expected_version:read.version});
    };
    const project=(await invoke('project.create',{name:'Advanced simulation browser fixture'})).data;
    await mutate('file.write',{project_id:project.id,path:'testbenches/pre.cir',content:source});
    await page.reload();await page.locator('body:not([inert])').waitFor();
    await dispatch('ui.workspace.select_project',{project_id:project.id});
    await dispatch('ui.simulation_batch.open');
    const dialog=page.locator('#simulation-batch-dialog');
    await dialog.locator('[data-batch-action=add_axis]').click();
    await dialog.locator('[data-batch-field="axes.0.parameter"]').fill('R');
    await dialog.locator('[data-batch-field="axes.0.values"]').fill('1000, 2000');
    await dialog.locator('[data-batch-field="measurements.0.frequency_hz"]').fill('100');
    await dialog.locator('[data-batch-action=preview]').click();
    await page.waitForFunction(()=>AFOperations.snapshot().simulation_batch.preview_current);
    assert.equal(await page.evaluate(()=>AFOperations.snapshot().simulation_batch.scenario_count),2);
    await dialog.locator('[data-batch-action=start]').click();
    await page.waitForFunction(()=>AFOperations.snapshot().simulation_batch.status==='succeeded',null,{timeout:30000});
    const state=await page.evaluate(()=>AFOperations.snapshot().simulation_batch);
    const job=(await invoke('job.get',{project_id:project.id,job_id:state.job_id})).data;
    assert.equal(job.iterations.length,2);
    assert.ok(Math.abs(job.iterations[0].metrics.gain_db+1.44507)<.02);
    assert.ok(job.iterations[1].metrics.gain_db<job.iterations[0].metrics.gain_db);
    assert.equal((await invoke('file.read',{project_id:project.id,path:'testbenches/pre.cir'})).data.content,source);
    await dialog.locator('[data-batch-inspect]').first().click();
    await page.waitForFunction(()=>Boolean(AFOperations.snapshot().simulation_batch.evidence_run_id));
    await dialog.locator('.evidence-details').last().waitFor();
    assert.match(await dialog.locator('.evidence-details').last().innerText(),/gain_db\s*=/i);

    // Stale human preview cannot silently launch a newer source revision.
    await dialog.locator('[data-batch-action=preview]').click();
    await page.waitForFunction(()=>AFOperations.snapshot().simulation_batch.preview_current);
    await mutate('file.write',{project_id:project.id,path:'changed.txt',content:'Concurrent source mutation'});
    const stale=await page.evaluate(async()=>{
      try{await AFOperations.dispatch('ui.simulation_batch.start');return null;}
      catch(error){return error.code;}
    });
    assert.equal(stale,'VERSION_CONFLICT');
    assert.equal((await invoke('job.list',{project_id:project.id})).data.filter(item=>item.kind==='simulation-batch').length,1);
    await dispatch('ui.simulation_batch.close');

    // Same controller is reachable through acknowledged semantic UI commands.
    await page.waitForFunction(()=>Boolean(AFOperations.status().client_id));
    const remote=async(operation_id,parameters={},attempt=0)=>{
      let ready;
      for(let attempt=0;attempt<100;attempt++) {
        ready=await page.evaluate(async()=>{
          const result=await AFOperations.invoke('ui.sessions');
          const session=result.data.sessions.find(item=>item.client_id===AFOperations.status().client_id);
          return session && JSON.stringify(session.state)===JSON.stringify(AFOperations.snapshot())?{client_id:session.client_id,ui_version:session.version,version:result.version}:null;
        });
        if(ready)break;
        await page.waitForTimeout(100);
      }
      assert.ok(ready,'Browser session must acknowledge the current scenario state');
      const {client_id,ui_version,version}=ready;
      try {
        return await invoke(operation_id,{client_id,ui_version,...parameters},
          ['ui.simulation_batch.start','ui.simulation_batch.qualify'].includes(operation_id)?{expected_version:version}:{});
      } catch (error) {
        // A normal background refresh can race this test's read. Retry only the
        // server's explicit pre-delivery rejection, never an uncertain effect.
        if(attempt<3 && error.code==='VERSION_CONFLICT' && /before command delivery/.test(error.message))
          return remote(operation_id,parameters,attempt+1);
        throw error;
      }
    };
    await remote('ui.simulation_batch.open');
    await remote('ui.simulation_batch.set_draft',{spec:{source:'testbenches/pre.cir',analysis:{type:'op'},
      supply:{parameter:'VDD',values:[1.5,2]},measurements:[{kind:'power',name:'power_w',supply_node:'vdd',supply_source:'VDD'}]}});
    await remote('ui.simulation_batch.preview');
    await remote('ui.simulation_batch.start');
    await page.waitForFunction(()=>AFOperations.snapshot().simulation_batch.status==='succeeded',null,{timeout:30000});
    const opState=await page.evaluate(()=>AFOperations.snapshot().simulation_batch);
    const opJob=(await invoke('job.get',{project_id:project.id,job_id:opState.job_id})).data;
    assert.deepEqual(opJob.iterations.map(item=>item.metrics.power_w),[.00225,.004]);
    await dispatch('ui.simulation_batch.close');
    // Choosing experimental semantics is explicit and identical for human and agent drafts.
    await remote('ui.simulation_batch.open');
    await dialog.locator('[data-batch-field="statistics.kind"]').selectOption('global_process');
    const semantics=dialog.locator('[data-batch-field="statistics.model_semantics"]');
    assert.equal(await semantics.count(),1,'Global model semantics require an explicit user control');
    assert.equal(await semantics.inputValue(),'installed');
    await semantics.selectOption('cache_global_fet_draws');
    assert.equal((await page.evaluate(()=>AFOperations.snapshot().simulation_batch.spec)).statistics.model_semantics,'cache_global_fet_draws');
    await remote('ui.simulation_batch.set_draft',{spec:{source:'testbenches/pre.cir',analysis:{type:'op'},
      corners:{library:'/missing-pdk-for-explicit-failure/sky130.lib.spice',sections:['mc']},
      statistics:{kind:'global_process',model_semantics:'cache_global_fet_draws',samples:2,seed:41}}});
    assert.equal(await semantics.inputValue(),'cache_global_fet_draws');
    let qualificationRequest;
    page.on('request',request=>{
      if(request.method()==='POST'&&request.url().endsWith('/api/operations/invoke')){
        const body=request.postDataJSON();if(body.operation_id==='simulation.statistics.qualify')qualificationRequest=body.parameters;
      }
    });
    const unavailable=await page.evaluate(async()=>{
      try{await AFOperations.dispatch('ui.simulation_batch.qualify');return null;}catch(error){return error.message;}
    });
    assert(unavailable,'Missing model library must remain an explicit failed prerequisite');
    assert.equal(qualificationRequest.model_semantics,'cache_global_fet_draws');
    await dispatch('ui.simulation_batch.close');
    assert.deepEqual(errors,[]);
    console.log('Advanced simulation browser: graphical sweep, actual gain/power, immutable source, stale rejection, logs and acknowledged UI parity passed.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
