// Disposable server workspace: two tabs in ONE storage profile plus delayed save verification.
'use strict';
const assert=require('node:assert/strict'),{chromium}=require('playwright');
const base=process.env.AF_URL||'http://127.0.0.1:8765';
(async()=>{
 const browser=await chromium.launch({channel:'chromium',headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext(),a=await context.newPage(),b=await context.newPage(),errors=[];
  const call=(page,operation_id,parameters={})=>page.evaluate(async({operation_id,parameters})=>{try{return {ok:true,data:await AFOperations.dispatch(operation_id,parameters)};}catch(error){return {ok:false,message:error.message,code:error.code};}},{operation_id,parameters});
  const command=async(page,id,params={})=>{const result=await call(page,id,params);assert.equal(result.ok,true,JSON.stringify(result));return result.data;};
  for(const page of [a,b]){page.on('pageerror',e=>errors.push(e.message));await page.goto(base);await page.waitForFunction(()=>AFOperations.status().client_id);}
  const created=await(await context.request.post(base+'/api/projects',{data:{name:'Same-profile draft owners',template:'amplifier'}})).json(),pid=(created.project||created).id,path='testbenches/pre.cir';
  const url=`${base}/api/projects/${pid}/files?path=${encodeURIComponent(path)}`;
  for(const page of [a,b]){await command(page,'ui.workspace.select_project',{project_id:pid});await command(page,'ui.editor.open',{path});}
  const baseText=(await command(a,'ui.editor.read')).content,draftA=baseText+'\n* A unsaved\n',draftB=baseText+'\n* B saved\n';
  await Promise.all([command(a,'ui.editor.set_content',{content:draftA}),command(b,'ui.editor.set_content',{content:draftB})]);
  if(!await a.locator('#editor-recovery-panel').evaluate(element=>element.open))await a.locator('#editor-recovery-panel summary').click();
  await a.locator('[data-recovery="list"]').click();await a.waitForFunction(()=>document.querySelector('#editor-recovery-file').options.length===3);
  const rows=await command(a,'ui.editor_recovery.list');assert.equal(rows.length,2,JSON.stringify({rows,editors:await Promise.all([command(a,'ui.editor.read'),command(b,'ui.editor.read')])}));
  const variants=await Promise.all(rows.map(row=>command(a,'ui.editor_recovery.read',{path,draft_id:row.draft_id}))),recordA=variants.find(row=>row.content===draftA);
  assert.ok(recordA);assert.equal((await call(a,'ui.editor_recovery.read',{path})).ok,false,'ambiguous path requires explicit ID');
  await command(b,'ui.editor.save');assert.equal((await command(a,'ui.editor_recovery.list')).length,1);
  assert.equal((await command(a,'ui.editor_recovery.read',{path,draft_id:recordA.draft_id})).content,draftA);
  await a.reload();await a.waitForFunction(()=>AFOperations.status().client_id);await command(a,'ui.editor.open',{path});
  await command(a,'ui.editor_recovery.restore',{path,draft_id:recordA.draft_id});
  assert.equal((await command(a,'ui.editor.read')).content,draftA);
  assert.equal((await call(a,'ui.editor.save')).ok,false,'restored original version rejects B saved source');
  assert.equal((await(await context.request.get(url)).json()).content,draftB);
  const inspection=await command(a,'ui.editor_recovery.inspect'),submitted=draftA+'* reviewed B\n';
  await command(a,'ui.editor_recovery.resolve',{inspection_id:inspection.inspection_id,content:submitted});
  let release,entered;const reading=new Promise(resolve=>{entered=resolve;});
  await a.route('**/files?path=*',async route=>{if(route.request().method()==='GET'){entered();await new Promise(resolve=>{release=resolve;});}await route.continue();});
  let puts=0;a.on('request',request=>{if(request.method()==='PUT'&&request.url()===url)puts++;});
  const saving=call(a,'ui.editor.save');await reading;
  const newer=submitted+'* typed during verification\n';await command(a,'ui.editor.set_content',{content:newer});release();
  assert.equal((await saving).ok,true);await a.unroute('**/files?path=*');
  assert.equal((await command(a,'ui.editor.read')).content,newer,'readback preserves new typing');
  assert.equal((await(await context.request.get(url)).json()).content,submitted,'typing is never auto-written');assert.equal(puts,1);
  const own=(await command(a,'ui.editor_recovery.list')).find(row=>row.own),backup=await command(a,'ui.editor_recovery.read',{path,draft_id:own.draft_id});
  assert.equal(backup.content,newer);assert.equal(backup.base,submitted);assert.notEqual(backup.version,recordA.version);
  const secondReview=await command(a,'ui.editor_recovery.inspect');await command(a,'ui.editor_recovery.resolve',{inspection_id:secondReview.inspection_id,content:newer});
  await a.route('**/files?path=*',route=>route.request().method()==='GET'?route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Readback unavailable'})}):route.continue());
  const unknown=await call(a,'ui.editor.save');assert.equal(unknown.ok,false);assert.equal(unknown.code,'OUTCOME_UNKNOWN');
  assert.equal((await command(a,'ui.editor.read')).content,newer);assert.equal(puts,2,'unknown write is not retried');
  await a.unroute('**/files?path=*');assert.equal((await(await context.request.get(url)).json()).content,newer);
  assert.match((await command(a,'ui.workspace.read')).editor_recovery.status,/unknown|failed/i);
  assert.equal((await command(a,'ui.editor_recovery.read',{path,draft_id:recordA.draft_id})).content,draftA,'foreign A original remains until explicit discard');
  await command(b,'ui.editor.open',{path});
  const beforeSwitch=(await command(b,'ui.editor.read')).content;
  await command(b,'ui.editor.set_content',{content:beforeSwitch+'* save before switching\n'});
  let finishSwitch,enteredSwitch;const switchRead=new Promise(resolve=>{enteredSwitch=resolve;});
  await b.route('**/files?path=*',async route=>{if(route.request().method()==='GET'&&route.request().url()===url){enteredSwitch();await new Promise(resolve=>{finishSwitch=resolve;});}await route.continue();});
  const switchingSave=call(b,'ui.editor.save').catch(error=>({ok:false,message:error.message,code:'BROWSER_CLOSED'}));await switchRead;
  await command(b,'ui.editor.set_content',{content:beforeSwitch}); // Explicitly leave a clean editor so normal open can proceed.
  await command(b,'ui.editor.open',{path:'netlists/amplifier.cir'});
  const selectedAfterSwitch=await command(b,'ui.editor.read');finishSwitch();
  const switched=await switchingSave;assert.equal(switched.ok,false);assert.equal(switched.code,'OUTCOME_UNKNOWN');
  assert.deepEqual(await command(b,'ui.editor.read'),selectedAfterSwitch,'late save verification cannot change the newly selected file');
  await b.unroute('**/files?path=*');
  const migration=await browser.newContext(),legacy={project_id:pid,path,version:'legacy opened version',base:'legacy opened base',content:'legacy retained text',updated_at:12};
  await migration.addInitScript(({legacy})=>{if(!localStorage.getItem('af-editor-recovery-v1'))localStorage.setItem('af-editor-recovery-v1',JSON.stringify({schema_version:1,records:{[JSON.stringify([legacy.project_id,legacy.path])]:legacy}}));},{legacy});
  const migrated=await migration.newPage();migrated.on('pageerror',error=>errors.push(error.message));await migrated.goto(base);await migrated.waitForFunction(()=>AFOperations.status().client_id);
  await command(migrated,'ui.workspace.select_project',{project_id:pid});await command(migrated,'ui.editor.open',{path});
  const legacyRecord=await command(migrated,'ui.editor_recovery.read',{path});
  await command(migrated,'ui.editor.set_content',{content:'migration new page draft'});
  assert.equal((await command(migrated,'ui.editor_recovery.list')).length,2);
  assert.deepEqual(await command(migrated,'ui.editor_recovery.read',{path,draft_id:legacyRecord.draft_id}),legacyRecord);
  await migrated.reload();await migrated.waitForFunction(()=>AFOperations.status().client_id);await command(migrated,'ui.editor.open',{path});
  assert.deepEqual(await command(migrated,'ui.editor_recovery.read',{path,draft_id:legacyRecord.draft_id}),legacyRecord,'migrated IndexedDB retains exact legacy text/base/version across reload');
  const bounds=await migrated.evaluate(async({pid,path})=>{
   const owners=Array.from({length:7},(_,i)=>AFEditorRecovery.create({state:{project:{id:pid},files:[{path}],file:path,content:'bounded owner '+i,savedContent:'base',fileVersion:'opened',dirty:true},readLatest:async()=>{},applyDraft:()=>{}}));
   await Promise.all(owners.map(owner=>owner.edited()));return {count:(await owners[0].commands.list()).length,last:owners[6].snapshot().storage_error};
  },{pid,path});assert.equal(bounds.count,8);assert.match(bounds.last,/eight/);
  assert.deepEqual(errors,[]);
  console.log('Editor ownership/readback: actual same-profile tabs retain A after B save/reload; typing/selection preserved, checked base/version, failed verification/no retries, real legacy migration and transactional shared bounds PASS.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
