'use strict';

// A semantic adapter over the existing controllers. No DOM selectors are accepted
// from remote callers, and browser state never includes credential input values.
window.AFOperations = (() => {
  const handlers = new Map(), readers = new Map(), etags = new Map();
  let session = null, running = false, version = 0, eventCursor = 0, refreshing = false;
  let onChange = null, lastError = '', polling = false;
  const scope = path => {
    const url = new URL(path, location.href), pathname = url.pathname;
    const project = pathname.match(/^\/api\/projects\/([^/]+)/);
    return project ? `/api/projects/${project[1]}` : url.searchParams.has('project_id') ? `/api/projects/${encodeURIComponent(url.searchParams.get('project_id'))}` : '/api/settings';
  };
  async function request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (session) headers.set('X-AF-Client', session.client_id);
    const method = (options.method || 'GET').toUpperCase();
    const key = scope(path);
    if (!headers.has('If-Match') && !['GET', 'HEAD', 'OPTIONS'].includes(method) && !String(path).startsWith('/api/ui/') && !String(path).startsWith('/api/operations/') && etags.has(key)) headers.set('If-Match', etags.get(key));
    const response = await fetch(path, {...options, headers});
    const etag = response.headers.get('ETag');
    if (etag && response.ok) etags.set(key, etag);
    return response;
  }
  function register(module, commands, read) {
    for (const [name, handler] of Object.entries(commands)) {
      const id = `ui.${module}.${name}`;
      if (handlers.has(id)) throw new Error(`Duplicate browser operation: ${id}`);
      handlers.set(id, handler);
    }
    if (read) readers.set(module, read);
  }
  function snapshot() {
    const value = Object.fromEntries([...readers].map(([module, read]) => [module, read()]));
    // Match FrontendSessions.clean_state before both publication and optimistic
    // comparison. Otherwise safe metadata such as tokenizer hashes is removed
    // server-side and every versioned command sees a false state conflict.
    const clean = item => Array.isArray(item) ? item.map(clean) : item && typeof item === 'object' ?
      Object.fromEntries(Object.entries(item).filter(([key])=>!['secret','password','api_key','token','authorization','credential']
        .some(part=>key.toLowerCase().includes(part))).map(([key,entry])=>[key,clean(entry)])) : item;
    return clean({project_id:value.workspace?.project_id || null, ...value});
  }
  async function dispatch(operationId, parameters = {}) {
    const handler = handlers.get(operationId);
    if (!handler) throw new Error(`Browser operation is unavailable: ${operationId}`);
    const result = await handler(parameters);
    return result === undefined ? {state:snapshot()} : result;
  }
  async function post(path, body) {
    const response = await request(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body), cache:'no-store'});
    const value = await response.json();
    if (!response.ok) throw new Error(value.error?.message || value.error || `Browser session request failed (${response.status})`);
    return value;
  }
  async function invoke(operationId, parameters = {}, options = {}) {
    const body = {operation_id:operationId,parameters};
    for (const key of ['expected_version','idempotency_key']) if (options[key] !== undefined) body[key] = options[key];
    // Legacy HTTP reads expose a quoted ETag, while registry queries return the
    // same revision as a bare digest. Both callers share the raw registry value.
    if (typeof body.expected_version === 'string' && /^"[a-f0-9]{64}"$/.test(body.expected_version)) body.expected_version = body.expected_version.slice(1,-1);
    const response = await request('/api/operations/invoke', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),cache:'no-store'});
    const envelope = await response.json();
    if (!response.ok || envelope.ok !== true) {
      const error = new Error(envelope.error?.message || `Operation failed (${response.status})`);
      error.code = envelope.error?.code; error.envelope = envelope;
      throw error;
    }
    return envelope;
  }
  async function catalog(path, parameters = {}) {
    const query = new URLSearchParams(Object.entries(parameters).filter(([,value]) => value !== undefined && value !== null));
    const response = await request(`${path}${query.size ? `?${query}` : ''}`, {cache:'no-store'});
    const value = await response.json();
    if (!response.ok) throw new Error(value.error?.message || value.error || `Discovery failed (${response.status})`);
    return value;
  }
  async function poll() {
    if (!running || polling) return;
    polling = true;
    try {
      if (!session) {
        session = await post('/api/ui/sessions', {state:snapshot()});
        version = session.version || 0;
      }
      const result = await post(`/api/ui/sessions/${encodeURIComponent(session.client_id)}/poll`, {token:session.token, state:snapshot(), version});
      if (Number.isInteger(result.version)) version = result.version;
      for (const command of result.commands || []) {
        let ack;
        try {
          if (command.expected_state && JSON.stringify(command.expected_state) !== JSON.stringify(snapshot())) {
            const error = new Error('Frontend state version conflict before execution'); error.code = 'VERSION_CONFLICT'; throw error;
          }
          const data = await dispatch(command.operation_id, command.parameters || {});
          const expectedProject = command.operation_id === 'ui.workspace.select_project' ? command.parameters?.project_id || null : command.expected_state?.project_id;
          if (command.expected_state && expectedProject !== snapshot().project_id) {
            const error = new Error('Frontend project changed during command execution'); error.code = 'VERSION_CONFLICT'; throw error;
          }
          ack = {ok:true, data};
        } catch (error) { ack = {ok:false, error:{code:error.code || 'ui_operation_failed', message:error.message || String(error)}}; }
        const response = await post(`/api/ui/sessions/${encodeURIComponent(session.client_id)}/ack`, {token:session.token, command_id:command.command_id, ...ack, state:snapshot()});
        if (Number.isInteger(response.version)) version = response.version;
      }
      lastError = '';
    } catch (error) {
      lastError = error.message;
      // A server restart invalidates browser leases. A fresh registration is safe;
      // commands already acknowledged are never replayed by the browser.
      if (/404|not found|expired|disconnected|unknown.*session/i.test(lastError)) session = null;
    } finally { polling = false; if (running) setTimeout(poll, 850); }
  }
  async function events() {
    if (!running) return;
    try {
      // Short polling leaves idle intervals for normal browser loading semantics.
      const response = await request(`/api/operations/events?after=${eventCursor}&wait=0`, {cache:'no-store'});
      if (response.ok) {
        const value = await response.json();
        eventCursor = value.cursor ?? eventCursor;
        const changes = (value.events || []).filter(event => event.client_id !== session?.client_id && !String(event.operation_id || '').startsWith('ui.'));
        if ((changes.length || value.reset) && onChange && !refreshing) {
          refreshing = true;
          try { await onChange(changes, Boolean(value.reset)); } finally { refreshing = false; }
        }
      }
    } catch (_) { /* Existing connection indicators remain authoritative. */ }
    if (running) setTimeout(events, 1800);
  }
  function start(refresh) {
    if (running) return;
    onChange = refresh; running = true;
    // Start after the initial view has rendered and first-paint tests can settle.
    setTimeout(poll, 700); setTimeout(events, 1700);
  }
  window.addEventListener('pagehide', () => { running = false; });
  return {register, dispatch, snapshot, request, invoke, list:parameters => catalog('/api/operations',parameters), describe:(id,parameters) => catalog(`/api/operations/${encodeURIComponent(id)}`,parameters), start, version:path => etags.get(scope(path)), names:() => [...handlers.keys()].sort(), status:() => ({client_id:session?.client_id || null, version, error:lastError})};
})();
