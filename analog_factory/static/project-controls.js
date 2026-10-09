'use strict';

// Lifecycle UI uses the same versioned business operations as CLI and agents.
window.AFProjectControls = {
  create({state, esc, t = value => value, toast, refreshProject, selectProject, openFile, onProjectsChanged}) {
    const operations = window.AFOperations;
    const escape = esc || (value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char])));
    const actions = new Set(['manage', 'rename', 'trash', 'move', 'file_trash', 'import', 'restore_project', 'restore_file']);
    let dialog, host, action = 'manage', busy = false, generation = 0, draftRevision = 0;
    let origin = null, targetProject = null, sourcePath = null, expectedVersion = null;
    let draft = {}, archiveFile = null, projectTrash = [], fileTrash = [];
    const label = value => escape(t(value));
    const fail = message => { const error = new Error(t(message)); error.code = 'VERSION_CONFLICT'; throw error; };
    const guard = () => {
      if ((state.project?.id || null) !== origin) fail('The selected project changed. Reopen the lifecycle dialog.');
      if (state.dirty && ['trash','move','file_trash','restore_file','import','restore_project'].includes(action)) fail('Save your editor draft before changing project files.');
      if (['move','file_trash'].includes(action) && state.file !== sourcePath) fail('The selected file changed. Reopen the lifecycle dialog.');
    };
    const report = promise => Promise.resolve(promise).catch(error => {
      if (dialog?.open) {
        const output = dialog.querySelector('[data-lifecycle-error]');
        if (output) { output.textContent = error.message; output.hidden = false; }
      }
      toast?.(error.message, true);
    });
    function snapshot() {
      const currentOrigin = (state.project?.id || null) === origin;
      return {open:Boolean(dialog?.open), action, busy, project_id:targetProject, source_path:sourcePath,
        name:currentOrigin ? draft.name || '' : '', destination:currentOrigin ? draft.destination || '' : '', trash_id:currentOrigin ? draft.trash_id || null : null,
        archive_loaded:Boolean(draft.content_base64 || archiveFile), archive_name:draft.archive_name || '',
        draft_revision:draftRevision, project_trash:currentOrigin ? projectTrash.filter(p => !origin || p.id === origin).map(p => ({id:p.id,name:p.name})) : [],
        file_trash:currentOrigin ? fileTrash.map(f => ({id:f.id,path:f.path})) : []};
    }
    function render() {
      if (!host) return;
      const backup = host.querySelector('[data-lifecycle-backup]');
      if (backup) backup.disabled = !state.project || busy;
    }
    function body() {
      if (!dialog) return;
      const project = Boolean(state.project);
      const titles = {manage:'Project and file management', rename:'Rename project', trash:'Move project to trash',
        move:'Move or rename file', file_trash:'Move file to trash', import:'Import project or restore backup',
        restore_project:'Restore project', restore_file:'Restore file'};
      let content;
      if (action === 'manage') {
        content = `<div class="button-row"><button type="button" class="secondary-button" data-lifecycle-open="rename" ${project ? '' : 'disabled'}>${label('Rename project')}</button><button type="button" class="secondary-button" data-lifecycle-open="trash" ${project ? '' : 'disabled'}>${label('Trash project')}</button><button type="button" class="secondary-button" data-lifecycle-open="move" ${state.file && state.file !== 'project.json' ? '' : 'disabled'}>${label('Move file')}</button><button type="button" class="secondary-button" data-lifecycle-open="file_trash" ${state.file && state.file !== 'project.json' ? '' : 'disabled'}>${label('Trash file')}</button></div>
          <div class="button-row"><button type="button" class="secondary-button" data-lifecycle-open="import">${label('Import ZIP or backup')}</button><button type="button" class="secondary-button" data-lifecycle-backup ${project ? '' : 'disabled'}>${label('Download full backup')}</button></div>
          <p>${label('Trash keeps project contents and file history until you restore them. Restore never replaces an existing file.')}</p>
          <h3>${label('Trashed projects')}</h3><ul>${projectTrash.length ? projectTrash.map(p => `<li>${escape(p.name)} <button type="button" class="secondary-button" data-lifecycle-restore-project="${escape(p.id)}">${label('Restore')}</button></li>`).join('') : `<li>${label('No trashed projects')}</li>`}</ul>
          <h3>${label('Trashed files in this project')}</h3><ul>${fileTrash.length ? fileTrash.map(f => `<li>${escape(f.path)} <button type="button" class="secondary-button" data-lifecycle-restore-file="${escape(f.id)}">${label('Restore')}</button></li>`).join('') : `<li>${label('No trashed files')}</li>`}</ul>`;
      } else if (action === 'rename') {
        content = `<label>${label('Project name')}<input name="name" maxlength="100" required value="${escape(draft.name)}"></label>`;
      } else if (action === 'move' || action === 'restore_file') {
        content = `<p>${escape(sourcePath || '')}</p><label>${label('Destination path')}<input name="destination" required value="${escape(draft.destination)}" spellcheck="false"></label><p>${label('The destination must be unused. Revision history follows the file.')}</p>`;
      } else if (action === 'import') {
        content = `<label>${label('Project name (optional)')}<input name="name" maxlength="100" value="${escape(draft.name)}"></label><label>${label('Project ZIP or full backup')}<input type="file" name="archive" accept=".zip,application/zip"></label><p data-lifecycle-archive>${escape(draft.archive_name || (draft.content_base64 ? t('Archive supplied') : ''))}</p><p>${label('Maximum ZIP size: 1 GiB, uploaded in bounded chunks. Restores sources, configuration and tool overrides as a new project. Imported execution and training evidence remain unverified; archived checkpoints cannot resume or evaluate.')}</p>`;
      } else {
        content = `<p>${action === 'file_trash' ? escape(sourcePath) : escape(draft.name || state.project?.name || '')}</p><p>${label(action === 'restore_project' ? 'The project will return with its original identity and contents.' : 'This change is reversible. Active runs, jobs, or agent turns must finish first.')}</p>`;
      }
      dialog.innerHTML = `<form class="dialog-form"><div class="card-head"><h2 id="lifecycle-title">${label(titles[action])}</h2><button type="button" class="icon-button" data-lifecycle-close aria-label="${label('Close')}">×</button></div>${content}<p data-lifecycle-error role="alert" hidden></p><div class="dialog-actions"><button type="button" class="secondary-button" data-lifecycle-close>${label('Cancel')}</button>${action !== 'manage' ? `<button type="submit" class="primary-button">${label(action === 'import' ? 'Import' : action.startsWith('restore') ? 'Restore' : 'Apply')}</button>` : ''}</div></form>`;
      dialog.querySelector('input:not([type="file"])')?.focus();
    }
    async function refreshProjects() {
      const result = await operations.invoke('project.list');
      state.projects = result.data;
      await onProjectsChanged?.(result.data);
      return result.data;
    }
    async function refresh_trash() {
      const ticket = generation, pid = state.project?.id || null;
      const results = await Promise.all([operations.invoke('project.trash.list'),
        pid ? operations.invoke('file.trash.list', {project_id:pid}) : Promise.resolve({data:[]})]);
      if (ticket !== generation || pid !== (state.project?.id || null)) fail('Project changed while reading trash. Reopen the dialog.');
      projectTrash = results[0].data; fileTrash = results[1].data;
      if (dialog?.open && action === 'manage') body();
      return snapshot();
    }
    async function open(parameters = {}) {
      if (busy) throw new Error(t('Wait for the current lifecycle operation to finish.'));
      const selected = parameters.action || 'manage';
      if (!actions.has(selected)) throw new Error(t('Unknown lifecycle action'));
      const ticket = ++generation;
      action = selected; origin = state.project?.id || null; targetProject = parameters.project_id || origin;
      sourcePath = state.file || null; expectedVersion = null; draft = {}; archiveFile = null; draftRevision++;
      if (selected !== 'manage' && selected !== 'import' && !targetProject) throw new Error(t('Select a project first.'));
      if (['move','file_trash'].includes(selected) && (!sourcePath || sourcePath === 'project.json')) throw new Error(t('Select a source file first.'));
      if (selected === 'rename' || selected === 'trash') draft.name = state.project?.name || '';
      if (selected === 'move') draft.destination = sourcePath;
      if (selected === 'restore_project') {
        const project = projectTrash.find(p => p.id === targetProject);
        draft.name = project?.name || '';
      }
      if (selected === 'restore_file') {
        const file = fileTrash.find(f => f.id === parameters.trash_id);
        if (!file) throw new Error(t('Refresh trash and select a file to restore.'));
        draft.trash_id = file.id; sourcePath = file.path; draft.destination = file.path;
      }
      guard();
      if (selected === 'manage') await refresh_trash();
      else {
        const result = await operations.invoke(selected === 'import' ? 'project.list' : 'project.detail',
          selected === 'import' ? {} : {project_id:targetProject});
        if (ticket !== generation) fail('Lifecycle dialog changed while loading.');
        guard(); expectedVersion = result.version;
      }
      body();
      if (!dialog.open) dialog.showModal();
      return snapshot();
    }
    function close() {
      if (busy) throw new Error(t('Wait for the current lifecycle operation to finish.'));
      generation++; draftRevision++; draft = {}; archiveFile = null; dialog?.close(); return snapshot();
    }
    function set_draft(parameters = {}) {
      if (!dialog?.open || busy) throw new Error(t('Open an editable lifecycle dialog first.'));
      guard();
      for (const key of ['name', 'destination', 'content_base64']) if (parameters[key] !== undefined) {
        if (typeof parameters[key] !== 'string') throw new Error(t('Lifecycle draft values must be strings.'));
        if (key === 'content_base64' && parameters[key].length > 2796204) throw new Error(t('ZIP exceeds the 2 MiB archive limit.'));
        draft[key] = parameters[key];
        if (key === 'content_base64') archiveFile = null;
        const input = dialog.querySelector(`[name="${key}"]`);
        if (input && input.value !== parameters[key]) input.value = parameters[key];
      }
      draftRevision++; return snapshot();
    }
    async function submit() {
      if (!dialog?.open || action === 'manage' || busy) throw new Error(t('Open an editable lifecycle dialog first.'));
      guard();
      const selected = action, pid = targetProject, path = sourcePath, originId = origin;
      const parameters = {project_id:pid};
      const ids = {rename:'project.rename',trash:'project.trash',move:'file.move',file_trash:'file.trash',
        restore_project:'project.restore',restore_file:'file.restore_deleted',import:'project.import'};
      if (selected === 'rename') parameters.name = draft.name;
      if (selected === 'move') Object.assign(parameters, {path, destination:draft.destination});
      if (selected === 'file_trash') parameters.path = path;
      if (selected === 'restore_file') Object.assign(parameters, {trash_id:draft.trash_id, destination:draft.destination});
      if (selected === 'import') {
        delete parameters.project_id;
        if (!draft.content_base64 && !archiveFile) throw new Error(t('Choose a ZIP archive first.'));
        if (draft.content_base64) parameters.content_base64 = draft.content_base64;
        if (draft.name?.trim()) parameters.name = draft.name.trim();
      }
      busy = true; render();
      for (const button of dialog.querySelectorAll('button')) button.disabled = true;
      let upload = null;
      try {
        if (selected === 'import' && archiveFile) {
          const file = archiveFile;
          upload = (await operations.invoke('project.import.begin', {size:file.size}, {idempotency_key:`upload-${crypto.randomUUID()}`})).data;
          for (let offset = 0; offset < file.size; offset += upload.chunk_bytes) {
            guard();
            const bytes = new Uint8Array(await file.slice(offset, offset + upload.chunk_bytes).arrayBuffer());
            let binary = '';
            for (let index = 0; index < bytes.length; index += 32768) binary += String.fromCharCode(...bytes.subarray(index,index + 32768));
            await operations.invoke('project.import.append', {transfer_id:upload.id,offset,content_base64:btoa(binary)},
              {idempotency_key:`upload-chunk-${crypto.randomUUID()}`});
            const progress = dialog.querySelector('[data-lifecycle-archive]');
            if (progress) progress.textContent = `${file.name} · ${Math.min(100,Math.round((offset + bytes.length) / file.size * 100))}%`;
          }
          delete parameters.content_base64; parameters.transfer_id = upload.id;
        }
        guard();
        const result = await operations.invoke(upload ? 'project.import.finish' : ids[selected], parameters, {expected_version:expectedVersion,
          idempotency_key:`lifecycle-${crypto.randomUUID()}`});
        upload = null; dialog.close(); draft = {}; archiveFile = null; draftRevision++; generation++;
        await refreshProjects();
        // A human can switch project while a request is in flight. Never replace
        // their new selection or draft with the old request's completion.
        if ((state.project?.id || null) === originId && !state.dirty) {
          if (selected === 'trash') await selectProject(state.projects[0]?.id || '');
          else if (selected === 'import' || selected === 'restore_project') await selectProject(result.data.id);
          else {
            if (selected === 'file_trash' && state.file === path) {
              state.file = null; state.content = ''; state.savedContent = ''; state.history = null; state.fileVersion = null;
            }
            if (selected === 'file_trash') state.context?.delete(path);
            if (selected === 'move' && state.context?.has(path)) {
              state.context.delete(path); state.context.add(result.data.path);
            }
            await refreshProject?.(selected === 'file_trash');
            if (selected === 'move' && state.file === path) await openFile?.(result.data.path, false);
            if (selected === 'restore_file') await openFile?.(result.data.path, false);
          }
        }
        toast?.(t('Project changes saved.'));
        return result.data;
      } finally {
        if (upload) await operations.invoke('transfer.release', {transfer_id:upload.id}).catch(() => {});
        busy = false; render(); for (const button of dialog.querySelectorAll('button')) button.disabled = false;
      }
    }
    async function backup() {
      const pid = state.project?.id;
      if (!pid) throw new Error(t('Select a project first.'));
      const result = await operations.invoke('project.archive.prepare', {project_id:pid,mode:'backup'});
      const anchor = document.createElement('a');
      anchor.href = `/api/transfers/download?project_id=${encodeURIComponent(pid)}&transfer_id=${encodeURIComponent(result.data.id)}`;
      anchor.download = result.data.filename; anchor.click();
      return {filename:result.data.filename, size:result.data.size};
    }
    function mount(element) {
      if (host) return;
      host = element;
      host.innerHTML = `<button type="button" class="secondary-button" data-lifecycle-open="manage">${label('Manage')}</button><button type="button" class="icon-button" data-lifecycle-backup title="${label('Download full backup')}" aria-label="${label('Download full backup')}">⇩</button>`;
      dialog = document.createElement('dialog'); dialog.id = 'project-lifecycle-dialog'; dialog.className = 'modal';
      dialog.setAttribute('aria-labelledby', 'lifecycle-title'); document.body.append(dialog);
      const click = event => {
        const button = event.target.closest('button');
        if (!button || button.disabled) return;
        if (button.dataset.lifecycleOpen) report(open({action:button.dataset.lifecycleOpen}));
        else if (button.hasAttribute('data-lifecycle-close')) report(Promise.resolve().then(close));
        else if (button.hasAttribute('data-lifecycle-backup')) report(backup());
        else if (button.dataset.lifecycleRestoreProject) report(open({action:'restore_project', project_id:button.dataset.lifecycleRestoreProject}));
        else if (button.dataset.lifecycleRestoreFile) report(open({action:'restore_file', trash_id:button.dataset.lifecycleRestoreFile}));
      };
      host.addEventListener('click', click); dialog.addEventListener('click', click);
      dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); else { generation++; draft = {}; archiveFile = null; draftRevision++; } });
      dialog.addEventListener('submit', event => { event.preventDefault(); report(submit()); });
      dialog.addEventListener('input', event => {
        if (['name','destination'].includes(event.target.name)) report(Promise.resolve().then(() => set_draft({[event.target.name]:event.target.value})));
      });
      dialog.addEventListener('change', event => {
        if (event.target.name !== 'archive') return;
        report(Promise.resolve().then(() => {
          const file = event.target.files?.[0];
          if (!file) return;
          if (!file.size || file.size > 1024 * 1024 * 1024) throw new Error(t('ZIP must contain 1 byte to 1 GiB.'));
          guard();
          archiveFile = file; delete draft.content_base64; draft.archive_name = file.name; draftRevision++;
          dialog.querySelector('[data-lifecycle-archive]').textContent = file.name;
        }));
      });
      render();
    }
    return {mount, render, snapshot, commands:{open, close, set_draft, submit, backup, refresh_trash}};
  }
};
