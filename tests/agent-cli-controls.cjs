'use strict';
// Controller boundary contracts; no real executable, authentication, network or model is invoked.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source='analog_factory/static/agent-cli-controls.js';
assert.ok(fs.existsSync(source),'Agent CLI settings controller is implemented');
const clone=value=>JSON.parse(JSON.stringify(value));
function fixture(){
 const calls=[],state={project:{id:'p',config:{tools:{magic:'/magic',codex:'/old'},pdk_root:'/pdk',provider:{model:'keep'}}}},dialogs=[];
 let projectVersion='p1',workspaceVersion='w1',delay=null,offline=false,drift=false;
 const settings={tools:{codex:'/workspace'},ui:{language:'en',theme:'dark'},cli_updates:{automatic:false,interval_hours:24}};
 const releases={backends:[{backend:'codex',source_url:'https://github.com/openai/codex/releases',label:'latest published release',latest_success:{version:'2.0.0',tag:'v2',url:'https://github.com/openai/codex/releases/tag/v2',published_at:'2026-10-09',checked_at:'2026-10-09',notes:'<script>public notes</script>',notes_truncated:true},latest_attempt:{checked_at:'2026-10-09',status:'failed',error:'Offline fixture'},stale:true}],automatic:{enabled:false,interval_hours:24,server_running_only:true}};
 const status=()=>({project_id:'p',tools:[{backend:'codex',available:true,path:'/old',configured_path:'/old',config_source:'project',version:'1.0.0',login:'not_checked',diagnostic:'Executable found',update_status:'update_available'}],releases:clone(releases)});
 const api={invoke:async(id,params={},options={})=>{calls.push({id,params:clone(params),options:clone(options)});
  if(delay && id===delay.id){const item=delay;delay=null;return item.promise;}
  if(id==='project.detail')return {data:{project:clone(state.project)},version:projectVersion};
  if(id==='file.read'){assert.equal(params.path,'project.json');const content=JSON.stringify(state.project),checked=projectVersion;
   if(drift){drift=false;state.project.config.provider.model='concurrent-provider';projectVersion='p-native';}
   return {data:{path:'project.json',content,source_version:checked},version:projectVersion};}
  if(id==='settings.get')return {data:clone(settings),version:workspaceVersion};
  if(id==='agent.cli.status')return {data:status()};
  if(id==='agent.cli.check_updates'){if(offline)throw Object.assign(new Error('Offline fixture'),{code:'NETWORK_ERROR'});return {data:clone(releases)};}
  if(id==='project.configure'){if(options.expected_version!==projectVersion)throw Object.assign(new Error('Project revision changed'),{code:'VERSION_CONFLICT'});state.project.config=clone(params.config);workspaceVersion='w-project';return {data:clone(state.project),version:projectVersion='p2'};}
  if(id==='settings.update'){if(options.expected_version!==workspaceVersion)throw Object.assign(new Error('Settings revision changed'),{code:'VERSION_CONFLICT'});Object.assign(settings,clone(params.settings));return {data:clone(settings),version:workspaceVersion='w2'};}
  throw new Error('Unexpected operation '+id);
 }};
 const element=()=>{const listeners={};return {innerHTML:'',open:false,disabled:false,addEventListener(name,handler){listeners[name]=handler;},emit(name,event){listeners[name]?.(event);},setAttribute(){},querySelector(){return element();},querySelectorAll(){return [];},showModal(){this.open=true;},close(){this.open=false;}};};
 const window={AFOperations:api};vm.runInNewContext(fs.readFileSync(source,'utf8'),{window,document:{createElement(){const el=element();dialogs.push(el);return el;},body:{append(){}}},crypto:{randomUUID:()=> 'test'},URL});
 const ui=window.AFAgentCLIControls.create({state});ui.mount(element());
 return {ui,state,calls,settings,dialog:dialogs[0],driftRead:()=>{drift=true;},changeVersion:()=>{projectVersion='p-new';},changeSettings:()=>{settings.ui.theme='light';workspaceVersion='w-other';},offline:()=>{offline=true;},delay:(id,promise)=>{delay={id,promise};}};
}
(async()=>{
 const f=fixture();await f.ui.commands.open();assert.equal(f.ui.snapshot().project_version,'p1');assert.equal(f.ui.snapshot().workspace_version,'w1');
 assert.equal(typeof f.ui.sync,'function','open CLI panel observes registered external changes');
 f.ui.commands.set_paths({codex:'/pending-path'});const beforeSync=f.calls.length;await f.ui.sync([{operation_id:'agent.cli.releases',project_id:null}],false);assert.equal(f.calls.length,beforeSync+1);assert.equal(f.ui.snapshot().paths.codex,'/pending-path');assert.equal(f.ui.snapshot().project_version,'p1');
 const ignored=f.calls.length;await f.ui.sync([{operation_id:'file.write',project_id:'p'}],false);assert.equal(f.calls.length,ignored,'unrelated events do not probe CLIs');
 assert.equal(typeof f.ui.commands.set_tracking_draft,'function','manual tracking drafts have a remote semantic equivalent');
 const beforeDraft=f.calls.length;f.ui.commands.set_tracking_draft({automatic:true,interval_hours:72});assert.equal(f.ui.snapshot().tracking.interval_hours,72);assert.equal(f.calls.length,beforeDraft,'tracking draft updates have no writer');await f.ui.commands.refresh();assert.equal(f.ui.snapshot().tracking.interval_hours,72);
 assert.match(f.dialog.innerHTML,/not checked|not_checked/i);assert.match(f.dialog.innerHTML,/&lt;script&gt;/);assert.doesNotMatch(f.dialog.innerHTML,/<script>/);
 assert.match(f.dialog.innerHTML,/data-cli-path="codex"/);assert.match(f.dialog.innerHTML,/data-cli-path="claude"/);
 f.ui.commands.set_paths({codex:'/draft'});await f.ui.commands.refresh();assert.equal(f.ui.snapshot().paths.codex,'/draft','refresh preserves draft');assert.equal(f.ui.snapshot().project_version,'p1','refresh cannot approve a stale draft');
 await f.ui.commands.save_paths();assert.equal(f.state.project.config.tools.magic,'/magic');assert.equal(f.state.project.config.pdk_root,'/pdk');assert.equal(f.state.project.config.provider.model,'keep');assert.equal(f.state.project.config.tools.codex,'/draft');
 await f.ui.commands.reset_paths();assert.equal(f.state.project.config.tools.codex,undefined);assert.equal(f.state.project.config.tools.magic,'/magic');
 await f.ui.commands.set_tracking({automatic:true,interval_hours:48});const write=f.calls.find(c=>c.id==='settings.update');assert.deepEqual(write.params,{settings:{cli_updates:{automatic:true,interval_hours:48}}},'preference writes only shared tracking fields');assert.equal(f.settings.ui.theme,'dark');
 for(const parameters of [{automatic:'true'},{automatic:true,interval_hours:5},{automatic:true,interval_hours:24.5}])await assert.rejects(f.ui.commands.set_tracking(parameters));
 const preferenceRace=fixture();await preferenceRace.ui.commands.open();preferenceRace.dialog.emit('change',{target:{hasAttribute:name=>name==='data-cli-automatic',checked:true}});await Promise.resolve();
 preferenceRace.changeSettings();await preferenceRace.ui.commands.save_paths();assert.equal(preferenceRace.ui.snapshot().workspace_version,'w1','concurrent settings changes do not approve old tracking draft');assert.equal(preferenceRace.ui.snapshot().tracking.automatic,true,'draft preference survives own writer status refresh');await assert.rejects(preferenceRace.ui.commands.set_tracking({automatic:true}),/revision/);assert.equal(preferenceRace.settings.ui.theme,'light');assert.equal(preferenceRace.ui.snapshot().tracking.automatic,true,'rejected preference save retains the human draft');
 const stale=fixture();await stale.ui.commands.open();stale.ui.commands.set_paths({claude:'/draft'});stale.changeVersion();await assert.rejects(stale.ui.commands.save_paths());assert.equal(stale.ui.snapshot().paths.claude,'/draft');
 const nativeDrift=fixture();nativeDrift.driftRead();await nativeDrift.ui.commands.open();assert.ok(nativeDrift.calls.some(c=>c.id==='file.read'),'project CLI draft uses the existing checked source producer');assert.equal(nativeDrift.ui.snapshot().project_version,'p1','native edit after handler cannot approve the old config base');nativeDrift.ui.commands.set_paths({claude:'/preserved'});await assert.rejects(nativeDrift.ui.commands.save_paths(),/revision/);assert.equal(nativeDrift.state.project.config.provider.model,'concurrent-provider');
 assert.equal(stale.ui.snapshot().uncertain,false,'an explicit rejected conflict has a known outcome');
 const unknown=fixture();await unknown.ui.commands.open();unknown.ui.commands.set_paths({codex:'/keep-draft'});let rejectWrite;unknown.delay('project.configure',new Promise((_,reject)=>{rejectWrite=reject;}));
 const writing=unknown.ui.commands.save_paths();assert.throws(()=>unknown.ui.commands.set_paths({codex:'/late'}),/Wait/,'in-flight writer refuses edits');rejectWrite(Object.assign(new Error('transport lost'),{code:'NETWORK_ERROR'}));await assert.rejects(writing);
 assert.equal(unknown.ui.snapshot().uncertain,true);assert.equal(unknown.ui.snapshot().paths.codex,'/keep-draft');await assert.rejects(unknown.ui.commands.save_paths(),/unknown/);assert.equal(unknown.calls.filter(c=>c.id==='project.configure').length,1,'uncertain writes never retry');
 const invalidResult=fixture();await invalidResult.ui.commands.open();let rejectAck;invalidResult.delay('project.configure',new Promise((_,reject)=>{rejectAck=reject;}));const brokenAck=invalidResult.ui.commands.save_paths();rejectAck(Object.assign(new Error('Handler result is invalid'),{code:'INVALID_RESULT'}));await assert.rejects(brokenAck);assert.equal(invalidResult.ui.snapshot().uncertain,true,'a post-write result failure cannot be treated as a refused mutation');
 const switching=fixture();await switching.ui.commands.open();let finishWrite;switching.delay('project.configure',new Promise(resolve=>{finishWrite=resolve;}));const oldWrite=switching.ui.commands.save_paths();switching.state.project={id:'new',config:{tools:{codex:'new-selection'}}};finishWrite({data:{id:'p',config:{tools:{codex:'old-ack'}}},version:'p2'});await assert.rejects(oldWrite,/changed/);assert.equal(switching.state.project.config.tools.codex,'new-selection');
 const offline=fixture();await offline.ui.commands.open();offline.offline();await assert.rejects(offline.ui.commands.check_updates());assert.equal(offline.ui.snapshot().status.releases.backends[0].latest_attempt.status,'failed');assert.ok(offline.calls.filter(c=>c.id==='agent.cli.status').length>=2,'failed check still refreshes recorded cache');
 const raced=fixture();await raced.ui.commands.open();let release;raced.delay('agent.cli.status',new Promise(resolve=>{release=resolve;}));const request=raced.ui.commands.refresh();raced.state.project={id:'other',config:{}};release({data:{project_id:'p',tools:[],releases:{}}});await assert.rejects(request,/changed/);assert.equal(raced.ui.snapshot().project_id,'p');assert.equal(raced.ui.snapshot().status,null,'foreign-selection snapshots hide prior-project details');
 assert.ok(f.calls.every(c=>!c.id.startsWith('agent.session') && !c.id.includes('install')));
 await f.ui.commands.close();const closed=f.calls.length;await f.ui.sync([],true);assert.equal(f.calls.length,closed,'closed panel does not automatically probe');
 console.log('Agent CLI controller: scope-preserving versioned paths, tracking validation, stale/offline/selection guards, escaped public notes PASS.');
})().catch(error=>{console.error(error);process.exit(1);});
