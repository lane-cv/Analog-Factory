'use strict';
// All values below come from real ngspice runs in the disposable browser workspace.
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
const waitForSuccessfulRun=require('./fixtures/browser-run-wait.cjs');
const base=process.env.AF_URL||'http://127.0.0.1:8765';
(async()=>{
  const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  try{
    const page=await browser.newPage({viewport:{width:1500,height:1050}}),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(base);await page.locator('body:not([inert])').waitFor();
    const invoke=(name,parameters={},options={})=>page.evaluate(({name,parameters,options})=>AFOperations.invoke(name,parameters,options),{name,parameters,options});
    const dispatch=(name,parameters={})=>page.evaluate(({name,parameters})=>AFOperations.dispatch(name,parameters),{name,parameters});
    const created=await invoke('project.create',{name:'Ordinary measurement comparison',template:'amplifier'});
    const project_id=created.data.id;
    await dispatch('ui.workspace.select_project',{project_id});
    const mutate=async(name,parameters)=>invoke(name,{project_id,...parameters},{expected_version:(await invoke('project.detail',{project_id})).version});
    const waitRun=run_id=>waitForSuccessfulRun(invoke,project_id,run_id);
    await page.locator('#run-primary').click();
    await page.waitForFunction(()=>AFOperations.snapshot().runs.selected);
    const first=await waitRun(await page.evaluate(()=>AFOperations.snapshot().runs.selected));
    const runAgain=async()=>waitRun((await mutate('run.start',{kind:'simulate',options:{netlist:'testbenches/pre.cir'}})).data.id);
    const same=await runAgain();
    const pre=(await invoke('file.read',{project_id,path:'testbenches/pre.cir'})).data.content;
    assert.match(pre,/RD=3300/);
    await mutate('file.write',{path:'testbenches/pre.cir',content:pre.replace('RD=3300','RD=4700')});
    const adjusted=await runAgain();
    const compare=async(a,b)=>page.evaluate(({a,b})=>AFResultComparison.compare(a,b),{a,b});
    const repeated=await compare(first,same),changed=await compare(first,adjusted);
    for(const name of ['vout_mean','vout_pp','vin_pp','supply_current','gain','power']){
      const row=repeated.rows.find(item=>item.name===name);
      assert(row&&row.comparable,`${name}: ${JSON.stringify(row?.reasons)}`);
      assert.equal(row.delta,0,`${name} repeats under the same conditions`);
      const next=changed.rows.find(item=>item.name===name);
      assert(next&&next.comparable,`${name} supports an RD-only design change`);
      assert.equal(next.delta,adjusted.metrics[name]-first.metrics[name]);
      assert(next.unit,'Explicit units are retained');
    }
    assert.notEqual(changed.rows.find(item=>item.name==='vout_mean').delta,0);
    assert.equal(changed.rows.find(item=>item.name==='power').unit,'W');
    await dispatch('ui.workspace.refresh');await dispatch('ui.workspace.navigate',{tab:'simulation'});
    await page.locator('#compare-a').selectOption(first.id);
    await page.locator('#compare-b').selectOption(adjusted.id);
    assert.equal(await page.locator('#comparison-results tr[data-comparable=true]').count(),6);
    const recorded=JSON.parse(await page.locator('.comparison-evidence pre').first().textContent());
    assert.equal(recorded.conditions.temperature_c,27);
    assert.equal(recorded.conditions.supply_v,3.3);
    assert.deepEqual(recorded.declared_conditions,{});
    await page.locator('[data-metric=vout_mean]').click();
    const metricEvidence=JSON.parse(await page.locator('#metric-evidence pre').textContent());
    assert.equal(metricEvidence.conditions.temperature_c,27);
    assert.equal(metricEvidence.definition.units,'V');
    // Acknowledged remote comparison takes the same path as the manual selectors.
    let ready;
    for(let i=0;i<100;i++){
      ready=await page.evaluate(async()=>{
        const read=await AFOperations.invoke('ui.sessions');
        const session=read.data.sessions.find(item=>item.client_id===AFOperations.status().client_id);
        return session&&JSON.stringify(session.state)===JSON.stringify(AFOperations.snapshot())?
          {client_id:session.client_id,ui_version:session.version,version:read.version}:null;
      });
      if(ready)break;await page.waitForTimeout(50);
    }
    assert(ready,'Comparison state was acknowledged');
    await invoke('ui.runs.compare',{baseline:first.id,comparison:same.id,client_id:ready.client_id,ui_version:ready.ui_version},{expected_version:ready.version});
    assert.equal(await page.locator('#compare-b').inputValue(),same.id);
    assert.equal(await page.locator('#comparison-results tr[data-comparable=true]').count(),6);
    const circuit=(await invoke('file.read',{project_id,path:'netlists/amplifier.cir'})).data.content;
    assert.match(circuit,/VDD vdd 0 3\.3/);
    await mutate('file.write',{path:'netlists/amplifier.cir',content:circuit.replace('VDD vdd 0 3.3','VDD vdd 0 3.0')});
    const changedSupply=await runAgain();
    const mismatch=await compare(adjusted,changedSupply);
    assert(mismatch.rows.every(row=>!row.comparable&&row.delta===null),'Changing the supply must block all comparisons');
    assert(mismatch.differences.length,'The mismatch is explained');
    await dispatch('ui.workspace.refresh');await dispatch('ui.workspace.navigate',{tab:'simulation'});
    await dispatch('ui.runs.compare',{baseline:adjusted.id,comparison:changedSupply.id});
    assert.equal(await page.locator('#comparison-results tr[data-comparable=true]').count(),0);
    const evidence=process.env.AF_BROWSER_ARTIFACTS;
    if(evidence){fs.mkdirSync(evidence,{recursive:true});await page.locator('#comparison-results').screenshot({path:path.join(evidence,'ordinary-comparison-conditions.png')});}
    assert.deepEqual(errors,[]);
    console.log('PASS ordinary comparison: actual ngspice repeatability, six units and deltas, RD changes, changed-supply rejection, manual/remote parity');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
