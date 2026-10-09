// Disposable workspace only; no provider requests or credentials are required.
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const base=process.env.AF_URL||'http://127.0.0.1:8883';
(async()=>{
 const browser=await chromium.launch({channel:'chromium',headless:true,args:['--no-sandbox']});
 try {
  const page=await browser.newPage({viewport:{width:1500,height:1000}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  const submissions=[];page.on('request',request=>{if(request.url().endsWith('/api/operations/invoke')){const body=request.postDataJSON();if(body.operation_id==='project.configure')submissions.push(body);}});
  await page.goto(base);await page.locator('body:not([inert])').waitFor();
  const invoke=(operation_id,parameters={},options={})=>page.evaluate(({operation_id,parameters,options})=>AFOperations.invoke(operation_id,parameters,options),{operation_id,parameters,options});
  const dispatch=(operation_id,parameters={})=>page.evaluate(({operation_id,parameters})=>AFOperations.dispatch(operation_id,parameters),{operation_id,parameters});
  const project=(await invoke('project.create',{name:'Provider controls browser'})).data;
  await dispatch('ui.workspace.select_project',{project_id:project.id});
  await dispatch('ui.workspace.navigate',{tab:'settings'});
  await page.locator('#settings-form [name=retries]').fill('2');
  await page.locator('#settings-form [name=cap_streaming]').check();
  await page.locator('#settings-form [name=stream]').check();
  await page.locator('#settings-form [name=cap_temperature]').check();
  await page.locator('#settings-form [name=generation_temperature]').fill('0.4');
  await page.locator('#settings-form button[type=submit]').click();
  for(let i=0;i<100;i++){
   if((await invoke('settings.get')).data.provider?.generation?.temperature===.4)break;
   if(i===99)throw new Error('Global provider settings were not saved');
   await new Promise(resolve=>setTimeout(resolve,50));
  }
  // Open a fresh form revision after global configuration changes.
  await dispatch('ui.workspace.navigate',{tab:'overview'});await dispatch('ui.workspace.navigate',{tab:'settings'});
  await page.locator('#project-provider-form [name=project_model]').fill('project-model');
  await page.locator('#project-provider-form [name=project_cap_images]').check();
  await page.locator('#project-provider-form [name=project_concurrency]').fill('1');
  await page.locator('#project-provider-form button[type=submit]').click();
  for(let i=0;i<100;i++){
   const current=(await invoke('project.detail',{project_id:project.id})).data.project;
   if(current.config.provider?.model==='project-model'){assert.equal(current.config.provider.capabilities.images,true);assert.equal(current.config.provider.concurrency,1);break;}
   if(i===99)throw new Error('Project override not persisted: '+JSON.stringify({submissions,invalid:await page.locator('#project-provider-form :invalid').evaluateAll(elements=>elements.map(e=>({name:e.name,value:e.value,message:e.validationMessage})))})+' '+await page.locator('#toast-container').innerText()+' '+JSON.stringify(await page.evaluate(()=>AFProviderFields.readAdvanced(new FormData(document.querySelector('#project-provider-form')),'project_'))));
   await new Promise(resolve=>setTimeout(resolve,50));
  }
  await dispatch('ui.settings.project_provider_draft',{values:{project_model:'agent-model'}});
  assert.equal(await page.locator('[name=project_model]').inputValue(),'agent-model');
  await dispatch('ui.settings.project_provider_save');
  assert.equal((await invoke('project.detail',{project_id:project.id})).data.project.config.provider.model,'agent-model');
  await dispatch('ui.settings.project_provider_reset');
  assert.equal((await invoke('project.detail',{project_id:project.id})).data.project.config.provider,undefined);
  // Restore neutral configuration for subsequent browser tests.
  const settingsRead=await invoke('settings.get');
  await invoke('settings.update',{settings:{provider:{stream:false,capabilities:{},generation:{},retries:0,concurrency:4}}},{expected_version:settingsRead.version});
  assert.deepEqual(errors,[]);
  console.log('Provider controls: global capabilities/generation, project overrides, semantic drafts/save/reset passed.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
