// Real registry/job handshake, isolated workspace, no external inference.
const assert = require('node:assert/strict');
const path = require('node:path');
const readline = require('node:readline');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {chromium} = require('playwright');

const harness = String.raw`
import hashlib,json,sys,tempfile,threading
from pathlib import Path
from analog_factory.service import Workspace
from analog_factory.server import create_server
from analog_factory.operation_graph import start_graph
with tempfile.TemporaryDirectory(prefix='af-graph-browser-') as root:
    workspace=Workspace(Path(root))
    project=workspace.projects.create('Graph browser integration')
    original=workspace.projects.read(project['id'],'testbenches/pre.cir')['content']
    graph={'schema_version':2,'name':'Review','nodes':[
      {'id':'review','type':'source_patch','config':{'path':'testbenches/pre.cir','prompt':'Review the resistor replacement'},'inputs':{'content':{'value':original.replace('RD=3300','RD=2200')},'expected_sha256':{'value':hashlib.sha256(original.encode()).hexdigest()}}},
      {'id':'report','type':'report','inputs':{'approved':{'node':'review','port':'approved'}}}
    ],'edges':[],'limits':{'runtime_seconds':60},'debug':{'step':True}}
    job=start_graph(workspace,project['id'],graph)
    server=create_server(workspace,port=0,desktop_url='')
    threading.Thread(target=server.serve_forever,daemon=True).start()
    print(json.dumps({'url':f'http://127.0.0.1:{server.server_port}','project':project['id'],'job':job['id']}),flush=True)
    try: sys.stdin.readline()
    finally:
        workspace.jobs.cancel(workspace.projects.path(project['id']),job['id'])
        workspace.jobs.wait(workspace.projects.path(project['id']),job['id'],5)
        workspace.agents.close()
        workspace.operations.close()
        server.shutdown()
        server.server_close()
`;

(async () => {
  const child = spawn('python3', ['-u','-c',harness], {cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
  let browser, stderr = '';
  child.stderr.on('data', data => { stderr += data; });
  const lines = readline.createInterface({input:child.stdout});
  try {
    const info = await new Promise((resolve,reject) => {
      const timer = setTimeout(() => reject(new Error(`Graph server did not start: ${stderr}`)),15000);
      lines.once('line', line => {clearTimeout(timer);resolve(JSON.parse(line));});
      child.once('exit', code => {clearTimeout(timer);reject(new Error(`Server exited ${code}: ${stderr}`));});
    });
    browser = await chromium.launch({headless:true,args:['--no-sandbox']});
    const page = await browser.newPage({viewport:{width:1400,height:1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(info.url);
    await page.waitForFunction(() => window.AFOperations && !document.body.inert);
    if (!await page.evaluate(() => Boolean(window.AFGraphControls))) {
      await page.addScriptTag({url:info.url+'/graph-controls.js'});
    }
    await page.evaluate(async info => {
      const root = document.createElement('div');
      root.id = 'graph-integration';
      root.style = 'position:fixed;inset:30px;background:var(--bg,#fff);z-index:9999;overflow:auto;padding:24px';
      document.body.append(root);
      window.refreshGraphFixture = async () => {
        const result = await AFOperations.invoke('job.get',{project_id:info.project,job_id:info.job});
        root.innerHTML = AFGraphControls.jobHTML(result.data);
      };
      AFGraphControls.bind(root,{projectId:info.project,onUpdate:job => {root.innerHTML=AFGraphControls.jobHTML(job);}});
      await refreshGraphFixture();
    },info);
    const getJob = async () => {
      const response = await page.request.post(info.url+'/api/operations/invoke', {data:{operation_id:'job.get',parameters:{project_id:info.project,job_id:info.job}}});
      const result = await response.json();
      assert.equal(result.ok,true,JSON.stringify(result.error));
      return result.data;
    };
    const waitJob = async predicate => {
      for (let attempt=0;attempt<100;attempt++) {
        const job=await getJob();
        if(predicate(job)) return job;
        await new Promise(resolve=>setTimeout(resolve,30));
      }
      throw new Error('Graph state did not arrive');
    };
    const panel = page.locator('#graph-integration');
    assert.equal((await getJob()).nodes.review.status,'pending');
    await panel.getByRole('button',{name:'Step one node'}).focus();
    await page.keyboard.press('Enter');
    await waitJob(job=>job.pending_review);
    await page.evaluate(()=>refreshGraphFixture());
    assert.match(await panel.innerText(),/Review the resistor replacement/);
    assert.match(await panel.innerText(),/-\.param RD=3300/);
    assert.match(await panel.innerText(),/\+\.param RD=2200/);
    await panel.getByRole('button',{name:'Apply reviewed change',exact:true}).click();
    await waitJob(job=>job.nodes.review.status==='succeeded');
    await page.evaluate(()=>refreshGraphFixture());
    await panel.getByRole('button',{name:'Continue',exact:true}).click();
    const final = await waitJob(job=>job.status==='succeeded');
    assert.equal(final.nodes.report.outputs.report.approved,true);
    assert.equal(final.nodes.review.outputs.applied,true);
    assert.equal(final.nodes.review.outputs.receipt.operation_id,'file.write');
    assert.equal(final.source_generations.length,2);
    assert.equal(final.source_generations[1].source_hashes['testbenches/pre.cir'], final.nodes.review.outputs.after_sha256);
    await page.evaluate(()=>refreshGraphFixture());
    assert.equal(await panel.locator('[data-graph-action]').count(),0,'Historical executions have no live control buttons');
    assert.match(await panel.innerText(),/Historical execution/);
    assert.deepEqual(errors,[]);
    console.log('Reviewed source patch browser: keyboard stepping, exact diff, token-bound registry approval, source generation and inactive history passed.');
  } finally {
    if(browser) await browser.close();
    lines.close();
    child.stdin.end('\n');
    if(child.exitCode===null) await Promise.race([once(child,'exit'),new Promise(resolve=>setTimeout(()=>{child.kill('SIGTERM');resolve();},5000))]);
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
