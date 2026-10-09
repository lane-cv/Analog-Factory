'use strict';

// Graphical scenario drafts and evidence use the same registry as CLI/MCP jobs.
window.AFSimulationBatches = {
  create({state, toast, onUpdate, onRunSelected, t = value => value}) {
    const operations = window.AFOperations;
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const label = value => esc(t(value));
    const clone = value => JSON.parse(JSON.stringify(value));
    const active = value => ['queued','running'].includes(value?.status);
    let host, dialog, origin = null, generation = 0, revision = 0, busy = false, polling = null;
    let draft, validated = null, capabilities = {}, jobs = [], job = null, error = '', evidence = null;
    const initial = () => ({source:'testbenches/pre.cir',analysis:{type:'ac',parameters:{start:1,stop:1e6,points:40}},
      axes:[],temperatures_c:[27],measurements:[{kind:'gain',name:'gain_db',input:'in',output:'out',frequency_hz:1000}],
      statistics:{kind:'none',samples:1,seed:41,variables:[]},limits:{max_runs:128,runtime_seconds:300,run_timeout:60}});
    const failure = message => { const value = new Error(t(message)); value.code = 'VERSION_CONFLICT'; throw value; };
    function guard(editable = false) {
      if (!dialog?.open || !origin || state.project?.id !== origin) failure('The selected project changed. Reopen advanced simulation.');
      if (editable && (busy || active(job))) throw new Error(t('Wait for the active simulation job or cancel it before editing the draft.'));
      if (state.dirty) failure('Save your editor draft before preparing simulation snapshots.');
    }
    function snapshot() {
      const same = state.project?.id === origin;
      return {open:Boolean(dialog?.open),project_id:same ? origin : null,busy,draft_revision:revision,
        spec:same && draft ? clone(draft) : null,scenario_count:same ? validated?.data.scenario_count || 0 : 0,
        preview_current:Boolean(same && validated && validated.revision === revision),
        job_id:same ? job?.id || null : null,status:same ? job?.status || null : null,
        completed_runs:same ? job?.completed_runs || 0 : 0,active_run:same ? job?.active_run || null : null,
        qualification_jobs:same ? jobs.filter(item => item.qualification?.qualified).map(item => ({id:item.id,kind:item.qualification.kind,section:item.qualification.section,model_semantics:item.qualification.model_semantics || 'installed'})) : [],
        evidence_run_id:same ? evidence?.run_id || null : null,error:same ? error : ''};
    }
    const field = (text, path, value, type = 'text', extra = '') => `<label>${label(text)}<input data-batch-field="${esc(path)}" type="${type}" ${type === 'number' ? 'step="any"' : ''} value="${esc(value ?? '')}" ${extra}></label>`;
    const select = (text,path,value,options) => `<label>${label(text)}<select data-batch-field="${esc(path)}">${options.map(([key,name]) => `<option value="${esc(key)}" ${key === value ? 'selected' : ''}>${label(name)}</option>`).join('')}</select></label>`;
    const values = value => (value || []).join(', ');
    function measurementFields(measure,index) {
      const prefix=`measurements.${index}`;
      let content = select('Measurement',`${prefix}.kind`,measure.kind,[['gain','Gain (dB)'],['bandwidth','Bandwidth (Hz)'],['phase','Phase (degrees)'],['phase_margin','Phase margin (degrees)'],['power','Supply power (W)'],['swing','Output swing (Vpp)']]);
      content += field('Result name',`${prefix}.name`,measure.name);
      if (['gain','bandwidth','phase','phase_margin'].includes(measure.kind)) {
        content += field('Input node',`${prefix}.input`,measure.input) + field('Input negative node (optional)',`${prefix}.input_negative`,measure.input_negative) + field('Output node',`${prefix}.output`,measure.output) + field('Output negative node (optional)',`${prefix}.output_negative`,measure.output_negative);
        if (measure.kind === 'phase_margin') {
          content += select('Loop gain convention',`${prefix}.loop_gain_convention`,measure.loop_gain_convention || '',[['','Declare the measured loop transfer'],['negative_feedback_L','Output/input is L(s) in 1+L(s)=0']]);
          content += `<p class="footnote">${label('Use a loop injection/return testbench with preserved loading. A closed-loop voltage response is not loop gain. The sweep must start above unity with phase between -90 and 90 degrees and include a falling unity crossing.')}</p>`;
        } else content += measure.kind === 'bandwidth' ? field('Reference frequency (Hz)',`${prefix}.reference_hz`,measure.reference_hz ?? 1,'number') + field('Gain drop (dB)',`${prefix}.drop_db`,measure.drop_db ?? 3,'number') : field('Frequency (Hz)',`${prefix}.frequency_hz`,measure.frequency_hz ?? 1000,'number');
      }
      if (measure.kind === 'power') content += field('Supply node',`${prefix}.supply_node`,measure.supply_node) + field('Supply voltage source',`${prefix}.supply_source`,measure.supply_source);
      if (measure.kind === 'swing') content += field('Output node',`${prefix}.output`,measure.output);
      if (draft.analysis.type === 'tran' && ['power','swing'].includes(measure.kind)) content += field('Measure from (s)',`${prefix}.start_s`,measure.start_s ?? 0,'number') + field('Measure to (s)',`${prefix}.stop_s`,measure.stop_s ?? draft.analysis.parameters.stop,'number');
      return `<fieldset class="batch-row"><legend>${label('Measurement')} ${index+1}</legend><div class="form-grid">${content}</div><button type="button" class="secondary-button" data-batch-remove-measure="${index}">${label('Remove measurement')}</button></fieldset>`;
    }
    function body() {
      if (!dialog || !draft) return;
      const p = draft.analysis.parameters || {}, statistics = draft.statistics || {kind:'none'};
      const locked = busy || active(job);
      const analysis = draft.analysis.type === 'ac' ? field('Start frequency (Hz)','analysis.parameters.start',p.start,'number') + field('Stop frequency (Hz)','analysis.parameters.stop',p.stop,'number') + field('Points per decade','analysis.parameters.points',p.points,'number') : draft.analysis.type === 'tran' ? field('Time step (s)','analysis.parameters.step',p.step,'number') + field('Stop time (s)','analysis.parameters.stop',p.stop,'number') : '';
      const axes = draft.axes.map((axis,index) => `<div class="batch-axis form-grid">${field('Parameter',`axes.${index}.parameter`,axis.parameter)}${field('Values (comma-separated)',`axes.${index}.values`,values(axis.values))}<button type="button" class="secondary-button" data-batch-remove-axis="${index}">${label('Remove axis')}</button></div>`).join('');
      const models = (capabilities.libraries || []).map(library => `<option value="${esc(library.path)}">${esc(library.sections.join(', '))}</option>`).join('');
      const variables = (statistics.variables || []).map((variable,index) => `<fieldset class="batch-row"><legend>${label('Engineering parameter')} ${index+1}</legend><div class="form-grid">${field('Parameter',`statistics.variables.${index}.parameter`,variable.parameter)}${select('Distribution',`statistics.variables.${index}.distribution`,variable.distribution,[['uniform','Bounded uniform'],['normal','Bounded normal']])}${field('Minimum',`statistics.variables.${index}.minimum`,variable.minimum,'number')}${field('Maximum',`statistics.variables.${index}.maximum`,variable.maximum,'number')}${variable.distribution === 'normal' ? field('Mean',`statistics.variables.${index}.mean`,variable.mean,'number') + field('Sigma',`statistics.variables.${index}.sigma`,variable.sigma,'number') : ''}</div><button type="button" class="secondary-button" data-batch-remove-variable="${index}">${label('Remove distribution')}</button></fieldset>`).join('');
      const qualified = jobs.filter(item => item.qualification?.qualified && item.qualification.kind === statistics.kind && (item.qualification.model_semantics || 'installed') === (statistics.model_semantics || 'installed'));
      const semanticOptions=(capabilities.model_semantics || [{id:'installed'}]).filter(item=>!item.kinds || item.kinds.includes(statistics.kind)).map(item=>[item.id,item.id==='installed'?'Original model behavior':'Shared global process draws (experimental)']);
      const semanticControl=statistics.kind==='global_process'?select('Model sampling semantics','statistics.model_semantics',statistics.model_semantics || 'installed',semanticOptions)+(statistics.model_semantics==='cache_global_fet_draws'?`<p class="callout warning">${label('The experimental adapter shares global process draws between devices in an isolated snapshot. The installed PDK stays unchanged. Qualification is still required; statistical distributions and yield are not validated.')}</p>`:''):'';
      const qualification = ['global_process','device_mismatch'].includes(statistics.kind) ? `${semanticControl}<p>${label('Qualification checks real matched SKY130 nfet_01v8/pfet_01v8 devices for seed repeatability and the selected correlation. It does not validate foundry distributions or silicon yield.')}</p>${select('Qualification record','statistics.qualification_job',statistics.qualification_job || '',[['','Run qualification first'],...qualified.map(item=>[item.id,`${item.qualification.section} · ${item.id.slice(0,8)}`])])}<button type="button" class="secondary-button" data-batch-action="qualify">${label('Run five qualification probes')}</button><p class="footnote">${label('Select one actual model section above: mc for global process, tt_mm for nominal device mismatch. Model loading can take several minutes. Records must be requalified after the server restarts.')}</p>` : '';
      dialog.innerHTML = `<div class="dialog-form"><div class="card-head"><h2 id="simulation-batch-title">${label('Advanced simulation')}</h2><button type="button" class="icon-button" data-batch-action="close" aria-label="${label('Close')}">×</button></div><p>${label('Each scenario runs a frozen input snapshot. Source files remain unchanged.')}</p><fieldset class="batch-settings" ${locked ? 'disabled' : ''}><legend>${label('Scenario configuration')}</legend><div class="form-grid">${field('Testbench path','source',draft.source)}${select('Analysis','analysis.type',draft.analysis.type,[['ac','AC frequency response'],['tran','Transient'],['op','Operating point']])}${analysis}</div><h3>${label('Parameter sweep')}</h3>${axes}<button type="button" class="secondary-button" data-batch-action="add_axis" ${draft.axes.length >= 3 ? 'disabled' : ''}>${label('Add parameter axis')}</button><p class="footnote">${label('Each axis and supply parameter must have exactly one .param declaration in this testbench. Axes, temperatures, corners and samples form a Cartesian product.')}</p><div class="form-grid">${field('Temperatures (°C, comma-separated)','temperatures_c',values(draft.temperatures_c))}${field('Supply parameter (optional)','supply.parameter',draft.supply?.parameter || '')}${field('Supply values (comma-separated)','supply.values',values(draft.supply?.values))}${field('Model library (optional)','corners.library',draft.corners?.library || '','text','list="batch-model-libraries"')}${field('Model sections (comma-separated)','corners.sections',(draft.corners?.sections || []).join(', '))}</div><datalist id="batch-model-libraries">${models}</datalist><p class="footnote">${label('Corner selection replaces the single top-level .lib declaration with an actual section of the selected project or configured PDK library.')}</p><h3>${label('Measurements')}</h3>${draft.measurements.map(measurementFields).join('')}<button type="button" class="secondary-button" data-batch-action="add_measure" ${draft.measurements.length >= 16 ? 'disabled' : ''}>${label('Add measurement')}</button><p class="footnote">${label('Gain is output/input amplitude in dB. Bandwidth is the first falling reference-gain crossing. Phase is continuous output/input phase, not phase margin. Swing is observed peak-to-peak voltage in the chosen interval.')}</p><h3>${label('Statistical variation')}</h3><div class="form-grid">${select('Variation kind','statistics.kind',statistics.kind,[['none','None'],['engineering_parameter','Engineering parameter perturbation'],['global_process','Qualified global process'],['device_mismatch','Qualified device mismatch']])}${statistics.kind !== 'none' ? field('Samples','statistics.samples',statistics.samples ?? 10,'number','min="1" max="128"') + field('Random seed','statistics.seed',statistics.seed ?? 41,'number','min="1"') : ''}</div>${statistics.kind === 'engineering_parameter' ? `<p>${label('Seeded bounded .param variation is an engineering experiment. It is not foundry Monte Carlo.')}</p>${variables}<button type="button" class="secondary-button" data-batch-action="add_variable" ${(statistics.variables || []).length >= 8 ? 'disabled' : ''}>${label('Add distribution')}</button>` : ''}${qualification}<h3>${label('Execution limits')}</h3><div class="form-grid">${field('Maximum runs','limits.max_runs',draft.limits.max_runs,'number','min="1" max="128"')}${field('Total runtime (seconds)','limits.runtime_seconds',draft.limits.runtime_seconds,'number','min="1" max="1800"')}${field('Each run timeout (seconds)','limits.run_timeout',draft.limits.run_timeout,'number','min="0.1" max="300"')}</div><div class="button-row"><button type="button" class="secondary-button" data-batch-action="preview">${label('Validate and preview')}</button><button type="button" class="primary-button" data-batch-action="start" ${!validated || validated.revision !== revision ? 'disabled' : ''}>${label('Run validated scenarios')}</button></div></fieldset><div data-batch-preview>${validated ? previewHTML() : ''}</div><section data-batch-job>${jobHTML()}</section><p role="alert" data-batch-error>${esc(error)}</p><div class="dialog-actions"><button type="button" class="secondary-button" data-batch-action="refresh">${label('Refresh evidence')}</button><button type="button" class="secondary-button" data-batch-action="close">${label('Close')}</button></div></div>`;
    }
    function previewHTML() {
      return `<h3>${validated.data.scenario_count} ${label('validated scenarios')}</h3><details><summary>${label('Inspect generated input and measurement definitions')}</summary><pre class="evidence-details">${esc(JSON.stringify(validated.data.first_inputs,null,2))}</pre></details><p class="footnote">${label('Running uses this project revision. Concurrent source changes require a new preview.')}</p>`;
    }
    function jobHTML() {
      if (!job) return '';
      const metrics = [...new Set((job.iterations || []).flatMap(item => Object.keys(item.metrics || {})))];
      const rows = (job.iterations || []).map(item => `<tr><td>${Number(item.index)+1}</td><td>${esc(JSON.stringify({parameters:item.conditions?.parameters,temperature_c:item.conditions?.temperature_c,corner:item.conditions?.corner,seed:item.conditions?.seed}))}</td>${metrics.map(name => `<td>${esc(item.metrics?.[name] ?? '—')}</td>`).join('')}<td>${label(item.status)}${item.stale ? ` · ${label('stale')}` : ''}</td><td><button type="button" class="secondary-button" data-batch-inspect="${esc(item.run_id)}">${label('Log and provenance')}</button><button type="button" class="secondary-button" data-batch-waveform="${esc(item.run_id)}">${label('Waveforms')}</button></td></tr>`).join('');
      return `<h3>${label(job.kind === 'statistical-qualification' ? 'Statistical qualification' : 'Batch results')}</h3><p role="status">${label(job.status)} · ${Number(job.completed_runs || 0)} / ${Number(job.scenario_count || 0)} · ${esc(job.termination || '')}</p>${active(job) ? `<button type="button" class="secondary-button" data-batch-action="cancel">${label('Cancel active job')}</button>` : ''}${job.error ? `<p class="callout warning">${esc(job.error)}</p>` : ''}${job.qualification ? `<p>${label(job.qualification.qualified ? 'Statistical behavior qualified' : 'Qualification failed')}</p><pre class="evidence-details">${esc(JSON.stringify(job.qualification.checks,null,2))}</pre>` : ''}<div class="table-wrap"><table class="data-table"><thead><tr><th>${label('Scenario')}</th><th>${label('Conditions')}</th>${metrics.map(name=>`<th>${esc(name)}</th>`).join('')}<th>${label('State')}</th><th>${label('Evidence')}</th></tr></thead><tbody>${rows}</tbody></table></div>${evidence ? `<details open><summary>${label('Run evidence')} · ${esc(evidence.run_id.slice(0,8))}</summary><pre class="evidence-details">${esc(JSON.stringify(evidence.provenance,null,2))}</pre><pre class="evidence-details">${esc(evidence.log)}</pre></details>` : ''}`;
    }
    function render() { if (host) { const button=host.querySelector('button'); if (button) button.disabled=!state.project; } }
    function stopPolling() { if (polling) clearTimeout(polling); polling=null; }
    function schedule() {
      stopPolling(); if (!dialog?.open || !active(job) || state.project?.id !== origin) return;
      polling=setTimeout(()=>report(refresh()),1000);
    }
    function report(promise) {
      return Promise.resolve(promise).catch(failure => {
        error=failure.message;
        const node=dialog?.querySelector('[data-batch-error]'); if (node) node.textContent=error;
        toast?.(failure.message,true);
      });
    }
    async function open(parameters = {}) {
      if (busy) throw new Error(t('Wait for the current request to finish.'));
      if (!state.project) throw new Error(t('Select a project first.'));
      if (state.dirty) failure('Save your editor draft before preparing simulation snapshots.');
      const ticket=++generation; origin=state.project.id; draft=initial(); revision++; validated=null; job=null; evidence=null; error='';
      const result=await Promise.all([operations.invoke('simulation.batch.capabilities',{project_id:origin}),operations.invoke('job.list',{project_id:origin})]);
      if (ticket!==generation || state.project?.id!==origin) failure('Project changed while opening advanced simulation.');
      capabilities=result[0].data; jobs=result[1].data;
      job=parameters.job_id ? jobs.find(item=>item.id===parameters.job_id) : jobs.find(item=>['simulation-batch','statistical-qualification'].includes(item.kind)&&active(item));
      if (parameters.job_id && !job) throw new Error(t('Requested simulation job was not found in this project.'));
      if (job?.spec?.analysis) draft=clone(job.spec);
      body(); if (!dialog.open) dialog.showModal(); schedule(); return snapshot();
    }
    function close() { generation++;stopPolling();dialog?.close();return snapshot(); }
    function set_draft({spec} = {}) {
      guard(true);
      if (!spec || typeof spec!=='object' || Array.isArray(spec) || JSON.stringify(spec).length>100000) throw new Error(t('Provide a bounded simulation specification object.'));
      draft={...initial(),...clone(spec),analysis:{...initial().analysis,...clone(spec.analysis || {})},statistics:{...initial().statistics,...clone(spec.statistics || {})},limits:{...initial().limits,...clone(spec.limits || {})}};
      draft.analysis.parameters = clone(spec.analysis?.parameters || {}); draft.axes ||= []; draft.measurements ||= []; draft.temperatures_c ||= [27];
      revision++; validated=null;error='';body();return snapshot();
    }
    function readySpec() {
      const spec=clone(draft);
      if (!spec.supply?.parameter) delete spec.supply;
      if (!spec.corners?.library) delete spec.corners;
      if (spec.statistics.kind==='none') spec.statistics={kind:'none',samples:1,seed:spec.statistics.seed || 41};
      if (spec.statistics.kind!=='engineering_parameter') delete spec.statistics.variables;
      if (!spec.statistics.qualification_job) delete spec.statistics.qualification_job;
      for (const measure of spec.measurements) for (const key of ['input_negative','output_negative']) if (!measure[key]) delete measure[key];
      return spec;
    }
    async function preview() {
      guard(true);const ticket=generation,version=revision;busy=true;error='';body();
      try {
        const result=await operations.invoke('simulation.batch.preview',{project_id:origin,spec:readySpec()});
        if (ticket!==generation || version!==revision) failure('Simulation draft changed while preparing the preview.');
        guard();validated={...result,revision};return result.data;
      } finally {busy=false;body();}
    }
    async function start() {
      guard(true);
      if (!validated || validated.revision!==revision) throw new Error(t('Validate the current scenario draft before running it.'));
      const pid=origin,ticket=generation;busy=true;error='';body();
      try {
        const result=await operations.invoke('simulation.batch.start',{project_id:pid,spec:validated.data.spec},
          {expected_version:validated.data.source_version,idempotency_key:`batch-${crypto.randomUUID()}`});
        if (ticket===generation && state.project?.id===pid) {job=result.data;validated=null;await onUpdate?.(job);schedule();}
        return result.data;
      } finally {busy=false;body();}
    }
    async function qualify(parameters = {}) {
      guard(true);
      const kind=parameters.kind || draft.statistics.kind, section=parameters.section || draft.corners?.sections?.[0];
      if (!['global_process','device_mismatch'].includes(kind) || !draft.corners?.library || !section || draft.corners.sections.length!==1) throw new Error(t('Select a statistical mode, a model library, and exactly one model section first.'));
      const pid=origin,ticket=generation,version=revision;busy=true;error='';body();
      try {
        const read=await operations.invoke('project.detail',{project_id:pid});
        if (ticket!==generation || version!==revision) failure('Simulation draft changed before qualification.');
        guard();
        const result=await operations.invoke('simulation.statistics.qualify',{project_id:pid,source:draft.source,
          library:draft.corners.library,kind,section,seed:draft.statistics.seed || 41,
          model_semantics:parameters.model_semantics || draft.statistics.model_semantics || 'installed'},
          {expected_version:read.version,idempotency_key:`qualification-${crypto.randomUUID()}`});
        if (ticket===generation && state.project?.id===pid) {job=result.data;validated=null;await onUpdate?.(job);schedule();}
        return result.data;
      } finally {busy=false;body();}
    }
    async function refresh() {
      guard();const ticket=generation,pid=origin,previous=job?.status;
      const results=await Promise.all([operations.invoke('job.list',{project_id:pid}),job ? operations.invoke('job.get',{project_id:pid,job_id:job.id}) : Promise.resolve(null)]);
      if (ticket!==generation || state.project?.id!==pid) failure('Project changed while refreshing simulation evidence.');
      jobs=results[0].data;
      if (results[1]) {
        job=results[1].data;
        if (job.qualification?.qualified && previous!==job.status) {draft.statistics.qualification_job=job.id;revision++;validated=null;}
        await onUpdate?.(job);
      }
      if (previous!==job?.status) body(); else {const section=dialog.querySelector('[data-batch-job]');if(section)section.innerHTML=jobHTML();}
      schedule();return snapshot();
    }
    async function cancel() {
      guard();if (!job || !active(job)) throw new Error(t('There is no active simulation job to cancel.'));
      await operations.invoke('job.cancel',{project_id:origin,job_id:job.id});await refresh();return snapshot();
    }
    async function inspect({run_id} = {}) {
      guard();if (!job?.iterations?.some(item=>item.run_id===run_id)) throw new Error(t('Select a run from this simulation job.'));
      const ticket=generation,pid=origin;
      const results=await Promise.all([operations.invoke('run.get',{project_id:pid,run_id}),operations.invoke('run.log',{project_id:pid,run_id})]);
      if (ticket!==generation || state.project?.id!==pid) failure('Project changed while reading simulation evidence.');
      evidence={run_id,provenance:{conditions:results[0].data.metadata?.conditions,metric_provenance:results[0].data.metric_provenance,
        tool_versions:results[0].data.tool_versions,provenance:results[0].data.provenance,stale:results[0].data.stale},log:results[1].data.content};
      dialog.querySelector('[data-batch-job]').innerHTML=jobHTML();return {run_id};
    }
    function writeField(input) {
      guard(true);
      const path=input.dataset.batchField.split('.'), leaf=path.at(-1);let target=draft;
      for(const key of path.slice(0,-1)) target=target[key] ||= {};
      let value=input.value;
      if (input.type==='number') {value=Number(value);if (!Number.isFinite(value)) throw new Error(t('Enter a finite number.'));}
      else if (leaf==='values' || path[0]==='temperatures_c') value=value.split(',').map(v=>v.trim()).filter(Boolean).map(v=>{const n=Number(v);if(!Number.isFinite(n))throw new Error(t('List values must be finite numbers.'));return n;});
      else if (leaf==='sections') value=value.split(',').map(v=>v.trim()).filter(Boolean);
      target[leaf]=value;revision++;validated=null;error='';
      if (input.dataset.batchField==='analysis.type') {
        draft.analysis.parameters=value==='ac'?{start:1,stop:1e6,points:40}:value==='tran'?{step:2e-6,stop:.005}:{};
        draft.measurements=[value==='ac'?{kind:'gain',name:'gain_db',input:'in',output:'out',frequency_hz:1000}:value==='tran'?{kind:'swing',name:'swing_vpp',output:'out',start_s:0,stop_s:.005}:{kind:'power',name:'power_w',supply_node:'vdd',supply_source:'VDD'}];
      }
      if (input.dataset.batchField==='statistics.kind') {draft.statistics={kind:value,samples:value==='none'?1:10,seed:41,variables:[]};}
      if (input.dataset.batchField==='statistics.model_semantics') delete draft.statistics.qualification_job;
      if (path[0]==='measurements' && leaf==='kind') {draft.measurements[path[1]]={kind:value,name:{gain:'gain_db',bandwidth:'bandwidth_hz',phase:'phase_deg',phase_margin:'phase_margin_deg',power:'power_w',swing:'swing_vpp'}[value],...(['gain','bandwidth','phase','phase_margin'].includes(value)?{input:'in',output:'out'}:value==='power'?{supply_node:'vdd',supply_source:'VDD'}:{output:'out'})};}
      if (input.tagName==='SELECT') body();
      else {dialog.querySelector('[data-batch-action="start"]').disabled=true;dialog.querySelector('[data-batch-preview]').innerHTML='';}
    }
    function mutate(action,index) {
      guard(true);
      if(action==='add_axis' && draft.axes.length<3)draft.axes.push({parameter:'R',values:[1000,2000]});
      if(action==='remove_axis')draft.axes.splice(index,1);
      if(action==='add_measure' && draft.measurements.length<16)draft.measurements.push({kind:'power',name:`power_${draft.measurements.length}`,supply_node:'vdd',supply_source:'VDD'});
      if(action==='remove_measure')draft.measurements.splice(index,1);
      if(action==='add_variable' && draft.statistics.variables.length<8)draft.statistics.variables.push({parameter:'R',distribution:'uniform',minimum:900,maximum:1100});
      if(action==='remove_variable')draft.statistics.variables.splice(index,1);
      revision++;validated=null;body();
    }
    function mount(element) {
      if(host)return;host=element;
      host.innerHTML=`<button type="button" class="secondary-button" data-batch-open>${label('Advanced simulation')}</button>`;
      dialog=document.createElement('dialog');dialog.id='simulation-batch-dialog';dialog.className='simulation-batch-dialog';dialog.setAttribute('aria-labelledby','simulation-batch-title');document.body.append(dialog);
      host.addEventListener('click',()=>report(open()));
      dialog.addEventListener('cancel',()=>{generation++;stopPolling();});
      dialog.addEventListener('change',event=>{if(event.target.dataset.batchField)report(Promise.resolve().then(()=>writeField(event.target)));});
      dialog.addEventListener('click',event=>{
        const button=event.target.closest('button');if(!button || button.disabled)return;
        const action=button.dataset.batchAction;
        const commands={close,preview,start,qualify,cancel,refresh};
        if(commands[action])report(Promise.resolve().then(()=>commands[action]()));
        else if(action?.startsWith('add_'))report(Promise.resolve().then(()=>mutate(action)));
        else if(button.dataset.batchRemoveAxis!==undefined)report(Promise.resolve().then(()=>mutate('remove_axis',Number(button.dataset.batchRemoveAxis))));
        else if(button.dataset.batchRemoveMeasure!==undefined)report(Promise.resolve().then(()=>mutate('remove_measure',Number(button.dataset.batchRemoveMeasure))));
        else if(button.dataset.batchRemoveVariable!==undefined)report(Promise.resolve().then(()=>mutate('remove_variable',Number(button.dataset.batchRemoveVariable))));
        else if(button.dataset.batchInspect)report(inspect({run_id:button.dataset.batchInspect}));
        else if(button.dataset.batchWaveform)report(Promise.resolve().then(async()=>{guard();const runId=button.dataset.batchWaveform;close();await onRunSelected?.(runId);}));
      });render();
    }
    return {mount,render,snapshot,commands:{open,close,set_draft,preview,start,qualify,cancel,refresh,inspect}};
  }
};
