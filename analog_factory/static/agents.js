'use strict';

// The sidebar owns its requests and never redraws the main workspace or its drafts.
window.AFAgents = {
  create({state, api, esc, t, html, formatDate, number, onSettled}) {
    const $ = selector => document.querySelector(selector);
    const active = session => ['running', 'waiting_approval', 'queued', 'starting'].includes(session?.status);
    const label = mode => t({'read-only':'Read only', confirm:'Confirm each action', 'workspace-write':'Allow project writes'}[mode] || mode);
    let backends = [], capabilitiesError = '', projectId = null, generation = 0, capabilityRequest = 0;
    let sessions = [], session = null, loading = false, busy = false, timer = null, error = '', resolvedApproval = null;
    let config = {backend:'api', model:'', mode:'read-only'};
    const url = (id = session?.id) => `/api/projects/${encodeURIComponent(projectId)}/agent/sessions${id ? `/${encodeURIComponent(id)}` : ''}`;
    const current = ticket => ticket === generation;
    const remember = id => { try { id ? localStorage.setItem(`af-agent-${projectId}`, id) : localStorage.removeItem(`af-agent-${projectId}`); } catch (_) { /* Browser cache is optional; history lives on the server. */ } };
    const selectedBackend = () => backends.find(backend => backend.id === config.backend);
    const modeOptions = () => selectedBackend()?.modes || ['read-only'];
    const projectActive = () => sessions.find(item => active(item) && item.id !== session?.id);
    function setOptions(element, markup, value) {
      if (element.dataset.options !== markup) { element.innerHTML = markup; element.dataset.options = markup; }
      element.value = value;
    }
    function render() {
      if (!$('#agent-backend')) return;
      setOptions($('#agent-backend'), backends.map(backend => `<option value="${esc(backend.id)}" ${backend.available ? '' : 'disabled'}>${esc(backend.name || backend.id)}${backend.available ? '' : ` · ${esc(t('unavailable'))}`}</option>`).join(''), config.backend);
      setOptions($('#agent-mode'), modeOptions().map(mode => `<option value="${esc(mode)}">${esc(label(mode))}</option>`).join(''), config.mode);
      if ($('#agent-model').value !== config.model) $('#agent-model').value = config.model;
      $('#agent-model').placeholder = config.backend === 'api' ? state.settings.provider?.model || t('Provider default') : t('CLI default');
      setOptions($('#agent-session'), `<option value="">${esc(t('New conversation'))}</option>` + sessions.map(item => `<option value="${esc(item.id)}">${esc(item.title || `${item.backend} · ${formatDate(item.updated_at || item.created_at)} · ${t(item.status)}`)}</option>`).join(''), session?.id || '');
      const running = active(session), blocked = loading || busy || running || Boolean(projectActive());
      for (const id of ['agent-backend', 'agent-mode', 'agent-model', 'agent-new']) $(`#${id}`).disabled = !projectId || blocked;
      $('#agent-session').disabled = !projectId || loading || busy;
      const backend = selectedBackend();
      $('#chat-send').disabled = !projectId || blocked || !backend?.available;
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
      $('#agent-permission-note').textContent = t(config.backend === 'api'
        ? config.mode === 'confirm' ? 'Each file write or simulation requires your approval.' : 'Read files and run records. Changes and simulations are disabled.'
        : config.mode === 'workspace-write' ? 'This session may edit project files without individual approval prompts.' : 'CLI runs in read-only mode.');
      $('#agent-context-note').textContent = t(config.backend === 'api' ? 'Selected saved files are attached. The agent can also read project files through tools.' : 'Selected saved files are attached. The CLI can read other files in this project.');
      $('#agent-attach-run').disabled = !state.run || !projectId || blocked;
      if (!state.run) $('#agent-attach-run').checked = false;
      $('#agent-run-label').textContent = state.run ? t('Attach selected run {id}', {id:String(state.run.id).slice(0, 8)}) : t('Select a run to attach its log');
      const usage = session?.turns?.at(-1)?.usage || (!running ? session?.usage : null);
      $('#assistant-usage').textContent = usage ? usage.total_tokens != null ? t('{count} tokens', {count:number(usage.total_tokens)}) : JSON.stringify(usage) : t('unknown');
      renderConversation();
      const approval = session?.pending_approval;
      $('#agent-approval').hidden = !approval;
      $('#agent-approval-tool').textContent = approval?.tool || '';
      const args = approval?.arguments || {};
      $('#agent-approval-details').textContent = approval ? typeof args.diff === 'string' ? `${args.path || ''}\n\n${args.diff}` : JSON.stringify(args, null, 2) : '';
      $('#agent-proposed-file').hidden = typeof args.content !== 'string';
      $('#agent-proposed-content').textContent = typeof args.content === 'string' ? args.content : '';
      $('#agent-allow').disabled = busy || approval?.id === resolvedApproval;
      $('#agent-deny').disabled = busy || approval?.id === resolvedApproval;
    }
    function renderConversation() {
      const panel = $('#chat-messages');
      const follow = panel.scrollHeight - panel.scrollTop - panel.clientHeight < 60;
      const turns = session?.turns || [];
      const markup = turns.length ? turns.map(turn => {
        const events = (turn.events || []).map(event => {
          if (['usage', 'session'].includes(event.type) || (event.type === 'message' && turn.reply && event.text === turn.reply)) return '';
          const title = [event.tool || t(event.type || 'status'), event.status ? t(event.status) : ''].filter(Boolean).join(' · ');
          const content = event.text || event.error || (event.arguments ? JSON.stringify(event.arguments, null, 2) : '');
          if (!content && !event.tool) return '';
          return html`<div class="agent-event"><small>${esc(title)}</small><pre>${esc(typeof content === 'string' ? content : JSON.stringify(content, null, 2))}</pre></div>`;
        }).join('');
        return html`<div class="chat-message user"><small>YOU</small><p>${esc(turn.message)}</p></div><div class="chat-message agent-turn" data-turn-id="${esc(turn.id)}"><small>${esc(t(turn.status || 'running'))}</small>${events}${turn.reply ? html`<p>${esc(turn.reply)}</p>` : ''}${turn.error ? html`<p class="agent-error">${esc(typeof turn.error === 'string' ? turn.error : JSON.stringify(turn.error))}</p>` : ''}${!events && !turn.reply && !turn.error && active(turn) ? html`<p>Waiting for response…</p>` : ''}</div>`;
      }).join('') : html`<div class="assistant-welcome"><span class="welcome-symbol">✧</span><h3>A second set of eyes.</h3><p>Explore your circuit, understand a tool failure, or discuss your next design step.</p><p class="muted">AI is optional. Every design and verification action works independently.</p></div>`;
      if (panel._agentMarkup !== markup) { panel.innerHTML = markup; panel._agentMarkup = markup; if (follow) panel.scrollTop = panel.scrollHeight; }
    }
    function storeSession(value) {
      const settled = session?.id === value.id && active(session) && !active(value);
      session = value;
      config = {backend:value.backend, model:value.model || '', mode:value.mode};
      sessions = [value, ...sessions.filter(item => item.id !== value.id)];
      remember(value.id);
      render(); schedule();
      if (settled && onSettled) {
        const ticket = generation;
        Promise.resolve(onSettled(projectId)).catch(cause => { if (current(ticket)) { error = cause.message; render(); } });
      }
    }
    function schedule() {
      clearTimeout(timer);
      if (active(session) || projectActive()) timer = setTimeout(poll, 1000);
    }
    async function poll() {
      const ticket = generation;
      if (busy) { schedule(); return; }
      try {
        if (session && active(session)) {
          const value = await api(url());
          if (!current(ticket)) return;
          storeSession(value);
        } else if (projectActive()) {
          const values = await api(url(null));
          if (!current(ticket)) return;
          sessions = Array.isArray(values) ? values : values.sessions || [];
          render(); schedule();
        }
        error = ''; render();
      } catch (cause) {
        if (!current(ticket)) return;
        error = t('Could not refresh agent: {detail}', {detail:cause.message}); render(); schedule();
      }
    }
    async function refreshCapabilities() {
      const ticket = generation, id = projectId, request = ++capabilityRequest;
      const valid = () => current(ticket) && id === projectId && request === capabilityRequest;
      try {
        const result = await api(`/api/agents${id ? `?project_id=${encodeURIComponent(id)}` : ''}`);
        if (!valid()) return;
        backends = result.backends || []; capabilitiesError = '';
        if (!session && !backends.some(backend => backend.id === config.backend && backend.available)) config = {backend:backends.find(backend => backend.available)?.id || backends[0]?.id || 'api',model:'',mode:'read-only'};
      } catch (cause) { if (!valid()) return; capabilitiesError = cause.message; }
      render();
    }
    async function selectSession(id) {
      const ticket = ++generation;
      clearTimeout(timer); error = ''; loading = true; session = null; render();
      try {
        const value = await api(url(id));
        if (!current(ticket)) return;
        loading = false; storeSession(value);
      } catch (cause) { if (current(ticket)) { loading = false; error = cause.message; render(); } }
    }
    function newSession(reset = false) {
      ++generation; clearTimeout(timer); session = null; busy = false; loading = false; error = '';
      if (reset) config = {...config, model:'', mode:'read-only'};
      remember(null); $('#chat-input').value = ''; render(); schedule();
    }
    async function selectProject(id) {
      const ticket = ++generation;
      clearTimeout(timer); projectId = id; sessions = []; session = null; loading = Boolean(id); busy = false; error = '';
      config = {backend:backends.find(backend => backend.id === 'api' && backend.available)?.id || backends.find(backend => backend.available)?.id || 'api',model:'',mode:'read-only'};
      $('#chat-input').value = ''; $('#agent-attach-run').checked = false; render();
      if (!id) return;
      try {
        const [values] = await Promise.all([api(url(null)), refreshCapabilities()]);
        if (!current(ticket)) return;
        sessions = Array.isArray(values) ? values : values.sessions || [];
        let saved; try { saved = localStorage.getItem(`af-agent-${id}`); } catch (_) { /* Optional cache. */ }
        const selected = sessions.find(item => item.id === saved) || sessions.find(active) || sessions[0];
        loading = false;
        if (selected) await selectSession(selected.id); else render();
      } catch (cause) { if (current(ticket)) { loading = false; error = cause.message; render(); } }
    }
    async function send() {
      const message = $('#chat-input').value.trim();
      if (!message || $('#chat-send').disabled) return;
      const ticket = ++generation, contents = {message,files:[...state.context]};
      if ($('#agent-attach-run').checked && state.run) contents.run_id = state.run.id;
      busy = true; error = ''; render();
      try {
        if (!session) {
          const value = await api(url(null), {method:'POST', body:{...config}});
          if (!current(ticket)) return;
          storeSession(value);
        }
        const value = await api(`${url()}/messages`, {method:'POST',body:contents});
        if (!current(ticket)) return;
        if ($('#chat-input').value.trim() === message) $('#chat-input').value = '';
        storeSession(value);
      } catch (cause) { if (current(ticket)) error = cause.message; }
      finally { if (current(ticket)) { busy = false; render(); schedule(); } }
    }
    async function mutate(action, body) {
      if (!session || busy) return;
      const ticket = ++generation;
      busy = true; error = ''; clearTimeout(timer); render();
      try {
        const value = await api(`${url()}/${action}`, {method:'POST',body});
        if (current(ticket)) { if (action === 'approval') resolvedApproval = body.approval_id; storeSession(value); }
      } catch (cause) { if (current(ticket)) error = cause.message; }
      finally { if (current(ticket)) { busy = false; render(); schedule(); } }
    }
    function bind() {
      $('#agent-new').addEventListener('click', () => newSession(true));
      $('#agent-session').addEventListener('change', event => event.target.value ? selectSession(event.target.value) : newSession());
      $('#agent-backend').addEventListener('change', event => { config = {backend:event.target.value,model:'',mode:'read-only'}; newSession(); });
      $('#agent-mode').addEventListener('change', event => { config.mode = event.target.value; newSession(); });
      $('#agent-model').addEventListener('change', event => { config.model = event.target.value.trim(); if (session) newSession(); });
      $('#agent-model').addEventListener('input', event => { config.model = event.target.value; });
      $('#agent-stop').addEventListener('click', () => mutate('cancel', {}));
      for (const [id, approved] of [['agent-allow',true],['agent-deny',false]]) $(`#${id}`).addEventListener('click', () => { if (session?.pending_approval) mutate('approval', {approval_id:session.pending_approval.id,approved}); });
    }
    return {refreshCapabilities, selectProject, render, send, bind};
  }
};
