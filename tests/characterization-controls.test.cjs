'use strict';
// Controller behavior with transport/DOM doubles; real browser coverage is separate.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const clone=value=>JSON.parse(JSON.stringify(value));
function fixture(){
 const calls=[],dialogs=[],state={project:{id:'p'},dirty:false};let delay=null,readDelay=null,version='v1';
 const spec={views:['pre'],analyses:['ac','op'],cases:[{corner:'tt',supply_v:1.8,temperature_c:27}],targets:{gain_db_min:30,power_w_max:0.00001,offset_v_abs_max:0.002},limits:{runtime_seconds:30,run_timeout:10}};
 const jobs={old:{id:'a'.repeat(32),kind:'ota-characterization',status:'succeeded',created_at:'2026-01-01',spec,source_version:'v0'},
  latest:{id:'b'.repeat(32),kind:'ota-characterization',status:'succeeded',created_at:'2026-01-02',spec,source_version:'v1',electrical_status:'unknown',completed_runs:2,scenario_count:3,
   groups:[{view:'pre',conditions:spec.cases[0],metrics:{gain_db:42,power_w:0.000006,offset_v:-0.003},assessment:{status:'fail',checks:{gain_db_min:{value:42,limit:30,status:'pass'},power_w_max:{value:0.000006,limit:0.00001,status:'pass'},offset_v_abs_max:{value:-0.003,limit:0.002,status:'fail'},bandwidth_hz_min:{value:null,limit:1000000,status:'unknown'}}},run_ids:[]}],iterations:[]}};
 const element=()=>({innerHTML:'',open:false,disabled:false,listeners:{},addEventListener(type,fn){this.listeners[type]=fn;},setAttribute(){},querySelector(){return element();},showModal(){this.open=true;},close(){this.open=false;}});
 const ops={invoke:async(id,params={},options={})=>{calls.push({id,params:clone(params),options:clone(options)});
  if(id==='project.detail'){if(readDelay){const pending=readDelay;readDelay=null;return pending;}return {data:{id:params.project_id},version};}
  if(id==='job.list')return {data:clone(Object.values(jobs))};
  if(id==='job.get'){if(delay){const pending=delay;delay=null;return pending;}return {data:clone(Object.values(jobs).find(j=>j.id===params.job_id))};}
  if(id==='job.cancel'){const job=Object.values(jobs).find(j=>j.id===params.job_id);job.status='cancelled';return {data:clone(job)};}
  if(id==='reference.characterize')return {data:{id:'c'.repeat(32),kind:'ota-characterization',status:'running',spec:params.spec,source_version:options.expected_version}};
  throw Error('Unexpected '+id);
 }};
 const window={AFOperations:ops};
 vm.runInNewContext(fs.readFileSync('analog_factory/static/characterization.js','utf8'),{window,document:{createElement(){const node=element();dialogs.push(node);return node;},body:{append(){}}},setTimeout:()=>1,clearTimeout(){},crypto:{randomUUID:()=> 'test'}});
 const ui=window.AFCharacterization.create({state});ui.mount(element());
 return {ui,state,jobs,calls,dialog:dialogs[0],delay:p=>{delay=p;},delayRead:p=>{readDelay=p;},version:v=>{version=v;}};
}
(async()=>{
 const f=fixture();await f.ui.commands.open();
 assert.equal(f.ui.snapshot().job_id,f.jobs.latest.id,'opening restores newest historical characterization when none is active');
 assert.match(f.dialog.innerHTML,/data-characterize-history/,'manual history selection exists');
 assert.match(f.dialog.innerHTML,/Source version/);assert.match(f.dialog.innerHTML,/v1/);
 assert.match(f.dialog.innerHTML,/data-characterize-currentness="current"/,'matching versions establish project source currentness at the recorded refresh');
 assert.match(f.dialog.innerHTML,/data-characterize-metric="gain_db"/);
 assert.match(f.dialog.innerHTML,/>12<\/td>/,'minimum gain margin is measured minus minimum');
 assert.match(f.dialog.innerHTML,/>-0\.001<\/td>/,'absolute offset margin uses absolute measured value');
 assert.match(f.dialog.innerHTML,/data-characterize-result="unknown"/,'missing measurements never pass');
 assert.ok(!f.calls.some(c=>c.id==='reference.characterize'),'restoring evidence never starts computation');
 const edited=clone(f.ui.snapshot().spec);edited.targets.gain_db_min=37;f.ui.commands.set_draft({spec:edited});
 await f.ui.commands.open({job_id:f.jobs.old.id});assert.equal(f.ui.snapshot().spec.targets.gain_db_min,37,'history selection preserves edited draft');
 assert.match(f.dialog.innerHTML,/data-characterize-currentness="stale"/);
 f.ui.commands.close();await f.ui.commands.open();assert.equal(f.ui.snapshot().spec.targets.gain_db_min,37,'closing and reopening preserves project draft');
 await f.ui.commands.open({new_job:true});assert.equal(f.ui.snapshot().job_id,null);assert.equal(f.ui.snapshot().spec.targets.gain_db_min,37,'new computation retains the prepared draft');
 const raced=fixture();await raced.ui.commands.open();let release;raced.delay(new Promise(resolve=>{release=resolve;}));
 const older=raced.ui.commands.open({job_id:raced.jobs.old.id});const rejected=assert.rejects(older,/changed/i);await new Promise(setImmediate);await raced.ui.commands.open({job_id:raced.jobs.latest.id});
 release({data:clone(raced.jobs.old)});await rejected;assert.equal(raced.ui.snapshot().job_id,raced.jobs.latest.id,'late history fetch cannot replace newer selection');
 const typing=fixture();await typing.ui.commands.open();let releaseTyping;typing.delay(new Promise(resolve=>{releaseTyping=resolve;}));
 const loading=typing.ui.commands.open({job_id:typing.jobs.old.id});await new Promise(setImmediate);const draft=clone(typing.ui.snapshot().spec);draft.targets.gain_db_min=39;typing.ui.commands.set_draft({spec:draft});
 releaseTyping({data:clone(typing.jobs.old)});await loading;assert.equal(typing.ui.snapshot().spec.targets.gain_db_min,39);
 const switched=fixture();await switched.ui.commands.open();let releaseSwitch;switched.delay(new Promise(resolve=>{releaseSwitch=resolve;}));
 const pending=switched.ui.commands.open({job_id:switched.jobs.old.id});const switchRejected=assert.rejects(pending,/changed/i);await new Promise(setImmediate);switched.state.project={id:'another'};switched.ui.render();
 releaseSwitch({data:clone(switched.jobs.old)});await switchRejected;assert.equal(switched.ui.snapshot().job_id,null);
 const running=fixture();running.jobs.old.status='running';await running.ui.commands.open();assert.equal(running.ui.snapshot().job_id,running.jobs.old.id,'active jobs take precedence over newer completed history');
 running.state.dirty=true;await running.ui.commands.cancel();assert.equal(running.calls.find(c=>c.id==='job.cancel').params.job_id,running.jobs.old.id);
 const history=fixture();history.jobs.old.status='running';await history.ui.commands.open({job_id:history.jobs.latest.id});await assert.rejects(history.ui.commands.cancel(),/active/);
 assert.ok(!history.calls.some(c=>c.id==='job.cancel'),'cancel never redirects from selected history to another active job');
 const unknown=fixture();delete unknown.jobs.latest.source_version;await unknown.ui.commands.open();assert.match(unknown.dialog.innerHTML,/data-characterize-currentness="unknown"/);
 const selecting=fixture();await selecting.ui.commands.open();let finishSelection;selecting.delay(new Promise(resolve=>{finishSelection=resolve;}));
 const selectingHistory=selecting.ui.commands.open({job_id:selecting.jobs.old.id});await new Promise(setImmediate);
 await assert.rejects(selecting.ui.commands.start(),/loading|request/i,'History loading must block new computation');
 assert.ok(!selecting.calls.some(call=>call.id==='reference.characterize'),'No new job starts while a historical selection is pending');
 finishSelection({data:clone(selecting.jobs.old)});await selectingHistory;
 assert.equal(selecting.ui.snapshot().job_id,selecting.jobs.old.id);
 const preparing=fixture();await preparing.ui.commands.open();let finishReview;preparing.delayRead(new Promise(resolve=>{finishReview=resolve;}));
 const review=preparing.ui.commands.open({new_job:true});await new Promise(setImmediate);
 await assert.rejects(preparing.ui.commands.start(),/loading|request/i,'New-source review must also block computation');
 finishReview({data:{id:'p'},version:'v1'});await review;assert.equal(preparing.ui.snapshot().loading,false);
 const closing=fixture();await closing.ui.commands.open();let finishClosed;closing.delay(new Promise(resolve=>{finishClosed=resolve;}));
 const closedRequest=closing.ui.commands.open({job_id:closing.jobs.old.id});const cancelledOpen=assert.rejects(closedRequest,/changed/i);
 await new Promise(setImmediate);closing.ui.commands.close();finishClosed({data:clone(closing.jobs.old)});await cancelledOpen;
 assert.equal(closing.ui.snapshot().open,false);assert.equal(closing.ui.snapshot().loading,false);
 const pinned=fixture();await pinned.ui.commands.open();const pinnedSpec=clone(pinned.ui.snapshot().spec);
 pinnedSpec.targets.gain_db_min=37;pinned.ui.commands.set_draft({spec:pinnedSpec});pinned.ui.commands.close();pinned.version('v2');
 await pinned.ui.commands.open();assert.equal(pinned.ui.snapshot().source_version,'v1','Closing and reopening must not rebase an edited draft');
 pinned.state.project={id:'other'};pinned.ui.render();await pinned.ui.commands.open();pinned.ui.commands.close();
 pinned.state.project={id:'p'};pinned.ui.render();await pinned.ui.commands.open();
 assert.equal(pinned.ui.snapshot().source_version,'v1','Returning to a project must retain the edited draft revision');
 await pinned.ui.commands.open({new_job:true});assert.equal(pinned.ui.snapshot().source_version,'v2','Explicit new-job review adopts current sources');
 assert.match(pinned.dialog.innerHTML,/current project sources/i,'The UI explains explicit source review');
 const coverage=fixture(),record=coverage.jobs.latest;
 record.spec.views=['pre','post'];record.spec.cases=[record.spec.cases[0],{corner:'ss',supply_v:1.62,temperature_c:125}];record.spec.targets={gain_db_min:30};
 record.groups=[];
 for(const [index,view] of ['pre','post'].entries())for(const [caseIndex,conditions] of record.spec.cases.entries()) {
   const value=[[42,35],[40,30.5]][index][caseIndex];
   record.groups.push({view,conditions:clone(conditions),metrics:{gain_db:value},assessment:{checks:{gain_db_min:{value,limit:30,status:'pass'}}}});
 }
 const summaryRow=(html,target)=>html.match(new RegExp('<tr[^>]*data-characterize-summary="'+target+'"[^>]*>[\\s\\S]*?<\\/tr>'))?.[0]||'';
 await coverage.ui.commands.open();let summary=summaryRow(coverage.dialog.innerHTML,'gain_db_min');
 assert.match(summary,/data-characterize-summary-result="pass"/,'Complete known coverage can pass');
 assert.match(summary,/>0\.5<\/td>/,'Worst gain margin is the smallest measured minus target');
 assert.match(summary,/post · ss · 1\.62 V · 125 °C/,'Worst margin names the exact view and PVT conditions');
 assert.match(summary,/>4 \/ 4<\/td>/,'Coverage counts requested view/PVT groups');
 record.electrical_status='pass';record.groups.pop();await coverage.ui.commands.open();summary=summaryRow(coverage.dialog.innerHTML,'gain_db_min');
 assert.match(summary,/data-characterize-summary-result="unknown"/,'Missing requested conditions cannot show a full pass');
 assert.match(summary,/>3 \/ 4<\/td>/);
 assert.match(coverage.dialog.innerHTML,/data-characterize-electrical-status="unknown"/,'An incomplete record cannot retain an overall pass label');
 record.groups[0].metrics={};await coverage.ui.commands.open();summary=summaryRow(coverage.dialog.innerHTML,'gain_db_min');
 assert.match(summary,/>2 \/ 4<\/td>/,'A check with a missing measurement does not count as measured coverage');
 record.groups=[];await coverage.ui.commands.open();summary=summaryRow(coverage.dialog.innerHTML,'gain_db_min');
 assert.match(summary,/data-characterize-summary-result="unknown"/);assert.match(summary,/>0 \/ 4<\/td>/);
 const extremes=fixture();await extremes.ui.commands.open();
 assert.match(summaryRow(extremes.dialog.innerHTML,'offset_v_abs_max'),/>-0\.001<\/td>/,'Worst absolute offset uses the absolute value, including negative observations');
 assert.match(summaryRow(extremes.dialog.innerHTML,'power_w_max'),/>0\.000004<\/td>/,'Maximum power margin is limit minus measured power');
 console.log('PASS characterization history restore/selection, measured margins, unknown evidence, draft/race safety and selected-job cancellation');
})().catch(error=>{console.error(error);process.exitCode=1;});
