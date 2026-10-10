// Actual disposable server and simulations; recorded source-run fixtures exercise publication entry only.
'use strict';
const assert=require('node:assert/strict'),path=require('node:path'),readline=require('node:readline');
const {spawn}=require('node:child_process'),{once}=require('node:events'),{chromium}=require('playwright');
const waitForSuccessfulRun=require('./fixtures/browser-run-wait.cjs');
const harness=String.raw`
import json,sys,tempfile,threading
from pathlib import Path
from analog_factory.service import Workspace
from analog_factory.server import create_server
from analog_factory.storage import atomic_json
with tempfile.TemporaryDirectory(prefix='af-design-navigation-') as temp:
    ws=Workspace(Path(temp)); cases={}; special={}
    for name in ('custom','missing','running','publication','unknown','blocked','other'):
        project=ws.projects.create('Design navigation '+name);pid=project['id'];root=ws.projects.path(pid)
        deck=ws.projects.read(pid,'testbenches/pre.cir')['content']
        project['design']={'netlist':'testbenches/chosen-pre.cir','post_netlist':'testbenches/chosen-post.cir',
            'schematic':'schematics/chosen.sch','layout':'layout/chosen.mag'}
        ws.projects.write(pid,'testbenches/chosen-pre.cir',deck+'\n* chosen pre source\n')
        if name!='missing':ws.projects.write(pid,'testbenches/chosen-post.cir',deck+'\n* chosen post source\n')
        ws.projects.write(pid,'schematics/chosen.sch','v {xschem version=3.4.5 file_version=1.2}\n')
        ws.projects.write(pid,'layout/chosen.mag','magic\ntech sky130A\n<< end >>\n')
        if name in ('publication','unknown'):project['template']='ota5'
        atomic_json(root/'project.json',project);cases[name]={'project_id':pid}
        if name=='blocked':ws.projects.configure(pid,{'tools':{'ngspice':'/missing-af-design-simulator'}})
        if name=='running':
            run=ws.runner.start(root,'simulate',['/bin/sleep','90'],timeout=100,
                metadata={'sources':{'netlist':'testbenches/chosen-post.cir'}})
            cases[name]['run_id']=run['id']
        if name in ('publication','unknown'):
            records={}
            for kind,key,source in (('xschem','schematic','schematics/chosen.sch'),('extract','layout','layout/chosen.mag')):
                run=ws.runner.start(root,kind,['/bin/true'],metadata={'sources':{key:source}})
                records[kind]=ws.runner.wait(root,run['id'])['id']
            cases[name]['runs']=records;special[pid]=name
    detail=ws.detail
    def navigation_detail(pid):
        value=detail(pid)
        if pid in special:
            case=cases[special[pid]]
            for stage in value['design_progress']:
                if stage['id'] in ('schematic','extract'):
                    stage.update(status='publication_required' if special[pid]=='publication' else 'unknown',
                        run_id=case['runs']['xschem' if stage['id']=='schematic' else 'extract'])
        return value
    ws.detail=navigation_detail
    server=create_server(ws,0,desktop_url='');threading.Thread(target=server.serve_forever,daemon=True).start()
    print(json.dumps({'url':f'http://127.0.0.1:{server.server_port}','cases':cases}),flush=True)
    try:sys.stdin.readline()
    finally:
        for run in ws.runs(cases['running']['project_id']):
            if run['status'] not in ('succeeded','failed','cancelled'):ws.runner.cancel(ws.projects.path(cases['running']['project_id']),run['id'])
        ws.agents.close();server.shutdown();server.server_close();ws.operations.close()
`;
(async()=>{
 const child=spawn('python3',['-u','-c',harness],{cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
 let browser,stderr='';child.stderr.on('data',data=>{stderr+=data;});const lines=readline.createInterface({input:child.stdout});
 try {
  const info=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Fixture did not start: '+stderr)),20000);lines.once('line',line=>{clearTimeout(timer);resolve(JSON.parse(line));});child.once('exit',code=>{clearTimeout(timer);reject(new Error('Fixture exited '+code+': '+stderr));});});
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));await page.goto(info.url);
  await page.waitForFunction(()=>window.AFOperations?.status().client_id);
  assert.ok(await page.evaluate(()=>AFOperations.names().includes('ui.workspace.continue_design')),
    'Design cards and remote calls require a shared semantic controller');
  const select=async name=>page.evaluate(id=>AFOperations.dispatch('ui.workspace.select_project',{project_id:id}),info.cases[name].project_id);
  const invoke=async(operation_id,parameters={})=>{
    const client_id=await page.evaluate(()=>AFOperations.status().client_id);
    const response=await page.request.post(info.url+'/api/operations/invoke',{data:{operation_id,parameters:operation_id.startsWith('ui.')?{client_id,...parameters}:parameters}});
    const result=await response.json();assert.equal(result.ok,true,JSON.stringify(result));return result;
  };
  const runs=async name=>{const value=await (await page.request.get(info.url+'/api/projects/'+info.cases[name].project_id+'/runs')).json();return value.runs || value;};
  await select('custom');await page.locator('[data-design-stage=pre]').click();
  await page.waitForFunction(()=>AFOperations.snapshot().runs.selected);
  let run=await page.evaluate(()=>state.run);
  assert.equal(run.metadata.sources.netlist,'testbenches/chosen-pre.cir');
  await waitForSuccessfulRun(invoke,info.cases.custom.project_id,run.id);
  await invoke('ui.workspace.continue_design',{stage:'post'});
  run=await page.evaluate(()=>state.run);assert.equal(run.metadata.sources.netlist,'testbenches/chosen-post.cir');
  await waitForSuccessfulRun(invoke,info.cases.custom.project_id,run.id);
  // Saving an editor draft changes completed evidence into stale evidence. The
  // clicked inspect intent must fail for review instead of becoming a new run.
  await page.evaluate(async()=>{await AFOperations.dispatch('ui.workspace.refresh');await AFOperations.dispatch('ui.editor.open',{path:'testbenches/chosen-pre.cir'});});
  await page.locator('#code-editor').fill((await page.locator('#code-editor').inputValue())+'\n* reviewed draft before task entry\n');
  const beforeDraft=(await runs('custom')).length;
  const changed=await page.evaluate(async()=>{try{await AFOperations.dispatch('ui.workspace.continue_design',{stage:'pre'});return {ok:true};}catch(error){return {ok:false,code:error.code};}});
  assert.deepEqual(changed,{ok:false,code:'VERSION_CONFLICT'});assert.equal((await runs('custom')).length,beforeDraft);
  assert.equal(await page.evaluate(()=>AFOperations.snapshot().editor.dirty),false);
  const savedDraft=await (await page.request.get(info.url+'/api/projects/'+info.cases.custom.project_id+'/files?path=testbenches/chosen-pre.cir')).json();
  assert.match(savedDraft.content,/reviewed draft before task entry/);
  // A source read may be slow enough for a new editor draft to begin after refresh.
  let releaseSource,sourceSeen;const sourceGate=new Promise(resolve=>{releaseSource=resolve;}),sourceStarted=new Promise(resolve=>{sourceSeen=resolve;});
  const sourcePattern='**/api/projects/'+info.cases.custom.project_id+'/files?path=specifications.json';
  await page.route(sourcePattern,async route=>{sourceSeen();await sourceGate;await route.continue();});
  const sourcePending=page.evaluate(async()=>{try{await AFOperations.dispatch('ui.workspace.continue_design',{stage:'specification'});return {ok:true};}catch(error){return {ok:false,code:error.code};}});
  await sourceStarted;await page.locator('#code-editor').fill(savedDraft.content+'\n* retain typing during task source read\n');releaseSource();
  assert.deepEqual(await sourcePending,{ok:false,code:'VERSION_CONFLICT'});
  assert.equal(await page.evaluate(()=>AFOperations.snapshot().editor.path),'testbenches/chosen-pre.cir');
  assert.match(await page.locator('#code-editor').inputValue(),/retain typing during task source read/);
  await page.unroute(sourcePattern);await page.evaluate(()=>AFOperations.dispatch('ui.editor.save'));
  // Holding delivery of one launch must not allow a second click to issue a duplicate.
  let releaseLaunch,launchSeen,held=false;const launchGate=new Promise(resolve=>{releaseLaunch=resolve;}),launchStarted=new Promise(resolve=>{launchSeen=resolve;});
  const invokePattern=info.url+'/api/operations/invoke';
  await page.route(invokePattern,async route=>{
    if(!held && route.request().postDataJSON()?.operation_id==='run.start'){held=true;launchSeen();await launchGate;}
    await route.continue();
  });
  const launchCount=(await runs('custom')).length;
  const firstLaunch=page.evaluate(async()=>{try{await AFOperations.dispatch('ui.workspace.continue_design',{stage:'pre'});return {ok:true};}catch(error){return {ok:false,code:error.code};}});
  await launchStarted;
  const secondLaunch=await page.evaluate(async()=>{try{await AFOperations.dispatch('ui.workspace.continue_design',{stage:'pre'});return {ok:true};}catch(error){return {ok:false,code:error.code};}});
  releaseLaunch();const firstResult=await firstLaunch;
  assert.equal(secondLaunch.ok,false,'A launch awaiting HTTP delivery must reject another start');
  assert.equal(firstResult.ok,true);assert.equal((await runs('custom')).length,launchCount+1);
  await page.unroute(invokePattern);
  await select('missing');await invoke('ui.workspace.continue_design',{stage:'post'});
  assert.equal(await page.locator('#new-file-dialog').evaluate(node=>node.open),true);
  assert.equal(await page.locator('#new-file-path').inputValue(),'testbenches/chosen-post.cir');
  assert.equal((await runs('missing')).length,0);
  await page.locator('#new-file-content').fill('preserve my unfinished source');
  const guarded=await page.evaluate(async()=>{try{await AFOperations.dispatch('ui.workspace.continue_design',{stage:'pre'});return false;}catch(_){return true;}});
  assert.equal(guarded,true);assert.equal(await page.locator('#new-file-content').inputValue(),'preserve my unfinished source');
  await page.locator('#close-file-dialog').click();
  const closedDraft=await page.evaluate(async()=>{try{await AFOperations.dispatch('ui.workspace.continue_design',{stage:'post'});return false;}catch(_){return true;}});
  assert.equal(closedDraft,true,'A dismissed source draft cannot be silently retargeted by task entry');
  assert.equal(await page.locator('#new-file-content').inputValue(),'preserve my unfinished source');
  await select('running');const before=(await runs('running')).length;
  await invoke('ui.workspace.continue_design',{stage:'post'});
  assert.equal(await page.evaluate(()=>AFOperations.snapshot().runs.selected),info.cases.running.run_id);
  assert.equal((await runs('running')).length,before,'Opening an active stage must never duplicate its execution');
  await select('publication');await invoke('ui.workspace.continue_design',{stage:'post'});
  assert.equal(await page.evaluate(()=>AFOperations.snapshot().runs.selected),info.cases.publication.runs.extract);
  assert.equal(await page.locator('[data-action=reference-publish]').isEnabled(),true);
  assert.equal(await page.locator('[data-action=reference-publish]').evaluate(node=>node===document.activeElement),true);
  assert.equal((await (await page.request.get(info.url+'/api/projects/'+info.cases.publication.project_id)).json()).files.some(file=>/derivation\.json$/.test(file.path)),false);
  await page.locator('.tabs [data-tab=overview]').click();await page.locator('[data-design-stage=schematic]').click();
  await page.waitForFunction(run_id=>AFOperations.snapshot().runs.selected===run_id,info.cases.publication.runs.xschem);
  assert.equal(await page.evaluate(()=>AFOperations.snapshot().runs.selected),info.cases.publication.runs.xschem);
  await select('unknown');await invoke('ui.workspace.continue_design',{stage:'extract'});
  assert.equal(await page.evaluate(()=>AFOperations.snapshot().runs.selected),info.cases.unknown.runs.extract);
  assert.equal((await runs('unknown')).length,2,'Unknown evidence opens its record without assuming completion or creating another run');
  let releaseSelection,selectionSeen;const selectionGate=new Promise(resolve=>{releaseSelection=resolve;}),selectionStarted=new Promise(resolve=>{selectionSeen=resolve;});
  const selectedRunPattern=info.url+'/api/projects/'+info.cases.unknown.project_id+'/runs/'+info.cases.unknown.runs.extract;
  await page.route(selectedRunPattern,async route=>{selectionSeen();await selectionGate;await route.continue();});
  const selectionPending=page.evaluate(async()=>{try{await AFOperations.dispatch('ui.workspace.continue_design',{stage:'extract'});return {ok:true};}catch(error){return {ok:false,code:error.code};}});
  await selectionStarted;await page.evaluate(run_id=>AFOperations.dispatch('ui.runs.select',{run_id}),info.cases.unknown.runs.xschem);releaseSelection();
  assert.deepEqual(await selectionPending,{ok:false,code:'VERSION_CONFLICT'},'A newer manual run selection must reject the older task entry acknowledgement');
  assert.equal(await page.evaluate(()=>AFOperations.snapshot().runs.selected),info.cases.unknown.runs.xschem);
  await page.unroute(selectedRunPattern);
  await select('blocked');await invoke('ui.workspace.continue_design',{stage:'post'});
  assert.equal(await page.evaluate(()=>AFOperations.snapshot().workspace.tab),'settings');
  assert.equal((await runs('blocked')).length,0,'Missing tools lead to configuration without attempting execution');
  await page.evaluate(()=>AFOperations.dispatch('ui.editor.open',{path:'testbenches/chosen-pre.cir'}));
  let releaseSettings,settingsSeen,detailReads=0;const settingsGate=new Promise(resolve=>{releaseSettings=resolve;}),settingsStarted=new Promise(resolve=>{settingsSeen=resolve;});
  const blockedDetail=info.url+'/api/projects/'+info.cases.blocked.project_id;
  await page.route(blockedDetail,async route=>{if(++detailReads===2){settingsSeen();await settingsGate;}await route.continue();});
  const settingsPending=page.evaluate(async()=>{try{await AFOperations.dispatch('ui.workspace.continue_design',{stage:'post'});return {ok:true};}catch(error){return {ok:false,code:error.code};}});
  await settingsStarted;await page.locator('#code-editor').fill('retain draft while requirements open\n');releaseSettings();
  assert.deepEqual(await settingsPending,{ok:false,code:'VERSION_CONFLICT'});
  assert.equal(await page.evaluate(()=>AFOperations.snapshot().workspace.tab),'editor');
  assert.equal(await page.locator('#code-editor').inputValue(),'retain draft while requirements open\n');
  await page.unroute(blockedDetail);await page.evaluate(()=>AFOperations.dispatch('ui.editor.save'));
  // Delay refresh after the intent is captured; selecting another project invalidates the action.
  await select('custom');let release,seen;const gate=new Promise(resolve=>{release=resolve;}),started=new Promise(resolve=>{seen=resolve;});
  const detailPattern=info.url+'/api/projects/'+info.cases.custom.project_id;
  await page.route(detailPattern,async route=>{if(route.request().method()==='GET'){seen();await gate;}await route.continue();});
  const count=(await runs('custom')).length;
  const pending=page.evaluate(async()=>{try{await AFOperations.dispatch('ui.workspace.continue_design',{stage:'pre'});return {ok:true};}catch(error){return {ok:false,code:error.code};}});
  await started;await select('other');release();
  assert.deepEqual(await pending,{ok:false,code:'VERSION_CONFLICT'});
  assert.equal((await runs('custom')).length,count);assert.equal((await runs('other')).length,0);
  await page.unroute(detailPattern);
  assert.deepEqual(errors,[]);console.log('Design navigation: real customized pre/post simulations, manual/acknowledged remote parity, missing source, retained drafts, dirty-save action conflict, active run, explicit publication entry, unknown evidence and stale project PASS.');
 } finally {if(browser)await browser.close();child.stdin.end('\n');await once(child,'exit');lines.close();}
})().catch(error=>{console.error(error);process.exit(1);});
