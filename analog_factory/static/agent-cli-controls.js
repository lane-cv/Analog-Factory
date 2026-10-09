'use strict';

// Paths and tracking preferences use registered versioned writers. This panel
// never installs software, logs in, or changes native CLI configuration.
window.AFAgentCLIControls = {
  create({state, toast, onUpdate, t = value => value}) {
    const api=window.AFOperations, clone=value=>JSON.parse(JSON.stringify(value));
    const esc=value=>String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const label=value=>esc(t(value));
    let dialog, origin=null, generation=0, statusRead=0, busy=false, status=null, error='', uncertain=false;
    let paths={codex:'',claude:''}, tracking={automatic:false,interval_hours:24}, settingsBase=null, projectBase=null, projectVersion=null, workspaceVersion=null;
    const canonical=value=>JSON.stringify(value,(key,item)=>item && typeof item==='object' && !Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(name=>[name,item[name]])):item);
    const failure=(message,code='VERSION_CONFLICT')=>Object.assign(new Error(t(message)),{code});
    const refused=cause=>['VERSION_CONFLICT','VERSION_REQUIRED','INVALID_ARGUMENT','INVALID_INPUT','PERMISSION_DENIED','CONFIRMATION_REQUIRED','PRECONDITION_FAILED','NOT_FOUND'].includes(cause.code);
    function current(ticket=generation,pid=origin) {
      if(ticket!==generation || pid!==(state.project?.id || null))throw failure('Project changed. Reopen Agent CLIs.');
    }
    function guard(project=false) {
      if(!dialog?.open)throw failure('Open Agent CLIs first.','PRECONDITION_FAILED');
      current();
      if(busy)throw failure('Wait for the current CLI request.','PRECONDITION_FAILED');
      if(project && !origin)throw failure('Select a project first.','PRECONDITION_FAILED');
    }
    function snapshot() {
      const same=origin===(state.project?.id || null);
      return {open:Boolean(dialog?.open),busy,project_id:origin,paths:same?clone(paths):{codex:'',claude:''},
        project_version:projectVersion,workspace_version:workspaceVersion,tracking:clone(tracking),status:same?clone(status):null,error,uncertain};
    }
    function safeLink(url,text) {
      try {const value=new URL(url);if(value.protocol==='https:' && ['github.com','api.github.com','code.claude.com','developers.openai.com','learn.chatgpt.com'].includes(value.hostname))return `<a href="${esc(value.href)}" target="_blank" rel="noopener noreferrer">${label(text)}</a>`;}catch(_){}
      return label(text);
    }
    function toolHTML(backend) {
      const tool=status?.tools?.find(item=>item.backend===backend), release=status?.releases?.backends?.find(item=>item.backend===backend);
      const success=release?.latest_success, attempt=release?.latest_attempt;
      return `<section class="card"><h3>${backend==='codex'?'Codex CLI':'Claude Code'}</h3>
        <dl class="file-info"><dt>${label('Tool availability')}</dt><dd>${label(tool?.available?'Detected':'Unavailable')}</dd><dt>${label('Effective executable')}</dt><dd>${esc(tool?.path || t('Executable not found'))}</dd>
        <dt>${label('Configuration source')}</dt><dd>${label({project:'Project',workspace:'Workspace defaults',PATH:'PATH'}[tool?.config_source] || 'unknown')}</dd>
        <dt>${label('Installed version')}</dt><dd>${esc(tool?.version || t('Version unknown'))}</dd>
        <dt>${label('Login status')}</dt><dd>${label('Not checked')}</dd>
        <dt>${label('Release comparison')}</dt><dd>${label(tool?.update_status || 'unknown')}</dd></dl>
        <p>${esc(t(tool?.diagnostic || ''))}</p>
        <p>${safeLink(release?.source_url,'Official release source')} · ${safeLink(release?.documentation_url,'CLI documentation')}<br>${label('Latest published release')}: ${success ? esc(success.version)+' · '+safeLink(success.url,'Release notes') : label('No successful release check yet.')}</p>
        ${success?`<p>${label('Published')}: ${esc(success.published_at || t('unknown'))}<br>${label('Last successful check')}: ${esc(success.checked_at || t('unknown'))}</p><details><summary>${label('Release notes')}</summary><pre class="evidence-details">${esc(success.notes || '')}</pre>${success.notes_truncated?`<small>${label('Notes are truncated. Open the official release for full details.')}</small>`:''}</details>`:''}
        <p>${label('Last check attempt')}: ${esc(attempt?.checked_at || t('Not checked'))} · ${label(attempt?.status || 'unknown')}${release?.stale?' · '+label('Cached release information may be stale.'):''}</p>
        ${attempt?.error?`<p role="status">${esc(t(attempt.error))}</p>`:''}</section>`;
    }
    function draw() {
      if(!dialog)return;
      const automatic={enabled:tracking.automatic,interval_hours:tracking.interval_hours};
      dialog.innerHTML=`<div class="dialog-form"><div class="card-head"><h2 id="agent-cli-title">${label('Agent CLIs')}</h2><button type="button" class="icon-button" data-cli-action="close" aria-label="${label('Close')}" ${busy?'disabled':''}>×</button></div>
        <p>${label('Inspect local CLI versions and public releases. Login and account access are not verified here.')}</p>
        <div class="button-row"><button type="button" class="secondary-button" data-cli-action="refresh" ${busy?'disabled':''}>${label('Refresh CLI status')}</button><button type="button" class="secondary-button" data-cli-action="check_updates" ${busy?'disabled':''}>${label('Check official updates')}</button></div>
        <p data-cli-error role="alert" ${error?'':'hidden'}>${esc(t(error))}</p>
        ${uncertain?`<p role="status">${label('CLI settings outcome is unknown. Inspect before saving again.')}</p>`:''}
        <div class="spaced" data-cli-status>${statusHTML()}</div>
        <fieldset ${busy||!origin||uncertain?'disabled':''}><legend>${label('Project CLI paths')}</legend><p>${label('Empty project paths inherit workspace defaults or PATH. Other project settings are preserved.')}</p>
        <div class="form-grid">${['codex','claude'].map(backend=>`<label>${backend} ${label('executable')}<input data-cli-path="${backend}" value="${esc(paths[backend])}" maxlength="4096" spellcheck="false" autocomplete="off"></label>`).join('')}</div>
        <div class="button-row"><button type="button" class="primary-button" data-cli-action="save_paths">${label('Save project CLI paths')}</button><button type="button" class="secondary-button" data-cli-action="reset_paths">${label('Use workspace CLI defaults')}</button></div></fieldset>
        <fieldset ${busy||uncertain?'disabled':''}><legend>${label('Official update tracking')}</legend><label class="check-label"><input type="checkbox" data-cli-automatic ${automatic.enabled?'checked':''}>${label('Check official releases automatically')}</label><label>${label('Check interval (hours)')}<input type="number" data-cli-interval min="6" max="168" step="1" value="${esc(automatic.interval_hours)}"></label>
        <p>${label('Checks run only while this server is running. Software is never installed or upgraded by this panel.')}</p><button type="button" class="secondary-button" data-cli-action="tracking">${label('Save update tracking')}</button></fieldset>
        <div class="dialog-actions"><button type="button" class="secondary-button" data-cli-action="close" ${busy?'disabled':''}>${label('Close')}</button></div></div>`;
      dialog.setAttribute('aria-busy',String(busy));
    }
    function statusHTML() {
      const automatic=status?.releases?.automatic;
      return `${automatic?`<p>${label('Saved update tracking')}: ${label(automatic.enabled?'Yes':'No')} · ${label('Check interval (hours)')}: ${esc(automatic.interval_hours)}</p>`:''}${toolHTML('codex')}${toolHTML('claude')}`;
    }
    function drawStatus() {
      const container=dialog?.querySelector('[data-cli-status]');if(!container)return;
      const opened=[...container.querySelectorAll('details')].map(item=>item.open);
      const focus=container.contains?.(document.activeElement)?document.activeElement.getAttribute('href'):null;
      container.innerHTML=statusHTML();[...container.querySelectorAll('details')].forEach((item,index)=>{item.open=opened[index] || false;});
      if(focus)[...container.querySelectorAll('a')].find(item=>item.getAttribute('href')===focus)?.focus();
    }
    async function readStatus(ticket,pid,background=false) {
      const request=++statusRead,result=await api.invoke('agent.cli.status',pid?{project_id:pid}:{});
      if(background && (ticket!==generation || pid!==(state.project?.id || null) || busy || !dialog?.open))return false;
      current(ticket,pid);if(request!==statusRead)return false;
      if((result.data.project_id ?? null)!==pid)throw failure('CLI status does not match the selected project.');
      status=result.data;return true;
    }
    async function sync(events=[],reset=false) {
      if(!dialog?.open || busy || origin!==(state.project?.id || null))return snapshot();
      const relevant=reset || events.some(event=>['agent.cli.releases','settings.update','project.configure'].includes(event.operation_id) && (!event.project_id || event.project_id===origin));
      if(!relevant)return snapshot();const ticket=generation,pid=origin;
      try{if(await readStatus(ticket,pid,true))drawStatus();}
      catch(cause){if(ticket===generation && pid===(state.project?.id || null)){error=cause.message;const output=dialog.querySelector('[data-cli-error]');if(output){output.hidden=false;output.textContent=t(error);}}}
      return snapshot();
    }
    async function open() {
      if(busy)throw failure('Wait for the current CLI request.','PRECONDITION_FAILED');
      const ticket=++generation,pid=origin=state.project?.id || null;
      busy=true;status=null;error='';uncertain=false;projectBase=null;settingsBase=null;projectVersion=null;workspaceVersion=null;paths={codex:'',claude:''};tracking={automatic:false,interval_hours:24};draw();
      if(!dialog.open)dialog.showModal();
      try {
        const results=await Promise.all([api.invoke('settings.get'),pid?api.invoke('file.read',{project_id:pid,path:'project.json'}):Promise.resolve(null)]);current(ticket,pid);
        workspaceVersion=results[0].version;if(!workspaceVersion)throw failure('CLI settings revision is unavailable.');settingsBase=clone(results[0].data);
        tracking={automatic:settingsBase.cli_updates?.automatic===true,interval_hours:settingsBase.cli_updates?.interval_hours ?? 24};
        if(pid){const project=JSON.parse(results[1].data.content);if(project.id!==pid)throw failure('CLI status does not match the selected project.');projectBase=clone(project.config || {});projectVersion=results[1].data.source_version;if(!projectVersion)throw failure('CLI settings revision is unavailable.');paths={codex:projectBase.tools?.codex || '',claude:projectBase.tools?.claude || ''};}
        await readStatus(ticket,pid);return snapshot();
      }catch(cause){if(ticket===generation)error=cause.message;throw cause;}
      finally{if(ticket===generation){busy=false;draw();}}
    }
    function close(){if(busy)throw failure('Wait for the current CLI request.','PRECONDITION_FAILED');generation++;dialog?.close();return snapshot();}
    function set_paths(parameters={}) {
      guard(true);if(uncertain)throw failure('CLI settings outcome is unknown. Inspect before saving again.','OUTCOME_UNKNOWN');
      const next={...paths};for(const [key,value] of Object.entries(parameters)){
        if(!['codex','claude'].includes(key)||typeof value!=='string'||value.length>4096||value.includes('\0'))throw failure('CLI paths must be strings of at most 4096 characters.','INVALID_INPUT');next[key]=value;
      }
      paths=next;for(const key of ['codex','claude']){const input=dialog.querySelector(`[data-cli-path="${key}"]`);if(input && input.value!==paths[key])input.value=paths[key];}return snapshot();
    }
    async function refresh() {
      guard();const ticket=generation,pid=origin;busy=true;error='';draw();
      try{await readStatus(ticket,pid);return snapshot();}catch(cause){if(ticket===generation)error=cause.message;throw cause;}finally{if(ticket===generation){busy=false;draw();}}
    }
    async function check_updates(parameters={}) {
      guard();const ticket=generation,pid=origin;busy=true;error='';draw();let cause;
      try{await api.invoke('agent.cli.check_updates',parameters);current(ticket,pid);}catch(failure){cause=failure;}
      try{await readStatus(ticket,pid);}catch(failure){cause=cause || failure;}
      if(ticket===generation){busy=false;error=cause?.message || '';draw();}if(cause)throw cause;return snapshot();
    }
    async function savePaths(reset=false) {
      guard(true);if(uncertain)throw failure('CLI settings outcome is unknown. Inspect before saving again.','OUTCOME_UNKNOWN');
      if(!projectVersion||!projectBase)throw failure('CLI settings revision is unavailable.');
      const ticket=generation,pid=origin,config=clone(projectBase);config.tools={...(config.tools || {})};
      for(const key of ['codex','claude']){const value=reset?'':paths[key].trim();if(value)config.tools[key]=value;else delete config.tools[key];}if(!Object.keys(config.tools).length)delete config.tools;
      busy=true;error='';draw();
      try{
        const result=await api.invoke('project.configure',{project_id:pid,config},{expected_version:projectVersion,idempotency_key:`cli-paths-${crypto.randomUUID()}`});current(ticket,pid);
        if(result.data?.id!==pid||!result.version)throw failure('CLI settings outcome is unknown. Inspect before saving again.','OUTCOME_UNKNOWN');
        projectBase=clone(result.data.config || {});projectVersion=result.version;paths={codex:projectBase.tools?.codex || '',claude:projectBase.tools?.claude || ''};state.project=result.data;
        await onUpdate?.({project_id:pid});current(ticket,pid);
        // Project metadata is part of the workspace token. Renew after our own
        // write only when the complete public settings still equal the inspected
        // base. A concurrent preference edit keeps the old token and draft.
        const latestSettings=await api.invoke('settings.get');current(ticket,pid);
        if(latestSettings.version && canonical(latestSettings.data)===canonical(settingsBase))workspaceVersion=latestSettings.version;
        await readStatus(ticket,pid);return snapshot();
      }catch(cause){if(ticket===generation){error=cause.message;if(!refused(cause))uncertain=true;}throw cause;}
      finally{if(ticket===generation){busy=false;draw();}}
    }
    function set_tracking_draft(parameters={}) {
      guard();if(uncertain)throw failure('CLI settings outcome is unknown. Inspect before saving again.','OUTCOME_UNKNOWN');
      const next={...tracking};for(const [key,value] of Object.entries(parameters)){
        if(key==='automatic' && typeof value==='boolean')next.automatic=value;
        else if(key==='interval_hours' && Number.isInteger(value) && value>=6 && value<=168)next.interval_hours=value;
        else throw failure('Update tracking requires a boolean and an interval from 6 to 168 hours.','INVALID_INPUT');
      }
      tracking=next;const automatic=dialog.querySelector('[data-cli-automatic]'),interval=dialog.querySelector('[data-cli-interval]');
      if(automatic)automatic.checked=tracking.automatic;if(interval)interval.value=String(tracking.interval_hours);return snapshot();
    }
    async function set_tracking({automatic,interval_hours}={}) {
      guard();if(typeof automatic!=='boolean'||(interval_hours!==undefined&&(!Number.isInteger(interval_hours)||interval_hours<6||interval_hours>168)))throw failure('Update tracking requires a boolean and an interval from 6 to 168 hours.','INVALID_INPUT');
      if(uncertain)throw failure('CLI settings outcome is unknown. Inspect before saving again.','OUTCOME_UNKNOWN');
      set_tracking_draft({automatic,...(interval_hours===undefined?{}:{interval_hours})});
      const ticket=generation,pid=origin,settings={cli_updates:{automatic,interval_hours:interval_hours ?? tracking.interval_hours}};
      if(!workspaceVersion)throw failure('CLI settings revision is unavailable.');busy=true;error='';draw();
      try{const result=await api.invoke('settings.update',{settings},{expected_version:workspaceVersion,idempotency_key:`cli-tracking-${crypto.randomUUID()}`});current(ticket,pid);
        if(!result.version)throw failure('CLI settings outcome is unknown. Inspect before saving again.','OUTCOME_UNKNOWN');workspaceVersion=result.version;state.settings=result.data;settingsBase=clone(result.data);tracking=clone(settings.cli_updates);await readStatus(ticket,pid);return snapshot();
      }catch(cause){if(ticket===generation){error=cause.message;if(!refused(cause))uncertain=true;}throw cause;}
      finally{if(ticket===generation){busy=false;draw();}}
    }
    const commands={open,close,refresh,check_updates,set_paths,save_paths:()=>savePaths(),reset_paths:()=>savePaths(true),set_tracking_draft,set_tracking};
    const report=promise=>Promise.resolve(promise).catch(cause=>{error=cause.message;draw();toast?.(t(cause.message),true);});
    function mount(host) {
      if(!dialog){dialog=document.createElement('dialog');dialog.id='agent-cli-dialog';dialog.className='modal';dialog.setAttribute('aria-labelledby','agent-cli-title');document.body.append(dialog);
        dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();else generation++;});
        dialog.addEventListener('click',event=>{const button=event.target.closest('[data-cli-action]');if(!button||button.disabled)return;const action=button.dataset.cliAction;
          if(action==='tracking')report(set_tracking({automatic:dialog.querySelector('[data-cli-automatic]').checked,interval_hours:Number(dialog.querySelector('[data-cli-interval]').value)}));else report(Promise.resolve().then(()=>commands[action]()));});
        dialog.addEventListener('input',event=>{if(event.target.dataset.cliPath)report(Promise.resolve().then(()=>set_paths({[event.target.dataset.cliPath]:event.target.value})));});
        dialog.addEventListener('change',event=>{
          if(event.target.hasAttribute('data-cli-automatic'))report(Promise.resolve().then(()=>set_tracking_draft({automatic:event.target.checked})));
          if(event.target.hasAttribute('data-cli-interval'))report(Promise.resolve().then(()=>set_tracking_draft({interval_hours:Number(event.target.value)})));
        });
      }
      if(host){host.innerHTML=`<button type="button" class="secondary-button" data-open-agent-cli>${label('Agent CLIs')}</button>`;host.querySelector('button').addEventListener('click',()=>report(open()));}
    }
    function render(){if(dialog?.open && origin!==(state.project?.id || null)){generation++;busy=false;status=null;dialog.close();}}
    return {mount,render,snapshot,commands,sync};
  }
};
