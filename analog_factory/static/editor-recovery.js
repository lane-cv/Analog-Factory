'use strict';
// Browser-owned drafts and explicit conflict review. This controller never saves a file.
(function(root) {
  const KEY = 'af-editor-recovery-v1', TEXT_LIMIT = 2 * 1024 * 1024, STORE_LIMIT = 8 * 1024 * 1024, RECORD_LIMIT = 8;
  const bytes = text => new TextEncoder().encode(text).length;
  const text = value => { if (typeof value !== 'string' || bytes(value) > TEXT_LIMIT) throw new Error('Recovery source text must fit within 2 MiB.'); return value; };
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const id = (project, path, draft) => JSON.stringify(draft===undefined?[project,path]:[project,path,draft]);
  const queues = new WeakMap();
  const uuid = () => root.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  function create({state,readLatest,applyDraft,storage,notify=()=>{},t=value=>value}) {
    let host=null, storageError='', error='', status='', inspection=null, revision=0, sequence=0, merge='', recoveryRead=null, busy=false, pending=Promise.resolve(), selectedRecoveryId='';
    const ownerId=uuid(),owned=new Map();
    // localStorage caches can lag in another renderer even after a Web Lock has
    // transferred. IndexedDB transactions supply the authoritative shared RMW.
    const durable=!storage&&typeof window!=='undefined';
    let cachedRaw=null,activeStore=null,opening;
    const cache={getItem:()=>cachedRaw,setItem:(_,value)=>{cachedRaw=value;}};
    storage=storage||(()=>activeStore||cache);
    function database() {
      if(!opening)opening=new Promise((resolve,reject)=>{
        if(!root.indexedDB){reject(new Error('Recovery requires browser transactional storage support.'));return;}
        const request=root.indexedDB.open(KEY,1);
        request.onupgradeneeded=()=>request.result.createObjectStore('drafts');
        request.onsuccess=()=>{const db=request.result;db.onversionchange=()=>db.close();resolve(db);};
        request.onerror=()=>reject(request.error||new Error('Recovery database is unavailable.'));
        request.onblocked=()=>reject(new Error('Recovery database is blocked by another page.'));
      });
      return opening;
    }
    async function durableTransaction(change) {
      const db=await database();
      return new Promise((resolve,reject)=>{
        const tx=db.transaction('drafts',change?'readwrite':'readonly'),table=tx.objectStore('drafts'),request=table.get(KEY);
        let nextRaw,result,failure;
        request.onsuccess=()=>{
          try {
            nextRaw=request.result===undefined?root.localStorage.getItem(KEY):request.result;
            activeStore={getItem:()=>nextRaw,setItem:(_,value)=>{nextRaw=value;}};
            try {load();result=change?.();} finally {activeStore=null;}
            if(change&&nextRaw!==null)table.put(nextRaw,KEY);
          } catch(error){failure=error;tx.abort();}
        };
        tx.oncomplete=()=>{cachedRaw=nextRaw;resolve(result);};
        tx.onabort=tx.onerror=()=>reject(failure||tx.error||new Error('Recovery storage transaction failed.'));
      });
    }
    async function refreshRecords() {
      await pending;
      if(durable)try{await durableTransaction();}catch(failure){storageError=t('Recovery storage is unavailable: ')+t(failure.message);throw new Error(storageError);}
    }
    function transaction(change) {
      const task=pending.then(async()=>{
        if(durable)return durableTransaction(change);
        const local=storage(),previous=queues.get(local)||Promise.resolve(),work=previous.then(change);
        queues.set(local,work.catch(()=>{}));return work;
      });
      pending=task.catch(()=>{});return task;
    }
    function fail(message, code='VERSION_CONFLICT') { const failure=new Error(t(message));failure.code=code;throw failure; }
    function selected() {
      if (!state.project || !state.file || state.file === 'project.json' || !state.files.some(file=>file.path===state.file)) fail('Select an editable project file first.', 'INVALID_PARAMETERS');
      return {project_id:state.project.id,path:state.file,content:state.content,base:state.savedContent,version:state.fileVersion,revision,
        project_request:state.projectRequest,file_request:state.fileRequest};
    }
    function unchanged(captured) {
      if (state.project?.id!==captured.project_id || state.file!==captured.path || state.content!==captured.content ||
          state.savedContent!==captured.base || state.fileVersion!==captured.version || revision!==captured.revision ||
          state.projectRequest!==captured.project_request || state.fileRequest!==captured.file_request ||
          !state.files.some(file=>file.path===captured.path)) fail('Editor selection or draft changed during review. Inspect again.');
    }
    function load() {
      try {
        const raw=storage().getItem(KEY);
        if (!raw) return {};
        if (bytes(raw)>STORE_LIMIT) throw new Error('Recovery storage exceeds 8 MiB.');
        const value=JSON.parse(raw);
        if (![1,2].includes(value.schema_version) || !value.records || typeof value.records!=='object' || Array.isArray(value.records) || Object.keys(value.records).length>RECORD_LIMIT) throw new Error('Invalid recovery storage.');
        const records={};let index=0;
        for (const [key,original] of Object.entries(value.records).sort(([a],[b])=>a.localeCompare(b))) {
          const record=value.schema_version===1?{...original,draft_id:`legacy-${index++}-${original.updated_at}`,owner_id:'legacy'}:original;
          if (!record || typeof record.project_id!=='string' || record.project_id.length>200 || typeof record.path!=='string' || record.path.length>2048 ||
              key!==(value.schema_version===1?id(record.project_id,record.path):id(record.project_id,record.path,record.draft_id)) ||
              typeof record.draft_id!=='string' || !record.draft_id || record.draft_id.length>200 ||
              typeof record.owner_id!=='string' || !record.owner_id || record.owner_id.length>200 ||
              typeof record.version!=='string' || !record.version || record.version.length>1024 || !Number.isFinite(record.updated_at)) throw new Error('Invalid recovery record.');
          text(record.content);text(record.base);
          records[id(record.project_id,record.path,record.draft_id)]=record;
        }
        return records;
      } catch (failure) {storageError=t('Recovery storage is unavailable: ')+t(failure.message);throw new Error(storageError);}
    }
    function store(records) {
      try {
        if (Object.keys(records).length>RECORD_LIMIT) throw new Error('Recovery retains at most eight drafts. Discard a retained draft first.');
        const raw=JSON.stringify({schema_version:2,records});
        if (bytes(raw)>STORE_LIMIT) throw new Error('Recovery storage exceeds 8 MiB. Discard a retained draft first.');
        storage().setItem(KEY,raw);storageError='';
      } catch (failure) {storageError=t('Recovery storage is unavailable: ')+t(failure.message);throw new Error(storageError);}
    }
    function persist(captured,dirty) {
      const records=load(),selection=id(captured.project_id,captured.path);
      if (!owned.has(selection)) owned.set(selection,uuid());
      const draftId=owned.get(selection),key=id(captured.project_id,captured.path,draftId);
      if (dirty) {
        if (typeof captured.version!=='string' || !captured.version) throw new Error('Recovery requires the exact opened file version.');
        records[key]={project_id:captured.project_id,path:captured.path,draft_id:draftId,owner_id:ownerId,
          content:text(captured.content),base:text(captured.base),version:captured.version,updated_at:Date.now()};
      } else delete records[key];
      store(records);
    }
    function edited() {
      revision++;
      let captured;
      try {captured=selected();} catch(failure) {storageError=t(failure.message);notify(storageError,true);render();return Promise.resolve();}
      const dirty=state.dirty;
      return transaction(()=>persist(captured,dirty)).catch(failure=>{storageError=t(failure.message);notify(storageError,true);}).finally(render);
    }
    function list() {
      const records=Object.values(load()).filter(record=>record.project_id===state.project?.id);
      return records.map(({project_id,path,version,updated_at,content,draft_id,owner_id})=>({project_id,path,version,updated_at,draft_id,owner_id,own:owner_id===ownerId,bytes:bytes(content)})).sort((a,b)=>a.path.localeCompare(b.path)||b.updated_at-a.updated_at||a.draft_id.localeCompare(b.draft_id));
    }
    function record(path,draftId,records=load()) {
      if (!state.project) fail('Select a project first.','INVALID_PARAMETERS');
      const found=Object.values(records).filter(value=>value.project_id===state.project.id&&value.path===path&&(draftId===undefined||value.draft_id===draftId));
      if (!found.length) fail('No retained draft for this project file.','NOT_FOUND');
      if(found.length>1)fail('Multiple retained draft variants exist. Select an explicit draft ID.','INVALID_PARAMETERS');
      return {...found[0]};
    }
    async function read({path,draft_id}) {const pid=state.project?.id;await refreshRecords();if(pid!==state.project?.id)fail('Project changed while reading recovery.');recoveryRead=record(path,draft_id);render();return {...recoveryRead};}
    async function restore({path,draft_id}) {
      const captured=selected();
      if (captured.path!==path) fail('Open the recovery file before restoring its draft.','INVALID_PARAMETERS');
      if (state.dirty) fail('Keep or save the current draft before restoring another draft.','INVALID_PARAMETERS');
      await refreshRecords();unchanged(captured);
      const value=record(path,draft_id);
      await readLatest(captured); // Verify source/project still exists; never adopt this newer version.
      unchanged(captured);
      applyDraft({content:value.content,base:value.base,version:value.version});
      inspection=null;status=t('Recovered draft retains its original version. Review before saving.');await edited();return snapshot();
    }
    async function discard({path,draft_id}) {const pid=state.project?.id;await transaction(()=>{if(pid!==state.project?.id)fail('Project changed while discarding recovery.');const records=load(),value=record(path,draft_id,records);delete records[id(value.project_id,path,value.draft_id)];store(records);});recoveryRead=null;render();return snapshot();}
    async function inspect() {
      const captured=selected();busy=true;error='';render();
      try {
        const latest=await readLatest(captured);unchanged(captured);
        if (typeof latest.version!=='string' || !latest.version) throw new Error('Latest source version is unavailable.');
        inspection={inspection_id:String(++sequence),project_id:captured.project_id,path:captured.path,base:captured.base,latest:text(latest.content),draft:captured.content,latest_version:latest.version,preview:latest.preview||null,captured};
        merge=captured.content;status=t('Review base, latest disk source and draft. Prepare merged text before saving.');
        return {inspection_id:inspection.inspection_id,project_id:inspection.project_id,path:inspection.path,base:inspection.base,latest:inspection.latest,draft:inspection.draft,latest_version:inspection.latest_version,preview:inspection.preview};
      } catch (failure) {error=t(failure.message);throw failure;} finally {busy=false;render();}
    }
    async function resolve({inspection_id,content}) {
      if (!inspection || inspection.inspection_id!==inspection_id) fail('Select the current conflict inspection. Inspect again.');
      unchanged(inspection.captured);text(content);
      const reviewed=inspection;
      applyDraft({content,base:reviewed.latest,version:reviewed.latest_version});
      inspection=null;error='';status=t('Merged text is an unsaved draft. Save file to apply it.');await edited();return snapshot();
    }
    function saveFailed(failure) {status=t(failure.code==='VERSION_CONFLICT' || failure.status===409 ? 'Save conflict. Inspect the latest disk source before merging.' : 'Save outcome is unknown or failed. Draft retained; inspect disk before retrying.');error=t(failure.message);render();}
    function saved() {inspection=null;error='';status=t('File save confirmed.');return edited();}
    function snapshot() {
      let count=0;try {count=list().length;} catch (_) { /* Error remains visible and source stays in memory. */ }
      return {project_id:state.project?.id||null,path:state.file||null,busy,recovery_count:count,draft_revision:revision,
        inspection_id:inspection?.project_id===state.project?.id&&inspection?.path===state.file?inspection.inspection_id:null,
        status,error,storage_error:storageError};
    }
    const label = value=>esc(t(value));
    const field=(name,value,readonly=true)=>`<label>${label(name)}<textarea class="directives" rows="5" ${readonly?'readonly':'id="editor-merged-text"'} spellcheck="false">${esc(value)}</textarea></label>`;
    function render() {
      if (!host) return;
      let records=[];try {records=list();} catch (_) {}
      const current=inspection?.project_id===state.project?.id&&inspection?.path===state.file?inspection:null;
      const preferred=records.find(item=>item.draft_id===selectedRecoveryId)||records.find(item=>item.path===state.file&&item.own)||records.find(item=>item.path===state.file);
      host.innerHTML=`<details id="editor-recovery-panel" class="card spaced" ${status||error||storageError||current?'open':''}><summary>${label('Conflict review and draft recovery')}</summary>
        <p class="footnote">${label('Dirty drafts are retained in this browser. Restore and merge only prepare drafts; Save file applies changes.')}</p>
        <p role="status" id="editor-recovery-status">${esc(status)}</p><p role="alert" id="editor-recovery-error">${esc([error,storageError].filter(Boolean).join(' · '))}</p>
        <div class="button-row"><button class="secondary-button" type="button" data-recovery="inspect" ${busy||!state.file||state.file==='project.json'?'disabled':''}>${label('Inspect base / disk / draft')}</button>
        <label>${label('Retained draft')}<select id="editor-recovery-file"><option value="">${label('Select a retained draft')}</option>${records.map(item=>`<option value="${esc(item.draft_id)}" ${item.draft_id===preferred?.draft_id?'selected':''}>${esc(item.path)} · ${label(item.own?'This page':'Retained page')} · ${esc(item.draft_id.slice(0,8))} · ${item.bytes} ${label('bytes')}</option>`).join('')}</select></label>
        <button class="secondary-button" type="button" data-recovery="list" ${busy||!state.project?'disabled':''}>${label('Refresh retained drafts')}</button>
        ${['read','restore','discard'].map(action=>`<button class="secondary-button" type="button" data-recovery="${action}" ${busy||!records.length?'disabled':''}>${label({read:'Read retained draft',restore:'Restore retained draft',discard:'Discard retained draft'}[action])}</button>`).join('')}</div>
        ${current?field('Opened base',current.base)+field('Latest disk source',current.latest)+field('Your draft',current.draft)+field('Merged text to prepare as draft',merge,false)+`<button class="secondary-button" type="button" data-recovery="resolve">${label('Prepare merged draft')}</button>`:''}
        ${recoveryRead && recoveryRead.project_id===state.project?.id?`<p>${esc(recoveryRead.path)} · ${label('Original version')}: ${esc(recoveryRead.version)}</p>`+field('Recovery base',recoveryRead.base)+field('Recovery draft',recoveryRead.content):''}</details>`;
    }
    function mount(element) {
      host=element;
      host.addEventListener('input',event=>{if(event.target.id==='editor-merged-text')merge=event.target.value;});
      host.addEventListener('change',event=>{if(event.target.id==='editor-recovery-file')selectedRecoveryId=event.target.value;});
      host.addEventListener('click',event=>{
        const button=event.target.closest('[data-recovery]');if(!button||button.disabled)return;
        const action=button.dataset.recovery,draftId=host.querySelector('#editor-recovery-file').value;
        Promise.resolve().then(()=>{const variant=list().find(item=>item.draft_id===draftId);return commands[action](action==='resolve'?{inspection_id:inspection?.inspection_id,content:merge}:{path:variant?.path||'',draft_id:draftId});}).catch(failure=>{error=t(failure.message);notify(error,true);render();});
      });render();
    }
    const commands={list:async()=>{const pid=state.project?.id;await refreshRecords();if(pid!==state.project?.id)fail('Project changed while reading recovery.');render();return list();},read,restore,discard,inspect,resolve};
    if(durable)refreshRecords().then(render).catch(failure=>{storageError=t(failure.message);notify(storageError,true);render();});
    return {commands,mount,render,snapshot,edited,saved,saveFailed};
  }
  const api={create};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.AFEditorRecovery=api;
})(typeof window==='undefined'?globalThis:window);
