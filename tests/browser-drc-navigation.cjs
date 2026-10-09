// Fixture checks UI acknowledgment/policy. Actual Magic GUI is tested separately
// by AF_DRC_FOCUS_REAL=1 under private Xvfb; this fixture never connects to X11.
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {chromium}=require('playwright');
const base=process.env.AF_URL||'http://127.0.0.1:8765';
(async()=>{
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'af-drc-browser-'));
  const executable=path.join(temp,'magic-fixture');
  fs.writeFileSync(executable,`#!/usr/bin/env python3
import sys,pathlib,re,time
if '--version' in sys.argv:print('Magic browser fixture 1.0');sys.exit(0)
text=pathlib.Path(sys.argv[-1]).read_text()
out=pathlib.Path('../output')
if 'drc find $af_index' not in text:
 (out/'drc.txt').write_text('AF_DRC_COUNT=2\\nAF_DRC_COMPLETE=1\\n');sys.exit(0)
nonce=re.search(r'set af_nonce "([a-f0-9]+)"',text)[1]
index=re.search(r'set af_index ([0-9]+)',text)[1]
time.sleep(.4)
(out/'drc-focus.txt').write_text('AF_DRC_FOCUS_NONCE='+nonce+'\\nAF_DRC_FOCUS_COMPLETE=1\\nAF_DRC_FOCUS_INDEX='+index+'\\nAF_DRC_FOCUS_COUNT=2\\nAF_DRC_FOCUS_BOX=0 0 1 1\\n')
time.sleep(30)
`,{mode:0o700});
  for(const relative of ['xschem/xschemrc','magic/sky130A.magicrc','netgen/sky130A_setup.tcl','ngspice/sky130.lib.spice']){
    const target=path.join(temp,'pdk/sky130A/libs.tech',relative);fs.mkdirSync(path.dirname(target),{recursive:true});fs.writeFileSync(target,'Browser fixture, not a PDK model');
  }
  const browser=await chromium.launch({channel:'chromium',headless:true,args:['--no-sandbox']});
  let page,pid;
  try{
    page=await browser.newPage({viewport:{width:1500,height:1100}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base);await page.locator('body:not([inert])').waitFor();
    await page.waitForFunction(()=>AFOperations.names().includes('ui.drc_navigation.focus'));
    const invoke=(name,params={},options={})=>page.evaluate(({name,params,options})=>AFOperations.invoke(name,params,options),{name,params,options});
    const dispatch=(name,params={})=>page.evaluate(({name,params})=>AFOperations.dispatch(name,params),{name,params});
    pid=(await invoke('project.create',{name:'DRC navigation browser fixture'})).data.id;
    let revision=await invoke('project.detail',{project_id:pid});
    await invoke('project.configure',{project_id:pid,config:{tools:{magic:executable},pdk_root:path.join(temp,'pdk')}},{expected_version:revision.version});
    revision=await invoke('project.detail',{project_id:pid});
    await invoke('file.write',{project_id:pid,path:'layout/amplifier.mag',content:'magic\ntech sky130A\ntimestamp 0\n<< metal1 >>\nrect 0 0 1 1\n<< end >>\n'},{expected_version:revision.version});
    revision=await invoke('project.detail',{project_id:pid});
    const run=(await invoke('run.start',{project_id:pid,kind:'drc'},{expected_version:revision.version})).data;
    await page.waitForFunction(async({pid,rid})=>(await AFOperations.invoke('run.get',{project_id:pid,run_id:rid})).data.status==='succeeded',{pid,rid:run.id});
    await page.reload();await page.locator('body:not([inert])').waitFor();
    await dispatch('ui.workspace.select_project',{project_id:pid});await dispatch('ui.runs.select',{run_id:run.id});
    const before=(await invoke('run.list',{project_id:pid})).data.length;
    const draft=await dispatch('ui.drc_navigation.set_index',{error_index:2});
    assert.equal(draft.error_index,2);assert.equal(draft.focused,false);
    assert.equal(await page.locator('[data-drc-error-index]').inputValue(),'2');
    assert.equal((await invoke('run.list',{project_id:pid})).data.length,before);
    await page.locator('[data-drc-error-index]').fill('1');
    await page.locator('[data-drc-error-index]').press('Tab');
    assert.equal(await page.evaluate(()=>AFOperations.snapshot().drc_navigation.error_index),1);
    const invalid=await page.evaluate(async()=>{try{await AFOperations.dispatch('ui.drc_navigation.set_index',{error_index:3});return '';}catch(e){return e.message;}});
    assert.ok(invalid);assert.equal(await page.locator('[data-drc-error-index]').inputValue(),'1');
    let ready;
    for(let attempt=0;attempt<100;attempt++){
      ready=await page.evaluate(async()=>{
        const sessions=(await AFOperations.invoke('ui.sessions')).data.sessions;
        const session=sessions.find(row=>row.client_id===AFOperations.status().client_id);
        return session&&JSON.stringify(session.state)===JSON.stringify(AFOperations.snapshot())?{client_id:session.client_id,ui_version:session.version}:null;
      });
      if(ready)break;await page.waitForTimeout(100);
    }
    assert.ok(ready,'DRC draft must be published before remote selection');
    const acknowledged=await invoke('ui.drc_navigation.set_index',{...ready,error_index:2});
    assert.equal(acknowledged.data.state.drc_navigation.error_index,2);
    assert.equal((await invoke('run.list',{project_id:pid})).data.length,before);
    await page.locator('[data-drc-focus]').click();
    await page.waitForFunction(()=>AFOperations.snapshot().drc_navigation.focused);
    const focus=await page.evaluate(()=>AFOperations.snapshot().drc_navigation);
    assert.deepEqual(focus.box,[0,0,1,1]);assert.equal(focus.source_run_id,run.id);
    assert.equal(focus.error_index,2);
    assert.match(await page.locator('[data-drc-focus-status]').innerText(),/acknowledged/);
    await page.locator('[data-drc-stop]').click();
    await page.waitForFunction(()=>AFOperations.snapshot().drc_navigation.status==='cancelled');
    await dispatch('ui.runs.select',{run_id:run.id});
    revision=await invoke('project.detail',{project_id:pid});
    await invoke('file.write',{project_id:pid,path:'notes.txt',content:'Concurrent source edit'},{expected_version:revision.version});
    const stale=await page.evaluate(async()=>{try{await AFOperations.dispatch('ui.drc_navigation.focus',{error_index:1});return '';}catch(e){return e.code;}});
    assert.equal(stale,'VERSION_CONFLICT');assert.deepEqual(errors,[]);
    console.log('DRC navigation browser: manual/semantic/acknowledged index draft parity without launch, bounds rejection, native acknowledgment display, managed cancellation, stale source rejection passed.');
  }finally{
    if(page&&pid)await page.evaluate(async pid=>{for(const run of (await AFOperations.invoke('run.list',{project_id:pid})).data)if(['running','queued'].includes(run.status))await AFOperations.invoke('run.cancel',{project_id:pid,run_id:run.id});},pid).catch(()=>{});
    await browser.close();fs.rmSync(temp,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error);process.exit(1);});
