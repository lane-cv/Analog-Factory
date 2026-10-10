'use strict';
// Real registry version rejections; no native process is launched or mocked.
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{
  const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  try{
    const page=await browser.newPage(),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(process.env.AF_URL||'http://127.0.0.1:8765');
    await page.locator('body:not([inert])').waitFor();
    const invoke=(name,parameters={},options={})=>page.evaluate(({name,parameters,options})=>AFOperations.invoke(name,parameters,options),{name,parameters,options});
    const dispatch=(name,parameters={})=>page.evaluate(({name,parameters})=>AFOperations.dispatch(name,parameters),{name,parameters});
    const project_id=(await invoke('project.create',{name:'Native prelaunch recovery'})).data.id;
    await dispatch('ui.workspace.select_project',{project_id});
    await dispatch('ui.native_canvas.open');
    const initial=await page.evaluate(()=>AFOperations.snapshot().native_canvas.source_version);
    await invoke('file.write',{project_id,path:'recovery-note.txt',content:'An external edit changes the project version.'},
      {expected_version:(await invoke('project.detail',{project_id})).version});
    const dialog=page.locator('#native-canvas-dialog');
    await dialog.locator('[data-native-action=start]').click();
    await page.waitForFunction(()=>{
      const state=AFOperations.snapshot().native_canvas;
      return !state.busy&&state.needs_inspection;
    });
    const rejected=await page.evaluate(()=>AFOperations.snapshot().native_canvas);
    assert.equal(rejected.unknown_outcome,false);
    assert.equal(rejected.run_id,null);assert.equal(rejected.session_id,null);
    assert.equal(await dialog.locator('[data-native-action=start]').isDisabled(),true);
    assert.equal(await dialog.locator('[data-native-action=refresh]').isEnabled(),true);
    assert.equal(await dialog.locator('[data-native-action=refresh]').textContent(),'Read latest project state');
    await dialog.locator('[data-native-action=refresh]').click();
    await page.waitForFunction(()=>!AFOperations.snapshot().native_canvas.busy&&!AFOperations.snapshot().native_canvas.needs_inspection);
    assert.equal(await dialog.locator('[data-native-action=start]').isEnabled(),true);
    assert.notEqual(await page.evaluate(()=>AFOperations.snapshot().native_canvas.source_version),initial);
    assert.deepEqual((await invoke('run.list',{project_id})).data,[],'Reading latest state must not retry a launch');
    // Repeat with the same semantic controller, recover through a remote acknowledged open.
    await invoke('file.write',{project_id,path:'recovery-note.txt',content:'A second external edit.'},
      {expected_version:(await invoke('project.detail',{project_id})).version});
    await assert.rejects(dispatch('ui.native_canvas.start'));
    await dispatch('ui.native_canvas.close');
    for(let attempt=0;attempt<4;attempt++){
      let target;
      for(let i=0;i<100;i++){
        target=await page.evaluate(async()=>{
          const read=await AFOperations.invoke('ui.sessions');
          const row=read.data.sessions.find(item=>item.client_id===AFOperations.status().client_id);
          return row&&JSON.stringify(row.state)===JSON.stringify(AFOperations.snapshot())?
            {client_id:row.client_id,ui_version:row.version}:null;
        });
        if(target)break;await page.waitForTimeout(50);
      }
      assert(target,'Native recovery state was acknowledged');
      try{await invoke('ui.native_canvas.open',target);break;}
      catch(error){
        // This read may be retried only when the registry proves no command was delivered.
        if(attempt===3||!/Frontend state version conflict before command delivery/.test(error.message))throw error;
      }
    }
    const recovered=await page.evaluate(()=>AFOperations.snapshot().native_canvas);
    assert.equal(recovered.open,true);assert.equal(recovered.needs_inspection,false);
    assert.equal(recovered.unknown_outcome,false);
    assert.deepEqual((await invoke('run.list',{project_id})).data,[]);
    assert.deepEqual(errors,[]);
    console.log('PASS native recovery: actual prelaunch rejection, manual reread, acknowledged remote reopen, no automatic launch');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
