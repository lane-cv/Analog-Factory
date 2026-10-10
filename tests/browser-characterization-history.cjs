'use strict';
// Actual browser/registry history behavior on explicitly seeded measurement records.
// This does not establish new SKY130 measurements or invoke inference/simulation.
const assert=require('node:assert/strict'),path=require('node:path'),readline=require('node:readline');
const {spawn}=require('node:child_process'),{once}=require('node:events'),{chromium}=require('playwright');
const harness=String.raw`
import json,os,sys,tempfile,threading,time
from pathlib import Path
from analog_factory.service import Workspace
from analog_factory.server import create_server
from analog_factory.storage import atomic_json
with tempfile.TemporaryDirectory(prefix='af-characterization-history-') as temporary:
    workspace=Workspace(Path(temporary)/'workspace')
    first=workspace.projects.create('Seeded characterization history fixture')['id']
    second=workspace.projects.create('Active characterization history fixture')['id']
    spec={'views':['pre'],'analyses':['ac','op'],'cases':[{'corner':'tt','supply_v':1.8,'temperature_c':27}],'targets':{'gain_db_min':30,'power_w_max':0.00001},'limits':{'runtime_seconds':30,'run_timeout':10}}
    root=workspace.projects.path(first)
    for job_id,created,version,groups in [
      ('a'*32,'2026-01-01T00:00:00Z','old-source',[]),
      ('b'*32,'2026-01-02T00:00:00Z',workspace.operations.version(first),[{'view':'pre','conditions':spec['cases'][0],'metrics':{'gain_db':42},'assessment':{'status':'unknown','checks':{'gain_db_min':{'value':42,'limit':30,'status':'pass'},'power_w_max':{'value':None,'limit':0.00001,'status':'unknown'}}},'run_ids':[]}])]:
        atomic_json(workspace.jobs._path(root,job_id),{'id':job_id,'kind':'ota-characterization','status':'succeeded','created_at':created,'ended_at':created,'spec':spec,'source_version':version,'groups':groups,'iterations':[],'electrical_status':'unknown','completed_runs':1,'scenario_count':2})
    def waiting(job,cancel,update):
        while not cancel.is_set():time.sleep(.02)
        return {'status':'cancelled','electrical_status':'unknown'}
    active=workspace.jobs.start(workspace.projects.path(second),'ota-characterization',waiting,{'spec':spec,'iterations':[],'source_version':workspace.operations.version(second),'scenario_count':2})
    server=create_server(workspace,port=0,desktop_url='');threading.Thread(target=server.serve_forever,daemon=True).start()
    print(json.dumps({'url':f'http://127.0.0.1:{server.server_port}','first':first,'second':second,'active':active['id']}),flush=True)
    try:sys.stdin.readline()
    finally:
        workspace.jobs.cancel(workspace.projects.path(second),active['id'])
        workspace.agents.close();workspace.operations.close();server.shutdown();server.server_close()
`;
(async()=>{
 const child=spawn(process.env.PYTHON||'python3',['-u','-c',harness],{cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});let stderr='',browser;
 child.stderr.on('data',data=>stderr+=data);const lines=readline.createInterface({input:child.stdout});
 try{
  const local=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error(stderr||'server timeout')),15000);lines.once('line',line=>{clearTimeout(timer);resolve(JSON.parse(line));});child.once('exit',code=>{clearTimeout(timer);reject(Error(`server exited ${code}: ${stderr}`));});});
  browser=await chromium.launch({channel:'chromium',headless:true,args:['--no-sandbox']});const page=await browser.newPage({viewport:{width:1500,height:1100}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));await page.goto(local.url);await page.locator('body:not([inert])').waitFor();
  const dispatch=(name,parameters={})=>page.evaluate(({name,parameters})=>AFOperations.dispatch(name,parameters),{name,parameters});
  const snapshot=()=>page.evaluate(()=>AFOperations.snapshot().characterization);
  const countJobs=()=>page.evaluate(async pid=>(await AFOperations.invoke('job.list',{project_id:pid})).data.length,local.first);
  await dispatch('ui.workspace.select_project',{project_id:local.first});await dispatch('ui.workspace.navigate',{tab:'simulation'});
  await page.locator('#characterization-controls button').click();const dialog=page.locator('#characterization-dialog');await dialog.waitFor();
  assert.equal((await snapshot()).job_id,'b'.repeat(32));assert.equal(await countJobs(),2);
  const gain=dialog.locator('[data-characterize-metric="gain_db"]');assert.equal(await gain.getAttribute('data-characterize-result'),'pass');
  assert.match(await gain.innerText(),/42/);assert.match(await gain.innerText(),/12/);
  assert.equal(await dialog.locator('[data-characterize-metric="power_w"]').getAttribute('data-characterize-result'),'unknown');
  const gainSummary=dialog.locator('[data-characterize-summary="gain_db_min"]');
  assert.equal(await gainSummary.getAttribute('data-characterize-summary-result'),'pass');
  assert.match(await gainSummary.innerText(),/12/);assert.match(await gainSummary.innerText(),/pre · tt · 1\.8 V · 27 °C/);
  assert.match(await gainSummary.innerText(),/1 \/ 1/,'Measured gain covers the one requested condition');
  const powerSummary=dialog.locator('[data-characterize-summary="power_w_max"]');
  assert.equal(await powerSummary.getAttribute('data-characterize-summary-result'),'unknown');
  assert.match(await powerSummary.innerText(),/0 \/ 1/,'A missing power measurement is excluded from measured coverage');
  assert.equal(await dialog.locator('[data-characterize-electrical-status]').getAttribute('data-characterize-electrical-status'),'unknown');
  assert.equal(await dialog.locator('[data-characterize-currentness]').getAttribute('data-characterize-currentness'),'current');
  await dialog.locator('[data-characterize-target="gain_db_min"]').fill('37');await dialog.locator('[data-characterize-target="gain_db_min"]').press('Tab');
  await dialog.locator('[data-characterize-history]').selectOption('a'.repeat(32));await page.waitForFunction(()=>AFOperations.snapshot().characterization.job_id==='a'.repeat(32));
  assert.equal((await snapshot()).spec.targets.gain_db_min,37);assert.equal(await dialog.locator('[data-characterize-currentness]').getAttribute('data-characterize-currentness'),'stale');
  assert.equal(await gainSummary.getAttribute('data-characterize-summary-result'),'unknown');
  assert.match(await gainSummary.innerText(),/0 \/ 1/,'Historical records with missing groups cannot imply complete coverage');
  // Acknowledged remote open uses the same selection path as the manual dropdown.
  let ready;for(let i=0;i<100;i++){ready=await page.evaluate(async()=>{const read=await AFOperations.invoke('ui.sessions');const s=read.data.sessions.find(s=>s.client_id===AFOperations.status().client_id);return s&&JSON.stringify(s.state)===JSON.stringify(AFOperations.snapshot())?{client_id:s.client_id,ui_version:s.version,version:read.version}:null;});if(ready)break;await page.waitForTimeout(40);}assert(ready);
  await page.evaluate(async({ready})=>AFOperations.invoke('ui.characterization.open',{job_id:'b'.repeat(32),client_id:ready.client_id,ui_version:ready.ui_version},{expected_version:ready.version}),{ready});
  assert.equal((await snapshot()).job_id,'b'.repeat(32));assert.equal((await snapshot()).spec.targets.gain_db_min,37);
  await dialog.locator('[data-characterize-action="new_draft"]').click();await page.waitForFunction(()=>AFOperations.snapshot().characterization.job_id===null);
  assert.equal((await snapshot()).spec.targets.gain_db_min,37);assert.equal(await countJobs(),2,'new draft and history selection do not start computation');
  await dispatch('ui.characterization.close');await dispatch('ui.workspace.select_project',{project_id:local.second});await dispatch('ui.characterization.open');
  assert.equal((await snapshot()).job_id,local.active);assert.equal(await dialog.locator('[data-characterize-action="cancel"]').isEnabled(),true);
  await dispatch('ui.characterization.cancel');await page.waitForFunction(()=>AFOperations.snapshot().characterization.status==='cancelled');
  await dispatch('ui.characterization.close');await dispatch('ui.workspace.select_project',{project_id:local.first});await dispatch('ui.characterization.open');
  assert.equal((await snapshot()).spec.targets.gain_db_min,37,'switching projects preserves each project draft');assert.deepEqual(errors,[]);
  console.log('PASS characterization history: real browser/manual and remote parity, measured margins/unknown, no compute, draft restoration and active cancellation');
 }finally{if(browser)await browser.close();child.stdin.end('\n');await once(child,'exit');lines.close();if(stderr)process.stderr.write(stderr);}
})().catch(error=>{console.error(error);process.exitCode=1;});
