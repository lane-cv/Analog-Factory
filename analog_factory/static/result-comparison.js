'use strict';
// Pure evidence comparison shared by the browser and Node contracts. Missing
// metadata never establishes equality; source hashes and pre/post design views
// may differ because comparing design revisions is an intended use case.
(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AFResultComparison = api;
})(typeof window === 'object' ? window : null, function() {
  const own = (value,key) => Boolean(value && Object.prototype.hasOwnProperty.call(value,key));
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const successful = run => run?.kind === 'simulate' && ['succeeded','completed','success','passed'].includes(run.status);
  const unique = values => [...new Set(values)];
  const canonical = value => {
    if (Array.isArray(value)) return value.map(canonical);
    if (object(value)) return Object.fromEntries(Object.keys(value).sort().filter(key=>value[key]!==undefined).map(key=>[key,canonical(value[key])]));
    return typeof value === 'string' ? value.trim() : value;
  };
  const key = value => JSON.stringify(canonical(value));
  const knownText = value => typeof value==='string' && Boolean(value.trim()) &&
    !/^(?:unknown|unavailable|not[_ -](?:checked|available|configured)|n\/a|none|null|undefined|pending|placeholder|expected|unverified|missing)(?:$|[\s:_-])/i.test(value.trim());
  const digest = value => typeof value==='string' && /^[a-f0-9]{64}$/i.test(value.trim());
  const simulatorKnown = value => knownText(value) || (object(value) &&
    (knownText(value.version) || digest(value.sha256)));
  function metrics(run) {
    if (Array.isArray(run?.metrics)) return new Map(run.metrics.filter(item=>item && typeof item.name==='string').map(item=>[item.name,item]));
    return new Map(Object.entries(object(run?.metrics) ? run.metrics : {}));
  }
  function metricValue(metric) {
    const value = object(metric) ? metric.value ?? metric.result ?? null : metric;
    return finite(value) ? value : null;
  }
  function eligibleRuns(runs) {
    return (Array.isArray(runs) ? runs : []).filter(run=>successful(run) && [...metrics(run).values()].some(value=>metricValue(value)!==null));
  }
  const conditionCandidates = (run,name) => [run?.metadata?.measurement_evidence?.conditions,run?.metadata?.conditions,run?.conditions,run?.metric_provenance?.[name]?.conditions]
    .filter(value=>object(value) && Object.keys(value).length);
  const conditions = (run,name) => conditionCandidates(run,name)[0] || {};
  function definitionEvidence(run,name,metric,context) {
    const definitions = [...conditionCandidates(run,name).flatMap(value=>[
      value.measurement_definitions?.[name],value.characterization_definitions?.[name]]),
      run?.metric_definitions?.[name],run?.metadata?.metric_definitions?.[name],
      run?.metadata?.measurement_definitions?.[name],run?.metadata?.characterization_definitions?.[name],
      run?.metric_provenance?.[name]?.definition,
      object(metric) ? metric.definition : null].filter(value=>typeof value==='string' ? value.trim() : object(value));
    const units = unique([object(metric) ? metric.unit : null,object(metric) ? metric.units : null,
      ...definitions.flatMap(value=>object(value) ? [value.unit,value.units] : [])]
      .filter(value=>typeof value==='string' && value.trim()).map(value=>value.trim()));
    // Merge complementary prose and parsed evidence; only contradictory values
    // for the same semantic field establish an internal conflict.
    const semantic={},fields=new Map();
    const generated = value => typeof value==='string' ? value.replace(/\baf_measure_\d+(?=_)/g,'af_measure') : value;
    const dependencySemantics = value => Array.isArray(value) ? value.map(dependencySemantics) :
      object(value) ? Object.fromEntries(Object.entries(value).filter(([field])=>!['path','line','limitations'].includes(field))
        .map(([field,entry])=>[field,dependencySemantics(entry)])) : value;
    for(const definition of definitions) {
      const value=typeof definition==='string'?{definition:definition.trim()}:definition;
      for(const [field,recorded] of Object.entries(value)) {
        if(['unit','units','name','path','line','limitations'].includes(field)||recorded===undefined)continue;
        const normalized=field==='dependencies'?dependencySemantics(recorded):field==='directives'&&Array.isArray(recorded)?recorded.map(generated):
          ['unity_frequency_metric','unity_phase_metric'].includes(field)?generated(recorded):recorded;
        if(!fields.has(field))fields.set(field,new Set());
        fields.get(field).add(key(normalized));semantic[field]=normalized;
      }
    }
    return {unit:units.length===1 ? units[0] : null,units,semantic:Object.keys(semantic).length?[key(semantic)]:[],
      conflict:[...fields.values()].some(values=>values.size>1),definitions};
  }
  function analysisEvidence(run,context,definitions) {
    const declared = context.analysis ?? run?.metadata?.analysis ?? run?.analysis;
    const types = unique([typeof declared==='string' ? declared : declared?.type,
      ...definitions.filter(object).map(value=>value.analysis)].filter(value=>typeof value==='string' && value.trim()).map(value=>value.trim().toLowerCase()));
    const generated = run?.metadata?.generated_files || {};
    const commands = unique(Object.entries(generated).filter(([path,text])=>/\.(cir|spice|sp|ckt)$/i.test(path) && typeof text==='string')
      .flatMap(([,text])=>text.split(/\r?\n/).filter(line=>/^\s*\.(op|ac|dc|tran|noise)\b/i.test(line))
        .map(line=>line.trim().replace(/\s+/g,' ').toLowerCase()))).sort();
    const commandTypes = unique(commands.map(line=>line.match(/^\.(\w+)/)[1]));
    const type = types[0] || commandTypes[0] || null;
    const conflict = types.length>1 || (type && commandTypes.some(value=>value!==type));
    const explicit = object(declared) && object(declared.parameters) ? declared : null;
    const complete = Boolean(type && !conflict && (commands.length || type==='op' || (explicit && Object.keys(explicit.parameters).length)));
    return {complete,value:{type,configuration:explicit,commands}};
  }
  function supplyEvidence(context) {
    if(object(context.dc_sources) && Object.keys(context.dc_sources).length && Object.values(context.dc_sources).every(finite)) return context.dc_sources;
    for (const field of ['supply_v','supply_voltage_v']) if (finite(context[field])) return {[field]:context[field]};
    // These are explicit source parameter names, never inferred metric units.
    const parameters = context.parameters;
    if (object(parameters)) {
      const supplies = Object.fromEntries(Object.entries(parameters).filter(([name,value])=>/^(vdd|vcc|supply)$/i.test(name) && finite(value)));
      if (Object.keys(supplies).length) return supplies;
    }
    return null;
  }
  function physicalConditions(context) {
    const excluded = new Set(['measurement_definitions','characterization_definitions','analysis','view',
      'batch_job_id','workflow_id','scenario_index','simulator_identity']);
    // Deterministic batches retain a seed for bookkeeping, with no random draw.
    if (context.statistical_classification==='none' || context.classification==='deterministic_PVT_not_Monte_Carlo') {
      excluded.add('seed');excluded.add('sample_index');
    }
    return Object.fromEntries(Object.entries(context).filter(([name])=>!excluded.has(name)));
  }
  function conditionEvidence(run,context,definition,name) {
    const analysis = analysisEvidence(run,context,definition.definitions);
    const simulator = context.simulator_identity ?? run?.tool_versions?.ngspice ?? run?.metadata?.tool_versions?.ngspice;
    const physical = physicalConditions(context);
    const library=object(context.model_library) && digest(context.model_library.sha256) ? context.model_library : null;
    const external=object(run?.external_dependencies) && Object.keys(run.external_dependencies).length &&
      Object.values(run.external_dependencies).every(digest) ? run.external_dependencies : null;
    const model={library,external};
    const missing = [];
    const evidence=run?.metadata?.measurement_evidence;
    if(evidence && evidence.complete!==true) missing.push('Executed source evidence is incomplete or inconsistent.');
    if(evidence && (!evidence.definitions?.[name] || evidence.definitions[name].limitations?.length)) missing.push('Measurement expression evidence is incomplete.');
    const candidates=conditionCandidates(run,name).map(physicalConditions),values=new Map();
    for (const candidate of candidates) for (const [field,value] of Object.entries(candidate)) {
      if (!values.has(field)) values.set(field,new Set());
      values.get(field).add(key(value));
    }
    if ([...values.values()].some(entries=>entries.size>1)) missing.push('Simulation condition evidence is inconsistent.');
    if (!finite(context.temperature_c)) missing.push('Temperature evidence is missing.');
    if (!own(context,'corner') || (context.corner!==null && !knownText(context.corner))) missing.push('Corner evidence is missing.');
    if (!supplyEvidence(context)) missing.push('Supply evidence is missing.');
    if (context.corner!==null && !library && !external) {
      missing.push('Model library identity evidence is missing.');
    }
    if (!analysis.complete) missing.push('Analysis configuration evidence is missing or inconsistent.');
    if (!simulatorKnown(simulator)) missing.push('Simulator identity evidence is missing.');
    return {missing,physical,analysis:analysis.value,simulator,model};
  }
  function compare(a,b) {
    const am=metrics(a),bm=metrics(b),differences=[];
    const rows=unique([...am.keys(),...bm.keys()]).map(name=>{
      const av=metricValue(am.get(name)),bv=metricValue(bm.get(name)),reasons=[];
      const ac=conditions(a,name),bc=conditions(b,name);
      const ad=definitionEvidence(a,name,am.get(name),ac),bd=definitionEvidence(b,name,bm.get(name),bc);
      const ae=conditionEvidence(a,ac,ad,name),be=conditionEvidence(b,bc,bd,name);
      if (!successful(a) || !successful(b)) reasons.push('Both executions must be successful simulations.');
      if (av===null || bv===null) reasons.push('A finite measurement is missing from one or both executions.');
      if (!ad.units.length || !bd.units.length) reasons.push('Measurement unit evidence is missing.');
      else if (ad.units.length!==1 || bd.units.length!==1) reasons.push('Measurement unit evidence is inconsistent.');
      else if (ad.unit!==bd.unit) reasons.push('Measurement units differ.');
      if(ad.conflict||bd.conflict)reasons.push('Measurement definition evidence is inconsistent.');
      if (!ad.semantic.length || !bd.semantic.length) reasons.push('Measurement definition evidence is missing.');
      else if (key(ad.semantic)!==key(bd.semantic)) reasons.push('Measurement definitions differ.');
      reasons.push(...ae.missing,...be.missing);
      if (key(ae.physical)!==key(be.physical)) reasons.push('Simulation conditions differ.');
      if (key(ae.analysis)!==key(be.analysis)) reasons.push('Analysis configurations differ.');
      if (key(ae.simulator)!==key(be.simulator)) reasons.push('Simulator identities differ.');
      if (key(ae.model)!==key(be.model)) reasons.push('Model library identities differ.');
      const resolved=unique(reasons);
      let delta=null,percent=null;
      if (!resolved.length) {
        delta=bv-av;
        if (!finite(delta)) {delta=null;resolved.push('The measurement difference exceeds the finite numeric range.');}
        else if (av!==0 && !/^db(?:\b|$)/i.test(ad.unit)) {
          const candidate=delta/Math.abs(av)*100;
          if (finite(candidate)) percent=candidate;
        }
      }
      differences.push(...resolved);
      return {name,unit:ad.unit===bd.unit ? ad.unit : null,a:av,b:bv,delta,percent,comparable:!resolved.length,reasons:resolved};
    });
    if (!rows.length) differences.push('No recorded measurements are available.');
    return {rows,differences:unique(differences)};
  }
  return {eligibleRuns,compare};
});
