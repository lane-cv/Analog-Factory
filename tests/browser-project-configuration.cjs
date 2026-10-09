// Disposable workspace: checks backend-owned effective model declarations in UI.
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const base=process.env.AF_URL||'http://127.0.0.1:8765';
(async()=>{
 const browser=await chromium.launch({channel:'chromium',headless:true,args:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:1500,height:1000}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base);await page.locator('body:not([inert])').waitFor();
  const invoke=(operation_id,parameters={},options={})=>page.evaluate(({operation_id,parameters,options})=>AFOperations.invoke(operation_id,parameters,options),{operation_id,parameters,options});
  const dispatch=(operation_id,parameters={})=>page.evaluate(({operation_id,parameters})=>AFOperations.dispatch(operation_id,parameters),{operation_id,parameters});
  const original=await invoke('settings.get');
  await invoke('settings.update',{settings:{provider:{base_url:'http://127.0.0.1:9/v1',model:'workspace-model',api_key_env:'FIXTURE_ONLY_UNUSED_CREDENTIAL',
   capabilities:{streaming:true,images:true,tools:true,generation_parameters:['temperature']},model_capabilities:{'catalog-model':{images:true}}}}},{expected_version:original.version});
  await page.reload();await page.locator('body:not([inert])').waitFor();
  for(const endpointChanged of [false,true]){
   const project=(await invoke('project.create',{name:'Different '+(endpointChanged?'endpoint':'model')})).data;
   const read=await invoke('project.detail',{project_id:project.id});
   await invoke('project.configure',{project_id:project.id,config:{provider:{model:'different-model',...(endpointChanged?{base_url:'http://127.0.0.1:10/v1'}:{})}}},{expected_version:read.version});
   await dispatch('ui.workspace.select_project',{project_id:project.id});await dispatch('ui.workspace.navigate',{tab:'settings'});
   const form=page.locator('#project-provider-form');
   assert.equal(await form.locator('[name=project_cap_images]').isChecked(),false);
   assert.equal(await form.locator('[name=project_cap_streaming]').isChecked(),false);
   assert.equal(await form.locator('[name=project_cap_temperature]').isChecked(),false);
   if(endpointChanged){
    assert.deepEqual(JSON.parse(await form.locator('[name=project_model_capabilities]').inputValue()),{});
    assert.equal(await form.locator('[name=project_api_key_env]').inputValue(),'');
   }
   await dispatch('ui.settings.project_provider_draft',{values:{project_name:'Unrelated display name'}});
   await dispatch('ui.settings.project_provider_save');
   const saved=(await invoke('project.detail',{project_id:project.id})).data;
   assert.equal(saved.project.config.provider.name,'Unrelated display name');
   assert.equal(saved.configuration.provider.capabilities.images,false);
   assert.equal(saved.configuration.provider.capabilities.streaming,false);
   assert.deepEqual(saved.configuration.provider.capabilities.generation_parameters,[]);
  }
  assert.deepEqual(errors,[]);
  console.log('Project provider effective configuration: model/endpoint inheritance invalidated; unrelated form save cannot restore unsupported capabilities.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
