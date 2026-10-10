'use strict';
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const modulePath = path.join(__dirname, '../analog_factory/static/result-comparison.js');
function api() {
  assert.ok(fs.existsSync(modulePath), 'The safe result comparison module is missing');
  return require(modulePath);
}
// Mirrors execution manifests and simulation_batches.prepare_batch metadata.
function run(value=40, changes={}) {
  return {
    id:'a'.repeat(32), kind:'simulate', status:'succeeded', metrics:{response:value},
    source_hashes:{'netlists/design.spice':'design-a'},
    tool_versions:{ngspice:'ngspice-42'},
    metadata:{
      conditions:{parameters:{VDD:1.8},temperature_c:27,corner:'tt',
        seed:42,sample_index:0,statistical_classification:'none',
        batch_job_id:'job-a',scenario_index:0,
        model_library:{path:'/pdk/models.spice',sha256:'a'.repeat(64)},
        measurement_definitions:{response:{name:'response',kind:'gain',input:'in',output:'out',
          frequency_hz:1000,units:'dB',analysis:'ac',directives:['let ratio = v(out)/v(in)','meas ac response FIND db(ratio) AT=1000']}}},
      generated_files:{'testbenches/pre.cir':'.ac dec 60 1 100Meg\n.end\n'}
    }, ...changes
  };
}
function row(a,b) { return api().compare(a,b).rows.find(item=>item.name==='response'); }

test('selectors omit failed, cancelled, nonfinite and empty runs even with logged metrics',()=>{
  const good=run(), array=run(0,{id:'b',metrics:[{name:'power',value:0,unit:'W'}]});
  assert.deepEqual(api().eligibleRuns([good,array,run(1,{status:'failed'}),run(1,{status:'cancelled'}),
    run(Infinity),run(NaN),run(1,{metrics:{}}),run(1,{metrics:{x:'4'}}),run(1,{kind:'drc'})]),[good,array]);
});

test('consistent conditions and definitions permit design changes without dB percentages',()=>{
  const a=run(), b=run(43,{id:'b'.repeat(32),source_hashes:{'netlists/design.spice':'design-b'},stale:true});
  b.metadata.conditions.batch_job_id='job-b'; b.metadata.conditions.scenario_index=5;
  b.metadata.conditions.seed=999; // A deterministic analysis has no statistical draw.
  const before=JSON.stringify([a,b]);
  assert.deepEqual(row(a,b),{name:'response',unit:'dB',a:40,b:43,delta:3,percent:null,comparable:true,reasons:[]});
  assert.equal(JSON.stringify([a,b]),before,'Comparison must not mutate evidence');
});

test('linear units permit a hand-checked percentage and zero baselines remain undefined',()=>{
  const a=run(2), b=run(3);
  for (const item of [a,b]) item.metadata.conditions.measurement_definitions.response.units='W';
  assert.equal(row(a,b).percent,50);
  a.metrics.response=0;
  assert.equal(row(a,b).delta,3); assert.equal(row(a,b).percent,null);
});

test('PVT, analysis range, model and simulator changes are exposed and prevent deltas',()=>{
  const mutations=[
    b=>b.metadata.conditions.temperature_c=125,
    b=>b.metadata.conditions.corner='ss',
    b=>b.metadata.conditions.parameters.VDD=1.62,
    b=>b.metadata.generated_files['testbenches/pre.cir']='.ac dec 60 1 1Meg\n.end',
    b=>b.metadata.conditions.model_library.sha256='b'.repeat(64),
    b=>b.tool_versions.ngspice='ngspice-43',
    b=>{b.metadata.conditions.statistical_classification='device_mismatch';b.metadata.conditions.seed=43;}
  ];
  for (const mutate of mutations) {
    const a=run(),b=run(43); mutate(b);
    const result=api().compare(a,b), metric=result.rows[0];
    assert.equal(metric.comparable,false); assert.equal(metric.delta,null); assert.equal(metric.percent,null);
    assert.ok(metric.reasons.length); assert.ok(result.differences.length);
  }
});

test('same metric names cannot override differing units or measurement definitions',()=>{
  for (const mutate of [b=>b.metadata.conditions.measurement_definitions.response.units='V',
    b=>b.metadata.conditions.measurement_definitions.response.frequency_hz=10]) {
    const a=run(),b=run(43);mutate(b);
    assert.equal(row(a,b).comparable,false); assert.equal(row(a,b).delta,null);
  }
});

test('two equally undocumented manual runs do not establish comparability',()=>{
  const a=run(),b=run(43);
  for(const item of [a,b]) {
    item.metadata={conditions:{}};
    item.metric_provenance={response:{source:'ngspice.log',definition:{path:'pre.cir',line:5,text:'.meas ac response FIND v(out) AT=1000'},conditions:{}}};
  }
  assert.equal(row(a,b).comparable,false); assert.equal(row(a,b).unit,null);
  assert.ok(row(a,b).reasons.length);
});

test('unknown temperature, supply, corner, analysis or simulator cannot silently match',()=>{
  for(const remove of [r=>delete r.metadata.conditions.temperature_c,
    r=>delete r.metadata.conditions.parameters.VDD,r=>delete r.metadata.conditions.corner,
    r=>delete r.metadata.generated_files,r=>delete r.tool_versions]) {
    const a=run(),b=run(43);remove(a);remove(b);
    assert.equal(row(a,b).comparable,false); assert.equal(row(a,b).delta,null);
  }
});

test('a missing or nonfinite metric is retained as an explained incomparable row',()=>{
  for(const metrics of [{}, {response:Infinity}, {response:{value:null}}]) {
    const item=row(run(),run(1,{metrics}));
    assert.equal(item.comparable,false);assert.equal(item.delta,null);assert.ok(item.reasons.length);
  }
  assert.equal(row(run(),run(43,{status:'failed'})).comparable,false);
});

test('explicit object metrics and top-level definitions use evidence rather than metric names',()=>{
  const a=run(1,{metrics:[{name:'response',result:2,unit:'W'}],metric_definitions:{response:{definition:'Mean supply power',units:'W'}}});
  const b=structuredClone(a); b.metrics[0].result=4;
  for(const item of [a,b]) delete item.metadata.conditions.measurement_definitions;
  assert.equal(row(a,b).delta,2);assert.equal(row(a,b).percent,100);
  b.metrics[0].unit='mW';assert.equal(row(a,b).comparable,false);
});

test('pre/post views and source locations may differ when physical conditions and definitions match',()=>{
  const a=run(),b=run(43);
  for(const [index,item] of [a,b].entries()) {
    item.metadata.conditions.analysis={type:'ac',parameters:{points:60,start:1,stop:1e8}};
    item.metadata.conditions.view=index?'post':'pre';
    delete item.metadata.conditions.measurement_definitions;
    item.metadata.conditions.characterization_definitions={response:{units:'dB',definition:'20 log10(abs(differential gain))'}};
  }
  assert.equal(row(a,b).delta,3);assert.equal(row(a,b).comparable,true);
});

test('finite inputs whose subtraction overflows cannot emit an infinite improvement',()=>{
  const item=row(run(-1e308),run(1e308));
  assert.equal(item.delta,null);assert.equal(item.percent,null);assert.equal(item.comparable,false);
});

test('model identity and contradictory recorded conditions cannot be treated as known equal evidence',()=>{
  const a=run(),b=run(43);
  delete a.metadata.conditions.model_library;delete b.metadata.conditions.model_library;
  assert.equal(row(a,b).comparable,false,'A foundry corner without model identity is not qualified evidence');
  const c=run(),d=run(43);
  d.metric_provenance={response:{conditions:{...d.metadata.conditions,temperature_c:125}}};
  assert.equal(row(c,d).comparable,false,'Conflicting run and measurement conditions require inspection');
});

test('a selected metric can compare despite another measurement using different definitions',()=>{
  const a=run(),b=run(43);
  b.metadata.conditions.measurement_definitions.unrelated={units:'V',definition:'Different metric'};
  assert.equal(row(a,b).comparable,true);
});

test('source path and line locations do not change an otherwise identical measurement definition',()=>{
  const a=run(),b=run(43);
  for(const [index,item] of [a,b].entries()) {
    item.metrics={response:{value:index?43:40,unit:'dB'}};
    delete item.metadata.conditions.measurement_definitions;
    item.metric_provenance={response:{definition:{path:index?'post.cir':'pre.cir',line:index?20:4,
      text:'.meas ac response FIND db(v(out)/v(in)) AT=1000'}}};
  }
  assert.equal(row(a,b).delta,3);
});

test('actual hashed external model dependencies establish identity and transitive changes block comparison',()=>{
  const a=run(),b=run(43);
  for(const item of [a,b]) {
    delete item.metadata.conditions.model_library;
    item.external_dependencies={'/pdk/sky130.lib.spice':'a'.repeat(64),'/pdk/fet.spice':'b'.repeat(64)};
  }
  assert.equal(row(a,b).delta,3);
  b.external_dependencies['/pdk/fet.spice']='c'.repeat(64);
  assert.equal(row(a,b).comparable,false); assert.equal(row(a,b).delta,null);
});

test('contradictory same-name definitions in either run block deltas even when both conflict sets match',()=>{
  for(const field of ['frequency_hz','definition']) {
    const a=run(),b=run(43);
    for(const item of [a,b]) {
      if(field==='definition')item.metadata.conditions.measurement_definitions.response.definition='Gain at 1000 Hz';
      item.metric_definitions={response:field==='frequency_hz'?{frequency_hz:10,units:'dB'}:{definition:'Gain at 10 Hz',units:'dB'}};
    }
    assert.equal(row(a,b).comparable,false);assert.equal(row(a,b).delta,null);
    assert.ok(row(a,b).reasons.includes('Measurement definition evidence is inconsistent.'));
  }
});

test('definitions and units inside all condition sources are checked rather than hidden by metadata priority',()=>{
  for(const source of ['conditions','metric_provenance']) for(const field of ['frequency_hz','units']) {
    const a=run(),b=run(43),contradiction={measurement_definitions:{response:{[field]:field==='units'?'V':10}}};
    if(source==='conditions')b.conditions=contradiction;
    else b.metric_provenance={response:{conditions:contradiction}};
    assert.equal(row(a,b).delta,null,`${source} ${field} conflict must remain visible`);
  }
});

test('compatible prose, parsed and repeated definition evidence merge without artificial conflicts',()=>{
  const a=run(),b=run(43);
  for(const item of [a,b]) {
    item.metadata.conditions.measurement_definitions.response.definition='Differential gain at 1000 Hz';
    item.metric_definitions={response:'Differential gain at 1000 Hz'};
    item.metric_provenance={response:{definition:{path:'pre.cir',line:20,text:'.meas ac response FIND db(v(out)/v(in)) AT=1000'},
      conditions:{measurement_definitions:{response:{frequency_hz:1000,units:'dB'}}}}};
  }
  assert.equal(row(a,b).delta,3);assert.equal(row(a,b).comparable,true);
});

test('unknown and placeholder identities never prove known corner model or simulator equality',()=>{
  for(const sentinel of ['unknown','UNKNOWN','not_checked','unavailable','pending','expected','placeholder','   ']) {
    for(const mutate of [r=>r.metadata.conditions.corner=sentinel,
      r=>r.metadata.conditions.model_library.sha256=sentinel,
      r=>r.tool_versions.ngspice=sentinel,
      r=>r.metadata.conditions.simulator_identity={version:sentinel}]) {
      const a=run(),b=run(43);mutate(a);mutate(b);
      assert.equal(row(a,b).comparable,false,`sentinel ${JSON.stringify(sentinel)} is not identity`);
      assert.equal(row(a,b).delta,null);
    }
  }
});

test('unknown external dependency digests do not establish model identity',()=>{
  const a=run(),b=run(43);
  for(const item of [a,b]){delete item.metadata.conditions.model_library;item.external_dependencies={'/pdk/fet.spice':'unknown'};}
  assert.equal(row(a,b).delta,null);
});

test('reordering batch measurements changes generated temporary names without changing metric semantics',()=>{
  const a=run(),b=run(43);
  for(const [index,item] of [a,b].entries())item.metadata.conditions.measurement_definitions.response.directives=[
    `let af_measure_${index}_ratio = v(out)/v(in)`,
    `let af_measure_${index}_trace = db(af_measure_${index}_ratio)`,
    `meas ac response FIND af_measure_${index}_trace AT=1000`];
  assert.equal(row(a,b).delta,3);assert.equal(row(a,b).percent,null);
  b.metadata.conditions.measurement_definitions.response.directives[2]='meas ac response FIND af_measure_1_trace AT=10';
  assert.equal(row(a,b).delta,null,'normalization cannot erase real frequency changes');
  b.metadata.conditions.measurement_definitions.response.directives[2]='meas ac response FIND af_measure_1_trace AT=1000';
  b.metadata.conditions.measurement_definitions.response.input='af_measure_1_real_input';
  assert.equal(row(a,b).delta,null,'real node names remain significant');
});

test('browser export provides the same pure comparison API',()=>{
  assert.ok(fs.existsSync(modulePath),'The safe result comparison module is missing');
  const context={window:{}};vm.runInNewContext(fs.readFileSync(modulePath,'utf8'),context);
  assert.equal(typeof context.window.AFResultComparison.compare,'function');
  assert.equal(context.window.AFResultComparison.compare(run(),run(43)).rows[0].delta,3);
});

test('incomplete executed-source evidence blocks identical records despite caller-supplied metadata',()=>{
  for(const complete of [false,undefined]) {
    const a=run(),b=run(43);
    for(const item of [a,b])item.metadata.measurement_evidence={complete,conditions:{...item.metadata.conditions},definitions:{}};
    assert.equal(row(a,b).delta,null);
    assert.ok(row(a,b).reasons.includes('Executed source evidence is incomplete or inconsistent.'));
  }
});

test('unknown source-expression semantics cannot be qualified by caller units',()=>{
  const a=run(),b=run(43);
  for(const item of [a,b])item.metadata.measurement_evidence={complete:true,
    conditions:{...item.metadata.conditions},definitions:{response:{limitations:['Unknown expression']}}};
  assert.equal(row(a,b).delta,null);
});

test('derived measurement dependency locations are provenance, not measurement semantics',()=>{
  const a=run(),b=run(43);
  for(const [index,item] of [a,b].entries())item.metadata.conditions.measurement_definitions.response.dependencies={
    voltage:{path:index?'revised.cir':'original.cir',line:index?50:5,text:'.meas tran voltage AVG v(out)',units:'V'}};
  assert.equal(row(a,b).delta,3);
  b.metadata.conditions.measurement_definitions.response.dependencies.voltage.text='.meas tran voltage AVG v(in)';
  assert.equal(row(a,b).delta,null);
});
