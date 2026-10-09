'use strict';

// Native terminal bytes stay outside published browser state and project files.
// xterm handles VT control sequences; all PTY I/O uses registered operations.
window.AFNativeTerminal = {
  create({state,toast,t=value=>value}) {
    const operations=window.AFOperations;
    const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const label=value=>esc(t(value));
    let host,dialog,terminal=null,pid=null,record=null,records=[],caps=null,busy=false,generation=0,offset=0,poll=null,error='';
    let inputQueue='',inputTimer=null,inputFlight=false;
    let options={backend:'codex',mode:'read-only',model:'',cols:100,rows:30,runtime_seconds:3600};
    const running=()=>record?.status==='running'||record?.status==='queued';
    function guard() {
      if(!dialog?.open||!pid||state.project?.id!==pid){const e=new Error(t('The selected project changed. Reopen the native terminal.'));e.code='VERSION_CONFLICT';throw e;}
    }
    function snapshot() {
      const same=state.project?.id===pid;
      return {open:Boolean(dialog?.open),project_id:same?pid:null,busy,terminal_id:same?record?.id||null:null,
        status:same?record?.status||null:null,options:{...options},output_offset:same?offset:0,
        sessions:same?records.map(item=>({id:item.id,backend:item.backend,status:item.status,mode:item.mode})):[],error:same?error:''};
    }
    function report(promise) {return Promise.resolve(promise).catch(e=>{error=e.message;const node=dialog?.querySelector('[data-terminal-error]');if(node)node.textContent=error;toast?.(error,true);});}
    function stopPolling(){if(poll)clearTimeout(poll);poll=null;}
    function refreshStatus() {
      if(!dialog)return;
      const status=dialog.querySelector('[data-terminal-status]');if(status)status.textContent=record?`${t(record.status)} · ${record.backend} · ${record.id.slice(0,8)}`:t('No native terminal selected');
      const start=dialog.querySelector('[data-terminal-action=start]');if(start)start.disabled=busy||running();
      const stop=dialog.querySelector('[data-terminal-action=stop]');if(stop)stop.disabled=busy||!running();
      const fields=dialog.querySelector('[data-terminal-settings]');if(fields)for(const input of fields.querySelectorAll('[data-terminal-option]'))input.disabled=busy||(running()&&!['cols','rows'].includes(input.dataset.terminalOption));
      if(terminal)terminal.options.disableStdin=!running();
    }
    function body() {
      terminal?.dispose();terminal=null;
      dialog.innerHTML=`<div class="card-head"><h2 id="native-terminal-title">${label('Native CLI terminal')}</h2><button type="button" class="icon-button" data-terminal-action="close" aria-label="${label('Close')}">×</button></div><p>${label('This terminal uses the native CLI’s existing login and settings. You control its interactive approval prompts. Native file permissions apply; the project API does not sandbox terminal commands.')}</p><fieldset data-terminal-settings><legend>${label('Native session')}</legend><div class="form-grid"><label>${label('Backend')}<select data-terminal-option="backend">${(caps?.backends||[]).map(item=>`<option value="${esc(item.id)}" ${options.backend===item.id?'selected':''} ${item.available?'':'disabled'}>${esc(item.id)}${item.available?'':` · ${label('unavailable')}`}</option>`).join('')}</select></label><label>${label('Native mode')}<select data-terminal-option="mode"><option value="read-only" ${options.mode==='read-only'?'selected':''}>${label('Read-only / plan')}</option><option value="workspace-write" ${options.mode==='workspace-write'?'selected':''}>${label('Workspace writes with native approval')}</option></select></label><label>${label('Model (optional)')}<input data-terminal-option="model" maxlength="256" value="${esc(options.model)}"></label><label>${label('Maximum runtime (seconds)')}<input type="number" min="1" max="14400" data-terminal-option="runtime_seconds" value="${Number(options.runtime_seconds)}"></label><label>${label('Columns')}<input type="number" min="20" max="240" data-terminal-option="cols" value="${Number(options.cols)}"></label><label>${label('Rows')}<input type="number" min="5" max="100" data-terminal-option="rows" value="${Number(options.rows)}"></label></div></fieldset><div class="button-row"><button type="button" class="primary-button" data-terminal-action="start">${label('Start native terminal')}</button><button type="button" class="secondary-button" data-terminal-action="stop">${label('Stop terminal')}</button><button type="button" class="secondary-button" data-terminal-action="refresh">${label('Refresh status')}</button><button type="button" class="secondary-button" data-terminal-action="interrupt">${label('Send Ctrl+C')}</button></div><p data-terminal-status role="status"></p><div data-terminal-screen class="native-terminal-screen" aria-label="${label('Native terminal input and output')}"></div><p class="footnote">${label('Output is retained only in this server’s bounded memory. Closing the panel detaches; Stop ends the process group. Agents cannot type into or read this human terminal.')}</p><details><summary>${label('Terminal sessions')}</summary><ul>${records.map(item=>`<li><button type="button" class="secondary-button" data-terminal-select="${esc(item.id)}">${esc(item.backend)} · ${label(item.status)} · ${esc(item.id.slice(0,8))}</button></li>`).join('')}</ul></details><p data-terminal-error role="alert">${esc(error)}</p><div class="dialog-actions"><button type="button" class="secondary-button" data-terminal-action="close">${label('Close')}</button></div>`;
      terminal=new window.Terminal({cols:options.cols,rows:options.rows,convertEol:false,scrollback:1000,
        fontSize:13,cursorBlink:true,allowProposedApi:false,disableStdin:!running(),
        theme:{background:'#101820',foreground:'#e1e7ed',cursor:'#92d3ff'}});
      terminal.open(dialog.querySelector('[data-terminal-screen]'));
      // Disable terminal-origin clipboard writes and automatic external links.
      terminal.parser.registerOscHandler(52,()=>true);
      terminal.onData(data=>{inputQueue+=data;if(inputQueue.length>8192){inputQueue='';report(Promise.reject(new Error(t('Terminal input paste exceeds 8192 characters.'))));return;}if(!inputTimer)inputTimer=setTimeout(()=>{inputTimer=null;report(flushInput());},12);});
      refreshStatus();
    }
    async function open(parameters={}) {
      if(busy)throw new Error(t('Wait for the current terminal request.'));
      if(!state.project)throw new Error(t('Select a project first.'));
      const ticket=++generation;pid=state.project.id;error='';stopPolling();inputQueue='';offset=0;
      const results=await Promise.all([operations.invoke('native.terminal.capabilities',{project_id:pid}),operations.invoke('native.terminal.list',{project_id:pid})]);
      if(ticket!==generation||state.project?.id!==pid)throw new Error(t('Project changed while opening native terminal.'));
      caps=results[0].data;records=results[1].data.terminals;
      record=parameters.terminal_id?records.find(item=>item.id===parameters.terminal_id):records.find(item=>['queued','running'].includes(item.status))||null;
      if(parameters.terminal_id&&!record)throw new Error(t('Terminal does not belong to this project.'));
      const available=caps.backends.find(item=>item.available);if(!caps.backends.some(item=>item.id===options.backend&&item.available)&&available)options.backend=available.id;
      if(record)options={...options,...Object.fromEntries(['backend','mode','model','cols','rows','runtime_seconds'].filter(key=>record[key]!==undefined).map(key=>[key,record[key]]))};
      if(!window.Terminal)throw new Error(t('The local terminal renderer is unavailable.'));
      body();if(!dialog.open)dialog.showModal();if(record)await refresh();terminal.focus();return snapshot();
    }
    function close() {generation++;stopPolling();if(inputTimer)clearTimeout(inputTimer);inputTimer=null;inputQueue='';terminal?.dispose();terminal=null;dialog?.close();return snapshot();}
    function set_options({options:patch}={}) {
      guard();if(busy||running())throw new Error(t('Stop the current native session before changing launch options.'));
      if(!patch||typeof patch!=='object'||Array.isArray(patch))throw new Error(t('Terminal options must be an object.'));
      const allowed=['backend','mode','model','cols','rows','runtime_seconds'];
      if(Object.keys(patch).some(key=>!allowed.includes(key)))throw new Error(t('Unknown terminal launch option.'));
      options={...options,...patch};error='';body();return snapshot();
    }
    async function start() {
      guard();if(busy||running())throw new Error(t('A native terminal is already running.'));
      if(state.dirty)throw new Error(t('Save your editor draft before opening a native CLI against project files.'));
      const project=pid,ticket=generation;busy=true;error='';refreshStatus();
      try {
        const revision=await operations.invoke('project.detail',{project_id:project});guard();
        const response=await operations.invoke('native.terminal.start',{project_id:project,...options},
          {expected_version:revision.version,idempotency_key:`terminal-${crypto.randomUUID()}`});
        if(ticket===generation&&state.project?.id===project){record=response.data;offset=0;terminal?.reset();await refresh();terminal?.focus();}
        return snapshot();
      } finally {busy=false;refreshStatus();}
    }
    async function refresh() {
      guard();if(!record)return snapshot();
      const project=pid,identifier=record.id,ticket=generation;
      const results=await Promise.all([operations.invoke('native.terminal.get',{project_id:project,terminal_id:identifier}),
        operations.invoke('native.terminal.read',{project_id:project,terminal_id:identifier,after:offset})]);
      if(ticket!==generation||state.project?.id!==project||record?.id!==identifier)return snapshot();
      record=results[0].data;const output=results[1].data;
      if(output.truncated)terminal?.writeln('\r\n'+t('[Earlier terminal output was truncated.]'));
      if(output.content_base64)terminal?.write(Uint8Array.from(atob(output.content_base64),c=>c.charCodeAt(0)));
      offset=output.next_offset;refreshStatus();stopPolling();
      if(running())poll=setTimeout(()=>report(refresh()),200);
      return snapshot();
    }
    async function input({text}={}) {
      guard();if(!running())throw new Error(t('The native terminal is not running.'));
      if(typeof text!=='string'||!text||new TextEncoder().encode(text).length>8192)throw new Error(t('Terminal input must contain 1–8192 UTF-8 bytes.'));
      const response=await operations.invoke('native.terminal.input',{project_id:pid,terminal_id:record.id,text},
        {idempotency_key:`terminal-input-${crypto.randomUUID()}`});
      if(!response.data.complete)throw new Error(t('The native CLI input buffer is full; only part of the input was accepted. Inspect the terminal before sending more.'));
      return {accepted_bytes:response.data.accepted_bytes};
    }
    async function flushInput() {
      if(inputFlight)return;
      inputFlight=true;
      try{while(inputQueue){const pending=inputQueue;inputQueue='';await input({text:pending});}}
      finally{inputFlight=false;}
    }
    async function resize({cols,rows}={}) {
      guard();const result=await operations.invoke('native.terminal.resize',{project_id:pid,terminal_id:record?.id,cols,rows});
      record=result.data;options={...options,cols,rows};terminal?.resize(cols,rows);return snapshot();
    }
    async function stop() {
      guard();if(!record)throw new Error(t('No native terminal selected.'));
      const response=await operations.invoke('native.terminal.stop',{project_id:pid,terminal_id:record.id});
      record=response.data.terminal;await refresh();return snapshot();
    }
    function render(){const button=host?.querySelector('button');if(button)button.disabled=!state.project;}
    function mount(element) {
      if(host)return;host=element;host.innerHTML=`<button type="button" class="secondary-button" data-native-terminal-open>${label('Native terminal')}</button>`;
      dialog=document.createElement('dialog');dialog.id='native-terminal-dialog';dialog.className='native-terminal-dialog';dialog.setAttribute('aria-labelledby','native-terminal-title');document.body.append(dialog);
      host.addEventListener('click',()=>report(open()));dialog.addEventListener('cancel',()=>close());
      dialog.addEventListener('change',event=>{
        const name=event.target.dataset.terminalOption;
        if(['cols','rows'].includes(name)&&running())report(resize({cols:Number(dialog.querySelector('[data-terminal-option=cols]').value),rows:Number(dialog.querySelector('[data-terminal-option=rows]').value)}));
        else if(name)report(Promise.resolve().then(()=>set_options({options:{[name]:event.target.type==='number'?Number(event.target.value):event.target.value}})));
      });
      dialog.addEventListener('click',event=>{
        const button=event.target.closest('button');if(!button||button.disabled)return;
        const action=button.dataset.terminalAction;
        if(button.dataset.terminalSelect)report(open({terminal_id:button.dataset.terminalSelect}));
        else if(action==='interrupt')report(input({text:'\x03'}));
        else if({open,close,start,stop,refresh}[action])report(Promise.resolve().then(()=>({open,close,start,stop,refresh}[action])()));
      });render();
    }
    return {mount,render,snapshot,commands:{open,close,set_options,start,refresh,input,resize,stop}};
  }
};
