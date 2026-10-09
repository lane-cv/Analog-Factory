'use strict';

window.AFTrainingControls = {
  create({state, toast, onUpdate, t = value => value}) {
    const api = window.AFOperations;
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const label = value => esc(t(value));
    const active = job => ['queued','running'].includes(job?.status);
    const clone = value => JSON.parse(JSON.stringify(value));
    const defaults = () => ({framework:'trl_cpu',taskset_id:'educational-amplifier-v1',steps:2,train_tasks:4,eval_tasks:4,seed:42,wall_seconds:1800,max_completion_length:96});
    const ranges = {steps:[1,128],train_tasks:[2,64],eval_tasks:[1,32],seed:[0,2147483647],wall_seconds:[60,14400],max_completion_length:[96,2048]};
    let host, dialog, origin = null, generation = 0, timer = null, busy = false;
    let options = defaults(), resources = {}, taskset = {}, jobs = [], job = null, error = '';
    let tasksets = [], tasksetDefaults = {}, tasksetDraft = '', tasksetPreview = null, optionRevision = 0, jobSelection = 0;
    function acceptJob(selected) {
      if(optionRevision===0){
        const budget=selected.options?.max_completion_length ?? selected.policy_limits?.completion_tokens ?? 96;
        if(!Number.isInteger(budget)||budget<96||budget>2048)throw new Error(t('Recorded completion budget is outside its allowed range.'));
        options.max_completion_length=budget;
        options.taskset_id=selected.options?.taskset_id || 'educational-amplifier-v1';
        taskset=tasksets.find(item=>item.id===options.taskset_id) || {};
      }
      job=selected;
    }
    function guard(edit = false) {
      if (!dialog?.open || !origin || state.project?.id !== origin) {
        const e = new Error(t('The selected project changed. Reopen training.'));e.code='VERSION_CONFLICT';throw e;
      }
      if (edit && (busy || active(job))) throw new Error(t('Wait for this job or cancel it before changing training options.'));
    }
    function snapshot() {
      const same = origin === state.project?.id;
      return {open:Boolean(dialog?.open),project_id:same ? origin : null,busy,
        options:same ? clone(options) : null,job_id:same ? job?.id || null : null,
        status:same ? job?.status || null : null,phase:same ? job?.phase || null : null,
        completed_episodes:same ? job?.completed_episodes || 0 : 0,
        model_weights_updated:Boolean(same && job?.model_weights_updated),
        taskset:same ? clone(taskset) : null,resources:same ? clone(resources) : null,
        checkpoint:same ? job?.checkpoint || null : null,error:same ? error : '',
        taskset_draft:same ? tasksetDraft : '',taskset_preview_id:same ? tasksetPreview?.id || null : null};
    }
    const number = value => typeof value === 'number' && Number.isFinite(value) ? value.toPrecision(5) : '—';
    function curve() {
      const rows = (job?.curves || []).filter(row=>Number.isFinite(row.reward));
      if (!rows.length) return `<p>${label('Training curves appear after optimizer updates.')}</p>`;
      const values = rows.map(row=>row.reward), lo=Math.min(...values)-.05, hi=Math.max(...values)+.05;
      const points=rows.map((row,i)=>`${30+(i/(rows.length-1 || 1))*640},${130-(row.reward-lo)/(hi-lo)*100}`).join(' ');
      return `<figure data-training-curve><svg viewBox="0 0 700 160" role="img" aria-label="${label('Mean reward by optimizer step')}"><line x1="30" y1="130" x2="670" y2="130" class="training-axis"/><polyline points="${points}" class="training-reward-line"/>${rows.map((row,i)=>`<circle cx="${30+(i/(rows.length-1 || 1))*640}" cy="${130-(row.reward-lo)/(hi-lo)*100}" r="4" class="training-reward-point"><title>${label('Step')} ${row.step}: ${number(row.reward)}</title></circle>`).join('')}</svg><figcaption>${label('Mean reward by optimizer step')}</figcaption></figure><div class="training-table-wrap"><table><thead><tr><th>${label('Step')}</th><th>${label('Reward')}</th><th>${label('Loss')}</th><th>${label('Gradient norm')}</th><th>${label('Tokens')}</th></tr></thead><tbody>${rows.map(row=>`<tr><td>${row.step}</td><td>${number(row.reward)}</td><td>${number(row.loss)}</td><td>${number(row.grad_norm)}</td><td>${number(row.num_tokens)}</td></tr>`).join('')}</tbody></table></div>`;
    }
    function comparison() {
      const result = job?.evaluation;
      if (!result) return `<p>${label('Held-out results appear after evaluation completes. No improvement is assumed.')}</p>`;
      const rows=[['before','Base model'],['after','Reloaded checkpoint'],['random','Random search']].filter(([key])=>result[key]);
      const episodes=rows.flatMap(([key,name])=>(result[key].episodes || []).map(episode=>({...episode,policy:name})));
      return `<div class="training-table-wrap"><table><caption>${label('Held-out comparison with the same task and simulator budgets')}</caption><thead><tr><th>${label('Policy')}</th><th>${label('Successes / tasks')}</th><th>${label('Mean reward')}</th><th>${label('Valid tool episodes / tasks')}</th><th>${label('Simulator calls')}</th></tr></thead><tbody>${rows.map(([key,name])=>{const row=result[key];return `<tr><th>${label(name)}</th><td>${row.successes} / ${row.tasks}</td><td>${number(row.mean_reward)}</td><td>${row.episodes_with_valid_tool_calls ?? (row.episodes || []).filter(episode=>episode.calls>0).length} / ${row.tasks}</td><td>${row.simulator_calls}</td></tr>`;}).join('')}</tbody></table></div><p>${label('Success rate change')}: ${number(result.success_rate_change)}. ${label('A saved checkpoint proves an update; it does not prove better performance.')}</p><details><summary>${label('Episode evidence and replay')}</summary><div class="training-table-wrap"><table><thead><tr><th>${label('Policy')}</th><th>${label('Episode')}</th><th>${label('Result')}</th><th>${label('Reward')}</th><th>${label('Action')}</th></tr></thead><tbody>${episodes.map(episode=>`<tr><td>${label(episode.policy)}</td><td>${episode.episode}</td><td>${label(episode.satisfied ? 'Satisfied' : episode.termination || 'Failed')}</td><td>${number(episode.reward)}</td><td><button type="button" class="secondary-button" data-training-replay="${episode.episode}" ${active(job) || !episode.results?.some(r=>r.run_ids?.length) ? 'disabled' : ''}>${label('Replay')}</button></td></tr>`).join('')}</tbody></table></div></details>`;
    }
    function policyEvidence() {
      const limits=job?.policy_limits, tokens=(job?.curves || []).filter(row=>Number.isFinite(row.num_tokens));
      return `<section data-training-policy-limits><h3>${label('Recorded policy limits')}</h3>${limits ? `<pre>${esc(JSON.stringify(limits,null,2))}</pre>` : `<p>${label('Recorded policy limits are not available for this job.')}</p>`}<h3>${label('Recorded token measurements')}</h3>${tokens.length ? `<div class="training-table-wrap"><table><thead><tr><th>${label('Step')}</th><th>${label('Tokens')}</th></tr></thead><tbody>${tokens.map(row=>`<tr><td>${esc(row.step ?? '—')}</td><td>${number(row.num_tokens)}</td></tr>`).join('')}</tbody></table></div>` : `<p>${label('Token measurements appear only when the job records them.')}</p>`}</section>`;
    }
    function jobHTML() {
      if (!job) return `<p>${label('Choose an existing job or start a bounded pilot.')}</p>`;
      const checkpoint=job.checkpoint;
      return `<div class="training-status" role="status" aria-live="polite">${label('Status')}: <strong>${esc(job.status)}</strong> · ${esc(job.phase || job.kind)} · ${job.completed_episodes || 0} ${label('episodes')}</div><p>${label('Elapsed seconds')}: ${number(job.compute_seconds)} · ${label('Peak memory (GiB)')}: ${number(job.peak_rss_bytes / 1073741824)} · ${label('Weight update')}: ${label(job.model_weights_updated ? 'Verified' : active(job) ? 'Pending' : 'None')}</p>${policyEvidence()}${job.error ? `<p class="training-error">${esc(job.error)}</p>` : ''}<div class="button-row"><button type="button" class="secondary-button" data-training-action="cancel" ${active(job) ? '' : 'disabled'}>${label('Cancel job')}</button><button type="button" class="secondary-button" data-training-action="evaluate" ${!active(job) && (checkpoint || job.checkpoint_job_id) ? '' : 'disabled'}>${label('Evaluate checkpoint')}</button><button type="button" class="secondary-button" data-training-action="resume_evaluation" ${job.kind==='training_evaluation' && ['failed','cancelled','interrupted'].includes(job.status) && job.parent_id && !job.resume_source_job_id ? '' : 'disabled'}>${label('Resume verified evaluation')}</button></div><h3>${label('Training curves')}</h3>${curve()}${checkpoint ? `<h3>${label('Checkpoint')}</h3><p>${label('Format')}: ${esc(checkpoint.format)} · ${label('Updated parameters')}: ${job.trainable_parameter_count || '—'} · ${label('Update norm')}: ${number(job.parameter_update_l2)}</p><details><summary>${label('Checkpoint hashes and provenance')}</summary><pre>${esc(JSON.stringify({model:job.model,checkpoint,training_process_pid:job.training_process_pid,reload_process_pid:job.evaluation?.reload_process_pid},null,2))}</pre></details>` : ''}<h3>${label('Evaluation')}</h3>${comparison()}${job.replayed ? `<h3>${label('Replay result')}</h3><p>${label('Original reward')}: ${number(job.original_reward)} · ${label('Replayed reward')}: ${number(job.replayed.reward)}</p>` : ''}<details><summary>${label('Trainer log')}</summary><pre>${esc(job.log_tail || t('No log output yet.'))}</pre></details>`;
    }
    function tasksetEditor(){
      let family;try{family=JSON.parse(tasksetDraft).family;}catch{}
      return `<details data-training-taskset-editor><summary>${label('Configure an independent task set')}</summary><p>${label('Choose a trusted circuit family, edit numerical grids, constraints, objective weights and split counts, then validate before publishing. Existing tasks stay immutable.')}</p><label>${label('Circuit family')}<select data-training-family>${Object.keys(tasksetDefaults).map(key=>`<option value="${esc(key)}" ${key===family ? 'selected' : ''}>${esc(key.replaceAll('_',' '))}</option>`).join('')}</select></label><label>${label('Taskset specification (JSON)')}<textarea data-training-taskset-draft rows="14" spellcheck="false">${esc(tasksetDraft)}</textarea></label><div class="button-row"><button type="button" class="secondary-button" data-training-action="validate_taskset">${label('Validate task set')}</button><button type="button" class="secondary-button" data-training-action="create_taskset" ${tasksetPreview ? '' : 'disabled'}>${label('Publish immutable task set')}</button></div><p>${tasksetPreview ? esc(tasksetPreview.id) : label('Validation required before publication.')}</p></details>`;
    }
    function draw() {
      if (!dialog) return;
      const locked=busy || active(job), configured=resources.frameworks?.trl_cpu?.available;
      const field=(name,text)=>`<label>${label(text)}<input type="number" data-training-option="${name}" min="${ranges[name][0]}" max="${ranges[name][1]}" step="1" value="${options[name]}"></label>`;
      const frameworks=[['trl_cpu','TRL CPU'],['agent_lightning','Agent Lightning'],['areal','AReaL'],['agentfly','AgentFly']];
      dialog.innerHTML=`<div class="dialog-form"><div class="card-head"><h2 id="training-controls-title">${label('Language-model training')}</h2><button type="button" class="icon-button" data-training-action="close" aria-label="${label('Close')}">×</button></div><p>${label('Train local model weights using independent rewards from real circuit simulations.')}</p><h3>${label('Resources')}</h3><dl class="training-resources">${frameworks.map(([key,name])=>{const item=resources.frameworks?.[key] || {};return `<div><dt>${esc(name)}</dt><dd>${label(item.available ? 'Available' : 'Unavailable')}${item.reason ? ` — ${esc(item.reason)}` : ''}</dd></div>`;}).join('')}</dl><p>${label('Model')}: ${esc(resources.model?.model_id || 'Qwen/Qwen2.5-0.5B-Instruct')} · ${label('CPU threads')}: ${resources.host?.cpu_threads ?? '—'} · ${label('Qualification')}: ${esc(resources.qualification?.status || 'unqualified')}</p><fieldset ${locked ? 'disabled' : ''}><legend>${label('Task set and limits')}</legend><div class="training-form-grid"><label>${label('Task set')}<select data-training-taskset>${tasksets.map(item=>`<option value="${esc(item.id)}" ${item.id===options.taskset_id ? 'selected' : ''}>${esc(item.name)} · ${esc(item.family)}</option>`).join('')}</select></label><label>${label('Framework')}<select data-training-option="framework"><option value="trl_cpu">TRL 1.14.2 · CPU · LoRA GRPO</option></select></label>${field('steps','Optimizer steps')}${field('train_tasks','Training tasks')}${field('eval_tasks','Held-out tasks')}${field('seed','Random seed')}${field('wall_seconds','Maximum seconds')}${field('max_completion_length','Completion budget (tokens)')}</div><p>${label('The completion budget includes generated text, tool observations and framing.')}</p><p>${label('Fixed splits')}: ${taskset.counts?.train ?? '—'} ${label('train')}, ${taskset.counts?.validation ?? '—'} ${label('validation')}, ${taskset.counts?.test ?? '—'} ${label('test')}. ${esc(taskset.coverage || '')}</p><details><summary>${label('Fixed evaluator and action bounds')}</summary><pre>${esc(JSON.stringify({bounds:taskset.bounds,hashes:taskset.hashes,evaluator:taskset.evaluation_specification,max_actions:taskset.max_actions},null,2))}</pre></details>${tasksetEditor()}</fieldset><div class="button-row"><button type="button" data-training-action="start" ${locked || !configured ? 'disabled' : ''}>${label('Start training pilot')}</button><button type="button" class="secondary-button" data-training-action="refresh" ${busy ? 'disabled' : ''}>${label('Refresh')}</button></div><p data-training-error class="training-error" role="alert">${esc(error)}</p><label>${label('Job history')}<select data-training-job><option value="">${label('Select a job')}</option>${jobs.map(item=>`<option value="${item.id}" ${item.id===job?.id ? 'selected' : ''}>${esc(item.kind)} · ${item.id.slice(0,8)} · ${esc(item.status)}</option>`).join('')}</select></label><section data-training-evidence>${jobHTML()}</section><div class="dialog-actions"><button type="button" class="secondary-button" data-training-action="close">${label('Close')}</button></div></div>`;
    }
    function stop() {if(timer)clearTimeout(timer);timer=null;}
    function schedule() {stop();if(dialog?.open && active(job))timer=setTimeout(()=>report(refresh()),1000);}
    function report(promise) {Promise.resolve(promise).catch(e=>{error=e.message;toast?.(error);if(dialog?.open){const p=dialog.querySelector('[data-training-error]');if(p)p.textContent=error;}});}
    function current(ticket,pid) {if(ticket!==generation || state.project?.id!==pid)throw new Error(t('The selected project changed. Reopen training.'));}
    async function open() {
      if(!state.project?.id)throw new Error(t('Select a project before opening training.'));
      origin=state.project.id;generation++;jobSelection++;const ticket=generation,pid=origin;
      stop();options=defaults();optionRevision=0;job=null;error='';busy=true;draw();if(!dialog.open)dialog.showModal();
      try {
        const result=await Promise.all([api.invoke('training.resources'),api.invoke('training.taskset.list',{project_id:pid}),api.invoke('training.list',{project_id:pid})]);
        current(ticket,pid);resources=result[0].data;tasksets=result[1].data.tasksets;tasksetDefaults=result[1].data.defaults;taskset=tasksets.find(item=>item.id===options.taskset_id);tasksetDraft=JSON.stringify(tasksetDefaults.amplifier,null,2);tasksetPreview=null;jobs=result[2].data.jobs;
        const latest=[...jobs].sort((a,b)=>b.created_at.localeCompare(a.created_at))[0];
        if(latest){const selected=await api.invoke('training.get',{project_id:pid,job_id:latest.id});current(ticket,pid);acceptJob(selected.data);}
        current(ticket,pid);return snapshot();
      } finally {if(ticket===generation){busy=false;draw();schedule();}}
    }
    function close(){generation++;stop();dialog?.close();busy=false;return snapshot();}
    function render(){if(dialog?.open && state.project?.id!==origin)close();if(host)host.querySelector('button').disabled=!state.project;}
    function set_options({options: patch}={}) {
      guard(true);if(!patch || typeof patch!=='object' || Array.isArray(patch))throw new Error(t('Training options must be an object.'));
      const next={...options};
      for(const [key,value] of Object.entries(patch)){
        if(key==='framework'){if(value!=='trl_cpu')throw new Error(t('This framework is not integrated.'));}
        else if(key==='taskset_id'){if(!tasksets.some(item=>item.id===value))throw new Error(t('Select a published task set.'));}
        else if(!ranges[key] || !Number.isInteger(value) || value<ranges[key][0] || value>ranges[key][1])throw new Error(t('Training option is unknown or outside its allowed range.'));
        next[key]=value;
      }
      options=next;if(Object.keys(patch).length)optionRevision++;taskset=tasksets.find(item=>item.id===options.taskset_id);draw();return snapshot();
    }
    function set_taskset_draft({specification}={}){
      guard(true);if(!specification || typeof specification!=='object' || Array.isArray(specification))throw new Error(t('Taskset specification must be an object.'));
      tasksetDraft=JSON.stringify(specification,null,2);tasksetPreview=null;draw();return snapshot();
    }
    async function validate_taskset(){
      guard(true);const ticket=generation,pid=origin,source=tasksetDraft;
      const specification=JSON.parse(source);busy=true;error='';draw();
      try{const result=await api.invoke('training.taskset.validate',{specification});current(ticket,pid);
        if(source!==tasksetDraft)throw new Error(t('Taskset draft changed; validate it again.'));
        tasksetPreview=result.data;return snapshot();
      }finally{if(ticket===generation){busy=false;draw();}}
    }
    async function create_taskset(){
      guard(true);if(!tasksetPreview)throw new Error(t('Validate this taskset before publishing it.'));
      const ticket=generation,pid=origin;busy=true;error='';draw();
      try{const result=await api.invoke('training.taskset.create',{project_id:pid,specification:tasksetPreview.specification},
        {idempotency_key:`taskset-${pid}-${tasksetPreview.id}`});current(ticket,pid);
        const listed=await api.invoke('training.taskset.list',{project_id:pid});current(ticket,pid);
        tasksets=listed.data.tasksets;options.taskset_id=result.data.id;optionRevision++;taskset=tasksets.find(item=>item.id===result.data.id);
        tasksetPreview=null;return snapshot();
      }finally{if(ticket===generation){busy=false;draw();}}
    }
    async function refresh(){
      guard();const ticket=generation,pid=origin;
      const selectedJob=job?.id;
      const result=await Promise.all([api.invoke('training.list',{project_id:pid}),job?api.invoke('training.get',{project_id:pid,job_id:job.id}):Promise.resolve(null)]);
      current(ticket,pid);if(selectedJob!==job?.id)return snapshot();jobs=result[0].data.jobs;if(result[1])job=result[1].data;
      const selected=dialog.querySelector('[data-training-job]');
      selected.innerHTML=`<option value="">${label('Select a job')}</option>`+jobs.map(item=>`<option value="${item.id}" ${item.id===job?.id?'selected':''}>${esc(item.kind)} · ${item.id.slice(0,8)} · ${esc(item.status)}</option>`).join('');
      dialog.querySelector('[data-training-evidence]').innerHTML=jobHTML();
      dialog.querySelector('fieldset').disabled=busy || active(job);
      dialog.querySelector('[data-training-action=start]').disabled=busy || active(job) || !resources.frameworks?.trl_cpu?.available;
      await onUpdate?.(job);schedule();return snapshot();
    }
    async function select_job({job_id}={}){guard();if(busy)throw new Error(t('Wait for this job or cancel it before changing training options.'));const ticket=generation,pid=origin,selection=++jobSelection;const result=await api.invoke('training.get',{project_id:pid,job_id});current(ticket,pid);if(selection!==jobSelection){const failure=new Error(t('Selected training job changed during the request.'));failure.code='VERSION_CONFLICT';throw failure;}acceptJob(result.data);draw();schedule();return snapshot();}
    async function launch(operation,extra={}){
      guard(true);const ticket=generation,pid=origin;busy=true;error='';draw();
      try {const result=await api.invoke(operation,{project_id:pid,...extra},{idempotency_key:`training-${crypto.randomUUID()}`});current(ticket,pid);job=result.data;jobs.push(job);await onUpdate?.(job);return job;}
      finally{if(ticket===generation){busy=false;draw();schedule();}}
    }
    const start=()=>launch('training.start',{options:clone(options)});
    const evaluate=({checkpoint_job_id}={})=>launch('training.evaluate',{checkpoint_job_id:checkpoint_job_id || job?.checkpoint_job_id || job?.id,options:clone(options)});
    const resume_evaluation=()=>launch('training.evaluate',{checkpoint_job_id:job?.parent_id,resume_job_id:job?.id,options:{wall_seconds:options.wall_seconds}});
    const replay=({episode}={})=>launch('training.replay',{job_id:job?.id,episode});
    async function cancel(){guard();if(!active(job))throw new Error(t('There is no active training job.'));await api.invoke('training.cancel',{project_id:origin,job_id:job.id});return refresh();}
    function mount(element){
      if(host)return;host=element;host.innerHTML=`<button type="button" class="secondary-button" data-training-open>${label('Model training')}</button>`;
      dialog=document.createElement('dialog');dialog.id='training-controls-dialog';dialog.className='training-controls-dialog';dialog.setAttribute('aria-labelledby','training-controls-title');document.body.append(dialog);
      host.addEventListener('click',()=>report(open()));dialog.addEventListener('cancel',()=>{generation++;stop();busy=false;});
      dialog.addEventListener('change',event=>{
        const key=event.target.dataset.trainingOption;
        if(key)report(Promise.resolve().then(()=>set_options({options:{[key]:key==='framework'?event.target.value:Number(event.target.value)}})));
        if(event.target.dataset.trainingJob!==undefined && event.target.value)report(select_job({job_id:event.target.value}));
        if(event.target.dataset.trainingTaskset!==undefined)report(Promise.resolve().then(()=>set_options({options:{taskset_id:event.target.value}})));
        if(event.target.dataset.trainingFamily!==undefined)report(Promise.resolve().then(()=>set_taskset_draft({specification:tasksetDefaults[event.target.value]})));
      });
      dialog.addEventListener('input',event=>{if(event.target.dataset.trainingTasksetDraft!==undefined){tasksetDraft=event.target.value;tasksetPreview=null;dialog.querySelector('[data-training-action=create_taskset]').disabled=true;}});
      dialog.addEventListener('click',event=>{
        const button=event.target.closest('button');if(!button || button.disabled)return;
        const actions={close,start,refresh,evaluate,resume_evaluation,cancel,validate_taskset,create_taskset};
        if(actions[button.dataset.trainingAction])report(Promise.resolve().then(()=>actions[button.dataset.trainingAction]()));
        if(button.dataset.trainingReplay!==undefined)report(replay({episode:Number(button.dataset.trainingReplay)}));
      });render();
    }
    return {mount,render,snapshot,commands:{open,close,set_options,set_taskset_draft,validate_taskset,create_taskset,start,refresh,select_job,evaluate,resume_evaluation,cancel,replay}};
  }
};
