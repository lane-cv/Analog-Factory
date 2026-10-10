'use strict';
// Native fields and acknowledged commands execute the shared characterization job.
window.AFCharacterization = {
  create({state,toast,onUpdate,onRunSelected,t=value=>value}) {
    const ops=window.AFOperations, clone=v=>JSON.parse(JSON.stringify(v));
    const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const label=v=>esc(t(v)),active=()=>['queued','running'].includes(job?.status);
    let host,dialog,origin=null,draft={},job=null,error='',evidence=null,busy=false,loading=false,revision=0,generation=0,poll=null,sourceVersion=null,currentVersion=null,history=[],draftEdited=false;
    const drafts=new Map(),isActive=value=>['queued','running'].includes(value?.status);
    const remember=()=>{if(origin)drafts.set(origin,{spec:clone(draft),edited:draftEdited,sourceVersion});};
    const initial=()=>({views:['pre','post'],cases:[{corner:'tt',supply_v:1.8,temperature_c:27}],analyses:['ac','op','dc','tran','noise'],targets:{gain_db_min:30},limits:{runtime_seconds:1800,run_timeout:180}});
    function guard(edit=false,saved=false) {
      if(!dialog?.open||origin!==state.project?.id)throw new Error(t('Project changed. Reopen OTA characterization.'));
      if((edit||saved)&&state.dirty)throw new Error(t('Save the editor draft before characterization.'));
      if(edit&&(busy||active()))throw new Error(t('Wait for the active request or cancel it first.'));
    }
    const snapshot=()=>{const same=origin===state.project?.id;return {open:Boolean(dialog?.open),project_id:same?origin:null,busy,loading,draft_revision:revision,spec:same?clone(draft):null,source_version:same?sourceVersion:null,job_id:same?job?.id||null:null,status:same?job?.status||null:null,completed_runs:same?job?.completed_runs||0:0,active_run:same?job?.active_run||null:null,error:same?error:''};};
    function report(value) {const ticket=generation;return Promise.resolve(value).catch(failure=>{if(ticket!==generation)return;error=failure.message;body();toast?.(error,true);});}
    function schedule(){clearTimeout(poll);if(!loading&&dialog?.open&&active()&&origin===state.project?.id)poll=setTimeout(()=>report(refresh()),1000);}
    function results(){
      if(!job)return '';
      const metrics={gain_db:['Gain','dB'],bandwidth_hz:['Bandwidth','Hz'],power_w:['Supply power','W'],output_dc_v:['DC output','V'],offset_v:['Input offset','V'],settling_s:['Settling time','s'],noise_v_rms:['Output noise','V RMS']};
      const targets={gain_db_min:['gain_db','min'],bandwidth_hz_min:['bandwidth_hz','min'],power_w_max:['power_w','max'],offset_v_abs_max:['offset_v','abs_max'],settling_s_max:['settling_s','max'],noise_v_rms_max:['noise_v_rms','max']};
      const finite=value=>typeof value==='number'&&Number.isFinite(value),number=value=>finite(value)?String(Number(value.toPrecision(6))):t('unknown');
      const conditions=(view,c={})=>`${esc(view)} · ${esc(c.corner??t('unknown'))} · ${esc(c.supply_v??t('unknown'))} V · ${esc(c.temperature_c??t('unknown'))} °C`;
      const planned=(job.spec?.views||[]).flatMap(view=>(job.spec?.cases||[]).map(c=>({view,conditions:c})));
      const summaries=Object.entries(job.spec?.targets||{}).filter(([target])=>targets[target]).map(([target,limit])=>{
        const [name,comparison]=targets[target],measured=[];
        for(const expected of planned){
          const matching=(job.groups||[]).filter(group=>group.view===expected.view&&
            ['corner','supply_v','temperature_c'].every(key=>group.conditions?.[key]===expected.conditions[key]));
          if(matching.length!==1)continue;
          const group=matching[0],value=group.metrics?.[name],check=group.assessment?.checks?.[target];
          if(!finite(value)||!finite(limit)||!check||check.value!==value||check.limit!==limit||!['pass','fail'].includes(check.status))continue;
          const margin=comparison==='min'?value-limit:limit-(comparison==='abs_max'?Math.abs(value):value);
          if(finite(margin))measured.push({group,margin});
        }
        const worst=measured.reduce((current,item)=>!current||item.margin<current.margin?item:current,null);
        const status=!planned.length||measured.length!==planned.length?'unknown':worst.margin<0?'fail':'pass';
        return {target,name,limit,comparison,measured:measured.length,total:planned.length,worst,status};
      });
      const assessedStatus=!summaries.length?'observed':summaries.some(item=>item.status==='unknown')?'unknown':summaries.some(item=>item.status==='fail')?'fail':'pass';
      const electricalStatus=assessedStatus==='pass'&&job.electrical_status!=='pass'?job.electrical_status||'unknown':assessedStatus;
      const summary=`<h3>${label('Worst condition summary')}</h3><p>${label('Coverage counts requested pre/post and PVT conditions with measured target evidence. Missing conditions keep the summary unknown.')}</p><div class="table-wrap"><table class="data-table"><thead><tr>${['Measurement','Unit','Target','Worst measured margin','Worst measured condition','Measured coverage','Result'].map(name=>`<th>${label(name)}</th>`).join('')}</tr></thead><tbody>${summaries.map(item=>`<tr data-characterize-summary="${esc(item.target)}" data-characterize-summary-result="${item.status}"><td>${label(metrics[item.name]?.[0]||item.name)}</td><td>${esc(metrics[item.name]?.[1]||'')}</td><td>${item.comparison==='min'?'≥':item.comparison==='abs_max'?'|x| ≤':'≤'} ${esc(number(item.limit))}</td><td>${esc(number(item.worst?.margin))}</td><td>${item.worst?conditions(item.worst.group.view,item.worst.group.conditions):label('unknown')}</td><td>${item.measured} / ${item.total}</td><td>${label(item.status)}</td></tr>`).join('')||`<tr><td colspan="7">${label('No electrical targets were specified; measurements are observations.')}</td></tr>`}</tbody></table></div>`;
      const rows=(job.groups||[]).flatMap(group=>{
        const checks=group.assessment?.checks||{},names=new Set(Object.keys(group.metrics||{}));
        for(const key of new Set([...Object.keys(job.spec?.targets||{}),...Object.keys(checks)]))if(targets[key])names.add(targets[key][0]);
        return [...names].map(name=>{
          const target=Object.keys(targets).find(key=>targets[key][0]===name&&(key in checks||key in (job.spec?.targets||{}))),check=checks[target],comparison=targets[target]?.[1];
          const value=group.metrics?.[name],valid=finite(value),limit=check?.limit??job.spec?.targets?.[target];
          const known=valid&&finite(limit)&&check&&finite(check.value)&&check.value===value;
          const status=target?(known&&['pass','fail'].includes(check.status)?check.status:'unknown'):(valid?'observed':'unknown');
          const margin=known?(comparison==='min'?value-limit:limit-(comparison==='abs_max'?Math.abs(value):value)):null;
          return `<tr data-characterize-metric="${esc(name)}" data-characterize-result="${esc(status)}"><td>${conditions(group.view,group.conditions)}</td><td>${label(metrics[name]?.[0]||name)}</td><td>${esc(number(value))}</td><td>${esc(metrics[name]?.[1]||'')}</td><td>${target?`${comparison==='min'?'≥':comparison==='abs_max'?'|x| ≤':'≤'} ${esc(number(limit))}`:label('Observe only')}</td><td>${target?esc(number(margin)):'—'}</td><td>${label(status)}</td></tr>`;
        });
      }).join('');
      // This label is scoped to the project version, not external PDK revalidation.
      const freshness=!job.source_version||!currentVersion?'unknown':job.source_version!==currentVersion||(job.iterations||[]).some(item=>item.stale===true)?'stale':'current';
      return `<p>${label('Execution')}: ${label(job.status)} · ${job.completed_runs||0}/${job.scenario_count||0} · <span data-characterize-electrical-status="${esc(electricalStatus)}">${label('Electrical targets')}: ${label(electricalStatus)}</span></p><p data-characterize-currentness="${freshness}">${label('Source version')}: <code>${esc(job.source_version||t('unknown'))}</code> · ${label('Project source status at last refresh')}: ${label(freshness)}</p>${job.error?`<p role="alert">${esc(job.error)}</p>`:''}<p>${label('Positive margin meets the target; negative margin misses it. Missing measurements remain unknown.')}</p>${summary}<h3>${label('Condition details')}</h3><div class="table-wrap"><table class="data-table"><thead><tr>${['Conditions','Measurement','Value','Unit','Target','Margin','Result'].map(name=>`<th>${label(name)}</th>`).join('')}</tr></thead><tbody>${rows||`<tr><td colspan="7">${label('No measured characterization results yet.')}</td></tr>`}</tbody></table></div><details><summary>${label('Simulation runs and evidence')}</summary><div class="table-wrap"><table class="data-table"><thead><tr><th>${label('Conditions')}</th><th>${label('Measurements')}</th><th>${label('Evidence')}</th></tr></thead><tbody>${(job.iterations||[]).map(item=>`<tr><td>${conditions(item.conditions?.view,item.conditions)} · ${esc(item.conditions?.analysis)}</td><td><pre>${esc(JSON.stringify(item.measurements||{},null,2))}</pre>${esc(item.measurement_error||item.status)}</td><td><button type="button" data-characterize-inspect="${esc(item.run_id)}">${label('Inspect run')}</button><button type="button" data-characterize-waveform="${esc(item.run_id)}">${label('Waveforms')}</button></td></tr>`).join('')}</tbody></table></div></details><details><summary>${label('Raw evidence and provenance')}</summary><pre>${esc(JSON.stringify({job,...(evidence?{selected_run:evidence}:{})},null,2))}</pre></details>`;
    }
    function body(){
      if(!dialog)return;const locked=busy||active();
      const selections=(key,values)=>values.map(value=>`<label><input type="checkbox" data-characterize-select="${key}" value="${value}" ${draft[key]?.includes(value)?'checked':''}>${label(value)}</label>`).join('');
      const targets=[['gain_db_min','Minimum gain (dB)'],['bandwidth_hz_min','Minimum bandwidth (Hz)'],['power_w_max','Maximum supply power (W)'],['offset_v_abs_max','Maximum absolute offset (V)'],['settling_s_max','Maximum settling time (s)'],['noise_v_rms_max','Maximum output noise (V RMS)']];
      dialog.innerHTML=`<div class="dialog-form"><div class="card-head"><h2 id="characterization-title">${label('OTA characterization')}</h2><button type="button" data-characterize-action="close" aria-label="${label('Close')}">×</button></div><div class="form-grid"><label>${label('Characterization history')}<select data-characterize-history><option value="" ${!job?'selected':''}>${label('New characterization')}</option>${history.map(item=>`<option value="${esc(item.id)}" ${job?.id===item.id?'selected':''}>${esc(item.created_at||item.id)} · ${label(item.status)} · ${label(item.electrical_status||'unknown')}</option>`).join('')}</select></label><button type="button" data-characterize-action="new_draft" ${busy?'disabled':''}>${label('New characterization')}</button></div><p>${label('Run saved pre/post OTA sources across explicit PVT conditions. Measurements are observations unless you specify targets. Deterministic corners are not Monte Carlo or silicon yield.')}</p><p class="footnote">${label('The reference retains its 0.9 V input common mode, 0.65 V bias and 1 pF load. Supply bounds are input limits, not a safe operating claim. Offset is the input for output VDD/2; settling is an observed open-loop step within 50 µs.')}</p>${job?`<p class="footnote">${label('Historical results use recorded targets. Draft settings apply to the next job.')}</p>`:''}<fieldset ${locked?'disabled':''}><legend>${label('Views and analyses')}</legend><div class="form-grid">${selections('views',['pre','post'])}${selections('analyses',['ac','op','dc','tran','noise'])}</div></fieldset><fieldset ${locked?'disabled':''}><legend>${label('PVT cases')}</legend>${(draft.cases||[]).map((c,i)=>`<div class="form-grid"><label>${label('Corner')}<select data-characterize-case="${i}" data-key="corner">${['tt','ff','ss','fs','sf'].map(v=>`<option ${c.corner===v?'selected':''}>${v}</option>`).join('')}</select></label><label>${label('Supply (V)')}<input type="number" step="any" min="1.2" max="1.95" data-characterize-case="${i}" data-key="supply_v" value="${esc(c.supply_v)}"></label><label>${label('Temperature (°C)')}<input type="number" step="any" min="-40" max="125" data-characterize-case="${i}" data-key="temperature_c" value="${esc(c.temperature_c)}"></label><button type="button" data-characterize-remove="${i}" ${draft.cases.length<=1?'disabled':''}>${label('Remove')}</button></div>`).join('')}<button type="button" data-characterize-action="add" ${draft.cases?.length>=12?'disabled':''}>${label('Add case')}</button><div class="form-grid">${targets.map(([key,name])=>`<label>${label(name)}<input type="number" min="0" step="any" data-characterize-target="${key}" value="${esc(draft.targets?.[key]??'')}" placeholder="${label('Observe only')}"></label>`).join('')}</div></fieldset><p class="footnote">${label('New characterization reviews the current project sources and keeps your prepared settings.')}</p>${loading?`<p role="status">${label('Loading characterization history…')}</p>`:''}<p role="alert">${esc(error)}</p><div class="dialog-actions"><button type="button" data-characterize-action="start" ${locked||loading?'disabled':''}>${label('Run characterization')}</button><button type="button" data-characterize-action="cancel" ${!active()?'disabled':''}>${label('Cancel job')}</button><button type="button" data-characterize-action="refresh">${label('Refresh')}</button></div><section data-characterize-results>${results()}</section></div>`;
    }
    async function open({job_id,new_job=false}={}){
      if(busy||!state.project)throw new Error(t('Select a project and wait for the current request.'));
      if(new_job&&job_id)throw new Error(t('Choose a historical job or a new characterization.'));
      if(origin!==state.project.id){remember();origin=state.project.id;const saved=drafts.get(origin);draft=clone(saved?.spec||initial());draftEdited=Boolean(saved?.edited);sourceVersion=saved?.sourceVersion||null;job=null;revision++;}
      const pid=origin,ticket=++generation,rev=revision;clearTimeout(poll);error='';loading=true;if(dialog.open)body();
      try {
      const [read,listed]=await Promise.all([ops.invoke('project.detail',{project_id:pid}),ops.invoke('job.list',{project_id:pid})]);
      if(ticket!==generation||pid!==state.project?.id)throw new Error(t('Project or selection changed while opening characterization.'));
      const jobs=listed.data.filter(j=>j.kind==='ota-characterization').sort((a,b)=>String(b.created_at||'').localeCompare(String(a.created_at||''))||b.id.localeCompare(a.id));
      const selected=new_job?null:job_id?jobs.find(j=>j.id===job_id):jobs.find(isActive)||jobs[0];
      if(job_id&&!selected)throw new Error(t('Characterization job not found in this project.'));
      const loaded=selected?await ops.invoke('job.get',{project_id:pid,job_id:selected.id}):null;
      if(ticket!==generation||pid!==state.project?.id)throw new Error(t('Project or selection changed while opening characterization.'));
      currentVersion=read.version;history=jobs;job=loaded?.data||null;evidence=null;
      if(rev===revision&&!draftEdited){if(job?.spec)draft=clone(job.spec);sourceVersion=read.version;revision++;}
      if(!sourceVersion||new_job)sourceVersion=read.version;
      if(new_job){draftEdited=true;revision++;}
      body();if(!dialog.open)dialog.showModal();
      } finally {if(ticket===generation){loading=false;body();schedule();}}
      return snapshot();
    }
    function close(){remember();generation++;loading=false;clearTimeout(poll);dialog?.close();return snapshot();}
    const new_draft=()=>open({new_job:true});
    function set_draft({spec}){guard(true);draft=clone(spec);draftEdited=true;revision++;error='';body();return snapshot();}
    async function start(){if(loading)throw new Error(t('Wait for characterization history to finish loading.'));guard(true);busy=true;error='';body();const pid=origin,ticket=generation,rev=revision,spec=clone(draft);
      try{guard(false,true);if(ticket!==generation||rev!==revision)throw new Error(t('Characterization draft changed.'));
        const result=await ops.invoke('reference.characterize',{project_id:pid,spec},{expected_version:sourceVersion,idempotency_key:`characterize-${crypto.randomUUID()}`});
        if(ticket!==generation||pid!==state.project?.id)throw new Error(t('Project changed after starting characterization.'));
        job=result.data;history=[job,...history.filter(item=>item.id!==job.id)];await onUpdate?.(job);schedule();return job;
      }finally{busy=false;body();}}
    async function refresh(){guard();if(!job)return snapshot();const pid=origin,ticket=generation,id=job.id;const [result,read]=await Promise.all([ops.invoke('job.get',{project_id:pid,job_id:id}),ops.invoke('project.detail',{project_id:pid})]);if(ticket!==generation||pid!==state.project?.id||id!==job?.id)throw new Error(t('Project or selection changed while refreshing.'));job=result.data;currentVersion=read.version;history=history.map(item=>item.id===id?job:item);await onUpdate?.(job);body();schedule();return snapshot();}
    async function cancel(){guard();if(!active())throw new Error(t('No active characterization job.'));const pid=origin,id=job.id,ticket=generation;await ops.invoke('job.cancel',{project_id:pid,job_id:id});if(ticket!==generation||pid!==state.project?.id||id!==job?.id)throw new Error(t('Project or selection changed while refreshing.'));return refresh();}
    async function inspect({run_id}){guard();if(!job?.iterations?.some(i=>i.run_id===run_id))throw new Error(t('Select a run from this job.'));const pid=origin,ticket=generation;const [run,log]=await Promise.all([ops.invoke('run.get',{project_id:pid,run_id}),ops.invoke('run.log',{project_id:pid,run_id})]);if(ticket!==generation||pid!==state.project?.id)throw new Error(t('Project changed while inspecting.'));evidence={run_id,run:run.data,log:log.data.content};body();return {run_id};}
    function render(){if(host)host.querySelector('button').disabled=!state.project;if(origin&&origin!==state.project?.id){remember();generation++;loading=false;clearTimeout(poll);dialog?.close();currentVersion=null;}}
    function mount(element){if(host)return;host=element;host.innerHTML=`<button type="button" class="secondary-button">${label('OTA characterization')}</button>`;dialog=document.createElement('dialog');dialog.id='characterization-dialog';dialog.className='simulation-batch-dialog';dialog.setAttribute('aria-labelledby','characterization-title');document.body.append(dialog);host.addEventListener('click',()=>report(open()));dialog.addEventListener('cancel',()=>{remember();generation++;loading=false;clearTimeout(poll);});
      dialog.addEventListener('change',event=>{const input=event.target;if(input.matches?.('[data-characterize-history]')||input.dataset.characterizeHistory!==undefined){report(input.value?open({job_id:input.value}):new_draft());return;}report(Promise.resolve().then(()=>{guard(true);
        if(input.dataset.characterizeSelect){const key=input.dataset.characterizeSelect;draft[key]=input.checked?[...draft[key],input.value]:draft[key].filter(v=>v!==input.value);}
        if(input.dataset.characterizeCase!==undefined){draft.cases[Number(input.dataset.characterizeCase)][input.dataset.key]=input.dataset.key==='corner'?input.value:Number(input.value);}
        if(input.dataset.characterizeTarget){draft.targets||={};if(input.value==='')delete draft.targets[input.dataset.characterizeTarget];else draft.targets[input.dataset.characterizeTarget]=Number(input.value);}
        draftEdited=true;revision++;error='';}));});
      dialog.addEventListener('click',event=>{const button=event.target.closest('button');if(!button||button.disabled)return;const action=button.dataset.characterizeAction;
        const commands={open,close,start,refresh,cancel,new_draft};if(commands[action])report(Promise.resolve().then(()=>commands[action]()));
        else if(action==='add')report(Promise.resolve().then(()=>{guard(true);draft.cases.push({corner:'tt',supply_v:1.8,temperature_c:27});draftEdited=true;revision++;body();}));
        else if(button.dataset.characterizeRemove!==undefined)report(Promise.resolve().then(()=>{guard(true);draft.cases.splice(Number(button.dataset.characterizeRemove),1);draftEdited=true;revision++;body();}));
        else if(button.dataset.characterizeInspect)report(inspect({run_id:button.dataset.characterizeInspect}));
        else if(button.dataset.characterizeWaveform)report(Promise.resolve().then(async()=>{guard();const runId=button.dataset.characterizeWaveform;close();await onRunSelected?.(runId);}));
      });render();}
    return {mount,render,snapshot,commands:{open,close,set_draft,start,refresh,cancel,inspect}};
  }
};
