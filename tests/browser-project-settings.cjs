// Disposable project. Conflicts must preserve a human's environment draft.
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const base=process.env.AF_URL||'http://127.0.0.1:8883';
(async()=>{
 const browser=await chromium.launch({channel:'chromium',headless:true,args:['--no-sandbox']});
 try {
  const page=await browser.newPage({viewport:{width:1500,height:1000}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base);await page.locator('body:not([inert])').waitFor();
  const invoke=(operation_id,parameters={},options={})=>page.evaluate(({operation_id,parameters,options})=>AFOperations.invoke(operation_id,parameters,options),{operation_id,parameters,options});
  const dispatch=(operation_id,parameters={})=>page.evaluate(({operation_id,parameters})=>AFOperations.dispatch(operation_id,parameters),{operation_id,parameters});
  const project=(await invoke('project.create',{name:'Project environment browser'})).data;
  const initial=await invoke('project.detail',{project_id:project.id});
  await invoke('project.configure',{project_id:project.id,config:{provider:{model:'preserved'},tools:{codex:'preserved-codex'}}},{expected_version:initial.version});
  await dispatch('ui.workspace.select_project',{project_id:project.id});
  await dispatch('ui.workspace.navigate',{tab:'settings'});
  await page.locator('[name=project_tool_ngspice]').fill('/missing/isolated-ngspice');
  await page.locator('[name=project_pdk_version]').fill('project-pin');
  await page.locator('#project-environment-form button[type=submit]').click();
  for(let i=0;i<100;i++){
   if((await invoke('project.detail',{project_id:project.id})).data.project.config.pdk_version==='project-pin')break;
   if(i===99)throw new Error('Manual project environment was not saved');
   await new Promise(resolve=>setTimeout(resolve,50));
  }
  assert.equal((await invoke('workspace.status',{project_id:project.id})).data.operation_availability.simulate.available,false);
  await page.waitForFunction(()=>AFOperations.status().client_id);
  const client_id=await page.evaluate(()=>AFOperations.status().client_id);
  const remote=async(operation_id,parameters={})=>{
   let ready;
   for(let i=0;i<100;i++){
    const sessions=await invoke('ui.sessions'),local=await page.evaluate(()=>AFOperations.snapshot());
    const target=sessions.data.sessions.find(item=>item.client_id===client_id);
    if(target&&JSON.stringify(target.state)===JSON.stringify(local)){ready={version:sessions.version,ui_version:target.version};break;}
    await new Promise(resolve=>setTimeout(resolve,50));
   }
   assert.ok(ready,'Frontend must publish its current state');
   const response=await page.request.post(base+'/api/operations/invoke',{data:{operation_id,parameters:{client_id,ui_version:ready.ui_version,...parameters},expected_version:ready.version}});
   const result=await response.json();assert.equal(result.ok,true,JSON.stringify(result.error));return result.data;
  };
  await remote('ui.settings.project_environment_draft',{values:{project_tool_magic:'/missing/project-magic'}});
  assert.equal(await page.locator('[name=project_tool_magic]').inputValue(),'/missing/project-magic');
  await remote('ui.settings.project_environment_save');
  let config=(await invoke('project.detail',{project_id:project.id})).data.project.config;
  assert.equal(config.tools.magic,'/missing/project-magic');assert.equal(config.provider.model,'preserved');
  await dispatch('ui.settings.project_environment_draft',{values:{project_pdk_version:'human-unsaved'}});
  const beforeEdit=await invoke('project.detail',{project_id:project.id});
  await invoke('file.write',{project_id:project.id,path:'reports/concurrent.txt',content:'External edit'},{expected_version:beforeEdit.version});
  await assert.rejects(dispatch('ui.settings.project_environment_save'),/changed|conflict|revision/i);
  assert.equal(await page.locator('[name=project_pdk_version]').inputValue(),'human-unsaved');
  assert.equal((await invoke('project.detail',{project_id:project.id})).data.project.config.pdk_version,'project-pin');
  // Preference rerender preserves the draft AND its original revision. A fresh
  // transport cache entry must not silently authorize this old form.
  await page.evaluate(async pid=>{
   await AFOperations.request('/api/projects/'+pid);
   await AFOperations.request('/api/settings');
  },project.id);
  const previousTheme=await page.locator('#ui-theme').inputValue();
  await dispatch('ui.workspace.set_preferences',{theme:previousTheme==='dark'?'light':'dark'});
  await assert.rejects(dispatch('ui.settings.project_environment_save'),/changed|conflict|revision/i);
  assert.equal(await page.locator('[name=project_pdk_version]').inputValue(),'human-unsaved');
  await dispatch('ui.workspace.set_preferences',{theme:previousTheme});

  await dispatch('ui.workspace.navigate',{tab:'overview'});await dispatch('ui.workspace.navigate',{tab:'settings'});
  await remote('ui.settings.project_environment_reset');
  config=(await invoke('project.detail',{project_id:project.id})).data.project.config;
  assert.deepEqual(config.tools,{codex:'preserved-codex'});assert.equal(config.provider.model,'preserved');
  assert.equal(config.pdk_version,undefined);
  assert.equal((await invoke('workspace.status',{project_id:project.id})).data.operation_availability.simulate.available,true);
  assert.deepEqual(errors,[]);
  console.log('Project environment: UI/acknowledged agent parity, availability, preserved config, stale draft and reset passed.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
