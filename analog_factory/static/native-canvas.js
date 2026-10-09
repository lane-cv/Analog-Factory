'use strict';
// Semantic controls operate on acknowledged native state, never screen positions.
window.AFNativeCanvas={create({state,toast,t=value=>value,onUpdate}){
  const ops=window.AFOperations,clone=value=>JSON.parse(JSON.stringify(value));
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const label=value=>esc(t(value));
  let host,dialog,origin=null,sourceVersion=null,runID=null,sessionID=null,native=null,busy=false,error='',unknown=false,needsInspection=false,generation=0;
  let draft={tool:'xschem',source:'schematics/amplifier.sch',instance_id:'',direction:'right',grid_steps:1,from_instance:'',from_pin:'',to_instance:'',to_pin:'',route:'horizontal_first',layer:''};
  function guard(mutation=false){
    if(!dialog?.open||origin!==state.project?.id)throw new Error(t('Project changed. Reopen the managed native canvas.'));
    if(busy)throw new Error(t('Wait for the current native request.'));
    if(mutation&&(unknown||needsInspection))throw new Error(t('Inspect the native session before another mutation. The previous command must not be retried automatically.'));
  }
  function target(){if(!runID||!sessionID||!native?.connected)throw new Error(t('Inspect a connected managed native session first.'));return {project_id:origin,run_id:runID,session_id:sessionID,expected_native_revision:native.native_revision};}
  function snapshot(){const same=origin===state.project?.id;return {open:Boolean(dialog?.open),project_id:same?origin:null,busy,
    options:same?clone(draft):null,source_version:same?sourceVersion:null,run_id:same?runID:null,session_id:same?sessionID:null,
    native:same&&native?clone(native):null,unknown_outcome:same?unknown:false,needs_inspection:same?needsInspection:false,error:same?error:''};}
  const option=(value,current,translate=false)=>`<option value="${esc(value)}" ${value===current?'selected':''}>${translate?label(value):esc(value)}</option>`;
  const listedLayers=()=>native?.layers?.filter(layer=>/^[A-Za-z_][A-Za-z0-9_.:/+-]{0,127}$/.test(layer))||[];
  function instanceSelect(key,name){return `<label>${label(name)}<select data-native-field="${key}">${option('',draft[key])}${(native?.instances||[]).map(item=>option(item.id,draft[key])).join('')}</select></label>`;}
  function pinSelect(key,instanceKey,name){const pins=native?.instances?.find(item=>item.id===draft[instanceKey])?.pins||[];return `<label>${label(name)}<select data-native-field="${key}">${option('',draft[key])}${pins.map(pin=>option(pin,draft[key])).join('')}</select></label>`;}
  function body(){if(!dialog)return;if(origin!==state.project?.id){dialog.innerHTML=`<div class="dialog-form"><p role="alert">${label('Project changed. Reopen the managed native canvas.')}</p><button type="button" data-native-action="close">${label('Close')}</button></div>`;return;}const locked=busy||unknown||needsInspection,connected=Boolean(native?.connected);
    const buttons=['refresh','stop'].map(action=>`<button type="button" data-native-action="${action}" ${busy||!runID?'disabled':''}>${label(action==='refresh'?'Inspect native state':'Stop managed window')}</button>`).join('');
    dialog.innerHTML=`<div class="dialog-form"><div class="card-head"><h2 id="native-canvas-title">${label('Managed native canvas')}</h2><button type="button" data-native-action="close" aria-label="${label('Close')}">×</button></div><p>${label('Open a new Xschem or Magic session and edit actual named objects. Existing native windows are not taken over. Edits stay in the native window until explicit save.')}</p><fieldset ${busy||connected?'disabled':''}><legend>${label('Native source')}</legend><div class="form-grid"><label>${label('Tool')}<select data-native-field="tool">${['xschem','magic'].map(tool=>option(tool,draft.tool)).join('')}</select></label><label>${label('Source file')}<input data-native-field="source" maxlength="2048" value="${esc(draft.source)}"></label></div><button type="button" data-native-action="start" ${locked?'disabled':''}>${label('Open managed window')}</button></fieldset><p role="status">${native?`${label('Native revision')}: ${native.native_revision} · ${label('Unsaved native work')}: ${label(native.modified?'Yes':'No')} · ${label('Run')}: ${esc(runID)}`:label('Native readiness requires an actual acknowledgment.')}</p>${connected&&native.tool==='xschem'?`<fieldset ${locked?'disabled':''}><legend>${label('Named schematic objects')}</legend><div class="form-grid">${instanceSelect('instance_id','Instance')}<label>${label('Direction')}<select data-native-field="direction">${['left','right','up','down'].map(direction=>option(direction,draft.direction,true)).join('')}</select></label><label>${label('Grid steps')}<input type="number" min="1" max="999" step="1" data-native-field="grid_steps" value="${Number(draft.grid_steps)}"></label></div><div class="button-row"><button type="button" data-native-action="select">${label('Select instance')}</button><button type="button" data-native-action="move">${label('Move instance')}</button></div><div class="form-grid">${instanceSelect('from_instance','From instance')}${pinSelect('from_pin','from_instance','From pin')}${instanceSelect('to_instance','To instance')}${pinSelect('to_pin','to_instance','To pin')}<label>${label('Route')}<select data-native-field="route">${['horizontal_first','vertical_first'].map(route=>option(route,draft.route,true)).join('')}</select></label></div><button type="button" data-native-action="connect_pins">${label('Connect named pins')}</button></fieldset>`:''}${connected&&native.tool==='magic'?`<fieldset ${locked?'disabled':''}><legend>${label('Acknowledged native box')}</legend><p>${esc(native.box.join(', '))}</p><label>${label('Layer')}<select data-native-field="layer">${option('',draft.layer)}${listedLayers().map(layer=>option(layer,draft.layer)).join('')}</select></label><button type="button" data-native-action="paint_box">${label('Paint current box')}</button></fieldset>`:''}<p role="alert">${esc(error)}</p>${unknown||needsInspection?`<p>${label('Inspect the native session before another mutation. The previous command must not be retried automatically.')}</p>`:''}<div class="dialog-actions">${buttons}<button type="button" data-native-action="save" ${locked||!connected||state.dirty?'disabled':''}>${label('Save native snapshot with history')}</button></div><p class="footnote">${label('Native save preserves project history and keeps the editor dirty flag explicit. An edit acknowledgment does not establish connectivity or DRC correctness.')}</p></div>`;
  }
  function pending(){return {project_id:origin,run_id:runID,session_id:sessionID};}
  function check(ticket,expected){
    if(ticket!==generation||expected.project_id!==origin||expected.project_id!==state.project?.id||
       ('run_id' in expected&&(expected.run_id!==runID||expected.session_id!==sessionID)))
      throw new Error(t('Project or native target changed. Inspect before continuing.'));
  }
  function accept(data,expected){
    if(!data?.state||['project_id','run_id','session_id'].some(key=>data.state[key]!==expected[key]))
      throw new Error(t('Native acknowledgment does not match the requested target.'));
    native=clone(data.state);const ids=native.instances.map(item=>item.id);for(const key of ['instance_id','from_instance','to_instance'])if(!ids.includes(draft[key]))draft[key]=ids[0]||'';
    for(const [key,instanceKey] of [['from_pin','from_instance'],['to_pin','to_instance']]){const pins=native.instances.find(item=>item.id===draft[instanceKey])?.pins||[];if(!pins.includes(draft[key]))draft[key]=pins[0]||'';}
    if(!listedLayers().includes(draft.layer))draft.layer='';
  }
  function updateDraft(changes){
    const next={...draft};
    const enums={tool:['xschem','magic'],direction:['left','right','up','down'],route:['horizontal_first','vertical_first']};
    for(const [key,value] of Object.entries(changes)){
      if(!(key in next))throw new Error(t('Unknown native launch option.'));
      if(key==='grid_steps'){
        const number=typeof value==='number'?value:typeof value==='string'&&value.trim()?Number(value):NaN;
        if(!Number.isInteger(number)||number<1||number>999)throw new Error(t('Grid steps must be an integer from 1 to 999.'));
        next[key]=number;
      }else if(enums[key]){
        if(!enums[key].includes(value))throw new Error(t('Choose a listed native option.'));
        next[key]=value;
      }else{
        if(typeof value!=='string'||value.length>(key==='source'?2048:128))throw new Error(t('Native field exceeds its allowed length.'));
        next[key]=value;
      }
    }
    if(native?.connected){
      const ids=native.instances.map(item=>item.id);
      for(const key of ['instance_id','from_instance','to_instance'])
        if(key in changes&&next[key]&&!ids.includes(next[key]))throw new Error(t('Choose a listed native option.'));
      for(const [key,instanceKey] of [['from_pin','from_instance'],['to_pin','to_instance']]){
        const pins=native.instances.find(item=>item.id===next[instanceKey])?.pins||[];
        const changed=instanceKey in changes&&next[instanceKey]!==draft[instanceKey];
        if(changed&&!(key in changes))next[key]=pins[0]||'';
        if((key in changes||changed)&&next[key]&&!pins.includes(next[key]))throw new Error(t('Choose a listed native option.'));
      }
      if('layer' in changes&&next.layer&&!listedLayers().includes(next.layer))throw new Error(t('Choose a listed native option.'));
    }
    draft=next;error='';
  }
  async function open({session_id,run_id}={}){
    if(busy||!state.project)throw new Error(t('Select a project and wait for the current request.'));
    if(Boolean(session_id)!==Boolean(run_id))throw new Error(t('Choose both the native session and run.'));
    const pid=state.project.id,ticket=++generation;
    if(origin!==pid||(session_id&&(session_id!==sessionID||run_id!==runID))){
      native=null;unknown=false;needsInspection=Boolean(session_id);sessionID=session_id||null;runID=run_id||null;
    }
    origin=pid;error='';busy=true;
    const expected=pending();
    try{
      const read=await ops.invoke('project.detail',{project_id:pid});check(ticket,expected);
      sourceVersion=read.version;if(!dialog.open)dialog.showModal();
      if(session_id){
        const inspected=await ops.invoke('native.canvas.get',expected);check(ticket,expected);
        accept(inspected.data,expected);sourceVersion=inspected.version;unknown=false;needsInspection=false;
      }
    }catch(failure){if(ticket===generation){error=failure.message;needsInspection=true;unknown=failure.code==='OUTCOME_UNKNOWN';}throw failure;
    }finally{busy=false;body();}
    return snapshot();
  }
  function close(){generation++;if(runID){needsInspection=true;if(busy)unknown=true;}dialog?.close();return snapshot();}
  function set_options(parameters){guard();
    if(native?.connected&&['tool','source'].some(key=>key in parameters))throw new Error(t('Native launch options cannot change while this session is connected.'));
    const changes={...parameters};
    if('tool' in parameters&&!('source' in parameters))changes.source=parameters.tool==='magic'?'layout/amplifier.mag':'schematics/amplifier.sch';
    updateDraft(changes);body();return snapshot();}
  async function start(){guard(true);if(state.dirty)throw new Error(t('Save or resolve the browser editor draft before opening a native source.'));
    busy=true;error='';body();const ticket=generation,expected=pending();
    try{
      const result=await ops.invoke('native.canvas.start',{project_id:expected.project_id,tool:draft.tool,source:draft.source},{expected_version:sourceVersion,idempotency_key:`native-start-${crypto.randomUUID()}`});
      check(ticket,expected);
      runID=result.data.id;sessionID=result.data.native_session_id;native=null;needsInspection=true;
      const launched=pending();await onUpdate?.(result.data);check(ticket,launched);
      const inspected=await ops.invoke('native.canvas.get',launched);check(ticket,launched);
      accept(inspected.data,launched);sourceVersion=inspected.version;unknown=false;needsInspection=false;
    }catch(failure){if(ticket===generation){needsInspection=true;if(!['VERSION_CONFLICT','INVALID_ARGUMENT','PERMISSION_DENIED','CONFIRMATION_REQUIRED','UNAVAILABLE'].includes(failure.code))unknown=true;error=failure.message;}throw failure;
    }finally{busy=false;body();}
    return snapshot();}
  async function refresh(){guard();if(!runID||!sessionID)throw new Error(t('Choose an actual recorded native run and session to inspect.'));
    busy=true;body();const ticket=generation,expected=pending();
    try{
      const result=await ops.invoke('native.canvas.get',expected);check(ticket,expected);
      accept(result.data,expected);sourceVersion=result.version;unknown=false;needsInspection=false;error='';
    }catch(failure){if(ticket===generation){needsInspection=true;if(native&&failure.code==='FRONTEND_DISCONNECTED')native.connected=false;error=failure.message;}throw failure;
    }finally{busy=false;body();}
    return snapshot();}
  async function mutation(name,parameters){guard(true);const args=target();if(name==='save'&&state.dirty)throw new Error(t('Save or resolve the browser editor draft before saving a native snapshot.'));
    busy=true;error='';body();const ticket=generation,expected=pending();
    try{
      const result=await ops.invoke('native.canvas.'+name,{...args,...parameters},name==='save'?{expected_version:sourceVersion}:{});check(ticket,expected);
      if(!result.data.acknowledged){unknown=true;throw new Error(t('Native command outcome is unknown. Inspect before continuing.'));}
      accept(result.data,expected);
      if(name==='save'){sourceVersion=result.version;await onUpdate?.(result.data);check(ticket,expected);}
    }catch(failure){if(ticket===generation){needsInspection=true;unknown=!['VERSION_CONFLICT','INVALID_ARGUMENT','PERMISSION_DENIED','CONFIRMATION_REQUIRED','UNAVAILABLE'].includes(failure.code);error=failure.message;}throw failure;
    }finally{busy=false;body();}
    return snapshot();}
  const select=async({instance_id}={})=>{guard(true);updateDraft({instance_id:instance_id??draft.instance_id});return mutation('select',{instance_id:draft.instance_id});};
  const move=async({instance_id,direction,grid_steps}={})=>{guard(true);updateDraft({instance_id:instance_id??draft.instance_id,direction:direction??draft.direction,grid_steps:grid_steps??draft.grid_steps});return mutation('move',{instance_id:draft.instance_id,direction:draft.direction,grid_steps:draft.grid_steps});};
  const connect_pins=async(parameters={})=>{guard(true);const values=Object.fromEntries(['from_instance','from_pin','to_instance','to_pin','route'].map(key=>[key,parameters[key]??draft[key]]));updateDraft(values);return mutation('connect_pins',values);};
  const paint_box=async({layer}={})=>{guard(true);updateDraft({layer:layer??draft.layer});return mutation('paint_box',{box_token:native?.box_token,layer:draft.layer});};
  const save=()=>mutation('save',{expected_source_sha256:native?.source_sha256});
  async function stop(){guard();if(!runID)throw new Error(t('Choose a managed native run.'));
    busy=true;body();const ticket=generation,expected=pending();
    try{
      const parameters={project_id:expected.project_id,run_id:expected.run_id};
      const terminal=row=>row?.id===expected.run_id&&['succeeded','completed','failed','cancelled','timed_out','unavailable','interrupted'].includes(row.status);
      let result=await ops.invoke('run.cancel',parameters);check(ticket,expected);
      // Cancellation may acknowledge only its request. Preserve the live target
      // until the registered run record confirms that the process has ended.
      for(let attempt=0;!terminal(result.data)&&attempt<30;attempt++){
        if(attempt){await new Promise(resolve=>setTimeout(resolve,100));check(ticket,expected);}
        result=await ops.invoke('run.get',parameters);check(ticket,expected);
      }
      if(!terminal(result.data))throw new Error(t('Inspect the native session before another mutation. The previous command must not be retried automatically.'));
      native=null;runID=null;sessionID=null;unknown=false;needsInspection=false;error='';
    }catch(failure){if(ticket===generation){needsInspection=true;unknown=!['VERSION_CONFLICT','INVALID_ARGUMENT','PERMISSION_DENIED','CONFIRMATION_REQUIRED','UNAVAILABLE'].includes(failure.code);error=failure.message;}throw failure;
    }finally{busy=false;body();}
    return snapshot();}
  function report(value){return Promise.resolve(value).catch(failure=>{error=failure.message;body();toast?.(error,true);});}
  function render(){if(host){const button=host.querySelector('button');button.disabled=!state.project;button.textContent=t('Managed native canvas');}if(dialog?.open)body();}
  // Acknowledge the settled controller state, after async finally clears busy.
  const commands=Object.fromEntries(Object.entries({open,close,set_options,start,refresh,select,move,connect_pins,paint_box,save,stop}).map(([name,fn])=>[name,(...args)=>{const result=fn(...args);return result?.then?result.then(()=>snapshot()):snapshot();}]));
  function mount(element){if(host)return;host=element;host.innerHTML=`<button type="button" class="secondary-button">${label('Managed native canvas')}</button>`;
    dialog=document.createElement('dialog');dialog.id='native-canvas-dialog';dialog.className='simulation-batch-dialog';dialog.setAttribute('aria-labelledby','native-canvas-title');document.body.append(dialog);
    host.addEventListener('click',()=>report(open()));dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
    dialog.addEventListener('change',event=>{try{const key=event.target.dataset.nativeField;if(!key)return;commands.set_options({[key]:event.target.value});}catch(failure){error=failure.message;body();toast?.(error,true);}});
    dialog.addEventListener('click',event=>{const button=event.target.closest('button');if(!button||button.disabled)return;const action=button.dataset.nativeAction;if(commands[action])report(Promise.resolve().then(()=>commands[action]()));});render();}
  return {mount,render,snapshot,commands};
}};
