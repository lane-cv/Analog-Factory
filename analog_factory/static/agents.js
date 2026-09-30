'use strict';

// The assistant owns its requests and never redraws the editor or settings drafts.
window.AFAgents = {
  create({state, api, esc, t, html, formatDate, number, onSettled, onContextChange}) {
    const $ = selector => document.querySelector(selector);
    const active = value => ['running', 'waiting_approval', 'queued', 'starting'].includes(value?.status);
    const label = mode => t({'read-only':'Read only', confirm:'Confirm each action', 'workspace-write':'Allow project writes'}[mode] || mode);
    const byteLength = value => new TextEncoder().encode(value).length;
    let backends = [], capabilitiesError = '', projectId = null, generation = 0, capabilityRequest = 0;
    let sessions = [], session = null, loading = false, busy = false, timer = null, error = '', resolvedApproval = null;
    let config = {backend:'api', model:'', mode:'read-only', fast_mode:false}, attachments = [], attachedRunId = null, drafts = {}, historyOpen = false;
    const modelCatalogs = new Map();
    let catalogRequest = 0;
    let historyResults = null, historyRequest = 0, historyTimer = null, attachmentSequence = 0, composing = false;
    const url = (id = session?.id) => `/api/projects/${encodeURIComponent(projectId)}/agent/sessions${id ? `/${encodeURIComponent(id)}` : ''}`;
    const current = ticket => ticket === generation;
    const readCache = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch (_) { return fallback; } };
    const writeCache = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) { /* Browser cache is optional. */ } };
    const remember = id => { try { id ? localStorage.setItem(`af-agent-${projectId}`, id) : localStorage.removeItem(`af-agent-${projectId}`); } catch (_) { /* Server history is authoritative. */ } };
    const selectedBackend = () => backends.find(backend => backend.id === config.backend);
    const modeOptions = () => selectedBackend()?.modes || ['read-only'];
    const projectActive = () => sessions.find(item => active(item) && item.id !== session?.id);
    const slot = () => `${config.backend}:${session?.id || 'new'}`;
    const snapshotDraft = () => ({message:$('#chat-input').value, files:[...state.context], attachments:attachments.map(item => ({...item})), runId:$('#agent-attach-run').checked ? attachedRunId || state.run?.id || null : null});
    function saveDraft() {
      if (!projectId) return;
      drafts[slot()] = snapshotDraft();
      writeCache(`af-agent-drafts-v1-${projectId}`, {config, drafts});
    }
    function restoreDraft(draft = drafts[slot()]) {
      $('#chat-input').value = typeof draft?.message === 'string' ? draft.message : '';
      attachments = Array.isArray(draft?.attachments) ? draft.attachments.filter(item => typeof item.name === 'string' && typeof item.content === 'string').slice(0, 20).map(item => ({...item, id:++attachmentSequence})) : [];
      state.context.clear();
      for (const path of Array.isArray(draft?.files) ? draft.files : []) if (typeof path === 'string') state.context.add(path);
      attachedRunId = typeof draft?.runId === 'string' && draft.runId ? draft.runId : draft?.attachRun ? state.run?.id || null : null;
      $('#agent-attach-run').checked = Boolean(attachedRunId);
      onContextChange?.();
      resizeComposer();
    }
    function setOptions(element, markup, value) {
      if (!element) return;
      if (element.dataset.options !== markup) { element.innerHTML = markup; element.dataset.options = markup; }
      element.value = value;
    }
    function setMarkup(element, markup) {
      if (element && element._agentMarkup !== markup) { element.innerHTML = markup; element._agentMarkup = markup; }
    }
    function render() {
      if (!$('#agent-backend')) return;
      setOptions($('#agent-backend'), backends.map(backend => `<option value="${esc(backend.id)}" ${backend.available ? '' : 'disabled'}>${esc(backend.name || backend.id)}${backend.available ? '' : ` · ${esc(t('unavailable'))}`}</option>`).join(''), config.backend);
      setOptions($('#agent-mode'), modeOptions().map(mode => `<option value="${esc(mode)}">${esc(label(mode))}</option>`).join(''), config.mode);
      if (document.activeElement !== $('#agent-model') && $('#agent-model').value !== config.model) $('#agent-model').value = config.model;
      $('#agent-model').placeholder = config.backend === 'api' ? state.settings.provider?.model || t('Provider default') : t('CLI default');
      setOptions($('#agent-session'), `<option value="">${esc(t('New conversation'))}</option>` + sessions.map(item => `<option value="${esc(item.id)}">${esc(sessionTitle(item))}</option>`).join(''), session?.id || '');
      const running = active(session), blocked = loading || busy || running || Boolean(projectActive());
      for (const id of ['agent-backend', 'agent-mode', 'agent-model', 'agent-model-toggle', 'agent-model-apply', 'agent-new']) if ($(`#${id}`)) $(`#${id}`).disabled = !projectId || blocked;
      if ($('#agent-session')) $('#agent-session').disabled = !projectId || loading || busy;
      const backend = selectedBackend();
      $('#chat-send').disabled = !projectId || blocked || !backend?.available;
      $('#chat-send').hidden = running;
      $('#chat-send').title = !projectId ? t('Select a project first') : backend?.diagnostic || '';
      $('#chat-input').disabled = !projectId || loading;
      $('#agent-stop').hidden = !running;
      $('#agent-stop').disabled = busy;
      $('#agent-status').textContent = loading ? t('Loading…') : busy ? t('Sending…') : t(session?.status || 'New conversation');
      $('#agent-status').dataset.status = session?.status || 'idle';
      $('#agent-diagnostic').textContent = error || capabilitiesError || (projectActive() ? t('Another conversation is running. Select it to view or stop it.') : !backend?.available ? backend?.diagnostic || t('Configure an API provider or install an agent CLI in Settings.') : '');
      $('#agent-diagnostic').hidden = !$('#agent-diagnostic').textContent;
      $('#assistant-model').textContent = session?.model || config.model || (config.backend === 'api' ? state.settings.provider?.model : null) || t('Default model');
      $('#assistant-backend').textContent = `${backend?.name || config.backend} · ${label(config.mode)}`;
      if ($('#agent-project-label')) $('#agent-project-label').textContent = state.project?.name || '';
      if ($('#agent-title')) $('#agent-title').textContent = session ? sessionTitle(session) : t('New conversation');
      $('#agent-permission-note').textContent = t(config.backend === 'api'
        ? config.mode === 'confirm' ? 'Each file write or simulation requires your approval.' : 'Read files and run records. Changes and simulations are disabled.'
        : config.mode === 'workspace-write' ? 'This session may edit project files without individual approval prompts.' : 'CLI runs in read-only mode.');
      $('#agent-context-note').textContent = t(config.backend === 'api' ? 'Selected saved files are attached. The agent can also read project files through tools.' : 'Selected saved files are attached. The CLI can read other files in this project.');
      const contextRunId = attachedRunId || state.run?.id;
      $('#agent-attach-run').disabled = !contextRunId || !projectId;
      if (!contextRunId) $('#agent-attach-run').checked = false;
      $('#agent-run-label').textContent = contextRunId ? t('Attach selected run {id}', {id:String(contextRunId).slice(0, 8)}) : t('Select a run to attach its log');
      const usage = session?.turns?.at(-1)?.usage || (!running ? session?.usage : null);
      $('#assistant-usage').textContent = usage ? usage.total_tokens != null ? t('{count} tokens', {count:number(usage.total_tokens)}) : JSON.stringify(usage) : '';
      if ($('#agent-export')) $('#agent-export').disabled = !session?.turns?.length;
      if ($('#agent-history-toggle')) $('#agent-history-toggle').disabled = !projectId || busy;
      if ($('#agent-add-context')) $('#agent-add-context').disabled = !projectId || loading;
      if ($('#agent-file-input')) $('#agent-file-input').disabled = !projectId || loading;
      renderHistory(); renderContext(); renderModels(); renderConversation();
      renderFast();
      const approval = session?.pending_approval;
      $('#agent-approval').hidden = !approval || historyOpen;
      $('#agent-approval-tool').textContent = approval?.tool || '';
      const args = approval?.arguments || {};
      $('#agent-approval-details').textContent = approval ? typeof args.diff === 'string' ? `${args.path || ''}\n\n${args.diff}` : JSON.stringify(args, null, 2) : '';
      $('#agent-proposed-file').hidden = typeof args.content !== 'string';
      $('#agent-proposed-content').textContent = typeof args.content === 'string' ? args.content : '';
      $('#agent-allow').disabled = busy || approval?.id === resolvedApproval;
      $('#agent-deny').disabled = busy || approval?.id === resolvedApproval;
    }
    function sessionTitle(item) { return item.title || item.turns?.[0]?.message?.slice(0, 80) || `${item.backend} · ${formatDate(item.updated_at || item.created_at)}`; }
    function historyRow(item, compact = false) {
      return `<div class="agent-history-row${item.id === session?.id ? ' selected' : ''}"><button type="button" class="agent-history-select" data-agent-session="${esc(item.id)}" ${busy ? 'disabled' : ''}><span>${esc(sessionTitle(item))}</span><small>${esc(item.backend)} · ${esc(formatDate(item.updated_at || item.created_at))}${active(item) ? ` · ${esc(t(item.status))}` : ''}</small></button>${compact ? '' : `<div class="agent-history-actions"><button type="button" data-agent-rename="${esc(item.id)}" title="${esc(t('Rename conversation'))}" aria-label="${esc(t('Rename conversation'))}" ${active(item) || busy ? 'disabled' : ''}>${icon('edit')}</button><button type="button" data-agent-delete="${esc(item.id)}" title="${esc(t('Delete conversation'))}" aria-label="${esc(t('Delete conversation'))}" ${active(item) || busy ? 'disabled' : ''}>${icon('trash')}</button></div>`}</div>`;
    }
    function renderHistory() {
      if ($('#agent-history')) $('#agent-history').hidden = !historyOpen;
      $('#chat-messages').hidden = historyOpen;
      if ($('#agent-conversation')) $('#agent-conversation').hidden = historyOpen;
      if ($('#agent-workspace-body')) $('#agent-workspace-body').hidden = historyOpen;
      $('#chat-form').hidden = historyOpen;
      $('#agent-history-toggle')?.setAttribute('aria-expanded', String(historyOpen));
      const listed = historyResults || sessions;
      setMarkup($('#agent-history-list'), listed.map(item => historyRow(item)).join('') || `<p class="agent-empty-small">${esc(t($('#agent-history-search')?.value ? 'No matching conversations' : 'No conversations yet'))}</p>`);
      const recent = $('#agent-recent');
      if (recent) {
        recent.hidden = historyOpen || Boolean(session?.turns?.length) || !sessions.length;
        setMarkup(recent, `<div class="agent-section-label">${esc(t('Recent conversations'))}</div>${sessions.slice(0, 3).map(item => historyRow(item, true)).join('')}`);
      }
    }
    function renderContext() {
      setMarkup($('#agent-project-files'), (state.files || []).map(file => `<label class="agent-context-file"><input type="checkbox" data-agent-project-context="${esc(file.path)}" ${state.context.has(file.path) ? 'checked' : ''}><span>${esc(file.path)}</span></label>`).join('') || `<p class="agent-empty-small">${esc(t('No project files'))}</p>`);
      // The shared explorer can update selection without calling this controller.
      $('#agent-project-files')?.querySelectorAll('[data-agent-project-context]').forEach(input => { input.checked = state.context.has(input.dataset.agentProjectContext); });
      setMarkup($('#agent-attachments'), attachments.map(item => `<span class="agent-attachment" title="${esc(item.name)}"><span>${esc(item.name)}</span><small>${number(Math.max(1, Math.ceil(byteLength(item.content) / 1024)))} KB</small><button type="button" data-remove-attachment="${item.id}" aria-label="${esc(t('Remove {path} from context', {path:item.name}))}">×</button></span>`).join(''));
      if ($('#agent-attachments')) $('#agent-attachments').hidden = !attachments.length;
    }
    function icon(name) {
      const paths = {edit:'M12 4l4 4M3 17l4-1L18 5l-4-4L3 12v5',trash:'M3 5h14M7 5V2h6v3M5 5l1 13h8l1-13M8 8v7m4-7v7',star:'m10 1 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L10 15.3l-5.6 2.9 1.1-6.2L1 7.6l6.2-.9Z'};
      return `<svg class="ui-icon" viewBox="0 0 20 20" aria-hidden="true"><path d="${paths[name] || paths.edit}"/></svg>`;
    }
    function renderConversation() {
      const panel = $('#chat-messages'), follow = panel.scrollHeight - panel.scrollTop - panel.clientHeight < 60;
      const turns = session?.turns || [];
      const renderer = window.AFAgentContent;
      const markup = turns.length ? turns.map(turn => renderer ? renderer.renderTurn(turn, {esc, t, html, number, formatDate}) : `<div class="chat-message user"><p>${esc(turn.message)}</p></div><div class="chat-message agent-turn" data-turn-id="${esc(turn.id)}"><p>${esc(turn.reply || turn.error || t('Waiting for response…'))}</p></div>`).join('') : `<div class="assistant-welcome"><span class="welcome-symbol" aria-hidden="true"><svg class="agent-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z"/></svg></span><h3>${esc(t('What can I help you build?'))}</h3><p>${esc(t('Ask about your circuit, review a design, or investigate a run.'))}</p><div class="agent-welcome-prompts"><button type="button" data-agent-prompt="explain">${esc(t('Explain this circuit'))}</button><button type="button" data-agent-prompt="review">${esc(t('Review my design'))}</button></div></div>`;
      if (panel._agentMarkup !== markup) {
        const disclosures = new Map([...panel.querySelectorAll('details[data-disclosure]')].map(item => [item.dataset.disclosure, item.open]));
        const focus = document.activeElement?.closest('[data-agent-action]');
        const focusAction = focus && panel.contains(focus) ? {action:focus.dataset.agentAction, turn:focus.dataset.turnId} : null;
        panel.innerHTML = markup; panel._agentMarkup = markup;
        panel.querySelectorAll('details[data-disclosure]').forEach(item => { if (disclosures.has(item.dataset.disclosure)) item.open = disclosures.get(item.dataset.disclosure); });
        if (focusAction) [...panel.querySelectorAll('[data-agent-action]')].find(item => item.dataset.agentAction === focusAction.action && item.dataset.turnId === focusAction.turn)?.focus({preventScroll:true});
        if (follow) panel.scrollTop = panel.scrollHeight;
      }
      updateScrollButton();
    }
    function updateScrollButton() { const panel = $('#chat-messages'); if ($('#agent-scroll-bottom')) $('#agent-scroll-bottom').hidden = historyOpen || panel.scrollHeight - panel.scrollTop - panel.clientHeight < 60; }
    function modelPrefs() { const value = readCache('af-agent-models-v1', {}); return value[config.backend] || {recent:[], favorites:[]}; }
    function writeModelPrefs(prefs) { const values = readCache('af-agent-models-v1', {}); values[config.backend] = prefs; writeCache('af-agent-models-v1', values); }
    function rememberModel(model) { if (!model) return; const prefs = modelPrefs(); prefs.recent = [model, ...prefs.recent.filter(item => item !== model)].slice(0, 8); writeModelPrefs(prefs); }
    const cliBackend = backend => ['codex', 'claude'].includes(backend);
    const catalogKey = (value = config) => `${projectId}:${value.backend}:${backends.find(item => item.id === value.backend)?.path || ''}`;
    function fastSupported(value = config) {
      const data = modelCatalogs.get(catalogKey(value))?.data;
      if (!cliBackend(value.backend) || !value.model || !data?.fast_mode?.supported) return false;
      const model = data.models?.find(item => item.id === value.model);
      if (model) return model.fast_mode === true;
      return value.backend === 'claude' && /^(opus|claude-opus-(5-5|5|4-8))(\[1m\])?$/.test(value.model);
    }
    function renderFast() {
      const button = $('#agent-fast-toggle'); if (!button) return;
      const entry = modelCatalogs.get(catalogKey()), isCLI = cliBackend(config.backend), requested = config.fast_mode === true, inherited = isCLI && config.fast_mode === null;
      button.hidden = !isCLI;
      button.setAttribute('aria-pressed', inherited ? 'mixed' : String(requested));
      button.disabled = !projectId || loading || busy || active(session) || Boolean(projectActive()) || (!requested && !inherited && !fastSupported());
      let note = !config.model ? t('Select a model to configure Fast mode.') : entry?.loading ? t('Loading models…') : !entry?.data?.fast_mode?.supported ? t(entry?.data?.fast_mode?.reason || 'Fast mode is unavailable for this CLI.') : !fastSupported() ? t('Fast mode is not supported by this model.') : t('Fast requests may cost more or consume usage faster. Availability depends on your account; the CLI may use standard speed.');
      if (requested) note = t('Fast mode requested. Availability and billing are determined by your CLI account.');
      if (inherited) note = t('This older conversation inherits CLI speed settings. Click to start a new conversation with Fast off.');
      button.title = note;
      const description = $('#agent-fast-note'); if (description) { description.hidden = !isCLI; description.textContent = note; }
    }
    async function loadModels(refresh = false) {
      if (!projectId || !cliBackend(config.backend)) return;
      const key = catalogKey(), cached = modelCatalogs.get(key);
      if (!refresh && (cached?.loading || cached?.expires > Date.now())) { renderModels(); renderFast(); return; }
      const request = ++catalogRequest, backend = config.backend;
      modelCatalogs.set(key, {...cached, loading:true, request}); renderModels(); renderFast();
      try {
        const data = await api(`/api/agents/models?backend=${encodeURIComponent(backend)}&project_id=${encodeURIComponent(projectId)}${refresh ? '&refresh=1' : ''}`);
        if (modelCatalogs.get(key)?.request !== request) return;
        if (data?.backend !== backend || !Array.isArray(data.models)) throw new Error(t('Could not load CLI models. You can still enter a custom model ID.'));
        modelCatalogs.set(key, {data, loading:false, expires:Date.now() + (data.source === 'unavailable' ? 30000 : 300000)});
      } catch (_) {
        if (modelCatalogs.get(key)?.request !== request) return;
        modelCatalogs.set(key, {data:{backend, models:[], source:'unavailable', diagnostic:'Could not load CLI models. You can still enter a custom model ID.', fast_mode:{supported:false}}, loading:false, expires:Date.now()+30000});
      }
      if (key === catalogKey()) { renderModels(); renderFast(); }
    }
    function renderModels() {
      const prefs = modelPrefs(), filter = ($('#agent-model-search')?.value || '').trim().toLowerCase();
      const entry = modelCatalogs.get(catalogKey()), catalog = entry?.data?.models || [];
      const models = [...new Set([...prefs.favorites, config.model, ...catalog.map(item => item.id), ...(config.backend === 'api' ? [state.settings.provider?.model, ...(state.providerModels || []).map(item => typeof item === 'string' ? item : item.id)] : []), ...prefs.recent].filter(Boolean))];
      const locked = !projectId || loading || busy || active(session) || Boolean(projectActive());
      setMarkup($('#agent-model-options'), `<button type="button" class="agent-model-option" data-agent-model="" ${locked ? 'disabled' : ''}>${esc(t(config.backend === 'api' ? 'Provider default' : 'CLI default'))}</button>` + models.filter(model => !filter || `${model} ${catalog.find(item => item.id === model)?.label || ''}`.toLowerCase().includes(filter)).map(model => { const detail = catalog.find(item => item.id === model); return `<div class="agent-model-row"><button type="button" class="agent-model-option" data-agent-model="${esc(model)}" title="${esc(detail?.description || model)}" ${locked ? 'disabled' : ''}><span>${esc(detail?.label || model)}${detail?.label && detail.label !== model ? `<small>${esc(model)}</small>` : ''}</span>${model === config.model ? '<span aria-hidden="true">✓</span>' : detail?.fast_mode ? '<span class="agent-model-speed">Fast</span>' : ''}</button><button type="button" class="agent-model-favorite${prefs.favorites.includes(model) ? ' selected' : ''}" data-agent-favorite="${esc(model)}" aria-label="${esc(t(prefs.favorites.includes(model) ? 'Remove favorite model' : 'Favorite model'))}" aria-pressed="${prefs.favorites.includes(model)}">${icon('star')}</button></div>`; }).join(''));
      const status = $('#agent-model-catalog-status');
      if (status) { status.hidden = !cliBackend(config.backend); status.textContent = entry?.loading ? t('Loading models…') : entry?.data?.source === 'cli' ? t('Models reported by the installed CLI. Account access may vary.') : entry?.data?.source === 'builtin' ? t('Built-in CLI aliases. Your account determines availability.') : t(entry?.data?.diagnostic || 'Choose a model or enter a custom model ID.'); }
      const refresh = $('#agent-model-refresh'); if (refresh) { refresh.hidden = !cliBackend(config.backend); refresh.disabled = locked || Boolean(entry?.loading); }
    }
    function storeSession(value) {
      const settled = session?.id === value.id && active(session) && !active(value);
      session = value;
      config = {backend:value.backend, model:value.model || '', mode:value.mode, fast_mode:typeof value.fast_mode === 'boolean' ? value.fast_mode : cliBackend(value.backend) ? null : false};
      sessions = [value, ...sessions.filter(item => item.id !== value.id)];
      remember(value.id);
      render(); schedule(); loadModels();
      if (settled && onSettled) {
        const ticket = generation;
        Promise.resolve(onSettled(projectId)).catch(cause => { if (current(ticket)) { error = cause.message; render(); } });
      }
    }
    function schedule() { clearTimeout(timer); if (active(session) || projectActive()) timer = setTimeout(poll, 1000); }
    async function poll() {
      const ticket = generation;
      if (busy) { schedule(); return; }
      try {
        if (session && active(session)) { const value = await api(url()); if (!current(ticket)) return; storeSession(value); }
        else if (projectActive()) { const values = await api(url(null)); if (!current(ticket)) return; sessions = Array.isArray(values) ? values : values.sessions || []; render(); schedule(); }
        error = ''; render();
      } catch (cause) { if (!current(ticket)) return; error = t('Could not refresh agent: {detail}', {detail:cause.message}); render(); schedule(); }
    }
    async function refreshCapabilities() {
      const ticket = generation, id = projectId, request = ++capabilityRequest;
      const valid = () => current(ticket) && id === projectId && request === capabilityRequest;
      try {
        const result = await api(`/api/agents${id ? `?project_id=${encodeURIComponent(id)}` : ''}`);
        if (!valid()) return;
        backends = result.backends || []; capabilitiesError = '';
        if (!session && !backends.some(backend => backend.id === config.backend && backend.available)) config = {backend:backends.find(backend => backend.available)?.id || backends[0]?.id || 'api', model:'', mode:'read-only', fast_mode:false};
      } catch (cause) { if (!valid()) return; capabilitiesError = cause.message; }
      render(); loadModels();
    }
    async function selectSession(id, save = true) {
      if (busy) return;
      if (save) saveDraft();
      const previous = session, ticket = ++generation;
      clearTimeout(timer); error = ''; resolvedApproval = null; loading = true; session = null; historyOpen = false; closePopovers(); render();
      try {
        const value = await api(url(id));
        if (!current(ticket)) return;
        loading = false; storeSession(value); restoreDraft(); render();
      } catch (cause) { if (current(ticket)) { loading = false; session = previous; restoreDraft(); error = cause.message; render(); schedule(); } }
    }
    function newSession(reset = false, preserve = false) {
      if (busy || active(session) || projectActive()) return;
      const draft = preserve ? snapshotDraft() : null;
      saveDraft(); ++generation; clearTimeout(timer); session = null; busy = false; loading = false; error = ''; resolvedApproval = null;
      if (reset) config = {...config, model:'', mode:'read-only', fast_mode:false};
      remember(null); historyOpen = false; closePopovers();
      restoreDraft(draft || {message:'', attachments:[]}); saveDraft(); render(); schedule(); $('#chat-input').focus();
    }
    function changeConfig(next) {
      if (busy || active(session) || projectActive()) return;
      const draft = snapshotDraft(); saveDraft();
      ++generation; clearTimeout(timer); session = null; error = ''; resolvedApproval = null;
      const modelChanged = next.backend !== config.backend || next.model !== config.model;
      config = {...next, fast_mode:next.fast_mode === true && cliBackend(next.backend) && (!modelChanged || fastSupported(next))};
      remember(null); restoreDraft(draft); saveDraft(); render(); schedule(); loadModels();
    }
    async function selectProject(id) {
      saveDraft();
      const ticket = ++generation;
      ++historyRequest; ++catalogRequest; clearTimeout(historyTimer); clearTimeout(timer);
      for (const [key, entry] of modelCatalogs) if (entry.loading) modelCatalogs.delete(key);
      projectId = id; sessions = []; session = null; loading = Boolean(id); busy = false; error = ''; resolvedApproval = null; historyResults = null; historyOpen = false;
      const cache = id ? readCache(`af-agent-drafts-v1-${id}`, {}) : {};
      drafts = cache.drafts && typeof cache.drafts === 'object' ? cache.drafts : {};
      config = cache.config || {backend:backends.find(backend => backend.id === 'api' && backend.available)?.id || backends.find(backend => backend.available)?.id || 'api', model:'', mode:'read-only', fast_mode:false};
      config.fast_mode = config.fast_mode === true && cliBackend(config.backend);
      if ($('#agent-history-search')) $('#agent-history-search').value = '';
      restoreDraft(); closePopovers(); render();
      if (!id) return;
      try {
        const [values] = await Promise.all([api(url(null)), refreshCapabilities()]);
        if (!current(ticket)) return;
        sessions = Array.isArray(values) ? values : values.sessions || [];
        let saved; try { saved = localStorage.getItem(`af-agent-${id}`); } catch (_) { /* Optional cache. */ }
        const selected = sessions.find(item => item.id === saved) || sessions.find(active);
        loading = false;
        if (selected) await selectSession(selected.id, false); else { restoreDraft(); render(); }
      } catch (cause) { if (current(ticket)) { loading = false; error = cause.message; render(); } }
    }
    async function send() {
      const rawMessage = $('#chat-input').value, message = rawMessage.trim();
      if (!message || $('#chat-send').disabled || composing) return;
      if (executeSlash(message)) return;
      const ticket = ++generation, sentAttachments = [...attachments], oldSlot = slot();
      const contents = {message, files:[...state.context], attachments:sentAttachments.map(({name, content}) => ({name, content}))};
      if (contents.files.length + contents.attachments.length > 20) { error = t('Attach at most 20 files.'); render(); return; }
      if ($('#agent-attach-run').checked && (attachedRunId || state.run?.id)) contents.run_id = attachedRunId || state.run.id;
      busy = true; error = ''; saveDraft(); render();
      try {
        if (!session) { const value = await api(url(null), {method:'POST', body:{...config}}); if (!current(ticket)) return; storeSession(value); }
        const value = await api(`${url()}/messages`, {method:'POST', body:contents});
        if (!current(ticket)) return;
        if ($('#chat-input').value === rawMessage) $('#chat-input').value = '';
        attachments = attachments.filter(item => !sentAttachments.some(sent => sent.id === item.id));
        delete drafts[oldSlot]; storeSession(value); rememberModel(config.model); saveDraft(); resizeComposer();
      } catch (cause) { if (current(ticket)) { error = cause.message; saveDraft(); } }
      finally { if (current(ticket)) { busy = false; render(); schedule(); } }
    }
    async function mutate(action, body) {
      if (!session || busy) return;
      const ticket = ++generation;
      busy = true; error = ''; clearTimeout(timer); render();
      try { const value = await api(`${url()}/${action}`, {method:'POST', body}); if (current(ticket)) { if (action === 'approval') resolvedApproval = body.approval_id; storeSession(value); } }
      catch (cause) { if (current(ticket)) error = cause.message; }
      finally { if (current(ticket)) { busy = false; render(); schedule(); } }
    }
    async function searchHistory() {
      const query = $('#agent-history-search')?.value.trim() || '', request = ++historyRequest, id = projectId;
      if (!id) return;
      try {
        const values = await api(`${url(null)}${query ? `?q=${encodeURIComponent(query)}` : ''}`);
        if (id !== projectId || request !== historyRequest) return;
        historyResults = query ? Array.isArray(values) ? values : values.sessions || [] : null;
        if (!query) sessions = Array.isArray(values) ? values : values.sessions || [];
        renderHistory();
      } catch (cause) { if (id === projectId && request === historyRequest) { error = cause.message; render(); } }
    }
    function showHistory(show) { historyOpen = show; closePopovers(); render(); if (show) { searchHistory(); $('#agent-history-search')?.focus(); } else $('#chat-input').focus(); }
    async function renameSession(id) {
      const item = sessions.find(value => value.id === id); if (!item || active(item) || busy) return;
      const title = window.prompt(t('Rename conversation'), sessionTitle(item)); if (!title?.trim() || title.trim() === item.title) return;
      if (title.trim().length > 120) { error = t('Conversation titles must be 120 characters or fewer.'); render(); return; }
      const ticket = generation;
      try { const value = await api(url(id), {method:'PUT', body:{title:title.trim()}}); if (!current(ticket)) return; sessions = sessions.map(item => item.id === id ? value : item); if (session?.id === id) session = value; await searchHistory(); render(); }
      catch (cause) { if (current(ticket)) { error = cause.message; render(); } }
    }
    async function deleteSession(id) {
      const item = sessions.find(value => value.id === id); if (!item || active(item) || busy) return;
      if (!window.confirm(t('Delete this conversation? This cannot be undone.'))) return;
      const ticket = generation;
      try {
        await api(url(id), {method:'DELETE'}); if (!current(ticket)) return;
        sessions = sessions.filter(item => item.id !== id); delete drafts[`${item.backend}:${id}`];
        if (session?.id === id) { session = null; remember(null); restoreDraft(); }
        saveDraft(); await searchHistory(); render();
      } catch (cause) { if (current(ticket)) { error = cause.message; render(); } }
    }
    function exportConversation(format) {
      if (!session?.turns?.length) return;
      const value = window.AFAgentContent?.exportSession(session, format) || {content:JSON.stringify(session, null, 2), filename:`conversation-${session.id}.json`, type:'application/json'};
      const link = document.createElement('a'), objectURL = URL.createObjectURL(new Blob([value.content], {type:value.type}));
      link.href = objectURL; link.download = value.filename; link.click(); setTimeout(() => URL.revokeObjectURL(objectURL), 1000); closePopovers();
    }
    const popovers = [['agent-export', 'agent-export-menu'], ['agent-add-context', 'agent-context-picker'], ['agent-model-toggle', 'agent-model-picker'], ['agent-commands', 'agent-command-list']];
    function closePopovers(except = '') { for (const [button, panel] of popovers) if (panel !== except && $(`#${panel}`)) { $(`#${panel}`).hidden = true; $(`#${button}`)?.setAttribute('aria-expanded', 'false'); } }
    function togglePopover(button, panel) {
      const element = $(`#${panel}`); if (!element) return;
      const show = element.hidden; closePopovers(); element.hidden = !show; $(`#${button}`)?.setAttribute('aria-expanded', String(show));
      if (show && panel === 'agent-model-picker') { $('#agent-model').value = config.model; if ($('#agent-model-search')) $('#agent-model-search').value = ''; renderModels(); loadModels(); ($('#agent-model-search') || $('#agent-model')).focus(); }
      if (show && panel === 'agent-context-picker') renderContext();
      if (show && panel === 'agent-command-list') renderCommands();
    }
    async function addFiles(files) {
      if (!projectId || loading) return;
      const ticket = generation, id = projectId;
      const added = [];
      try {
        if (attachments.length + files.length + state.context.size > 20) throw new Error(t('Attach at most 20 files.'));
        let total = attachments.reduce((size, item) => size + byteLength(item.content), 0);
        for (const file of files) {
          if (file.size > 65536) throw new Error(t('Each text attachment must fit within 64 KiB.'));
          if (total + file.size > 256000) throw new Error(t('Attached text must fit within 256 KB.'));
          if (file.type && !/^text\//.test(file.type) && !/^(application\/(json|xml|javascript|x-yaml|yaml|toml|sql|x-sh|octet-stream))$/.test(file.type)) throw new Error(t('Only text files can be attached.'));
          const bytes = new Uint8Array(await file.arrayBuffer());
          const content = new TextDecoder('utf-8', {fatal:true}).decode(bytes);
          if (content.includes('\0')) throw new Error(t('Only text files can be attached.'));
          total += byteLength(content); if (total > 256000) throw new Error(t('Attached text must fit within 256 KB.'));
          added.push({id:++attachmentSequence, name:file.name, content});
        }
        if (!current(ticket) || id !== projectId) return;
        if (attachments.length + added.length + state.context.size > 20) throw new Error(t('Attach at most 20 files.'));
        if ([...attachments, ...added].reduce((size, item) => size + byteLength(item.content), 0) > 256000) throw new Error(t('Attached text must fit within 256 KB.'));
        attachments.push(...added); error = ''; saveDraft(); render();
      } catch (cause) { if (current(ticket) && id === projectId) { error = cause instanceof TypeError ? t('Only UTF-8 text files can be attached.') : cause.message; render(); } }
      finally { if ($('#agent-file-input')) $('#agent-file-input').value = ''; }
    }
    function resizeComposer() { const input = $('#chat-input'); if (input) input.style.height = ''; }
    function insertPrompt(value) { $('#chat-input').value = value; closePopovers(); saveDraft(); resizeComposer(); $('#chat-input').focus(); }
    function editResend(turn) {
      if (!projectId || busy || loading) return;
      restoreDraft({message:turn.message, files:turn.files || [], attachments:turn.attachments || [], runId:turn.run_id || null});
      closePopovers(); saveDraft(); render(); $('#chat-input').focus();
    }
    const promptKeys = {explain:'Explain the selected circuit files, including their topology, signal flow, and key design assumptions.', review:'Review the selected circuit files for design errors, missing checks, and opportunities to improve the design.', simulate:'Inspect the selected testbench and run records. Explain how to validate the design and run a simulation if permissions allow.'};
    const commandLabels = {new:'New conversation', history:'Conversation history', export:'Export conversation', explain:'Explain this circuit', review:'Review my design', simulate:'Inspect simulation'};
    const quickMessages = () => readCache('af-agent-quick-messages-v1', []).filter(item => typeof item.title === 'string' && typeof item.message === 'string');
    function renderCommands() {
      const query = $('#chat-input').value.trim().startsWith('/') ? $('#chat-input').value.trim().slice(1).toLowerCase() : '';
      setMarkup($('#agent-command-list'), Object.entries(commandLabels).filter(([key, value]) => !query || `${key} ${t(value)}`.toLowerCase().includes(query)).map(([key, value]) => `<button type="button" data-agent-command="${key}"><span>/${key}</span><small>${esc(t(value))}</small></button>`).join('') + quickMessages().map((item, index) => `<button type="button" data-agent-quick="${index}"><span>${esc(item.title)}</span></button>`).join('') + `<button type="button" data-agent-manage-quick>${esc(t('Manage quick messages'))}</button>`);
    }
    function executeSlash(message) {
      const match = message.match(/^\/(new|history|export|explain|review|simulate)$/); if (!match) return false;
      $('#chat-input').value = ''; saveDraft(); runCommand(match[1]); return true;
    }
    function runCommand(command) {
      closePopovers();
      if (command === 'new') newSession(true);
      else if (command === 'history') showHistory(true);
      else if (command === 'export') { if (session?.turns?.length) togglePopover('agent-export', 'agent-export-menu'); }
      else if (promptKeys[command]) insertPrompt(t(promptKeys[command]));
    }
    function manageQuickMessages() {
      closePopovers();
      let dialog = $('#agent-quick-message-dialog');
      if (!dialog) {
        dialog = document.createElement('dialog'); dialog.id = 'agent-quick-message-dialog'; dialog.className = 'agent-quick-dialog';
        dialog.setAttribute('aria-labelledby', 'agent-quick-title');
        dialog.addEventListener('close', () => { const input = $('#chat-input'); if (!dialog.open && input?.getClientRects().length && !input.disabled) input.focus(); });
        document.body.append(dialog);
      }
      dialog.innerHTML = `<form method="dialog"><h3 id="agent-quick-title">${esc(t('Quick messages'))}</h3><p>${esc(t('Save reusable prompts in this browser.'))}</p><div id="agent-quick-list">${quickMessages().map((item, index) => `<div class="agent-quick-row"><span>${esc(item.title)}</span><button type="button" data-delete-quick="${index}" aria-label="${esc(t('Delete'))}">×</button></div>`).join('')}</div><label>${esc(t('Title'))}<input name="title" maxlength="80"></label><label>${esc(t('Message'))}<textarea name="message" rows="4"></textarea></label><div class="button-row"><button type="button" data-save-quick class="primary-button">${esc(t('Save quick message'))}</button><button type="submit" class="secondary-button">${esc(t('Close'))}</button></div></form>`;
      dialog.querySelector('[data-save-quick]').addEventListener('click', () => {
        const title = dialog.querySelector('[name=title]').value.trim(), message = dialog.querySelector('[name=message]').value.trim();
        if (!title || !message) return;
        writeCache('af-agent-quick-messages-v1', [...quickMessages(), {title, message}].slice(-30)); dialog.close(); renderCommands();
      });
      dialog.querySelectorAll('[data-delete-quick]').forEach(button => button.addEventListener('click', () => { writeCache('af-agent-quick-messages-v1', quickMessages().filter((_, index) => index !== Number(button.dataset.deleteQuick))); dialog.close(); manageQuickMessages(); }));
      dialog.showModal();
    }
    async function copyText(text, button) {
      try {
        if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
        else { const field = document.createElement('textarea'); field.value = text; field.style.position = 'fixed'; field.style.opacity = '0'; document.body.append(field); field.select(); const copied = document.execCommand('copy'); field.remove(); if (!copied) throw new Error(t('Could not copy text.')); }
        const title = button.title; button.title = t('Copied'); button.dataset.copied = 'true'; setTimeout(() => { button.title = title; delete button.dataset.copied; }, 1500);
      } catch (cause) { error = cause.message; render(); }
    }
    function bind() {
      const on = (id, event, fn) => $(`#${id}`)?.addEventListener(event, fn);
      on('agent-new', 'click', () => newSession(true));
      on('agent-session', 'change', event => event.target.value ? selectSession(event.target.value) : newSession());
      on('agent-backend', 'change', event => changeConfig({backend:event.target.value, model:'', mode:'read-only', fast_mode:false}));
      on('agent-model-refresh', 'click', () => loadModels(true));
      on('agent-fast-toggle', 'click', () => { if (config.fast_mode !== false || fastSupported()) changeConfig({...config, fast_mode:config.fast_mode === null ? false : !config.fast_mode}); });
      on('agent-mode', 'change', event => changeConfig({...config, mode:event.target.value}));
      on('agent-model', 'input', renderModels);
      on('agent-model-search', 'input', renderModels);
      on('agent-model', 'change', event => { if ($('#agent-model-picker')?.hidden !== false) changeConfig({...config, model:event.target.value.trim()}); });
      on('agent-model', 'keydown', event => { if (event.key === 'Enter') { event.preventDefault(); event.stopPropagation(); $('#agent-model-apply')?.click(); } });
      on('agent-model-apply', 'click', () => { const model = $('#agent-model').value.trim(); changeConfig({...config, model}); rememberModel(model); closePopovers(); $('#chat-input').focus(); });
      on('agent-stop', 'click', () => mutate('cancel', {}));
      for (const [id, approved] of [['agent-allow', true], ['agent-deny', false]]) on(id, 'click', () => { if (session?.pending_approval) mutate('approval', {approval_id:session.pending_approval.id, approved}); });
      on('agent-history-toggle', 'click', () => showHistory(!historyOpen));
      on('agent-history-close', 'click', () => showHistory(false));
      if ($('#agent-history-search')) $('#agent-history-search').maxLength = 200;
      on('agent-history-search', 'input', () => { clearTimeout(historyTimer); ++historyRequest; historyTimer = setTimeout(searchHistory, 180); });
      on('agent-file-input', 'change', event => addFiles([...event.target.files]));
      on('agent-attach-run', 'change', () => { attachedRunId = $('#agent-attach-run').checked ? state.run?.id || attachedRunId : null; saveDraft(); render(); });
      on('chat-input', 'compositionstart', () => { composing = true; });
      on('chat-input', 'compositionend', () => { composing = false; saveDraft(); });
      on('chat-input', 'input', () => { saveDraft(); resizeComposer(); const value = $('#chat-input').value; if (value.startsWith('/') && !value.includes('\n')) { closePopovers('agent-command-list'); if ($('#agent-command-list')) $('#agent-command-list').hidden = false; renderCommands(); } else if ($('#agent-command-list')) $('#agent-command-list').hidden = true; });
      on('chat-input', 'keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && !composing && event.keyCode !== 229) { event.preventDefault(); event.stopPropagation(); send(); } });
      on('chat-input', 'paste', event => { const files = [...(event.clipboardData?.files || [])]; if (files.length) { event.preventDefault(); addFiles(files); } });
      on('chat-form', 'dragover', event => { if (event.dataTransfer?.types.includes('Files')) { event.preventDefault(); event.currentTarget.classList.add('is-dragging'); } });
      on('chat-form', 'dragleave', event => { if (!event.currentTarget.contains(event.relatedTarget)) event.currentTarget.classList.remove('is-dragging'); });
      on('chat-form', 'drop', event => { event.currentTarget.classList.remove('is-dragging'); if (event.dataTransfer?.files.length) { event.preventDefault(); addFiles([...event.dataTransfer.files]); } });
      on('chat-messages', 'scroll', updateScrollButton);
      on('agent-scroll-bottom', 'click', () => { $('#chat-messages').scrollTop = $('#chat-messages').scrollHeight; updateScrollButton(); });
      for (const [button, panel] of popovers) on(button, 'click', () => togglePopover(button, panel));
      if ($('#agent-export-menu') && !$('#agent-export-menu').children.length) $('#agent-export-menu').innerHTML = ['md', 'json', 'txt'].map(format => `<button type="button" data-agent-export="${format}">${{md:'Markdown', json:'JSON', txt:t('Plain text')}[format]}</button>`).join('');
      document.addEventListener('click', event => {
        const target = event.target.closest('button');
        if (target && !target.disabled) {
          const data = target.dataset;
          if (data.agentSession) selectSession(data.agentSession);
          else if (data.agentRename) renameSession(data.agentRename);
          else if (data.agentDelete) deleteSession(data.agentDelete);
          else if (data.agentExport) exportConversation(data.agentExport);
          else if (data.removeAttachment) { attachments = attachments.filter(item => String(item.id) !== data.removeAttachment); saveDraft(); renderContext(); }
          else if (data.agentModel !== undefined) { changeConfig({...config, model:data.agentModel}); rememberModel(data.agentModel); closePopovers(); }
          else if (data.agentFavorite !== undefined) { const prefs = modelPrefs(), model = data.agentFavorite; prefs.favorites = prefs.favorites.includes(model) ? prefs.favorites.filter(item => item !== model) : [...prefs.favorites, model]; writeModelPrefs(prefs); renderModels(); }
          else if (data.agentCommand) runCommand(data.agentCommand);
          else if (data.agentPrompt) insertPrompt(t(promptKeys[data.agentPrompt] || data.agentPrompt));
          else if (data.agentQuick !== undefined) { const item = quickMessages()[Number(data.agentQuick)]; if (item) insertPrompt(item.message); }
          else if (data.agentManageQuick !== undefined) manageQuickMessages();
          else if (data.copyCode !== undefined) { try { copyText(decodeURIComponent(data.copyCode), target); } catch (_) { /* Invalid external markup is ignored. */ } }
          else if (data.agentAction) { const turn = session?.turns?.find(item => item.id === data.turnId); if (turn) { if (data.agentAction === 'edit-resend') editResend(turn); else copyText(data.agentAction === 'copy-user' ? turn.message : turn.reply || '', target); } }
        }
        if (!popovers.some(([button, panel]) => $(`#${button}`)?.contains(event.target) || $(`#${panel}`)?.contains(event.target))) closePopovers();
      });
      document.addEventListener('change', event => { if (event.target.dataset.agentProjectContext !== undefined) { const path = event.target.dataset.agentProjectContext; event.target.checked ? state.context.add(path) : state.context.delete(path); onContextChange?.(); renderContext(); } });
      document.addEventListener('keydown', event => { if (event.key === 'Escape') { const open = popovers.find(([, panel]) => $(`#${panel}`) && !$(`#${panel}`).hidden); closePopovers(); if (open) { event.preventDefault(); $(`#${open[0]}`)?.focus(); } } });
      window.addEventListener('pagehide', saveDraft);
      renderCommands();
    }
    return {refreshCapabilities, selectProject, render, send, bind};
  }
};
