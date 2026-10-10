const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const modulePath = require('node:path').join(__dirname,'../analog_factory/static/design-navigation.js');

test('design actions use configured sources and current evidence rather than generic tab defaults', () => {
  assert.ok(fs.existsSync(modulePath), 'Design navigation must resolve a concrete action');
  const {resolve, fingerprint} = require(modulePath);
  const project = {id:'project-a',template:'ota5',design:{netlist:'testbenches/custom-pre.cir',
    post_netlist:'testbenches/custom-post.cir',schematic:'schematics/custom.sch',layout:'layout/custom.mag',
    schematic_netlist:'netlists/custom-sch.spice',layout_netlist:'netlists/custom-ext.spice',cell:'custom'}};
  const files = ['specifications.json',...Object.values(project.design).filter(v=>v.includes('/'))].map(path=>({path}));
  const designProgress = ['specification','schematic','pre','layout','drc','extract','lvs','post','archive'].map(id=>({id,status:
    ['schematic','extract'].includes(id)?'published':'not_run'}));
  const status = {project_id:project.id,operation_availability:Object.fromEntries(
    ['simulate','xschem','magic','drc','extract','lvs'].map(kind=>[kind,{available:true}])),
    toolchain:{tools:{xschem:{available:true}},pdk:{available:true},desktop:{available:true}}};
  const input = {project,files,designProgress,status,runs:[]};
  assert.deepEqual(resolve(input,'pre'),{type:'run',stage:'pre',kind:'simulate',options:{netlist:'testbenches/custom-pre.cir'},tab:'simulation'});
  assert.deepEqual(resolve(input,'post'),{type:'run',stage:'post',kind:'simulate',options:{netlist:'testbenches/custom-post.cir'},tab:'simulation'});
  assert.equal(resolve(input,'drc').options.layout,'layout/custom.mag');
  assert.equal(resolve(input,'lvs').options.schematic_netlist,'netlists/custom-sch.spice');

  const missing = {...input,files:files.filter(file=>file.path!=='testbenches/custom-post.cir')};
  assert.deepEqual(resolve(missing,'post'),{type:'create_file',stage:'post',path:'testbenches/custom-post.cir',tab:'editor'});
  assert.notEqual(fingerprint(resolve(input,'post')),fingerprint(resolve(missing,'post')),
    'Refresh must not silently turn a run intent into create-file or vice versa');
  const noTool = {...input,status:{...status,operation_availability:{...status.operation_availability,simulate:{available:false,reasons:['Missing ngspice']}}}};
  assert.equal(resolve(noTool,'post').type,'settings');
  assert.equal(resolve({...input,status:{...status,project_id:'other'}},'pre').type,'settings');

  const run = {id:'source-run',kind:'extract',status:'succeeded',stale:false,metadata:{sources:{layout:'layout/custom.mag'}}};
  const unpublished = {...input,runs:[run],designProgress:designProgress.map(stage=>stage.id==='extract'?{...stage,status:'publication_required',run_id:run.id}:stage)};
  assert.deepEqual(resolve(unpublished,'post'),{type:'publish',stage:'post',source_stage:'extract',run_id:'source-run',tab:'verification'});
  assert.deepEqual(resolve(unpublished,'lvs'),{type:'publish',stage:'lvs',source_stage:'extract',run_id:'source-run',tab:'verification'});
  assert.deepEqual(resolve(unpublished,'extract'),{type:'publish',stage:'extract',source_stage:'extract',run_id:'source-run',tab:'verification'});
  const unrelated = {...unpublished,runs:[{...run,metadata:{sources:{layout:'layout/other.mag'}}}]};
  assert.equal(resolve(unrelated,'post').type,'select_run','Uncertain publication evidence is inspected, never silently published or rerun');

  const active = {id:'active-post',kind:'simulate',status:'running',metadata:{sources:{netlist:'testbenches/custom-post.cir'}}};
  assert.deepEqual(resolve({...input,runs:[active]},'post'),{type:'select_run',stage:'post',run_id:'active-post',tab:'simulation'});
  const uncertain = {...input,runs:[{...active,status:'succeeded'}],designProgress:designProgress.map(stage=>stage.id==='post'?{...stage,status:'unknown',run_id:active.id}:stage)};
  assert.equal(resolve(uncertain,'post').type,'select_run');
  assert.deepEqual(resolve({...uncertain,runs:[],designProgress:uncertain.designProgress.map(stage=>({...stage,run_id:null}))},'post'),
    {type:'open_file',stage:'post',path:'testbenches/custom-post.cir',tab:'editor'});
  assert.equal(resolve({...uncertain,designProgress:uncertain.designProgress.map(stage=>stage.id==='post'?{...stage,status:'future_unrecognized_status'}:stage)},'post').type,
    'select_run','Unrecognized future evidence statuses must remain inspection-only');
  assert.throws(()=>resolve(input,'not-a-stage'),/Unknown design stage/);
  assert.deepEqual(resolve(input,'specification'),{type:'open_file',stage:'specification',path:'specifications.json',tab:'editor'});
  assert.equal(resolve(input,'layout').options.layout,'layout/custom.mag');
  assert.deepEqual(resolve({...input,designProgress:[]},'specification'),{type:'open_file',stage:'specification',path:'specifications.json',tab:'editor'});
  assert.deepEqual(resolve({...input,designProgress:[{id:'archive',status:'unknown'}]},'archive'),{type:'export',stage:'archive'});
  const drawing = {id:'active-drawing',kind:'xschem',status:'running',metadata:{gui_edits_original:true,sources:{schematic:'schematics/custom.sch'}}};
  assert.deepEqual(resolve({...input,runs:[drawing]},'schematic'),{type:'select_run',stage:'schematic',run_id:'active-drawing',tab:'desktop'},
    'An already open native schematic stays in its drawing desktop without a second editor or netlisting launch');
});
