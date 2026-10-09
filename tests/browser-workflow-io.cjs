// Real main-workspace file picker/downloads plus a YAML-imported ngspice graph.
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const readline=require('node:readline');
const {chromium}=require('playwright');
const harness=String.raw`
import json,sys,tempfile,threading
from pathlib import Path
from analog_factory.service import Workspace
from analog_factory.server import create_server
with tempfile.TemporaryDirectory(prefix='af-workflow-io-') as root:
    workspace=Workspace(Path(root));project=workspace.projects.create('Workflow interchange')
    server=create_server(workspace,port=0,desktop_url='');threading.Thread(target=server.serve_forever,daemon=True).start()
    print(json.dumps({'url':f'http://127.0.0.1:{server.server_port}','project':project['id']}),flush=True)
    try:sys.stdin.readline()
    finally:server.shutdown();server.server_close();workspace.operations.close()
`;
const document=`schema_version: 2
name: YAML browser run
nodes:
  - id: simulate
    type: simulate
    config:
      options: {netlist: testbenches/pre.cir}
  - id: report
    type: report
    inputs: {run_id: {node: simulate, port: run_id}}
edges:
  - {source: simulate, target: report, on: success}
`;
(async()=>{
  const child=spawn('python3',['-u','-c',harness],{stdio:['pipe','pipe','pipe']});
  const lines=readline.createInterface({input:child.stdout});
  let stderr='',browser;child.stderr.on('data',value=>stderr+=value);
  try {
    const config=JSON.parse((await once(lines,'line'))[0]);
    browser=await chromium.launch({channel:'chromium',headless:true,args:['--no-sandbox']});
    const context=await browser.newContext({viewport:{width:1500,height:1000}}),page=await context.newPage();
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(config.url);await page.locator('body:not([inert])').waitFor();
    await page.waitForFunction(()=>window.AFOperations?.status().client_id);
    const client_id=await page.evaluate(()=>AFOperations.status().client_id);
    const call=async(operation_id,parameters={})=>{
      const receipt=await(await context.request.post(`${config.url}/api/operations/invoke`,{data:{operation_id,parameters:{client_id,...parameters}}})).json();
      assert.equal(receipt.ok,true,JSON.stringify(receipt));return receipt.data.data;
    };
    await call('ui.workspace.select_project',{project_id:config.project});
    await call('ui.workspace.navigate',{tab:'workflows'});
    assert.match(await page.locator('#workflow-file').getAttribute('accept'),/yaml/);
    await page.locator('#workflow-file').setInputFiles({name:'workflow.yaml',mimeType:'application/yaml',buffer:Buffer.from(document)});
    await page.waitForFunction(()=>document.querySelector('#workflow-name')?.value==='YAML browser run');
    const before=await call('ui.workflow.read');assert.equal(before.schema_version,2);assert.equal(before.edges[0].on,'success');
    for(const format of ['json','yaml']) {
      const pending=page.waitForEvent('download');
      await page.locator(`[data-action="export-workflow${format==='yaml'?'-yaml':''}"]`).click();
      const download=await pending;assert.ok(download.suggestedFilename().endsWith('.'+format));
      const stream=await download.createReadStream();let content='';for await(const part of stream)content+=part;
      const receipt=await(await context.request.post(`${config.url}/api/operations/invoke`,{data:{operation_id:'workflow.parse',parameters:{content,format}}})).json();
      assert.equal(receipt.ok,true,JSON.stringify(receipt));assert.equal(receipt.data.workflow.name,before.name);
    }
    await page.locator('#workflow-file').setInputFiles({name:'unsafe.yaml',mimeType:'application/yaml',buffer:Buffer.from('nodes: &nodes [*nodes]')});
    await page.waitForFunction(()=>document.querySelector('#toast-container')?.textContent.includes('anchors'));
    assert.deepEqual(await call('ui.workflow.read'),before,'Invalid import must preserve the draft');
    await page.locator('[data-action="run-workflow"]').click();await page.locator('[data-graph-job]').waitFor();
    const id=await page.locator('[data-graph-job]').getAttribute('data-graph-job');let job;
    for(let i=0;i<100;i++) {
      job=await(await context.request.get(`${config.url}/api/projects/${config.project}/jobs/${id}`)).json();
      if(!['queued','running'].includes(job.status))break;
      await page.waitForTimeout(100);
    }
    assert.equal(job.status,'succeeded',JSON.stringify(job));assert.ok(job.nodes.simulate.outputs.run_id);
    assert.deepEqual(errors,[]);console.log('PASS: YAML file import, JSON/YAML downloads, invalid draft preservation and actual ngspice run');
  } finally {
    if(browser)await browser.close();child.stdin.end('\n');await once(child,'exit');lines.close();
    if(stderr)process.stderr.write(stderr);
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
