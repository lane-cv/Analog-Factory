// Disposable workspace only: genuine two-browser writers and reload recovery.
'use strict';
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const base=process.env.AF_URL||'http://127.0.0.1:8765';
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 try {
  const context=await browser.newContext({viewport:{width:1500,height:1000}}),page=await context.newPage(),otherContext=await browser.newContext(),other=await otherContext.newPage(),errors=[];
  for(const p of [page,other]){p.on('pageerror',e=>errors.push(e.message));await p.goto(base);try{await p.waitForFunction(()=>AFOperations.status().client_id);}catch(error){throw new Error(error.message+'; browser errors: '+JSON.stringify(errors));}}
  const dispatch=(p,operation_id,parameters={})=>p.evaluate(async({operation_id,parameters})=>{try{return {ok:true,data:await AFOperations.dispatch(operation_id,parameters)};}catch(error){return {ok:false,code:error.code,message:error.message};}},{operation_id,parameters});
  const command=async(p,id,params={})=>{const r=await dispatch(p,id,params);assert.equal(r.ok,true,JSON.stringify(r));return r.data;};
  const created=await (await context.request.post(`${base}/api/projects`,{data:{name:'Editor recovery two writers',template:'amplifier'}})).json(),project=created.project||created;
  const pid=project.id,path='testbenches/pre.cir',fileURL=`${base}/api/projects/${pid}/files?path=${encodeURIComponent(path)}`;
  for(const p of [page,other]){await command(p,'ui.workspace.select_project',{project_id:pid});await command(p,'ui.editor.open',{path});}
  const original=(await command(page,'ui.editor.read')).content,human=original+'\n* actor one draft\n',disk=original+'\n* actor two disk\n';
  await command(page,'ui.editor.set_content',{content:human});
  await command(other,'ui.editor.set_content',{content:disk});await command(other,'ui.editor.save');
  const refused=await dispatch(page,'ui.editor.save');assert.equal(refused.ok,false);assert.match(refused.message,/version|conflict|state changed/i);
  assert.equal((await command(page,'ui.editor.read')).content,human);
  assert.equal((await (await context.request.get(fileURL)).json()).content,disk);
  const client=await page.evaluate(()=>AFOperations.status().client_id);
  const semantic=async(operation_id,parameters={})=>{
   const r=await (await context.request.post(`${base}/api/operations/invoke`,{data:{operation_id,parameters:{client_id:client,...parameters}}})).json();
   assert.equal(r.ok,true,JSON.stringify(r.error));return r.data.data;
  };
  const review=await semantic('ui.editor_recovery.inspect');
  assert.deepEqual([review.base,review.latest,review.draft],[original,disk,human]);
  const inspectedDiskVersion=(await context.request.get(fileURL)).headers().etag;
  await semantic('ui.editor_recovery.resolve',{inspection_id:review.inspection_id,content:human+'* merged actor two\n'});
  assert.equal((await context.request.get(fileURL)).headers().etag,inspectedDiskVersion,'resolve cannot write');
  await command(other,'ui.editor.set_content',{content:disk+'* third write after review\n'});await command(other,'ui.editor.save');
  const stale=await dispatch(page,'ui.editor.save');assert.equal(stale.ok,false);
  const review2=await command(page,'ui.editor_recovery.inspect');
  await command(page,'ui.editor.set_content',{content:human+'* typed after inspect\n'});
  assert.equal((await dispatch(page,'ui.editor_recovery.resolve',{inspection_id:review2.inspection_id,content:'must not replace'})).ok,false);
  let entered;const enteredRead=new Promise(resolve=>{entered=resolve;}),release=[];
  await page.route('**/files?path=*',async route=>{if(route.request().method()==='GET'){entered();await new Promise(resolve=>release.push(resolve));}await route.continue();});
  const pending=dispatch(page,'ui.editor_recovery.inspect');await enteredRead;
  await command(page,'ui.editor.set_content',{content:human+'* typed while read awaits\n'});release.forEach(resolve=>resolve());
  assert.equal((await pending).ok,false);await page.unroute('**/files?path=*');
  const retained=(await command(page,'ui.editor.read')).content;
  const record=await command(page,'ui.editor_recovery.read',{path});
  await page.reload();await page.waitForFunction(()=>AFOperations.status().client_id);
  await command(page,'ui.editor.open',{path});await command(page,'ui.editor_recovery.restore',{path,draft_id:record.draft_id});
  assert.equal((await command(page,'ui.editor.read')).content,retained);
  const restored=await command(page,'ui.editor_recovery.read',{path,draft_id:record.draft_id});assert.equal(restored.version,record.version);assert.equal(restored.base,record.base);
  assert.equal((await dispatch(page,'ui.editor.save')).ok,false,'restored old pin still conflicts');
  const fullState=await command(page,'ui.workspace.read');
  assert.ok(!JSON.stringify(fullState).includes('typed while read awaits'),'normal state excludes source text');
  await page.locator('[data-recovery="inspect"]').click();await page.locator('#editor-merged-text').waitFor();
  await page.locator('#editor-merged-text').fill(retained);await page.locator('[data-recovery="resolve"]').click();
  await page.waitForFunction(()=>AFOperations.snapshot().editor_recovery.inspection_id===null);
  await command(page,'ui.editor.save');
  assert.equal((await (await context.request.get(fileURL)).json()).content,retained);
  assert.equal((await command(page,'ui.editor_recovery.list')).length,1,'confirmed clean save retains foreign backup');
  await command(page,'ui.editor_recovery.discard',{path,draft_id:record.draft_id});
  assert.equal((await command(page,'ui.editor_recovery.list')).length,0,'explicit discard removes the foreign backup');
  const oldLarge='a\n'.repeat(30000),newLarge='b\n'.repeat(30000),current=await context.request.get(fileURL);
  assert.equal((await context.request.put(fileURL,{headers:{'If-Match':current.headers().etag},data:{content:oldLarge}})).ok(),true);
  await command(page,'ui.editor.open',{path});await command(page,'ui.editor.set_content',{content:newLarge});
  const complete=await command(page,'ui.editor_recovery.inspect');
  assert.equal(complete.latest,oldLarge);assert.equal(complete.draft,newLarge);assert.equal(complete.preview,null,'oversized diff uses complete three-way read instead of clipping');
  await command(page,'ui.editor_recovery.resolve',{inspection_id:complete.inspection_id,content:retained});await command(page,'ui.editor.save');
  const backedUp=retained+'* confirmed backup\n';await command(page,'ui.editor.set_content',{content:backedUp});
  const beforeQuota=(await command(page,'ui.editor_recovery.list')).find(row=>row.own);
  await page.evaluate(()=>{const put=IDBObjectStore.prototype.put;IDBObjectStore.prototype.put=function(...args){if(this.name==='drafts')throw new DOMException('Test quota exceeded','QuotaExceededError');return put.apply(this,args);};});
  await command(page,'ui.editor.set_content',{content:retained+'* quota draft\n'});
  assert.match((await command(page,'ui.workspace.read')).editor_recovery.storage_error,/quota/i);
  assert.equal((await command(page,'ui.editor.read')).content,retained+'* quota draft\n');
  assert.match(await page.locator('#editor-recovery-error').innerText(),/quota/i);
  assert.equal((await command(page,'ui.editor_recovery.read',{path,draft_id:beforeQuota.draft_id})).content,backedUp,'aborted quota transaction preserves the previous durable backup');
  assert.equal((await (await context.request.get(fileURL)).json()).content,retained,'storage failure never writes');
  assert.deepEqual(errors,[]);
  console.log('Editor conflict/recovery: actual two writers, semantic acknowledgement, stale review, typing during read, reload exact pin, successful explicit merge, quota and no unintended writes PASS.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
