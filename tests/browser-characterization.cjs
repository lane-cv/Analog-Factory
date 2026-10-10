// Actual browser/registry/ngspice on a labelled linear fixture, not SKY130 proof.
const assert = require('node:assert/strict');
const path = require('node:path');
const readline = require('node:readline');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {chromium} = require('playwright');
const harness = String.raw`
import json,shlex,shutil,sys,tempfile,threading
from pathlib import Path
from analog_factory.service import Workspace
from analog_factory.server import create_server
with tempfile.TemporaryDirectory(prefix='af-characterization-browser-') as temporary:
    base=Path(temporary);library=base/'pdk/sky130A/libs.tech/combined/sky130.lib.spice'
    library.parent.mkdir(parents=True);library.write_text('.lib tt\n.param TEST_ONLY=1\n.endl tt\n')
    workspace=Workspace(base/'workspace')
    workspace.save_settings({'pdk_root':str(base/'pdk'),'pdk_variant':'sky130A'})
    project=workspace.projects.create('Linear browser fixture, not SKY130 acceptance');pid=project['id']
    workspace.projects.write(pid,'netlists/schematic.spice','.subckt ota5 inp inn bias vdd vss out\nVCM mid 0 .9\nEAMP amp mid inp inn 100\nRAMP amp out 100k\nRPOWER vdd 0 1Meg\n.ends ota5\n')
    workspace.projects.write(pid,'testbenches/pre.cir',f'Linear fixture\n.lib "{library}" tt\n.include "netlists/schematic.spice"\nXOTA inp inn bias vdd 0 out ota5\nVDD vdd 0 1.8\nVP inp 0 DC .9 AC .5\nVN inn 0 DC .9 AC .5 180\nVB bias 0 .65\nCL out 0 1p\n.end\n')
    simulator=shutil.which('ngspice')
    if not simulator: raise RuntimeError('Actual ngspice is required for browser characterization')
    slow=base/'slow-ngspice';slow.write_text('#!/bin/sh\ncase "$1" in -v|--version) ;; *) sleep 20 ;; esac\nexec '+shlex.quote(simulator)+' "$@"\n');slow.chmod(0o755)
    server=create_server(workspace,port=0,desktop_url='');threading.Thread(target=server.serve_forever,daemon=True).start()
    print(json.dumps({'url':f'http://127.0.0.1:{server.server_port}','project':pid,'slow':str(slow)}),flush=True)
    try:sys.stdin.readline()
    finally:
        workspace.agents.close();workspace.operations.close();server.shutdown();server.server_close()
`;
(async()=>{
  const child=spawn('python3',['-u','-c',harness],{cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
  let stderr='',browser;child.stderr.on('data',data=>stderr+=data);
  const lines=readline.createInterface({input:child.stdout});
  try {
    const local=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(Error(stderr||'server timeout')),15000);
      lines.once('line',line=>{clearTimeout(timer);resolve(JSON.parse(line));});
      child.once('exit',code=>{clearTimeout(timer);reject(Error(`server exited ${code}: ${stderr}`));});
    });
    browser=await chromium.launch({channel:'chromium',headless:true,args:['--no-sandbox']});
    const page=await browser.newPage({viewport:{width:1500,height:1100}}),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(local.url);await page.locator('body:not([inert])').waitFor();
    const invoke=async(name,parameters={},options={})=>{
      const result=await page.evaluate(async({name,parameters,options})=>{
        try{return {value:await AFOperations.invoke(name,parameters,options)};}
        catch(error){return {error:{code:error.code,message:error.message}};}
      },{name,parameters,options});
      if(result.error)throw Object.assign(new Error(result.error.message),result.error);
      return result.value;
    };
    const dispatch=(name,parameters={})=>page.evaluate(({name,parameters})=>AFOperations.dispatch(name,parameters),{name,parameters});
    const mutate=async(name,parameters)=>invoke(name,parameters,{expected_version:(await invoke('project.detail',{project_id:local.project})).version});
    await dispatch('ui.workspace.select_project',{project_id:local.project});
    await dispatch('ui.workspace.navigate',{tab:'simulation'});
    await page.locator('#characterization-controls button').click();
    const dialog=page.locator('#characterization-dialog');await dialog.waitFor();
    const spec={views:['pre'],analyses:['ac','op'],cases:[{corner:'tt',supply_v:1.8,temperature_c:27}],targets:{gain_db_min:30},limits:{runtime_seconds:30,run_timeout:10}};
    await dispatch('ui.characterization.set_draft',{spec});
    await mutate('file.write',{project_id:local.project,path:'reports/concurrent.txt',content:'New source revision'});
    const stale=await page.evaluate(async()=>{try{await AFOperations.dispatch('ui.characterization.start');return null;}catch(error){return error.code;}});
    assert.equal(stale,'VERSION_CONFLICT','An opened characterization draft must retain its original source revision');
    await dispatch('ui.characterization.close');await dispatch('ui.characterization.open');
    const reopenedStale=await page.evaluate(async()=>{try{await AFOperations.dispatch('ui.characterization.start');return null;}catch(error){return error.code;}});
    assert.equal(reopenedStale,'VERSION_CONFLICT','Reopening an edited draft must not silently accept changed source files');
    await dispatch('ui.characterization.open',{new_job:true});
    await dispatch('ui.characterization.set_draft',{spec});
    const gain=dialog.locator('[data-characterize-target="gain_db_min"]');
    await gain.fill('31');await gain.press('Tab');
    assert.equal(await page.evaluate(()=>AFOperations.snapshot().characterization.spec.targets.gain_db_min),31);
    const remote=async(name,parameters={},attempt=0)=>{
      let ready;
      for(let i=0;i<100;i++){
        ready=await page.evaluate(async()=>{
          const read=await AFOperations.invoke('ui.sessions');
          const session=read.data.sessions.find(s=>s.client_id===AFOperations.status().client_id);
          return session&&JSON.stringify(session.state)===JSON.stringify(AFOperations.snapshot())?{client_id:session.client_id,ui_version:session.version,version:read.version}:null;
        });
        if(ready)break;await page.waitForTimeout(50);
      }
      assert(ready,'Browser snapshot must be acknowledged');
      try {
        return await invoke(name,{...parameters,client_id:ready.client_id,ui_version:ready.ui_version},{expected_version:ready.version});
      } catch(error) {
        // Retry only an explicit rejection before delivery; never replay an
        // executed command or a command whose outcome is unknown.
        if(attempt<3 && error.code==='VERSION_CONFLICT' && /before command delivery/.test(error.message))
          return remote(name,parameters,attempt+1);
        throw error;
      }
    };
    const changed=await remote('ui.characterization.set_draft',{spec});
    assert.equal(changed.data.state.characterization.spec.targets.gain_db_min,30);
    assert.equal(await gain.inputValue(),'30');
    const started=await remote('ui.characterization.start');
    assert(started.data.data.id);
    await page.waitForFunction(()=>AFOperations.snapshot().characterization.status==='succeeded',null,{timeout:30000});
    const snapshot=await page.evaluate(()=>AFOperations.snapshot().characterization);
    const job=(await invoke('job.get',{project_id:local.project,job_id:snapshot.job_id})).data;
    assert.equal(job.electrical_status,'pass');assert.equal(job.completed_runs,2);
    assert(Math.abs(job.groups[0].metrics.gain_db-40)<.01);
    await remote('ui.characterization.inspect',{run_id:job.iterations[0].run_id});
    assert.match(await dialog.innerText(),/Raw evidence and provenance/);
    await remote('ui.characterization.close');

    // A separate delayed process fixture proves cancellation even after a human draft edit.
    await mutate('project.configure',{project_id:local.project,config:{tools:{ngspice:local.slow}}});
    await dispatch('ui.editor.open',{path:'testbenches/pre.cir'});
    await dispatch('ui.characterization.open',{new_job:true});await dispatch('ui.characterization.set_draft',{spec});
    await dispatch('ui.characterization.start');
    await page.waitForFunction(()=>Boolean(AFOperations.snapshot().characterization.active_run),null,{timeout:10000});
    await dispatch('ui.editor.set_content',{content:'unsaved human draft'});
    await remote('ui.characterization.cancel');
    await page.waitForFunction(()=>AFOperations.snapshot().characterization.status==='cancelled',null,{timeout:10000});
    assert.equal((await dispatch('ui.editor.read')).content,'unsaved human draft');
    assert.deepEqual(errors,[]);
    console.log('PASS: characterization actual simulator/UI/agent parity, pinned revision, evidence and dirty-draft cancellation');
  }finally{
    if(browser)await browser.close();child.stdin.end('\n');await once(child,'exit');lines.close();
    if(stderr)process.stderr.write(stderr);
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
