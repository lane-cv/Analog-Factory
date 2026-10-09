// Opt-in real native acceptance: disposable server under private Xvfb with
// prepared Xschem/Magic/PDK. No protocol fixture or existing desktop is used.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {chromium}=require('playwright');
const base=process.env.AF_URL||'http://127.0.0.1:8922';
if(process.env.AF_NATIVE_REAL!=='1')throw new Error('Set AF_NATIVE_REAL=1 for a disposable actual-tool server.');
const schematic='v {xschem version=3.4.7 file_version=1.2}\nG {}\nK {}\nV {}\nS {.param untouched=17}\nE {}\nC {/opt/eda/share/xschem/xschem_library/devices/res.sym} 100 100 0 0 {name=R1 value=1k}\nC {/opt/eda/share/xschem/xschem_library/devices/res.sym} 300 100 0 0 {name=R2 value=2k}\n';
const layout='magic\ntech sky130A\ntimestamp 0\n<< metal1 >>\nrect 0 0 10 10\n<< end >>\n';
(async()=>{
  const browser=await chromium.launch({channel:'chromium',headless:true,args:['--no-sandbox']});
  let page,pid;
  const report={status:'failed',checks:[]};
  try{
    page=await browser.newPage({viewport:{width:1500,height:1100}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(base);await page.locator('body:not([inert])').waitFor();
    await page.waitForFunction(()=>AFOperations.names().includes('ui.native_canvas.open'));
    const invoke=async(name,parameters={},options={})=>{
      const result=await page.evaluate(async({name,parameters,options})=>{
        try{return {value:await AFOperations.invoke(name,parameters,options)};}
        catch(error){return {error:{message:error.message,code:error.code}};}
      },{name,parameters,options});
      if(result.error)throw Object.assign(new Error(result.error.message),result.error);
      return result.value;
    };
    const dispatch=(name,parameters={})=>page.evaluate(({name,parameters})=>AFOperations.dispatch(name,parameters),{name,parameters});
    const remote=async(name,parameters={},attempt=0)=>{
      let ready;
      for(let count=0;count<100;count++){
        ready=await page.evaluate(async()=>{
          const result=await AFOperations.invoke('ui.sessions');
          const row=result.data.sessions.find(item=>item.client_id===AFOperations.status().client_id);
          return row&&JSON.stringify(row.state)===JSON.stringify(AFOperations.snapshot())?{client_id:row.client_id,ui_version:row.version,version:result.version}:null;
        });
        if(ready)break;await page.waitForTimeout(100);
      }
      assert.ok(ready,'Target browser must publish its current actual state');
      const {version,...target}=ready;
      try{return await invoke(name,{...target,...parameters},['ui.native_canvas.start','ui.native_canvas.save'].includes(name)?{expected_version:version}:{});}
      catch(error){
        // Only retry an explicit rejection before browser command delivery.
        if(attempt<3&&error.code==='VERSION_CONFLICT'&&/before command delivery/.test(error.message))return remote(name,parameters,attempt+1);
        throw error;
      }
    };
    const write=async(path,content)=>invoke('file.write',{project_id:pid,path,content},{expected_version:(await invoke('project.detail',{project_id:pid})).version});
    pid=(await invoke('project.create',{name:'Actual browser native canvas acceptance'})).data.id;
    await invoke('project.configure',{project_id:pid,config:{tools:{xschem:'/opt/eda/bin/xschem',magic:'/opt/eda/bin/magic',ngspice:'/opt/eda/bin/ngspice-sky130'},pdk_root:'/pdk',pdk_variant:'sky130A'}},{expected_version:(await invoke('project.detail',{project_id:pid})).version});
    await write('schematics/native.sch',schematic);await write('layout/native.mag',layout);
    await page.reload();await page.locator('body:not([inert])').waitFor();
    await dispatch('ui.workspace.select_project',{project_id:pid});
    await dispatch('ui.workspace.navigate',{tab:'desktop'});
    await page.locator('#native-canvas-controls button').click();
    const dialog=page.locator('#native-canvas-dialog');await dialog.waitFor();
    await dialog.locator('[data-native-field=source]').fill('schematics/native.sch');
    await dialog.locator('[data-native-field=source]').press('Tab');
    await dialog.locator('[data-native-action=start]').click();
    await page.waitForFunction(()=>AFOperations.snapshot().native_canvas.native?.connected&&!AFOperations.snapshot().native_canvas.busy,null,{timeout:30000});
    const initial=await page.evaluate(()=>AFOperations.snapshot().native_canvas);
    assert.equal(initial.native.tool,'xschem');assert.equal(initial.native.instances.length,2);
    await dialog.locator('[data-native-field=instance_id]').selectOption('R1');
    await dialog.locator('[data-native-action=move]').click();
    await page.waitForFunction(revision=>AFOperations.snapshot().native_canvas.native?.native_revision>revision&&!AFOperations.snapshot().native_canvas.busy,initial.native.native_revision);
    const manual=await page.evaluate(()=>AFOperations.snapshot().native_canvas);
    const r1=state=>state.instances.find(item=>item.id==='R1');
    const step=r1(manual.native).x-r1(initial.native).x;assert.ok(step>0);
    const draft={instance_id:'R1',direction:'right',grid_steps:2,from_instance:'R1',from_pin:'P',
      to_instance:'R2',to_pin:'P',route:'vertical_first'};
    const drafted=(await remote('ui.native_canvas.set_options',draft)).data.data;
    for(const [field,value] of Object.entries(draft)){
      assert.equal(drafted.options[field],value);
      assert.equal(await dialog.locator(`[data-native-field=${field}]`).inputValue(),String(value));
    }
    assert.equal(drafted.native.native_revision,manual.native.native_revision,'Draft acknowledgment must not report a native edit');
    const target={project_id:pid,run_id:initial.run_id,session_id:initial.session_id};
    const unchanged=(await invoke('native.canvas.get',target)).data.state;
    assert.equal(unchanged.native_revision,manual.native.native_revision,'Actual native state is unchanged by remote control drafts');
    assert.equal(r1(unchanged).x,r1(manual.native).x);
    const moved=(await remote('ui.native_canvas.move')).data;
    assert.equal(moved.data.busy,false);
    assert.equal(r1(moved.data.native).x-r1(manual.native).x,2*step,'The explicit mutation consumes the acknowledged two-step draft with identical native grid semantics');
    const direct=(await invoke('native.canvas.move',{...target,expected_native_revision:moved.data.native.native_revision,instance_id:'R1',direction:'right',grid_steps:1})).data;
    await dialog.locator('[data-native-action=refresh]').click();
    await page.waitForFunction(revision=>AFOperations.snapshot().native_canvas.native?.native_revision===revision&&!AFOperations.snapshot().native_canvas.busy,direct.state.native_revision);
    assert.equal(r1(direct.state).x-r1(moved.data.native).x,step);
    assert.equal((await invoke('file.read',{project_id:pid,path:'schematics/native.sch'})).data.content,schematic,'Native edits remain unsaved until explicit save');
    await remote('ui.native_canvas.connect_pins',{from_instance:'R1',from_pin:'P',to_instance:'R2',to_pin:'P',route:'horizontal_first'});
    await remote('ui.native_canvas.refresh');
    const saved=(await remote('ui.native_canvas.save')).data.data;
    const actual=(await invoke('file.read',{project_id:pid,path:'schematics/native.sch'})).data.content;
    assert.match(actual,/N /);assert.match(actual,/\.param untouched=17/);assert.equal(saved.native.modified,true);
    const history=(await invoke('file.history',{project_id:pid,path:'schematics/native.sch'})).data;
    assert.ok((history.revisions||history).length,'Registered native save retains source history');
    report.checks.push({tool:'xschem',run_id:initial.run_id,session_id:initial.session_id,manual_agent_grid_delta:step,remote_draft_preserved_native_revision:true,source_saved:true,history_preserved:true});
    await remote('ui.native_canvas.stop');
    await assert.rejects(invoke('native.canvas.get',target),error=>error.code==='FRONTEND_DISCONNECTED');
    await remote('ui.native_canvas.close');

    await remote('ui.native_canvas.open');
    await remote('ui.native_canvas.set_options',{tool:'magic',source:'layout/native.mag'});
    await remote('ui.native_canvas.start');
    const magic=await page.evaluate(()=>AFOperations.snapshot().native_canvas);
    assert.equal(magic.native.tool,'magic');assert.ok(magic.native.layers.includes('metal2'));
    await dialog.locator('[data-native-field=layer]').selectOption('metal2');
    await dialog.locator('[data-native-action=paint_box]').click();
    await page.waitForFunction(()=>AFOperations.snapshot().native_canvas.native?.modified&&!AFOperations.snapshot().native_canvas.busy);
    await remote('ui.native_canvas.refresh');
    await remote('ui.native_canvas.save');
    const geometry=(await invoke('file.read',{project_id:pid,path:'layout/native.mag'})).data.content;
    assert.match(geometry,/<< metal2 >>/);assert.match(geometry,/rect 0 0 10 10/);
    report.checks.push({tool:'magic',run_id:magic.run_id,session_id:magic.session_id,box:magic.native.box,paint_saved:true,original_geometry_preserved:true});
    await remote('ui.native_canvas.stop');await remote('ui.native_canvas.close');
    assert.deepEqual(errors,[]);report.status='passed';
    console.log('Actual native browser: manual/agent/direct-registry geometry parity, named pin connection, explicit history save, Magic box paint, disconnected targets passed.');
  }finally{
    if(page&&pid)await page.evaluate(async pid=>{for(const run of (await AFOperations.invoke('run.list',{project_id:pid})).data)if(['running','queued'].includes(run.status))await AFOperations.invoke('run.cancel',{project_id:pid,run_id:run.id});},pid).catch(()=>{});
    await browser.close();if(process.env.AF_NATIVE_REPORT)fs.writeFileSync(process.env.AF_NATIVE_REPORT,JSON.stringify(report,null,2)+'\n');
  }
})().catch(error=>{console.error(error);process.exit(1);});
