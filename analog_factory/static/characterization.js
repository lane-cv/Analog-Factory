'use strict';
// Native fields and acknowledged commands execute the shared characterization job.
window.AFCharacterization = {
  create({state,toast,onUpdate,onRunSelected,t=value=>value}) {
    const ops=window.AFOperations, clone=v=>JSON.parse(JSON.stringify(v));
    const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const label=v=>esc(t(v)),active=()=>['queued','running'].includes(job?.status);
    let host,dialog,origin=null,draft={},job=null,error='',evidence=null,busy=false,revision=0,generation=0,poll=null,sourceVersion=null;
    const initial=()=>({views:['pre','post'],cases:[{corner:'tt',supply_v:1.8,temperature_c:27}],analyses:['ac','op','dc','tran','noise'],targets:{gain_db_min:30},limits:{runtime_seconds:1800,run_timeout:180}});
    function guard(edit=false,saved=false) {
      if(!dialog?.open||origin!==state.project?.id)throw new Error(t('Project changed. Reopen OTA characterization.'));
      if((edit||saved)&&state.dirty)throw new Error(t('Save the editor draft before characterization.'));
      if(edit&&(busy||active()))throw new Error(t('Wait for the active request or cancel it first.'));
    }
    const snapshot=()=>{const same=origin===state.project?.id;return {open:Boolean(dialog?.open),project_id:same?origin:null,busy,draft_revision:revision,spec:same?clone(draft):null,source_version:same?sourceVersion:null,job_id:same?job?.id||null:null,status:same?job?.status||null:null,completed_runs:same?job?.completed_runs||0:0,active_run:same?job?.active_run||null:null,error:same?error:''};};
    function report(value) {return Promise.resolve(value).catch(failure=>{error=failure.message;body();toast?.(error,true);});}
    function schedule(){clearTimeout(poll);if(dialog?.open&&active()&&origin===state.project?.id)poll=setTimeout(()=>report(refresh()),1000);}
    function results(){
      if(!job)return '';
      return `<p>${label('Execution')}: ${esc(job.status)} · ${job.completed_runs||0}/${job.scenario_count||0} · ${label('Electrical targets')}: ${esc(job.electrical_status||'pending')}</p><div class="table-wrap"><table class="data-table"><thead><tr><th>${label('Conditions')}</th><th>${label('Measurements')}</th><th>${label('Evidence')}</th></tr></thead><tbody>${(job.iterations||[]).map(item=>`<tr><td>${esc(item.conditions.view)} · ${esc(item.conditions.analysis)} · ${esc(item.conditions.corner)} · ${esc(item.conditions.supply_v)} V · ${esc(item.conditions.temperature_c)} °C</td><td><pre>${esc(JSON.stringify(item.measurements||{},null,2))}</pre>${esc(item.measurement_error||item.status)}</td><td><button type="button" data-characterize-inspect="${esc(item.run_id)}">${label('Inspect run')}</button><button type="button" data-characterize-waveform="${esc(item.run_id)}">${label('Waveforms')}</button></td></tr>`).join('')}</tbody></table></div>${job.groups?`<details><summary>${label('Explicit specification checks')}</summary><pre>${esc(JSON.stringify(job.groups,null,2))}</pre></details>`:''}${evidence?`<details open><summary>${label('Raw evidence and provenance')}</summary><pre>${esc(JSON.stringify(evidence,null,2))}</pre></details>`:''}`;
    }
    function body(){
      if(!dialog)return;const locked=busy||active();
      const selections=(key,values)=>values.map(value=>`<label><input type="checkbox" data-characterize-select="${key}" value="${value}" ${draft[key]?.includes(value)?'checked':''}>${label(value)}</label>`).join('');
      const targets=[['gain_db_min','Minimum gain (dB)'],['bandwidth_hz_min','Minimum bandwidth (Hz)'],['power_w_max','Maximum supply power (W)'],['offset_v_abs_max','Maximum absolute offset (V)'],['settling_s_max','Maximum settling time (s)'],['noise_v_rms_max','Maximum output noise (V RMS)']];
      dialog.innerHTML=`<div class="dialog-form"><div class="card-head"><h2 id="characterization-title">${label('OTA characterization')}</h2><button type="button" data-characterize-action="close" aria-label="${label('Close')}">×</button></div><p>${label('Run saved pre/post OTA sources across explicit PVT conditions. Measurements are observations unless you specify targets. Deterministic corners are not Monte Carlo or silicon yield.')}</p><p class="footnote">${label('The reference retains its 0.9 V input common mode, 0.65 V bias and 1 pF load. Supply bounds are input limits, not a safe operating claim. Offset is the input for output VDD/2; settling is an observed open-loop step within 50 µs.')}</p><fieldset ${locked?'disabled':''}><legend>${label('Views and analyses')}</legend><div class="form-grid">${selections('views',['pre','post'])}${selections('analyses',['ac','op','dc','tran','noise'])}</div></fieldset><fieldset ${locked?'disabled':''}><legend>${label('PVT cases')}</legend>${(draft.cases||[]).map((c,i)=>`<div class="form-grid"><label>${label('Corner')}<select data-characterize-case="${i}" data-key="corner">${['tt','ff','ss','fs','sf'].map(v=>`<option ${c.corner===v?'selected':''}>${v}</option>`).join('')}</select></label><label>${label('Supply (V)')}<input type="number" step="any" min="1.2" max="1.95" data-characterize-case="${i}" data-key="supply_v" value="${esc(c.supply_v)}"></label><label>${label('Temperature (°C)')}<input type="number" step="any" min="-40" max="125" data-characterize-case="${i}" data-key="temperature_c" value="${esc(c.temperature_c)}"></label><button type="button" data-characterize-remove="${i}" ${draft.cases.length<=1?'disabled':''}>${label('Remove')}</button></div>`).join('')}<button type="button" data-characterize-action="add" ${draft.cases?.length>=12?'disabled':''}>${label('Add case')}</button><div class="form-grid">${targets.map(([key,name])=>`<label>${label(name)}<input type="number" min="0" step="any" data-characterize-target="${key}" value="${esc(draft.targets?.[key]??'')}" placeholder="${label('Observe only')}"></label>`).join('')}</div></fieldset><p role="alert">${esc(error)}</p><div class="dialog-actions"><button type="button" data-characterize-action="start" ${locked?'disabled':''}>${label('Run characterization')}</button><button type="button" data-characterize-action="cancel" ${!active()?'disabled':''}>${label('Cancel job')}</button><button type="button" data-characterize-action="refresh">${label('Refresh')}</button></div><section data-characterize-results>${results()}</section></div>`;
    }
    async function open({job_id}={}){
      if(busy||!state.project)throw new Error(t('Select a project and wait for the current request.'));
      if(state.dirty)throw new Error(t('Save the editor draft before characterization.'));
      origin=state.project.id;const ticket=++generation;draft=initial();job=null;evidence=null;error='';revision++;
      const [read,listed]=await Promise.all([ops.invoke('project.detail',{project_id:origin}),ops.invoke('job.list',{project_id:origin})]);const jobs=listed.data;
      if(ticket!==generation||origin!==state.project?.id)throw new Error(t('Project changed while opening characterization.'));
      sourceVersion=read.version;
      job=job_id?jobs.find(j=>j.id===job_id&&j.kind==='ota-characterization'):jobs.find(j=>j.kind==='ota-characterization'&&['queued','running'].includes(j.status));
      if(job_id&&!job)throw new Error(t('Characterization job not found in this project.'));
      if(job?.spec)draft=clone(job.spec);body();dialog.showModal();schedule();return snapshot();
    }
    function close(){generation++;clearTimeout(poll);dialog?.close();return snapshot();}
    function set_draft({spec}){guard(true);draft=clone(spec);revision++;error='';body();return snapshot();}
    async function start(){guard(true);busy=true;error='';body();const pid=origin,ticket=generation,rev=revision,spec=clone(draft);
      try{guard(false,true);if(ticket!==generation||rev!==revision)throw new Error(t('Characterization draft changed.'));
        const result=await ops.invoke('reference.characterize',{project_id:pid,spec},{expected_version:sourceVersion,idempotency_key:`characterize-${crypto.randomUUID()}`});
        if(ticket!==generation||pid!==state.project?.id)throw new Error(t('Project changed after starting characterization.'));
        job=result.data;await onUpdate?.(job);schedule();return job;
      }finally{busy=false;body();}}
    async function refresh(){guard();if(!job)return snapshot();const pid=origin,ticket=generation;const result=await ops.invoke('job.get',{project_id:pid,job_id:job.id});if(ticket!==generation||pid!==state.project?.id)throw new Error(t('Project changed while refreshing.'));job=result.data;await onUpdate?.(job);body();schedule();return snapshot();}
    async function cancel(){guard();if(!active())throw new Error(t('No active characterization job.'));await ops.invoke('job.cancel',{project_id:origin,job_id:job.id});return refresh();}
    async function inspect({run_id}){guard();if(!job?.iterations?.some(i=>i.run_id===run_id))throw new Error(t('Select a run from this job.'));const pid=origin,ticket=generation;const [run,log]=await Promise.all([ops.invoke('run.get',{project_id:pid,run_id}),ops.invoke('run.log',{project_id:pid,run_id})]);if(ticket!==generation||pid!==state.project?.id)throw new Error(t('Project changed while inspecting.'));evidence={run_id,run:run.data,log:log.data.content};body();return {run_id};}
    function render(){if(host)host.querySelector('button').disabled=!state.project;}
    function mount(element){if(host)return;host=element;host.innerHTML=`<button type="button" class="secondary-button">${label('OTA characterization')}</button>`;dialog=document.createElement('dialog');dialog.id='characterization-dialog';dialog.className='simulation-batch-dialog';dialog.setAttribute('aria-labelledby','characterization-title');document.body.append(dialog);host.addEventListener('click',()=>report(open()));dialog.addEventListener('cancel',()=>{generation++;clearTimeout(poll);});
      dialog.addEventListener('change',event=>report(Promise.resolve().then(()=>{guard(true);const input=event.target;
        if(input.dataset.characterizeSelect){const key=input.dataset.characterizeSelect;draft[key]=input.checked?[...draft[key],input.value]:draft[key].filter(v=>v!==input.value);}
        if(input.dataset.characterizeCase!==undefined){draft.cases[Number(input.dataset.characterizeCase)][input.dataset.key]=input.dataset.key==='corner'?input.value:Number(input.value);}
        if(input.dataset.characterizeTarget){draft.targets||={};if(input.value==='')delete draft.targets[input.dataset.characterizeTarget];else draft.targets[input.dataset.characterizeTarget]=Number(input.value);}
        revision++;error='';})));
      dialog.addEventListener('click',event=>{const button=event.target.closest('button');if(!button||button.disabled)return;const action=button.dataset.characterizeAction;
        const commands={open,close,start,refresh,cancel};if(commands[action])report(Promise.resolve().then(()=>commands[action]()));
        else if(action==='add')report(Promise.resolve().then(()=>{guard(true);draft.cases.push({corner:'tt',supply_v:1.8,temperature_c:27});revision++;body();}));
        else if(button.dataset.characterizeRemove!==undefined)report(Promise.resolve().then(()=>{guard(true);draft.cases.splice(Number(button.dataset.characterizeRemove),1);revision++;body();}));
        else if(button.dataset.characterizeInspect)report(inspect({run_id:button.dataset.characterizeInspect}));
        else if(button.dataset.characterizeWaveform)report(Promise.resolve().then(async()=>{guard();const runId=button.dataset.characterizeWaveform;close();await onRunSelected?.(runId);}));
      });render();}
    return {mount,render,snapshot,commands:{open,close,set_draft,start,refresh,cancel,inspect}};
  }
};
