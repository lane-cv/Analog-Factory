// Full workspace v2 editor to real ngspice execution, using an isolated server.
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const baseURL = process.env.AF_URL || 'http://127.0.0.1:8883';
(async () => {
  const browser = await chromium.launch({channel:'chromium',headless:true,args:['--no-sandbox']});
  try {
    const context = await browser.newContext({viewport:{width:1500,height:1000}}), page = await context.newPage();
    const errors = []; page.on('pageerror',error => errors.push(error.message));
    await page.goto(baseURL); await page.locator('body:not([inert])').waitFor();
    await page.waitForFunction(() => window.AFOperations?.status().client_id);
    const client_id = await page.evaluate(() => AFOperations.status().client_id);
    const call = async (operation_id, parameters={}) => {
      const result = await (await context.request.post(`${baseURL}/api/operations/invoke`,{data:{operation_id,parameters:{client_id,...parameters}}})).json();
      assert.equal(result.ok,true,JSON.stringify(result.error)); return result;
    };
    const project = await (await context.request.post(`${baseURL}/api/projects`,{data:{name:'General graph UI',template:'amplifier'}})).json();
    await call('ui.workspace.select_project',{project_id:project.id});
    await call('ui.workspace.navigate',{tab:'workflows'});
    await page.locator('[data-action="new-graph"]').click({timeout:4000});
    const graph = (await call('ui.workflow.read')).data.data;
    assert.equal(graph.schema_version,2);
    await call('ui.workflow.select_node',{node_id:'measure'});
    await call('ui.workflow.update_node',{node_id:'measure',retries:1,timeout_seconds:5,
      outputs:{bias:{path:'value',type:'number'}}});
    assert.equal(await page.locator('#node-retries').inputValue(),'1');
    await page.locator('#node-inputs').fill(JSON.stringify({result:{node:'simulate',port:'result'}}));
    await page.locator('[data-action="save-node"]').click();
    await call('ui.workflow.add_node',{type:'condition'});
    const condition=(await call('ui.workflow.read')).data.data.nodes.at(-1).id;
    await call('ui.workflow.update_node',{node_id:condition,config:{operator:'gt'},
      inputs:{left:{node:'measure',port:'value'},right:{value:0}}});
    await call('ui.workflow.add_node',{type:'report'});
    const skipped=(await call('ui.workflow.read')).data.data.nodes.at(-1).id;
    await call('ui.workflow.connect',{source:condition,target:skipped,when:false,on:'success'});
    const mapped=(await call('ui.workflow.read')).data.data;
    assert.equal(mapped.edges.at(-1).when,false);
    assert.equal(mapped.nodes.find(n=>n.id==='measure').retries,1);
    await page.locator('[data-action="save-workflow"]').click();
    await page.waitForFunction(() => document.querySelector('#toast-container')?.textContent.includes('saved'));
    await page.locator('[data-action="run-workflow"]').click();
    await page.locator('[data-graph-job]').waitFor();
    const jobId = await page.locator('[data-graph-job]').getAttribute('data-graph-job');
    let job;
    for (let i=0;i<120;i++) {
      job = await (await context.request.get(`${baseURL}/api/projects/${project.id}/jobs/${jobId}`)).json();
      if (!['queued','running'].includes(job.status)) break;
      await new Promise(resolve => setTimeout(resolve,100));
    }
    assert.equal(job.status,'succeeded',JSON.stringify(job));
    assert.ok(job.nodes.simulate.outputs.run_id);
    assert.equal(job.nodes.measure.status,'succeeded');
    assert.ok(Number.isFinite(job.nodes.measure.outputs.value));
    assert.equal(job.nodes.measure.outputs.bias,job.nodes.measure.outputs.value);
    assert.equal(job.nodes[skipped].status,'skipped','The false branch never executes');
    await page.waitForFunction(() => document.querySelector('[data-graph-job]')?.textContent.includes('Historical execution'));
    assert.equal(await page.locator('[data-graph-action]').count(),0);
    const loaded = (await context.request.get(`${baseURL}/api/projects/${project.id}/workflows`));
    const workflows = await loaded.json();
    assert.ok(workflows.some(entry => entry.workflow?.schema_version===2 || entry.schema_version===2));
    // The original six-node tuning editor remains available via the same command.
    await call('ui.workflow.new_graph',{schema_version:1});
    assert.equal((await call('ui.workflow.read')).data.data.schema_version,1);
    assert.equal(await page.locator('#tuning-parameter').inputValue(),'RD');
    assert.deepEqual(errors,[]);
    console.log('Workflow editor passed: v2 construction/save/real ngspice/node evidence/history and v1 compatibility.');
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode=1;});
