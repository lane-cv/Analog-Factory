'use strict';
// Requires the disposable --agent-cli-fixture server: no public update requests,
// CLI authentication, installation, or inference are performed by this suite.
const assert=require('node:assert/strict'),{chromium}=require('playwright');
const base=process.env.AF_URL || 'http://127.0.0.1:8930';
(async()=>{
 const browser=await chromium.launch({channel:'chromium',headless:true,args:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:1450,height:1000}}),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base);await page.locator('body:not([inert])').waitFor();
  const invoke=(operation_id,parameters={},options={})=>page.evaluate(({operation_id,parameters,options})=>AFOperations.invoke(operation_id,parameters,options),{operation_id,parameters,options});
  const dispatch=(name,parameters={})=>page.evaluate(({name,parameters})=>AFOperations.dispatch(name,parameters),{name,parameters});
  const project=(await invoke('project.create',{name:'Agent CLI browser fixture'})).data;
  const detail=await invoke('project.detail',{project_id:project.id});
  await invoke('project.configure',{project_id:project.id,config:{tools:{magic:'/missing/keep-magic',codex:'/fixture/codex'},pdk_version:'keep-pdk',provider:{model:'keep-provider'}}},{expected_version:detail.version});
  await dispatch('ui.workspace.select_project',{project_id:project.id});await dispatch('ui.workspace.navigate',{tab:'settings'});
  await page.locator('[data-open-agent-cli]').click();const dialog=page.locator('#agent-cli-dialog');
  await page.waitForFunction(()=>AFOperations.snapshot().agent_cli.open && !AFOperations.snapshot().agent_cli.busy);
  const snapshot=()=>page.evaluate(()=>AFOperations.snapshot().agent_cli);
  assert.match(await dialog.innerText(),/Not checked|未检查/);assert.match(await dialog.innerText(),/latest|Latest|最新/);
  assert.equal((await snapshot()).status.project_id,project.id);assert.equal((await snapshot()).status.tools.find(t=>t.backend==='codex').login,'not_checked');
  await dialog.locator('[data-cli-path=claude]').fill('/fixture/claude');
  await page.waitForFunction(()=>AFOperations.snapshot().agent_cli.paths.claude==='/fixture/claude');
  await dialog.locator('[data-cli-action=save_paths]').click();
  await page.waitForFunction(()=>!AFOperations.snapshot().agent_cli.busy);
  let config=(await invoke('project.detail',{project_id:project.id})).data.project.config;
  assert.equal(config.tools.claude,'/fixture/claude');assert.equal(config.tools.magic,'/missing/keep-magic');assert.equal(config.pdk_version,'keep-pdk');assert.equal(config.provider.model,'keep-provider');
  // Another client uses the actual registered checker. Its persisted failure
  // emits a real event; an open panel updates only the status section and keeps
  // the focused path input, its draft and the original configuration guards.
  const eventCursor=(await (await page.request.get(base+'/api/operations/events?after=0&wait=0')).json()).cursor;
  await dispatch('ui.agent_cli.set_tracking_draft',{automatic:true,interval_hours:72});
  await dialog.locator('[data-cli-path=codex]').fill('/pending-before-external');
  const pinned=(await snapshot()).project_version;
  const external=await (await page.request.post(base+'/api/operations/invoke',{data:{operation_id:'agent.cli.check_updates',parameters:{force:true}}})).json();
  assert.equal(external.ok,false);assert.equal(external.error.code,'UNAVAILABLE');
  const notification=await (await page.request.get(base+`/api/operations/events?after=${eventCursor}&wait=0`)).json();
  assert.ok(notification.events.some(event=>event.operation_id==='agent.cli.releases' && event.status==='changed'),'persisted external attempt emits its registered change');
  await page.waitForFunction(()=>AFOperations.snapshot().agent_cli.status.releases.backends.some(row=>row.latest_attempt?.status==='failed'));
  assert.match(await dialog.locator('[data-cli-status]').innerText(),/failed|失败/i);
  assert.equal(await dialog.locator('[data-cli-path=codex]').inputValue(),'/pending-before-external');
  assert.equal(await page.evaluate(()=>document.activeElement?.dataset.cliPath),'codex','status sync preserves active draft input');
  assert.equal((await snapshot()).tracking.interval_hours,72);assert.equal((await snapshot()).project_version,pinned);
  // Registered remote commands must reach this exact mounted controller and
  // return the acknowledged browser state, rather than a fabricated backend ack.
  const remote=async(operation_id,parameters={})=>{
   let ready;for(let i=0;i<100;i++){
    const result=await invoke('ui.sessions'),local=await page.evaluate(()=>AFOperations.snapshot()),client=await page.evaluate(()=>AFOperations.status().client_id);
    const session=result.data.sessions.find(s=>s.client_id===client);
    if(session && JSON.stringify(session.state)===JSON.stringify(local)){ready={client_id:client,ui_version:session.version};break;}await page.waitForTimeout(50);
   }
   assert.ok(ready,'current browser snapshot must be acknowledged');return invoke(operation_id,{...ready,...parameters});
  };
  const ack=await remote('ui.agent_cli.set_paths',{codex:'/fixture/remote-codex'});assert.equal(ack.data.state.agent_cli.paths.codex,'/fixture/remote-codex');
  await remote('ui.agent_cli.save_paths');config=(await invoke('project.detail',{project_id:project.id})).data.project.config;assert.equal(config.tools.codex,'/fixture/remote-codex');
  // Two actors: an external source write invalidates the opened project draft.
  await remote('ui.agent_cli.set_paths',{claude:'/unsaved-human-draft'});const prior=await invoke('project.detail',{project_id:project.id});
  await invoke('file.write',{project_id:project.id,path:'reports/cli-concurrent.txt',content:'other actor'},{expected_version:prior.version});
  await assert.rejects(dispatch('ui.agent_cli.save_paths'),/changed|conflict|revision/i);
  assert.equal(await dialog.locator('[data-cli-path=claude]').inputValue(),'/unsaved-human-draft');
  await dispatch('ui.agent_cli.refresh');await assert.rejects(dispatch('ui.agent_cli.save_paths'),/changed|conflict|revision/i);
  assert.equal((await invoke('project.detail',{project_id:project.id})).data.project.config.tools.claude,'/fixture/claude');
  await remote('ui.agent_cli.close');await remote('ui.agent_cli.open');
  await remote('ui.agent_cli.reset_paths');config=(await invoke('project.detail',{project_id:project.id})).data.project.config;
  assert.deepEqual(config.tools,{magic:'/missing/keep-magic'});assert.equal(config.provider.model,'keep-provider');assert.equal(config.pdk_version,'keep-pdk');
  const unchanged=await invoke('settings.get');await remote('ui.agent_cli.set_tracking_draft',{automatic:true,interval_hours:72});
  assert.equal(await dialog.locator('[data-cli-interval]').inputValue(),'72');assert.equal(await dialog.locator('[data-cli-automatic]').isChecked(),true);
  const afterDraft=await invoke('settings.get');assert.equal(afterDraft.version,unchanged.version);assert.deepEqual(afterDraft.data.cli_updates,unchanged.data.cli_updates,'acknowledged tracking draft has no configuration write');
  await remote('ui.agent_cli.set_tracking_draft',{automatic:false,interval_hours:24});
  await dialog.locator('[data-cli-automatic]').check();await dialog.locator('[data-cli-interval]').fill('48');await dialog.locator('[data-cli-action=tracking]').click();
  await page.waitForFunction(()=>!AFOperations.snapshot().agent_cli.busy && AFOperations.snapshot().agent_cli.status.releases.automatic.enabled);
  assert.equal((await invoke('settings.get')).data.cli_updates.interval_hours,48,'manual automatic/interval controls use the shared writer');
  await remote('ui.agent_cli.set_tracking',{automatic:false,interval_hours:24});
  assert.equal(await dialog.locator('[data-cli-automatic]').isChecked(),false);
  await remote('ui.agent_cli.set_tracking',{automatic:true,interval_hours:48});
  const prefs=(await invoke('settings.get')).data.cli_updates;assert.deepEqual(prefs,{automatic:true,interval_hours:48});assert.equal((await snapshot()).status.releases.automatic.enabled,true);
  // Fixture check records a failed attempt while retaining its previous public
  // successful release. A failed check must still refresh the visible cache.
  await assert.rejects(remote('ui.agent_cli.check_updates',{force:true}),/failed|失败/i);
  const cached=(await snapshot()).status.releases.backends;
  assert.ok(cached.some(r=>r.latest_attempt?.status==='failed' && r.latest_success && r.stale));
  assert.match(await dialog.innerText(),/failed|失败/i);assert.equal(await dialog.locator('script').count(),0,'release notes remain text');
  // Workspace version drift cannot silently overwrite somebody else's prefs.
  const before=await invoke('settings.get');await invoke('settings.update',{settings:{ui:{theme:'light'}}},{expected_version:before.version});
  await assert.rejects(dispatch('ui.agent_cli.set_tracking',{automatic:false}),/changed|conflict|revision/i);
  assert.equal((await invoke('settings.get')).data.cli_updates.automatic,true);
  await remote('ui.agent_cli.close');assert.deepEqual(errors,[]);
  console.log(JSON.stringify({agent_cli:true,real_registration_ack:true,manual_remote_paths:true,scope_preserved:true,stale_project_and_workspace:true,offline_cache:true,external_event_sync_preserves_focus_and_drafts:true,no_install_login_model:true}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
