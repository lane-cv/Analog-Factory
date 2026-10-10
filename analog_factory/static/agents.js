'use strict';

// The assistant owns its requests and never redraws the editor or settings drafts.
window.AFAgents = {
  create({state, api, esc, t, html, formatDate, number, onSettled, onContextChange}) {
    const $ = selector => document.querySelector(selector);
    const active = value => ['running', 'waiting_approval', 'queued', 'starting'].includes(value?.status);
    const label = mode => t({'read-only':'Read only', confirm:'Confirm each action', automatic:'Automatic within scope', 'workspace-write':'Allow project writes'}[mode] || mode);
    const byteLength = value => new TextEncoder().encode(value).length;
    const attachmentBytes = item => item.data_base64 ? Math.floor(item.data_base64.length * .75) : byteLength(item.content || '');
    const defaultScope = () => ({operations:['file.write','run.start'],paths:['notes/','testbenches/'],max_actions:4,max_runs:2});
    let backends = [], capabilitiesError = '', projectId = null, generation = 0, capabilityRequest = 0;
    let sessions = [], session = null, loading = false, busy = false, timer = null, error = '', resolvedApproval = null;
    let autoApprovalChange = null;
    let config = {backend:'api', model:'', mode:'read-only', fast_mode:false, effort:null}, attachments = [], attachedRunId = null, drafts = {}, historyOpen = false;
    const modelCatalogs = new Map(), effortMetadata = new Map();
    let catalogRequest = 0, effortRequest = 0;
    let historyResults = null, historyRequest = 0, historyTimer = null, attachmentSequence = 0, composing = false, draftRevision = 0;
    const url = (id = session?.id) => `/api/projects/${encodeURIComponent(projectId)}/agent/sessions${id ? `/${encodeURIComponent(id)}` : ''}`;
    const current = ticket => ticket === generation;
    const readCache = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch (_) { return fallback; } };
    const writeCache = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) { /* Browser cache is optional. */ } };
    const remember = id => { try { id ? localStorage.setItem(`af-agent-${projectId}`, id) : localStorage.removeItem(`af-agent-${projectId}`); } catch (_) { /* Server history is authoritative. */ } };
    const selectedBackend = () => backends.find(backend => backend.id === config.backend);
    const imageCapable = () => {
      const backend = selectedBackend(), model = config.model || backend?.default_model;
      return config.backend === 'api' && Boolean(backend?.model_capabilities?.[model]?.images ?? (model === backend?.default_model && backend?.capabilities?.images));
    };
    const modeOptions = () => selectedBackend()?.modes || ['read-only'];
    const projectActive = () => sessions.find(item => active(item) && item.id !== session?.id);
    const slot = () => `${config.backend}:${session?.id || 'new'}`;
    const snapshotDraft = () => ({message:$('#chat-input').value, files:[...state.context], attachments:attachments.map(item => ({...item})), runId:$('#agent-attach-run').checked ? attachedRunId || state.run?.id || null : null});
    function saveDraft() {
      if (!projectId) return;
      draftRevision++;
      drafts[slot()] = snapshotDraft();
      writeCache(`af-agent-drafts-v1-${projectId}`, {config, drafts});
    }
    function restoreDraft(draft = drafts[slot()]) {
      $('#chat-input').value = typeof draft?.message === 'string' ? draft.message : '';
      attachments = Array.isArray(draft?.attachments) ? draft.attachments.filter(item => typeof item.name === 'string' && (typeof item.content === 'string' || typeof item.data_base64 === 'string')).slice(0, 20).map(item => ({...item, id:++attachmentSequence})) : [];
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
      $('#agent-model').placeholder = config.backend === 'api' ? selectedBackend()?.default_model || state.settings.provider?.model || t('Provider default') : t('CLI default');
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
      $('#assistant-model').textContent = session?.model || config.model || (config.backend === 'api' ? selectedBackend()?.default_model || state.settings.provider?.model : null) || t('Default model');
      $('#assistant-backend').textContent = `${backend?.name || config.backend} · ${label(config.mode)}`;
      if ($('#agent-project-label')) $('#agent-project-label').textContent = state.project?.name || '';
      if ($('#agent-title')) $('#agent-title').textContent = session ? sessionTitle(session) : t('New conversation');
      $('#agent-permission-note').textContent = t(officialReview() && session?.auto_approval
        ? 'Actions needing approval are reviewed by Codex. You can stop this conversation.'
        : config.mode === 'automatic' ? 'The saved scope authorizes limited actions. Other changes still require your approval.'
        : ['confirm','workspace-write'].includes(config.mode) ? 'Each change or execution requires your approval.'
        : config.backend === 'api' ? 'Read files and run records. Changes and simulations are disabled.' : 'CLI runs in read-only mode.');
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
      renderFast(); renderEffort(); renderScope();
      const approval = session?.pending_approval;
      $('#agent-approval').hidden = !approval || historyOpen;
      $('#agent-approval-tool').textContent = approval?.tool || '';
      const args = approval?.arguments || {};
      $('#agent-approval-details').textContent = approval ? typeof args.diff === 'string' ? `${args.path || ''}\n\n${args.diff}` : JSON.stringify(args, null, 2) : '';
      $('#agent-proposed-file').hidden = typeof args.content !== 'string';
      $('#agent-proposed-content').textContent = typeof args.content === 'string' ? args.content : '';
      $('#agent-allow').disabled = busy || approval?.id === resolvedApproval;
      $('#agent-deny').disabled = busy || approval?.id === resolvedApproval;
      renderApprovalReview(approval);
    }
    function reviewCapability() { return selectedBackend()?.approval_review || {}; }
    function officialReview() { return config.backend === 'codex' || reviewCapability().provider === 'codex_official'; }
    function reviewUnavailable() { return reviewCapability().diagnostic || t(officialReview() ? 'Codex official Auto-review is unavailable. Check the installed Codex CLI and account.' : 'Configure a project Model API to review approvals.'); }
    function renderApprovalReview(approval) {
      const capability = reviewCapability(), review = approval?.review, official = officialReview();
      const enabled = session?.auto_approval === true, readOnly = session?.mode === 'read-only';
      const locked = active(session) && (official || capability.runtime_toggle === false);
      const toggle = $('#agent-auto-approval'), pendingChange = autoApprovalChange?.project_id === projectId && autoApprovalChange?.session_id === session?.id;
      toggle.checked = pendingChange ? autoApprovalChange.enabled : enabled;
      toggle.disabled = !session || loading || busy || locked || (!enabled && (readOnly || !capability.available));
      const label = $('#agent-auto-approval-label');
      label.dataset.i18n = official ? 'Codex official automatic approval' : 'Project AI automatic approval';
      label.textContent = t(label.dataset.i18n);
      $('#agent-auto-approval-note').textContent = !session ? t('Start a conversation to enable automatic approval.') : readOnly ? t('Read-only conversations cannot enable automatic approval.') : locked ? t('Stop the current turn before changing Codex official Auto-review.') : !capability.available ? reviewUnavailable() : t(official ? 'Uses Codex official Auto-review through your Codex account. Turn it off for manual approvals. This conversation only; resets when the server restarts.' : 'The project Model API reviews each action; uncertain decisions stay with you. This conversation only; resets when the server restarts.');
      const status = $('#agent-review-status');
      const labels = {reviewing:'AI is reviewing this action…',manual:'Human decision required',approved:'AI approved this action',rejected:'AI rejected this action',cancelled:'AI review stopped'};
      status.textContent = official && approval ? t('Codex official Auto-review does not support single-action review here. Allow or deny this action, or stop the turn to change automatic approval.') : review ? [t(labels[review.status] || 'Human decision required'), review.model, review.reason].filter(Boolean).join('\n') : approval && !capability.available ? reviewUnavailable() : '';
      status.hidden = !status.textContent;
      $('#agent-review').hidden = official || capability.single_action === false;
      $('#agent-review').disabled = !approval || loading || busy || approval.id === resolvedApproval || review?.status === 'reviewing' || !capability.available || readOnly || official || capability.single_action === false;
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
      setMarkup($('#agent-attachments'), attachments.map(item => `<span class="agent-attachment" title="${esc(item.name)}"><span>${item.data_base64 ? `${esc(t('Image'))}: ` : ''}${esc(item.name)}</span><small>${number(Math.max(1, Math.ceil(attachmentBytes(item) / 1024)))} KB</small><button type="button" data-remove-attachment="${item.id}" aria-label="${esc(t('Remove {path} from context', {path:item.name}))}">×</button></span>`).join(''));
      if ($('#agent-attachments')) $('#agent-attachments').hidden = !attachments.length;
      const picker = $('#agent-file-input');
      if (picker) {
        if (!picker.dataset.textAccept) picker.dataset.textAccept = picker.accept;
        picker.accept = picker.dataset.textAccept + (imageCapable() ? ',image/png,image/jpeg,image/webp' : '');
        const title = t(imageCapable() ? 'Attach text or images' : 'Attach text files');
        picker.setAttribute('aria-label', title);
        const text = picker.closest('label')?.querySelector('span');
        if (text) text.textContent = title;
      }
    }
    function renderScope() {
      let panel = $('#agent-auto-scope');
      if (!panel) {
        panel = document.createElement('details'); panel.id = 'agent-auto-scope'; panel.className = 'agent-auto-scope';
        $('#chat-form')?.prepend(panel);
        panel.addEventListener('change', () => {
          if (active(session) || busy || loading) return;
          const scope = {operations:[...panel.querySelectorAll('[data-scope-operation]:checked')].map(item => item.value),
            paths:panel.querySelector('[data-scope-paths]').value.split('\n').map(path => path.trim()).filter(Boolean),
            max_actions:Number(panel.querySelector('[data-scope-actions]').value),max_runs:Number(panel.querySelector('[data-scope-runs]').value)};
          changeConfig({...config, scope});
        });
      }
      panel.hidden = config.mode !== 'automatic';
      if (panel.hidden) return;
      const scope = config.scope || defaultScope(), locked = active(session) || busy || loading;
      const summary = `${t('Automatic scope')} · ${scope.max_actions} ${t('actions')} / ${scope.max_runs} ${t('runs')}`;
      setMarkup(panel, `<summary>${esc(summary)}</summary><p class="agent-note">${esc(t('Sending creates a fixed grant for this conversation. Actions outside it require approval.'))}</p><fieldset ${locked ? 'disabled' : ''}><label><input type="checkbox" data-scope-operation value="file.write" ${scope.operations.includes('file.write') ? 'checked' : ''}>${esc(t('Write files'))}</label><label><input type="checkbox" data-scope-operation value="run.start" ${scope.operations.includes('run.start') ? 'checked' : ''}>${esc(t('Run simulation and verification'))}</label><label>${esc(t('Allowed paths, one per line; end directories with /'))}<textarea data-scope-paths rows="2">${esc(scope.paths.join('\n'))}</textarea></label><div class="agent-scope-limits"><label>${esc(t('Action limit'))}<input type="number" data-scope-actions min="1" max="100" value="${scope.max_actions}"></label><label>${esc(t('Run limit'))}<input type="number" data-scope-runs min="0" max="20" value="${scope.max_runs}"></label></div></fieldset>`);
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
    const effortKey = (value = config) => `${catalogKey(value)}:${value.model || ''}`;
    const effortLabel = value => t({none:'None',minimal:'Minimal',low:'Low',medium:'Medium',high:'High',xhigh:'Extra high',max:'Max',ultra:'Ultra'}[value] || value);
    function effortModel(value = config) {
      if (!cliBackend(value.backend) || !value.model) return null;
      return modelCatalogs.get(catalogKey(value))?.data?.models?.find(item => item.id === value.model) || effortMetadata.get(effortKey(value))?.model || null;
    }
    function effortLevels(value = config) {
      const levels = effortModel(value)?.effort_levels;
      return Array.isArray(levels) ? [...new Set(levels.filter(level => typeof level === 'string' && level.length > 0 && level.length <= 40))] : [];
    }
    function renderEffort() {
      const select = $('#agent-effort'); if (!select) return;
      const levels = effortLevels(), saved = typeof config.effort === 'string' ? config.effort : null;
      const checking = modelCatalogs.get(catalogKey())?.loading || effortMetadata.get(effortKey())?.loading;
      const unknownSaved = saved && !levels.includes(saved);
      setOptions(select, `<option value="">${esc(t('Default'))}</option>` + levels.map(level => `<option value="${esc(level)}">${esc(effortLabel(level))}</option>`).join('') + (unknownSaved ? `<option value="${esc(saved)}" disabled>${esc(t('{effort} (saved)', {effort:effortLabel(saved)}))}</option>` : ''), saved || '');
      select.disabled = !projectId || loading || busy || active(session) || Boolean(projectActive()) || Boolean(checking) || (!levels.length && !saved);
      let note = config.backend === 'api' ? t('Effort overrides are not supported for Model API sessions. Default preserves provider behavior.') : !config.model ? t('Select a model to configure reasoning effort. Default preserves CLI behavior.') : checking ? t('Loading models…') : !levels.length ? t('Effort support could not be confirmed for this model. Default preserves CLI behavior.') : t("Default keeps the CLI's configured effort. Higher effort may take longer and use more tokens.");
      const nativeDefault = effortModel()?.default_effort;
      if (levels.length && typeof nativeDefault === 'string' && levels.includes(nativeDefault)) note += ` ${t('Model default: {effort}. Default still preserves your CLI configuration.', {effort:effortLabel(nativeDefault)})}`;
      if (unknownSaved && !checking) note = t('Saved effort: {effort}. Current support could not be confirmed; choose Default for a new conversation.', {effort:effortLabel(saved)});
      select.title = note; select.closest('label').title = note;
      $('#agent-effort-note').textContent = note;
    }
    async function loadEffortMetadata(value = config, refresh = false) {
      if (!projectId || !cliBackend(value.backend) || !value.model) return;
      if (modelCatalogs.get(catalogKey(value))?.data?.models?.some(item => item.id === value.model)) return;
      const key = effortKey(value), cached = effortMetadata.get(key);
      if (!refresh && cached?.loading) return cached.promise;
      if (!refresh && cached?.expires > Date.now()) return;
      const request = ++effortRequest, backend = value.backend, model = value.model, selectedProject = projectId;
      const promise = (async () => {
        let metadata = null;
        try {
          const data = await api(`/api/agents/models?backend=${encodeURIComponent(backend)}&project_id=${encodeURIComponent(selectedProject)}&model=${encodeURIComponent(model)}${refresh ? '&refresh=1' : ''}`);
          if (data?.backend === backend) metadata = data.selected_model?.id === model ? data.selected_model : data.models?.find(item => item.id === model) || null;
        } catch (_) { /* Unknown capability leaves Default available. */ }
        if (effortMetadata.get(key)?.request !== request) return;
        effortMetadata.set(key, {model:metadata, loading:false, expires:Date.now() + (metadata ? 300000 : 30000)});
        if (key === effortKey()) renderEffort();
      })();
      effortMetadata.set(key, {loading:true, request, promise}); renderEffort();
      return promise;
    }
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
      if (!refresh && (cached?.loading || cached?.expires > Date.now())) { renderModels(); renderFast(); renderEffort(); if (!cached.loading) loadEffortMetadata(); return; }
      const request = ++catalogRequest, backend = config.backend;
      modelCatalogs.set(key, {...cached, loading:true, request}); renderModels(); renderFast(); renderEffort();
      try {
        const data = await api(`/api/agents/models?backend=${encodeURIComponent(backend)}&project_id=${encodeURIComponent(projectId)}${refresh ? '&refresh=1' : ''}`);
        if (modelCatalogs.get(key)?.request !== request) return;
        if (data?.backend !== backend || !Array.isArray(data.models)) throw new Error(t('Could not load CLI models. You can still enter a custom model ID.'));
        modelCatalogs.set(key, {data, loading:false, expires:Date.now() + (data.source === 'unavailable' ? 30000 : 300000)});
      } catch (_) {
        if (modelCatalogs.get(key)?.request !== request) return;
        modelCatalogs.set(key, {data:{backend, models:[], source:'unavailable', diagnostic:'Could not load CLI models. You can still enter a custom model ID.', fast_mode:{supported:false}}, loading:false, expires:Date.now()+30000});
      }
      if (key === catalogKey()) { renderModels(); renderFast(); renderEffort(); loadEffortMetadata(config, refresh); }
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
      config = {backend:value.backend, model:value.model || '', mode:value.mode, fast_mode:typeof value.fast_mode === 'boolean' ? value.fast_mode : cliBackend(value.backend) ? null : false, effort:typeof value.effort === 'string' ? value.effort : null};
      if (value.mode === 'automatic') config.scope = value.scope;
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
        if (!session && !backends.some(backend => backend.id === config.backend && backend.available)) config = {backend:backends.find(backend => backend.available)?.id || backends[0]?.id || 'api', model:'', mode:'read-only', fast_mode:false, effort:null};
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
      if (reset) config = {...config, model:'', mode:'read-only', fast_mode:false, effort:null};
      if (config.mode !== 'automatic') delete config.scope;
      remember(null); historyOpen = false; closePopovers();
      restoreDraft(draft || {message:'', attachments:[]}); saveDraft(); render(); schedule(); $('#chat-input').focus();
    }
    function changeConfig(next) {
      if (loading || busy || active(session) || projectActive()) return;
      const draft = snapshotDraft(); saveDraft();
      ++generation; clearTimeout(timer); session = null; error = ''; resolvedApproval = null;
      const modelChanged = next.backend !== config.backend || next.model !== config.model;
      config = {...next, fast_mode:next.fast_mode === true && cliBackend(next.backend) && (!modelChanged || fastSupported(next)), effort:typeof next.effort === 'string' && cliBackend(next.backend) && (!modelChanged || effortLevels(next).includes(next.effort)) ? next.effort : null};
      if (config.mode === 'automatic') config.scope ||= defaultScope(); else delete config.scope;
      remember(null); restoreDraft(draft); saveDraft(); render(); schedule(); loadModels();
    }
    async function selectProject(id) {
      saveDraft();
      const ticket = ++generation;
      ++historyRequest; ++catalogRequest; clearTimeout(historyTimer); clearTimeout(timer);
      for (const [key, entry] of modelCatalogs) if (entry.loading) modelCatalogs.delete(key);
      for (const [key, entry] of effortMetadata) if (entry.loading) effortMetadata.delete(key);
      projectId = id; sessions = []; session = null; loading = Boolean(id); busy = false; error = ''; resolvedApproval = null; historyResults = null; historyOpen = false;
      const cache = id ? readCache(`af-agent-drafts-v1-${id}`, {}) : {};
      drafts = cache.drafts && typeof cache.drafts === 'object' ? cache.drafts : {};
      config = cache.config || {backend:backends.find(backend => backend.id === 'api' && backend.available)?.id || backends.find(backend => backend.available)?.id || 'api', model:'', mode:'read-only', fast_mode:false};
      config.fast_mode = config.fast_mode === true && cliBackend(config.backend);
      config.effort = typeof config.effort === 'string' && cliBackend(config.backend) ? config.effort : null;
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
    async function send(untrustedRequest = false) {
      const rawMessage = $('#chat-input').value, message = rawMessage.trim();
      if (!message || $('#chat-send').disabled || composing) return;
      if (executeSlash(message)) return;
      const ticket = ++generation, sentAttachments = [...attachments], oldSlot = slot();
      const contents = {message, files:[...state.context], attachments:sentAttachments.map(({id,...item}) => item)};
      if (untrustedRequest === true) contents.untrusted_request = true;
      if (contents.files.length + contents.attachments.length > 20) { error = t('Attach at most 20 files.'); render(); return; }
      if ($('#agent-attach-run').checked && (attachedRunId || state.run?.id)) contents.run_id = attachedRunId || state.run.id;
      busy = true; error = ''; saveDraft(); render();
      try {
        if (!session) { const options = {...config}; if (options.mode !== 'automatic') delete options.scope; const value = await api(url(null), {method:'POST', body:options}); if (!current(ticket)) return; storeSession(value); }
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
      try { const value = await api(`${url()}/${action}`, {method:'POST', body}); if (current(ticket)) { if (action === 'approval') resolvedApproval = body.approval_id; storeSession(value); return value; } }
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
        let total = attachments.filter(item => !item.data_base64).reduce((size, item) => size + attachmentBytes(item), 0);
        let imageTotal = attachments.filter(item => item.data_base64).reduce((size, item) => size + attachmentBytes(item), 0);
        let imageCount = attachments.filter(item => item.data_base64).length;
        for (const file of files) {
          if (/^image\/(png|jpeg|webp)$/.test(file.type)) {
            if (!imageCapable()) throw new Error(t('Image capability has not been declared for this model.'));
            if (file.size > 262144 || imageTotal + file.size > 524288 || ++imageCount > 4) throw new Error(t('Attach up to four images, 256 KiB each and 512 KiB combined.'));
            const bytes = new Uint8Array(await file.arrayBuffer());
            let binary = ''; for (let offset = 0; offset < bytes.length; offset += 8192) binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
            added.push({id:++attachmentSequence, name:file.name, mime_type:file.type, data_base64:btoa(binary)});
            imageTotal += bytes.length;
            continue;
          }
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
        if ([...attachments, ...added].filter(item => !item.data_base64).reduce((size, item) => size + attachmentBytes(item), 0) > 256000) throw new Error(t('Attached text must fit within 256 KB.'));
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
      const dispatch = (name, parameters) => {
        const selectedProject = projectId, selectedSession = session?.id;
        return window.AFOperations.dispatch(`ui.agent.${name}`, parameters).catch(cause => { if (projectId === selectedProject && session?.id === selectedSession) { error = cause.message; render(); } });
      };
      on('agent-new', 'click', () => newSession(true));
      on('agent-session', 'change', event => event.target.value ? selectSession(event.target.value) : newSession());
      on('agent-backend', 'change', event => dispatch('set_config', {backend:event.target.value, model:'', mode:'read-only', fast_mode:false}));
      on('agent-model-refresh', 'click', () => loadModels(true));
      on('agent-fast-toggle', 'click', () => { if (config.fast_mode !== false || fastSupported()) changeConfig({...config, fast_mode:config.fast_mode === null ? false : !config.fast_mode}); });
      on('agent-effort', 'change', event => dispatch('set_config', {effort:event.target.value || null}));
      on('agent-mode', 'change', event => dispatch('set_config', {mode:event.target.value}));
      on('agent-model', 'input', renderModels);
      on('agent-model-search', 'input', renderModels);
      on('agent-model', 'change', event => { if ($('#agent-model-picker')?.hidden !== false) changeConfig({...config, model:event.target.value.trim()}); });
      on('agent-model', 'keydown', event => { if (event.key === 'Enter') { event.preventDefault(); event.stopPropagation(); $('#agent-model-apply')?.click(); } });
      on('agent-model-apply', 'click', () => { const model = $('#agent-model').value.trim(); changeConfig({...config, model}); rememberModel(model); closePopovers(); $('#chat-input').focus(); });
      on('agent-stop', 'click', () => mutate('cancel', {}));
      for (const [id, approved] of [['agent-allow', true], ['agent-deny', false]]) on(id, 'click', () => { if (session?.pending_approval) dispatch('approve', {approval_id:session.pending_approval.id, approved}); });
      on('agent-review', 'click', () => { if (session?.pending_approval) dispatch('review_approval', {approval_id:session.pending_approval.id}); });
      on('agent-auto-approval', 'change', event => dispatch('set_auto_approval', {enabled:event.target.checked}));
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
      document.addEventListener('change', event => { if (event.target.dataset.agentProjectContext !== undefined) { const paths = new Set(state.context), path = event.target.dataset.agentProjectContext; event.target.checked ? paths.add(path) : paths.delete(path); dispatch('set_context', {paths:[...paths]}); } });
      document.addEventListener('keydown', event => { if (event.key === 'Escape') { const open = popovers.find(([, panel]) => $(`#${panel}`) && !$(`#${panel}`).hidden); closePopovers(); if (open) { event.preventDefault(); $(`#${open[0]}`)?.focus(); } } });
      window.addEventListener('pagehide', saveDraft);
      registerOperations();
      renderCommands();
    }
    function registerOperations() {
      if (!window.AFOperations) return;
      const ready = (idle = false) => {
        if (!projectId || loading || busy) throw new Error('The agent workspace is not ready.');
        if (idle && (active(session) || projectActive())) throw new Error('Stop or wait for the running conversation first.');
      };
      const checked = async action => { error = ''; const result = await action(); if (error) throw new Error(error); return result; };
      const summary = () => ({project_id:projectId,session_id:session?.id || null,status:session?.status || 'new',config:{...config},auto_approval:session?.auto_approval === true,pending_approval:session?.pending_approval ? {id:session.pending_approval.id,tool:session.pending_approval.tool,review:session.pending_approval.review ? {...session.pending_approval.review} : null} : null,history_open:historyOpen,context:[...state.context],attachments:attachments.map(({id,name}) => ({id,name})),run_id:attachedRunId,has_draft:Boolean($('#chat-input').value),draft_revision:draftRevision,quick_dialog_open:Boolean($('#agent-quick-message-dialog')?.open),disclosures:[...$('#chat-messages').querySelectorAll('details[data-disclosure]')].map(item => ({id:item.dataset.disclosure,open:item.open})),sessions:sessions.map(item => ({id:item.id,title:sessionTitle(item),status:item.status,backend:item.backend})),popovers:popovers.filter(([,panel]) => !$(`#${panel}`).hidden).map(([,panel]) => panel)});
      const pendingApproval = approvalId => {
        ready();
        if (!session?.pending_approval || session.pending_approval.id !== approvalId || approvalId === resolvedApproval) throw new Error(t('This approval is no longer pending.'));
        return session.pending_approval;
      };
      const sessionMutation = async (action, parameters) => {
        const selectedProject = projectId, selectedSession = session?.id;
        const value = await checked(() => mutate(action, parameters));
        if (!value || projectId !== selectedProject || session?.id !== selectedSession || value.id !== selectedSession) {
          const cause = new Error('Frontend state version conflict: approval changes could not be confirmed in the selected conversation; inspect it before retrying.');
          cause.code = 'VERSION_CONFLICT'; throw cause;
        }
        return summary();
      };
      window.AFOperations.register('agent', {
        read:summary,
        new:() => { ready(true); newSession(true); },
        select_session:async ({session_id}) => { ready(); const selectedProject = projectId; if (!sessions.some(item => item.id === session_id)) { await searchHistory(); if (!sessions.some(item => item.id === session_id)) throw new Error('Unknown conversation.'); } await checked(() => selectSession(session_id)); if (projectId !== selectedProject || session?.id !== session_id) { const cause = new Error('Frontend state version conflict: selected conversation changed before the command completed.'); cause.code = 'VERSION_CONFLICT'; throw cause; } },
        set_config:async ({backend,model,mode,fast_mode,effort,scope}) => {
          ready(true); const next = {...config,...Object.fromEntries(Object.entries({backend,model,mode,fast_mode,effort,scope}).filter(([,value]) => value !== undefined))};
          const selected = backends.find(item => item.id === next.backend && item.available);
          if (!selected || !selected.modes?.includes(next.mode)) throw new Error('Backend or permission mode is unavailable.');
          if (typeof next.model !== 'string' || next.model.length > 200) throw new Error('Invalid model ID.');
          if (next.fast_mode && !fastSupported(next)) throw new Error('Fast mode is unavailable for this model.');
          if (scope !== undefined && (!scope || !Array.isArray(scope.operations) || !Array.isArray(scope.paths) || !Number.isInteger(scope.max_actions) || !Number.isInteger(scope.max_runs))) throw new Error(t('Provide a valid automatic scope.'));
          if (effort !== undefined && effort !== null) {
            if (typeof effort !== 'string') throw new Error(t('Reasoning effort is not supported by this model.'));
            const ticket = generation;
            await loadEffortMetadata(next);
            if (!current(ticket)) { const cause = new Error('Frontend state version conflict: agent configuration changed before effort validation completed.'); cause.code = 'VERSION_CONFLICT'; throw cause; }
            ready(true);
            if (!effortLevels(next).includes(effort)) throw new Error(t('Reasoning effort is not supported by this model.'));
          }
          changeConfig(next); rememberModel(next.model);
        },
        set_draft:({message}) => { ready(); if (typeof message !== 'string' || message.length > 32000) throw new Error('Messages must fit within 32000 characters.'); insertPrompt(message); },
        read_draft:() => { ready(); return {message:$('#chat-input').value,files:[...state.context],attachments:attachments.map(({id,name}) => ({id,name})),run_id:$('#agent-attach-run').checked ? attachedRunId || state.run?.id || null : null}; },
        set_context:({paths}) => { ready(); if (!Array.isArray(paths) || paths.length + attachments.length > 20 || paths.some(path => !state.files.some(file => file.path === path))) throw new Error('Choose existing project files within the 20-file context limit.'); state.context = new Set(paths); onContextChange?.(); saveDraft(); renderContext(); },
        attach_run:({run_id}) => { ready(); if (run_id !== null && !state.runs.some(run => run.id === run_id)) throw new Error('Unknown run.'); attachedRunId = run_id; $('#agent-attach-run').checked = Boolean(run_id); saveDraft(); render(); },
        add_attachments:async ({files}) => { ready(); if (!Array.isArray(files) || files.some(file => typeof file.name !== 'string' || typeof file.content !== 'string' && typeof file.data_base64 !== 'string')) throw new Error('Expected named text or image attachments.'); await checked(() => addFiles(files.map(file => typeof file.content === 'string' ? new File([file.content], file.name, {type:'text/plain'}) : new File([Uint8Array.from(atob(file.data_base64), char => char.charCodeAt(0))], file.name, {type:file.mime_type})))); },
        remove_attachment:({attachment_id}) => { ready(); if (!attachments.some(item => item.id === attachment_id)) throw new Error('Unknown attachment.'); attachments = attachments.filter(item => item.id !== attachment_id); saveDraft(); renderContext(); },
        send:async ({untrusted_request} = {}) => {
          ready(true);
          const message = $('#chat-input').value.trim(), selectedProject = projectId, selectedSession = session?.id || null;
          const previousTurns = new Set((session?.turns || []).map(turn => turn.id));
          const slashCommand = /^\/(new|history|export|explain|review|simulate)$/.test(message);
          if (!message || $('#chat-send').disabled || composing) throw new Error('Enter a message and choose an available backend first.');
          await checked(() => send(untrusted_request === true));
          const accepted = session?.turns?.find(turn => !previousTurns.has(turn.id) && turn.message === message);
          if (projectId !== selectedProject || (!slashCommand && (!session?.id || selectedSession && session.id !== selectedSession || !accepted))) {
            const cause = new Error('Frontend state version conflict: message acceptance could not be confirmed in the selected conversation; inspect history before retrying.');
            cause.code = 'VERSION_CONFLICT'; throw cause;
          }
          return {...summary(), ...(accepted ? {accepted_turn_id:accepted.id} : {})};
        },
        stop:async () => { ready(); if (!active(session)) throw new Error('No conversation is running.'); await checked(() => mutate('cancel', {})); return summary(); },
        approve:async ({approval_id,approved}) => {
          pendingApproval(approval_id);
          if (typeof approved !== 'boolean') throw new Error(t('Expected an approval decision.'));
          return sessionMutation('approval', {approval_id,approved});
        },
        review_approval:async ({approval_id}) => {
          const approval = pendingApproval(approval_id);
          if (officialReview() || reviewCapability().single_action === false) throw new Error(t('Codex official Auto-review does not support single-action review here. Allow or deny this action, or stop the turn to change automatic approval.'));
          if (approval.review?.status === 'reviewing') throw new Error(t('An AI review is already in progress.'));
          if (session.mode === 'read-only') throw new Error(t('Read-only conversations cannot enable automatic approval.'));
          if (!reviewCapability().available) throw new Error(reviewUnavailable());
          return sessionMutation('approval/review', {approval_id});
        },
        set_auto_approval:async ({enabled}) => {
          ready();
          if (!session) throw new Error(t('Select an existing conversation first.'));
          if (typeof enabled !== 'boolean') throw new Error(t('Expected an automatic approval setting.'));
          if (active(session) && (officialReview() || reviewCapability().runtime_toggle === false)) throw new Error(t('Stop the current turn before changing Codex official Auto-review.'));
          if (enabled && session.mode === 'read-only') throw new Error(t('Read-only conversations cannot enable automatic approval.'));
          if (enabled && !reviewCapability().available) throw new Error(reviewUnavailable());
          const change = {project_id:projectId,session_id:session.id,enabled};
          autoApprovalChange = change;
          try { return await sessionMutation('auto-approval', {enabled}); }
          finally { if (autoApprovalChange === change) { autoApprovalChange = null; render(); } }
        },
        history:async ({open,query}) => { ready(); if (query !== undefined && (typeof query !== 'string' || query.length > 200)) throw new Error('Search must fit within 200 characters.'); historyOpen = Boolean(open); if (query !== undefined) $('#agent-history-search').value = query; closePopovers(); render(); if (open) await checked(searchHistory); return (historyResults || sessions).map(item => ({id:item.id,title:sessionTitle(item),status:item.status,backend:item.backend})); },
        search_models:({query}) => { if (typeof query !== 'string') throw new Error('Expected search text.'); $('#agent-model-search').value = query; renderModels(); },
        refresh_models:() => { ready(); if (!cliBackend(config.backend)) throw new Error('Model catalog refresh requires an installed CLI backend.'); return loadModels(true); },
        favorite_model:({model,favorite}) => { if (typeof model !== 'string' || !model.trim() || model.length > 200) throw new Error('Invalid model ID.'); const prefs = modelPrefs(); prefs.favorites = favorite ? [...new Set([...prefs.favorites,model])] : prefs.favorites.filter(item => item !== model); writeModelPrefs(prefs); renderModels(); },
        popover:({name,open}) => { const mapping = {export:'agent-export-menu',context:'agent-context-picker',models:'agent-model-picker',commands:'agent-command-list'}; const entry = popovers.find(([,panel]) => panel === mapping[name]); if (!entry) throw new Error('Unknown agent popover.'); if ($(`#${entry[1]}`).hidden === Boolean(open)) togglePopover(...entry); },
        prompt:({name}) => { ready(); if (!promptKeys[name]) throw new Error('Unknown circuit prompt.'); insertPrompt(t(promptKeys[name])); },
        export:({format}) => { if (!['md','json','txt'].includes(format) || !session?.turns?.length) throw new Error('Select a conversation with messages and an export format.'); exportConversation(format); },
        edit_turn:({turn_id}) => { ready(); const turn = session?.turns?.find(item => item.id === turn_id); if (!turn) throw new Error('Unknown conversation turn.'); editResend(turn); },
        copy_turn:async ({turn_id,role}) => { const turn = session?.turns?.find(item => item.id === turn_id); if (!turn || !['user','assistant'].includes(role)) throw new Error('Unknown conversation turn or role.'); await navigator.clipboard.writeText(role === 'user' ? turn.message : turn.reply || ''); },
        scroll_latest:() => { $('#chat-messages').scrollTop = $('#chat-messages').scrollHeight; updateScrollButton(); },
        disclosure:({id,open}) => { const disclosure = [...$('#chat-messages').querySelectorAll('details[data-disclosure]')].find(item => item.dataset.disclosure === id); if (!disclosure) throw new Error('Unknown message disclosure.'); disclosure.open = Boolean(open); },
        quick_messages:() => quickMessages(),
        manage_quick:({open}) => { const dialog = $('#agent-quick-message-dialog'); if (open && !dialog?.open) manageQuickMessages(); else if (!open && dialog?.open) dialog.close(); },
        save_quick:({title,message}) => { if (typeof title !== 'string' || !title.trim() || title.length > 80 || typeof message !== 'string' || !message.trim() || message.length > 32000) throw new Error('Expected a title and message within their limits.'); writeCache('af-agent-quick-messages-v1', [...quickMessages(), {title:title.trim(),message:message.trim()}].slice(-30)); renderCommands(); },
        remove_quick:({index}) => { const items = quickMessages(); if (!Number.isInteger(index) || index < 0 || index >= items.length) throw new Error('Unknown quick message.'); writeCache('af-agent-quick-messages-v1', items.filter((_,i) => i !== index)); renderCommands(); }
      }, summary);
    }
    return {refreshCapabilities, selectProject, render, send, bind};
  }
};
