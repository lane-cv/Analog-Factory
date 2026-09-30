'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
const encoded = value => encodeURIComponent(value);
const {t, html} = window.AFI18n;
const preferences = window.AFPreferences;
let preferencesSaving = false;
let providerSaving = false;
const state = {
  status: null, settings: {}, projects: [], project: null, files: [], runs: [], tab: 'overview',
  file: null, content: '', savedContent: '', dirty: false, context: new Set(), run: null, log: '',
  waveform: null, traces: new Set(), zoom: [0, 1], cursor: null, workflow: null, workflowSelection: null,
  job: null, evaluation: null, jobs: [], chatting: false, chat: [], connected: false, polling: false,
  compare: ['', ''], assistantUsage: null, history: null, fileRequest: 0, runRequest: 0, loadingProject: false,
  providerTest: null, providerModels: [], projectRequest: 0
};
const traceColors = Array.from({length:6}, (_, index) => `var(--trace-${index + 1})`);
const themeColor = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const terminalStatuses = new Set(['succeeded', 'completed', 'success', 'passed', 'failed', 'cancelled', 'canceled', 'timeout', 'timed_out', 'error', 'unavailable']);
const editorLaunches = new Set();
const isActive = run => run && !terminalStatuses.has(run.status) && ['queued', 'running', 'starting', 'pending'].includes(run.status);
const projectURL = suffix => `/api/projects/${encoded(state.project.id)}${suffix || ''}`;
const formatDate = value => value ? new Date(value).toLocaleString(window.AFI18n.language(), {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '—';
const shortId = value => String(value || '').slice(0, 8);
const pretty = value => JSON.stringify(value, null, 2);
const number = value => typeof value === 'number' && Number.isFinite(value) ? (Math.abs(value) >= 100000 || (value !== 0 && Math.abs(value) < 0.001) ? value.toExponential(3) : Number(value.toPrecision(6)).toLocaleString(window.AFI18n.language(), {maximumFractionDigits:6})) : String(value ?? '—');
const runTime = run => run.started_at || run.created_at || run.created;
const runName = run => `${t(run.kind || 'run')} · ${shortId(run.id)} · ${t(run.status || 'unknown')}`;
const agentUI = window.AFAgents.create({state, api, esc, t, html, formatDate, number, onSettled:refreshAgentProject});
const tool = name => state.status?.toolchain?.tools?.[name] || {available:false, diagnostic:t('Tool detection has not completed.')};
const pdk = () => state.status?.toolchain?.pdk || {available:false, diagnostic:t('No prepared PDK detected.')};
function badge(status, label) {
  const s = String(status || 'unknown');
  const type = ['succeeded', 'completed', 'success', 'passed', 'available', 'implemented'].includes(s) ? 'ok' : ['failed','error','unavailable','timed_out','timeout'].includes(s) ? 'error' : ['running','queued','starting'].includes(s) ? 'running' : 'warning';
  return html`<span class="badge ${type}">${esc(label || t(s).replaceAll('_', ' '))}</span>`;
}
function reasonFor(kind) {
  if (!state.project) return t('Create or select a project first.');
  const required = {simulate:'ngspice', drc:'magic', lvs:'netgen', extract:'magic', xschem:'xschem', magic:'magic'}[kind];
  if (required && !tool(required).available) return tool(required).diagnostic || t('{tool} is not installed or configured.', {tool:required});
  if (kind === 'simulate' && state.project.template === 'sky130' && !pdk().available) return t('The SKY130 template requires a prepared PDK. Configure it in Settings and update the testbench model path.');
  if (['drc','lvs','extract','magic'].includes(kind) && !pdk().available) return pdk().diagnostic || t('A prepared PDK installation is required.');
  if (['xschem','magic'].includes(kind)) {
    const desktop = state.status?.toolchain?.desktop;
    if (desktop && (desktop.available === false || desktop.gui_available === false || (desktop.display === false && desktop.wayland_display === false))) return desktop.diagnostic || t('No graphical desktop session is available. Launch from an X11 or Wayland desktop.');
  }
  return '';
}
function actionButton(kind, label, className = 'secondary-button') {
  const reason = reasonFor(kind);
  return html`<button class="${className}" data-run-kind="${kind}" ${reason ? `disabled title="${esc(reason)}"` : ''}>${label}</button>`;
}
function toast(message, error = false) {
  const element = document.createElement('div'); element.className = `toast${error ? ' error' : ''}`; element.textContent = message;
  $('#toast-container').append(element); setTimeout(() => element.remove(), error ? 10000 : 4500);
}
async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.long ? 180000 : 30000);
  try {
    const response = await fetch(path, {method:options.method || 'GET', headers:options.body === undefined ? {} : {'Content-Type':'application/json'}, body:options.body === undefined ? undefined : JSON.stringify(options.body), signal:controller.signal, cache:'no-store'});
    const type = response.headers.get('content-type') || '';
    const data = type.includes('json') ? await response.json() : await response.text();
    if (!response.ok) throw new Error(typeof data === 'object' ? data.error?.message || data.error || data.message || t('Request failed ({status})', {status:response.status}) : data || t('Request failed ({status})', {status:response.status}));
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error(t('The local server request timed out. Check the execution log and connection.'));
    throw error;
  } finally { clearTimeout(timeout); }
}
function download(content, filename, type = 'application/json') {
  const url = URL.createObjectURL(new Blob([content], {type}));
  const a = document.createElement('a'); a.href = url; a.download = filename; a.click(); setTimeout(() => URL.revokeObjectURL(url), 5000);
}
function heading(eyebrow, title, description, actions = '') {
  return html`<div class="page-heading"><div><p class="eyebrow">${eyebrow}</p><h1>${title}</h1><p>${description}</p></div>${actions ? html`<div class="heading-actions">${actions}</div>` : ''}</div>`;
}
function noProject() {
  return html`<div class="empty-state"><span class="empty-symbol">◈</span><p class="eyebrow">YOUR NEXT CIRCUIT STARTS HERE</p><h1>Make room for a new idea.</h1><p>Create a local project to edit circuit files, run real EDA tools, and keep every result connected to its inputs.</p><button class="primary-button" data-action="new-project">＋ Create a project</button><p class="footnote">The educational amplifier runs with ngspice and needs no PDK.</p></div>`;
}
async function initialize() {
  window.AFI18n.applyDOM();
  const responses = await Promise.allSettled([api('/api/status'), api('/api/projects'), api('/api/settings')]);
  if (responses[0].status === 'fulfilled') { state.status = responses[0].value; state.connected = true; }
  if (responses[1].status === 'fulfilled') state.projects = asArray(responses[1].value, 'projects');
  if (responses[2].status === 'fulfilled') { state.settings = responses[2].value; preferences.apply(state.settings.ui); window.AFI18n.applyDOM(); }
  await agentUI.refreshCapabilities();
  updateChrome();
  let projectId; try { projectId = localStorage.getItem('af-project'); } catch (_) { /* Optional browser cache. */ }
  const initial = state.projects.find(project => project.id === projectId) || state.projects[0];
  state.tab = ['overview','editor','desktop','simulation','verification','workflows','evaluation','settings'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'overview';
  if (initial) await selectProject(initial.id); else render();
  // Enable navigation after the initial project can no longer replace a draft.
  bindGlobal();
  if (!state.connected) toast(t('Cannot reach the local workspace server. Check that Analog-factory is running.'), true);
  setInterval(poll, 2500);
}
function asArray(data, key) { return Array.isArray(data) ? data : data?.[key] || []; }
function updateChrome() {
  $('#connection-status').className = `connection ${state.connected ? 'online' : 'offline'}`;
  $('#connection-status').innerHTML = html`<i></i> ${state.connected ? t('Local workspace') : t('Disconnected')}`;
  $('#project-select').innerHTML = html`<option value="">Choose a project</option>${state.projects.map(project => html`<option value="${esc(project.id)}" ${project.id === state.project?.id ? 'selected' : ''}>${esc(project.name)}</option>`).join('')}`;
  $('#project-summary').innerHTML = html`<span class="project-icon">◈</span><div><strong>${esc(state.project?.name || t('No project selected'))}</strong><small>${state.project ? esc(t('{template} workspace', {template:state.project.template || t('Local')})) : t('Create a workspace to begin')}</small></div>`;
  $('#footer-project').textContent = state.project ? `◈ ${state.project.name}` : t('No project');
  $('#footer-runs').textContent = t('{count} executions', {count:number(state.runs.length)});
  $('#tool-summary').innerHTML = ['ngspice','xschem','magic','netgen'].map(name => html`<div class="tool-mini"><span class="dot ${tool(name).available ? 'teal' : ''}"></span><span>${name}</span><small>${tool(name).available ? t('available') : t('not detected')}</small></div>`).join('') + html`<div class="tool-mini"><span class="dot ${pdk().available ? 'teal' : ''}"></span><span>SKY130 PDK</span><small>${pdk().available ? esc(pdk().variant || t('available')) : t('not configured')}</small></div>`;
  const reason = reasonFor('simulate'); $('#run-primary').disabled = Boolean(reason); $('#run-primary').title = reason || t('Run testbenches/pre.cir with ngspice');
  agentUI.render();
  updateContext();
}
function renderTree() {
  if (!state.project) { $('#file-tree').innerHTML = html('<p class="empty-small">Your design files will appear here.</p>'); return; }
  const folders = new Map();
  for (const file of state.files) { const split = file.path.lastIndexOf('/'); const folder = split < 0 ? '' : file.path.slice(0, split); if (!folders.has(folder)) folders.set(folder, []); folders.get(folder).push(file); }
  $('#file-tree').innerHTML = [...folders.entries()].map(([folder, files]) => html`<details class="tree-folder" open><summary>${esc(folder || t('Project root'))}</summary>${files.map(file => html`<div class="file-row ${state.file === file.path ? 'selected' : ''}"><button data-file="${esc(file.path)}" title="${esc(file.path)}"><span class="file-symbol">${/\.(cir|spice|sp)$/.test(file.path) ? '∿' : /\.(sch|mag)$/.test(file.path) ? '◇' : '≡'}</span>${esc(file.path.split('/').pop())}</button><input type="checkbox" data-context="${esc(file.path)}" ${state.context.has(file.path) ? 'checked' : ''} aria-label="${esc(t('Include {path} in AI context', {path:file.path}))}"></div>`).join('')}</details>`).join('') || html('<p class="empty-small">This project has no source files.</p>');
}
function updateContext() {
  $('#context-count').textContent = t('{count} files', {count:number(state.context.size)});
  $('#context-files').innerHTML = state.context.size ? [...state.context].map(path => html`<span class="context-chip" title="${esc(path)}">${esc(path.split('/').pop())}<button data-remove-context="${esc(path)}" aria-label="${esc(t('Remove {path} from context', {path}))}">×</button></span>`).join('') : html('<small>No files selected. Use the explorer checkboxes.</small>');
}
async function selectProject(id) {
  if (state.dirty) await saveFile();
  const request = ++state.projectRequest;
  ++state.fileRequest; ++state.runRequest;
  agentUI.selectProject(null);
  if (!id) { state.loadingProject = false; state.project = null; state.files = []; state.runs = []; state.run = null; state.file = null; state.context.clear(); state.log = ''; state.chat = []; renderTree(); updateChrome(); renderChat(); updateLog(); render(); return; }
  state.loadingProject = true;
  try {
    const data = await api(`/api/projects/${encoded(id)}`);
    if (request !== state.projectRequest) return;
    state.project = data.project; state.files = data.files || []; state.runs = data.runs || [];
    state.file = null; state.content = ''; state.savedContent = ''; state.dirty = false; state.context.clear(); state.run = null; state.waveform = null; state.job = null; state.evaluation = null; state.chat = []; state.log = ''; state.history = null;
    agentUI.selectProject(id);
    state.workflow = defaultWorkflow(); state.workflowSelection = null;
    const jobs = asArray(await api(projectURL('/jobs')), 'jobs');
    if (request !== state.projectRequest) return;
    state.jobs = jobs;
    state.job = state.jobs.find(job => job.kind === 'tuning') || null;
    state.evaluation = state.jobs.find(job => job.kind === 'evaluation') || null;
    try { localStorage.setItem('af-project', id); } catch (_) { /* Optional browser cache. */ } renderTree(); updateChrome(); renderChat(); render(); updateLog();
    if (state.runs.length) await selectRun(state.runs[0].id, false);
  } finally { if (request === state.projectRequest) state.loadingProject = false; }
}
function render() {
  const simulationForm = state.tab === 'simulation' && $('#simulation-form') ? Object.fromEntries(new FormData($('#simulation-form'))) : null;
  $$('.tabs button').forEach(button => button.classList.toggle('active', button.dataset.tab === state.tab));
  const drawing = state.tab === 'desktop';
  $('#main-content').hidden = drawing;
  $('#desktop-panel').hidden = !drawing;
  if (drawing) { renderDesktop(); return; }
  const views = {overview:overviewView, editor:editorView, simulation:simulationView, verification:verificationView, workflows:workflowView, evaluation:evaluationView, settings:settingsView};
  $('#main-content').innerHTML = !state.project && state.tab !== 'settings' ? noProject() : views[state.tab]();
  bindView();
  if (simulationForm && $('#simulation-form')) {
    $('#analysis-select').value = simulationForm.analysis;
    $('#analysis-parameters').innerHTML = analysisFields(simulationForm.analysis);
    for (const [key,value] of Object.entries(simulationForm)) {
      const element = $('#simulation-form').elements.namedItem(key);
      if (element) element.value = value;
    }
  }
}
function desktopURL() {
  const configured = state.status?.toolchain?.desktop?.browser_url;
  if (!configured) return '';
  try {
    const url = new URL(configured, location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith('/desktop/')) return '';
    // noVNC otherwise prefers saved connection settings over the page's origin.
    url.searchParams.set('host', location.hostname);
    url.searchParams.set('port', location.port || (location.protocol === 'https:' ? '443' : '80'));
    url.searchParams.set('encrypt', String(location.protocol === 'https:'));
    return url.href;
  } catch (_) { return ''; }
}
function updateDesktopStatus(frame) {
  let message = t('Loading the drawing desktop…');
  if (frame.dataset.loaded === 'true') {
    message = t('Desktop page loaded. Check the connection message inside the desktop.');
    try {
      const doc = frame.contentDocument;
      const status = doc?.getElementById('noVNC_status');
      const credentials = doc?.getElementById('noVNC_credentials_dlg');
      const classes = doc?.documentElement.classList;
      if (status?.classList.contains('noVNC_open') && status.classList.contains('noVNC_status_error')) {
        const detail = status.textContent.replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 500);
        message = t('Desktop connection failed: {detail}', {detail});
      } else if (credentials?.classList.contains('noVNC_open')) {
        message = t('Enter your existing desktop password in the desktop below.');
      } else if (classes?.contains('noVNC_connected')) {
        message = t('Desktop connected. You can draw in Xschem and Magic.');
      } else if (classes?.contains('noVNC_connecting') || classes?.contains('noVNC_reconnecting')) {
        message = t('Connecting to the drawing desktop…');
      }
    } catch (_) { /* The fallback also covers an inaccessible or replaced viewer document. */ }
  }
  const status = $('#desktop-status');
  if (status.textContent !== message) status.textContent = message;
}
function observeDesktopStatus(frame) {
  frame.desktopObserver?.disconnect();
  try {
    const doc = frame.contentDocument;
    if (doc?.documentElement) {
      const observer = new MutationObserver(() => updateDesktopStatus(frame));
      observer.observe(doc.documentElement, {attributes:true, attributeFilter:['class']});
      const status = doc.getElementById('noVNC_status');
      if (status) observer.observe(status, {attributes:true, attributeFilter:['class'], childList:true, characterData:true, subtree:true});
      const credentials = doc.getElementById('noVNC_credentials_dlg');
      // Observe only whether the credentials dialog opens; never read its fields.
      if (credentials) observer.observe(credentials, {attributes:true, attributeFilter:['class']});
      frame.desktopObserver = observer;
    }
  } catch (_) { /* Viewer content must remain same-origin before it can be observed. */ }
  updateDesktopStatus(frame);
}
function renderDesktop(reload = false) {
  const url = desktopURL();
  $('#desktop-toolbar').innerHTML = html`<div><strong>Drawing desktop</strong><p>Draw in Xschem and Magic here. Enter your existing desktop password if requested.</p></div><div class="button-row">${actionButton('xschem',t('Open in xschem'))}${actionButton('magic',t('Open magic'))}${url ? html`<button class="secondary-button" data-action="reload-desktop">Reconnect desktop</button><a class="secondary-button" href="${esc(url)}" target="_blank" rel="noopener">Open in new tab ↗</a>` : ''}</div>`;
  const container = $('#desktop-frame-container');
  let frame = $('#desktop-frame');
  container.hidden = !url;
  if (!url) {
    $('#desktop-status').textContent = t('The browser desktop is not configured. Native editor windows still use the configured graphical desktop.');
    return;
  }
  if (!frame) {
    frame = document.createElement('iframe');
    frame.id = 'desktop-frame';
    frame.allow = 'fullscreen';
    frame.addEventListener('load', () => {
      frame.dataset.loaded = 'true';
      observeDesktopStatus(frame);
    });
    container.append(frame);
  }
  frame.title = t('Drawing desktop');
  if (reload || frame.getAttribute('src') !== url) {
    frame.desktopObserver?.disconnect();
    frame.dataset.loaded = 'false';
    frame.src = url;
  }
  updateDesktopStatus(frame);
}
async function setTab(tab) {
  if (state.tab === 'editor' && state.dirty && tab !== 'editor') await saveFile();
  state.tab = tab; history.replaceState(null, '', `#${tab}`); render(); $('#main-content').scrollTop = 0;
  if (tab === 'editor' && !state.file && state.files.length) await openFile(state.files.find(file => /pre\.cir$/.test(file.path))?.path || state.files[0].path, false);
}
function overviewView() {
  const completed = state.runs.filter(run => ['succeeded','completed','success','passed'].includes(run.status));
  const tools = ['ngspice','xschem','magic','netgen'].filter(name => tool(name).available).length;
  const latest = state.runs[0];
  const stages = [['01',t('Specification'),t('Edit the design brief'),'editor',true],['02',t('Schematic'),t('Open in xschem'),'xschem',!reasonFor('xschem')],['03',t('Pre-layout simulation'),t('Run real ngspice'),'simulation',!reasonFor('simulate')],['04',t('Layout'),t('Open in magic'),'magic',!reasonFor('magic')],['05','DRC / LVS',t('Inspect actual reports'),'verification',!reasonFor('drc') && !reasonFor('lvs')],['06',t('Extraction'),t('Generate parasitics'),'verification',!reasonFor('extract')],['07',t('Post-layout comparison'),t('Compare genuine runs'),'simulation',!reasonFor('simulate')],['08',t('Archive'),t('Download project + evidence'),'export',true]];
  return heading(t('PROJECT WORKSPACE'), esc(state.project.name), t('From the first netlist to the evidence behind every result.'), html`<a href="${projectURL('/export')}" class="secondary-button">↗ Export project</a>`) +
    html`<div class="hero-card card"><p class="eyebrow">DESIGN. SIMULATE. UNDERSTAND.</p><h2>Your circuit, with a clear trail.</h2><p>${state.project.template === 'sky130' ? t('This SKY130 workspace connects design sources to your prepared PDK. Tool and process readiness is checked before execution.') : t('A common-source amplifier is ready to explore. Edit its parameters, run ngspice, and inspect the measurements and raw waveform.')}</p><div class="button-row">${actionButton('simulate',t('▶ Run pre-layout simulation'),'primary-button')}<button class="secondary-button" data-tab="editor">⌘ Open netlist</button></div>${reasonFor('simulate') ? html`<p class="reason">${esc(reasonFor('simulate'))}</p>` : ''}</div>` +
    html`<div class="grid-3"><div class="metric-card"><small>Design sources</small><div class="metric-value">${state.files.length}<span class="muted small"> files</span></div><p>Versioned when saved</p></div><div class="metric-card"><small>Recorded executions</small><div class="metric-value">${state.runs.length}<span class="muted small"> runs</span></div><p>${t('{completed} completed · {stale} stale', {completed:number(completed.length), stale:number(state.runs.filter(run => run.stale).length)})}</p></div><div class="metric-card"><small>Toolchain readiness</small><div class="metric-value">${tools}<span class="muted"> / 4</span></div><p>${pdk().available ? t('Prepared PDK detected') : t('PDK not configured')}</p></div></div>` +
    html`<div class="section-heading"><h2>The design path</h2><span>Manual workflow · AI optional</span></div><div class="stage-list">${stages.map(([index,title,description,target,available]) => html`<button class="stage ${available ? 'available' : ''}" ${['xschem','magic'].includes(target) ? `data-run-kind="${target}" ${available ? '' : `disabled title="${esc(reasonFor(target))}"`}` : target === 'export' ? 'data-action="export"' : `data-tab="${target}"`}><span class="stage-number">${index} <span style="float:right">${available ? '↗' : '○'}</span></span><strong>${title}</strong><small>${description}</small></button>`).join('')}</div>` +
    html`<p class="footnote">Open Xschem or Magic to draw in the Drawing desktop tab. A blank design file is created on first open if needed.</p>` +
    html`<div class="section-heading"><h2>Recent executions</h2><span>${latest ? `${t('Latest')} ${esc(formatDate(runTime(latest)))}` : t('No tool executions yet')}</span></div><div class="card" style="padding:0">${runsTable(state.runs.slice(0, 5))}</div>` +
    html`<p class="footnote">${state.project.template === 'sky130' ? t('DRC and LVS success require report evidence. Extraction coverage is tool and PDK dependent; a completed process is not tapeout signoff.') : t('Educational model · This example uses a level-1 MOS model, not foundry-qualified SKY130 devices. Verification results appear only after actual tool execution.')}</p>`;
}
function runsTable(runs) {
  if (!runs.length) return html('<div class="empty-state" style="min-height:140px"><span class="empty-symbol">∿</span><p>No executions yet. Start a run to create a reproducible record.</p></div>');
  return html`<div class="table-wrap"><table class="data-table"><thead><tr><th>Execution</th><th>State</th><th>Inputs</th><th>Started</th></tr></thead><tbody>${runs.map(run => html`<tr class="clickable" data-run-id="${esc(run.id)}"><td><button class="run-select-link" data-run-id="${esc(run.id)}">${esc(t(run.kind || 'run'))} <span class="muted">${shortId(run.id)}</span></button></td><td>${badge(run.status)}</td><td>${run.stale ? badge('warning',t('stale')) : html('<span class="muted">Current</span>')}</td><td class="muted">${esc(formatDate(runTime(run)))}</td></tr>`).join('')}</tbody></table></div>`;
}
function editorView() {
  return heading(t('DESIGN SOURCES'), t('A direct line to your circuit.'), t('Edit project files locally. Every save records a revision; changes invalidate previous results.')) +
    html`<div class="editor-toolbar"><select id="editor-file" aria-label="File to edit"><option value="">Select a file</option>${state.files.map(file => html`<option value="${esc(file.path)}" ${file.path === state.file ? 'selected' : ''}>${esc(file.path)}</option>`).join('')}</select><button class="secondary-button" data-action="new-file">＋ Add file</button><button class="secondary-button" data-action="history" ${!state.file ? 'disabled' : ''}>◷ History</button><button id="save-file" class="primary-button" data-action="save-file" ${!state.file || state.file === 'project.json' ? 'disabled' : ''}>Save file <span class="small">⌘S</span></button></div>` +
    `${state.file ? html`<div class="editor-surface"><pre class="line-numbers" aria-hidden="true">${lineNumbers(state.content)}</pre><textarea id="code-editor" class="code-editor" ${state.file === 'project.json' ? 'readonly' : ''} aria-label="${esc(t('Source editor for {path}', {path:state.file}))}" spellcheck="false" autocomplete="off" autocapitalize="off">${esc(state.content)}</textarea></div><div class="editor-status"><span id="file-save-status">${state.file === 'project.json' ? t('Project metadata · read only') : state.dirty ? t('Unsaved changes') : t('Saved locally')}</span><span>${esc(state.file)} · UTF-8</span></div><div id="file-history">${historyHTML()}</div>` : html('<div class="empty-state"><span class="empty-symbol">⌘</span><p>Select a source file from the explorer.</p></div>')}`;
}
function lineNumbers(content) { return Array.from({length:content.split('\n').length}, (_, index) => index + 1).join('\n'); }
function historyHTML() {
  if (!state.history) return '';
  return html`<div class="card spaced"><div class="card-head"><h2>File revisions</h2><small>Restoring creates a recoverable edit</small></div>${state.history.length ? state.history.map(revision => { const id = revision.id || revision.revision || revision.sha256 || revision.name; return html`<div class="history-row"><span>${esc(shortId(id))} · ${esc(formatDate(revision.created_at || revision.timestamp || revision.time))}</span><button class="secondary-button" data-restore="${esc(id)}">Restore revision</button></div>`; }).join('') : html('<p>No previous saved revisions for this file.</p>')}</div>`;
}
async function openFile(path, switchTab = true) {
  if (state.dirty) await saveFile();
  const request = ++state.fileRequest;
  const data = await api(projectURL(`/files?path=${encoded(path)}`));
  if (request !== state.fileRequest) return;
  state.file = path; state.content = data.content; state.savedContent = data.content; state.dirty = false; state.history = null;
  if (switchTab) { state.tab = 'editor'; history.replaceState(null, '', '#editor'); }
  renderTree(); render(); $('#main-content').scrollTop = 0; document.body.classList.remove('show-tree');
}
async function saveFile() {
  if (!state.file || !state.dirty) return;
  const content = state.content; const path = state.file;
  await api(projectURL(`/files?path=${encoded(path)}`), {method:'PUT', body:{content}});
  if (state.file === path) { state.savedContent = content; state.dirty = state.content !== content; }
  $('#file-save-status')?.replaceChildren(document.createTextNode(state.dirty ? t('Unsaved changes') : t('Saved locally · revision recorded')));
  await refreshProject(false); toast(t('File saved. Previous results will be checked against the new inputs.'));
}
function simulationView() {
  return heading(t('SIMULATION LAB'), t('See how your circuit behaves.'), t('Configure an analysis, inspect real output, and compare measurements with their source evidence.')) +
    html`<div class="card"><div class="card-head"><h2>Waveform viewer</h2><small id="waveform-title">${esc(state.waveform?.title || t('Actual simulator output'))}</small></div><div class="simulation-toolbar"><select id="waveform-run" aria-label="Execution for waveform"><option value="">Select a simulation execution</option>${state.runs.filter(run => run.kind === 'simulate').map(run => html`<option value="${esc(run.id)}" ${run.id === state.run?.id ? 'selected' : ''}>${esc(runName(run))}${run.stale ? t(' · stale inputs') : ''}</option>`).join('')}</select><button class="secondary-button" data-action="reset-waveform">Reset zoom</button><button class="secondary-button" data-action="export-waveform" ${!state.waveform?.points?.length ? 'disabled' : ''}>↓ CSV</button></div><div class="waveform-wrap"><canvas id="waveform" aria-label="Measured waveforms; scroll to zoom and move pointer for cursor readout" role="img"></canvas><div id="waveform-empty" class="waveform-empty" ${state.waveform?.points?.length ? 'hidden' : ''}><span class="empty-symbol">∿</span><strong>No waveform loaded</strong><span>Select a completed simulation with recorded waveform data.</span></div></div><div id="trace-controls" class="trace-controls"></div><div class="waveform-hint"><span>Scroll to zoom · drag to pan · hover for cursor</span><span id="waveform-cursor">No cursor</span></div>${state.run?.stale ? html('<div class="callout warning">These results use earlier inputs. Run the current design to obtain up-to-date evidence.</div>') : ''}</div>` +
    html`<div id="run-metrics">${metricsHTML()}</div>` +
    html`<details class="card" open><summary class="detail-summary">ANALYSIS CONFIGURATION <span class="muted">· ngspice</span></summary><form id="simulation-form" class="spaced"><div class="form-grid"><label>Analysis<select name="analysis" id="analysis-select"><option value="tran">Transient</option><option value="op">Operating point</option><option value="dc">DC sweep</option><option value="ac">AC frequency</option><option value="noise">Noise</option></select></label><label>Output testbench<input name="netlist" value="testbenches/configured.cir" required></label><div id="analysis-parameters" class="full-width form-grid">${analysisFields('tran')}</div><label class="full-width">Additional SPICE directives<textarea name="directives" class="directives" placeholder=".meas tran vout_mean AVG v(out) FROM=1m TO=2m"></textarea><small>Directives are written into the testbench. Review the generated file in the editor.</small></label></div><div class="form-actions"><button class="secondary-button" type="submit" name="action" value="save">Save testbench</button><button class="primary-button" type="submit" name="action" value="run" ${reasonFor('simulate') ? `disabled title="${esc(reasonFor('simulate'))}"` : ''}>Save & run ▶</button></div></form></details>` +
    html`<div class="card"><div class="card-head"><h2>Compare recorded results</h2><small>Pre / post-layout or two design iterations</small></div>${comparisonHTML()}</div>` +
    html`<details class="card"><summary class="detail-summary">PARAMETER, PVT & STATISTICAL ANALYSIS</summary><p>Use direct SPICE directives in the editor for simulator-supported parameter sweeps and process corners. Bounded resistor search is available in Workflows. Dedicated PVT and Monte Carlo controls are planned.</p><p>Global process variation and device mismatch are different analyses. Statistical support must be verified against the installed PDK models before use.</p></details><div class="section-heading"><h2>Execution history</h2><span>Click an execution for raw logs and provenance</span></div>${runsTable(state.runs.filter(run => run.kind === 'simulate'))}`;
}
function analysisFields(type) {
  const fields = {
    op: [],
    tran:[['step',t('Time step'),'1u'],['stop',t('Stop time'),'2m']],
    dc:[['source',t('Source name'),'VDD'],['start',t('Start value'),'0'],['stop',t('Stop value'),'5'],['step',t('Step'),'0.1']],
    ac:[['points',t('Points per interval'),'100'],['start',t('Start frequency (Hz)'),'1'],['stop',t('Stop frequency (Hz)'),'1Meg']],
    noise:[['output',t('Output node'),'out'],['source',t('Input source'),'VIN'],['points',t('Points per interval'),'100'],['start',t('Start frequency (Hz)'),'1'],['stop',t('Stop frequency (Hz)'),'1Meg']]
  };
  return fields[type].length ? fields[type].map(([name,label,value]) => html`<label>${label}<input name="param-${name}" value="${value}" required></label>`).join('') : html('<p class="muted small">Calculate the DC operating point with the current circuit and source values.</p>');
}
function metricEntries(run = state.run) {
  if (!run?.metrics) return [];
  if (Array.isArray(run.metrics)) return run.metrics.map(metric => [metric.name, metric]);
  return Object.entries(run.metrics);
}
function metricValue(metric) { return typeof metric === 'object' && metric !== null ? metric.value ?? metric.result ?? null : metric; }
function metricsHTML() {
  if (!state.run) return '';
  const entries = metricEntries();
  return html`<div class="card"><div class="card-head"><h2>Measured results</h2>${badge(state.run.status)}</div>${entries.length ? html`<div class="metric-list">${entries.map(([name,metric]) => html`<button class="metric-item" data-metric="${esc(name)}"><span>${esc(name)}</span><strong>${esc(number(metricValue(metric)))} <span style="display:inline;font-size:10px">${esc(metric?.unit || '')}</span></strong><small>Inspect definition and source ↗</small></button>`).join('')}</div>` : html('<p>This execution has no parsed measurements. Inspect the log for simulator output and measurement definitions.</p>')}<div id="metric-evidence"></div><details class="spaced"><summary class="detail-summary">Execution record & artifacts · ${shortId(state.run.id)}</summary>${runDetailsHTML(state.run)}</details></div>`;
}
function runDetailsHTML(run) {
  const artifacts = Array.isArray(run.artifacts) ? run.artifacts : Object.values(run.artifacts || {});
  return html`${run.provenance?.complete === false ? html`<div class="callout warning">Input provenance is incomplete. ${esc((run.provenance.limitations || []).join(" "))} Current project hashes do not establish complete external dependency reproducibility.</div>` : ''}<div class="run-details-grid spaced"><div><strong>Started</strong><span>${esc(formatDate(runTime(run)))}</span></div><div><strong>Finished</strong><span>${esc(formatDate(run.finished_at || run.ended_at))}</span></div><div><strong>Exit code</strong><span>${esc(run.exit_code ?? t('pending / unknown'))}</span></div><div><strong>Input validity</strong><span>${run.stale ? t('Stale — current sources differ') : t('Current project source hashes')}</span></div></div>${run.error || run.diagnostic ? html`<p class="callout error">${esc(run.error || run.diagnostic)}</p>` : ''}<pre class="evidence-details">${esc(pretty(run))}</pre>${artifacts.length ? html`<div class="artifact-links">${artifacts.map(artifact => { const path = typeof artifact === 'string' ? artifact : artifact.path || artifact.name; return path ? html`<a href="${projectURL(`/runs/${encoded(run.id)}/artifact?path=${encoded(path)}`)}">↓ ${esc(path.split('/').pop())}</a>` : ''; }).join('')}</div>` : ''}`;
}
function comparisonHTML() {
  const completed = state.runs.filter(run => run.kind === 'simulate' && terminalStatuses.has(run.status));
  if (completed.length < 2) return html('<p>Complete at least two real simulations to compare metrics. No post-layout results are fabricated; run an extracted testbench when available.</p>');
  const options = selected => html`<option value="">Choose an execution</option>${completed.map(run => html`<option value="${esc(run.id)}" ${selected === run.id ? 'selected' : ''}>${esc(runName(run))}${run.stale ? t(' · stale') : ''}</option>`).join('')}`;
  return html`<div class="comparison-controls"><select id="compare-a" aria-label="Baseline execution">${options(state.compare[0])}</select><span>→</span><select id="compare-b" aria-label="Comparison execution">${options(state.compare[1])}</select></div><div id="comparison-results">${comparisonResults()}</div>`;
}
function comparisonResults() {
  const a = state.runs.find(run => run.id === state.compare[0]), b = state.runs.find(run => run.id === state.compare[1]);
  if (!a || !b) return html('<p>Choose a baseline and comparison run. Each result retains its original inputs and conditions.</p>');
  const am = new Map(metricEntries(a)), bm = new Map(metricEntries(b)), names = [...new Set([...am.keys(), ...bm.keys()])];
  return html`${a.stale || b.stale ? html('<div class="callout warning">One or both runs use older source files. This is a historical comparison.</div>') : ''}${a.id === b.id ? html('<p class="callout warning">Both selectors refer to the same execution.</p>') : ''}<table class="data-table"><thead><tr><th>Metric</th><th>Baseline</th><th>Comparison</th><th>Δ</th><th>Δ %</th></tr></thead><tbody>${names.map(name => { const av = metricValue(am.get(name)), bv = metricValue(bm.get(name)); const numeric = typeof av === 'number' && typeof bv === 'number'; return html`<tr><td>${esc(name)}</td><td>${esc(number(av))}</td><td>${esc(number(bv))}</td><td>${numeric ? esc(number(bv - av)) : '—'}</td><td>${numeric && av !== 0 ? `${esc(number((bv - av) / Math.abs(av) * 100))}%` : '—'}</td></tr>`; }).join('')}</tbody></table>${names.length ? '' : html('<p>No common measurement evidence is available. Inspect each execution log.</p>')}<p class="footnote">Deltas compare recorded values. Verify the run conditions, measurement definitions, and units before drawing a design conclusion.</p>`;
}
function verificationView() {
  const actions = [
    ['magic','◇',t('Layout editor'),t('Open Magic in the Drawing desktop tab. A blank layout is created if the file does not exist.'),t('Open magic')],
    ['drc','⌗',t('Design rule check'),t('Run magic against the configured process rules. Success is determined from the recorded DRC report.'),t('Run DRC')],
    ['lvs','⇄',t('Layout versus schematic'),t('Compare the extracted layout netlist with the schematic source through netgen. Inspect the report to assess the match.'),t('Run LVS')],
    ['extract','⊞',t('Parasitic extraction'),t('Extract layout connectivity and supported parasitics. Review the extraction coverage before post-layout simulation.'),t('Run extraction')]
  ];
  return heading(t('PHYSICAL VERIFICATION'),t('Follow the evidence.'), t('Coordinate native layout tools and inspect their reports, inputs, and execution records.')) +
    html`<div class="callout ${pdk().available ? '' : 'warning'}">${pdk().available ? html`Prepared PDK detected: ${esc(pdk().root)} · ${esc(pdk().variant || '')}. Every verification result still requires its actual tool report.` : html`Verification needs a prepared PDK installation. ${esc(pdk().diagnostic || t('Configure PDK_ROOT in Settings.'))}`}</div><div class="grid-2 spaced">${actions.map(([kind,icon,title,description,label]) => html`<div class="card verification-card"><span class="verification-icon">${icon}</span><div><h2>${title}</h2><p>${description}</p>${actionButton(kind,label)}${reasonFor(kind) ? html`<p class="reason spaced">${esc(reasonFor(kind))}</p>` : ''}</div></div>`).join('')}</div>` +
    html`<div class="card"><h2>Execution inputs</h2><p>These values are passed to the next verification execution. They must refer to actual project sources.</p><div class="form-grid"><label>Layout source<input id="verification-layout" value="layout/amplifier.mag"></label><label>Extracted layout netlist<input id="verification-extracted" value="netlists/extracted.spice"></label><label>Schematic netlist<input id="verification-schematic" value="netlists/schematic.spice"></label><label>Top cell<input id="verification-cell" value="amplifier"></label></div></div>` +
    html`<div class="section-heading"><h2>Verification executions</h2><span>Process completion ≠ verification pass</span></div>${runsTable(state.runs.filter(run => ['drc','lvs','extract','magic'].includes(run.kind)))}` +
    html`${state.run && ['drc','lvs','extract','magic'].includes(state.run.kind) ? html`<div class="card spaced"><div class="card-head"><h2>Selected execution</h2>${badge(state.run.status)}</div>${runDetailsHTML(state.run)}</div>` : ''}<p class="footnote">A working flow is not tapeout signoff. Extraction accuracy and RC coverage depend on the installed tool and PDK configuration. No unexecuted check is shown as passing.</p>`;
}
const nodeCatalog = [
  ['specification','Design specification',true],['parameter','Parameter adjustment',true],['simulate','ngspice simulation',true],['measure','Metric extraction',true],['condition','Target condition',true],['report','Result report',true],
  ['model','Model inference',false],['retrieval','Project knowledge',false],['netlist','Netlist processing',false],['drc','DRC',false],['lvs','LVS',false],['extract','Parasitic extraction',false],['loop','General loop',false],['review','Human review',false]
];
function defaultWorkflow() {
  return {schema_version:1,name:'Amplifier bias tuning',nodes:nodeCatalog.slice(0,6).map(([type,label], index) => ({id:`n${index + 1}`,type,position:{x:35 + (index % 3) * 210,y:55 + Math.floor(index / 3) * 160},config:{label}})),edges:[['n1','n2'],['n2','n3'],['n3','n4'],['n4','n5'],['n5','n6']].map(([source,target]) => ({source,target})),limits:{iterations:8,runtime_seconds:120},tuning:{parameter:'RD',minimum:1000,maximum:10000,target:1.5,tolerance:0.05,metric:'vout_mean',netlist:'testbenches/pre.cir'}};
}
function workflowView() {
  if (!state.workflow) state.workflow = defaultWorkflow();
  const workflow = state.workflow;
  return heading(t('VISUAL WORKFLOWS'),t('Connect a repeatable design loop.'), t('Build a versioned graph and execute bounded parameter tuning with real simulation evidence.'), badge('implemented',t('Experimental execution'))) +
    html`<div class="workflow-toolbar"><input id="workflow-name" value="${esc(workflow.name)}" aria-label="Workflow name" style="max-width:210px"><button class="secondary-button" data-action="save-workflow">Save</button><button class="secondary-button" data-action="load-workflow">Load saved</button><button class="secondary-button" data-action="export-workflow">↓ JSON</button><button class="secondary-button" data-action="import-workflow">↑ Import</button><input type="file" id="workflow-file" accept=".json,application/json" hidden><button class="primary-button" data-action="run-workflow" ${reasonFor('simulate') ? `disabled title="${esc(reasonFor('simulate'))}"` : ''}>▶ Run loop</button></div>` +
    html`<div id="saved-workflows"></div><div class="workflow-board"><div id="workflow-plane" class="workflow-plane"><svg class="workflow-edges" id="workflow-edges" aria-hidden="true"></svg><div id="workflow-nodes"></div></div></div><p class="waveform-hint">Drag nodes to arrange · click a node to configure · graph saved as portable JSON</p>` +
    html`<div class="workflow-toolbar spaced"><select id="new-node-type" aria-label="Node type to add">${nodeCatalog.map(([type,label,implemented]) => html`<option value="${type}">${t(label)}${implemented ? '' : t(' (planned)')}</option>`).join('')}</select><button class="secondary-button" data-action="add-node">＋ Add node</button><select id="edge-from" aria-label="Connection source">${workflow.nodes.map(node => html`<option value="${esc(node.id)}">From: ${esc(node.config?.label || node.type)}</option>`).join('')}</select><select id="edge-to" aria-label="Connection destination">${workflow.nodes.map(node => html`<option value="${esc(node.id)}">To: ${esc(node.config?.label || node.type)}</option>`).join('')}</select><button class="secondary-button" data-action="add-edge">Connect →</button></div>` +
    html`<div id="node-configuration" class="workflow-configuration">${nodeConfiguration()}</div><div class="callout">Executable graph: specification → parameter → simulate → measure → condition → report. The engine runs a bounded resistor search. Other node types and arbitrary graph execution are planned and rejected by validation.</div>` +
    html`<div class="card"><div class="card-head"><h2>Search bounds & stopping conditions</h2><small>Circuit optimization · no model weight updates</small></div><div class="form-grid"><label>Parameter<input id="tuning-parameter" value="${esc(workflow.tuning.parameter)}"></label><label>Measurement<input id="tuning-metric" value="${esc(workflow.tuning.metric)}"></label><label>Minimum<input type="number" id="tuning-minimum" value="${workflow.tuning.minimum}" step="any"></label><label>Maximum<input type="number" id="tuning-maximum" value="${workflow.tuning.maximum}" step="any"></label><label>Target value<input type="number" id="tuning-target" value="${workflow.tuning.target}" step="any"></label><label>Absolute tolerance<input type="number" id="tuning-tolerance" value="${workflow.tuning.tolerance}" min="0" step="any"></label><label>Maximum iterations<input type="number" id="limit-iterations" value="${workflow.limits.iterations}" min="3" max="100"></label><label>Runtime limit (seconds)<input type="number" id="limit-runtime" value="${workflow.limits.runtime_seconds}" min="1" max="3600"></label><label class="full-width">Testbench<input id="tuning-netlist" value="${esc(workflow.tuning.netlist)}"></label></div></div>` +
    html`${jobHistoryHTML('tuning',state.job)}<div id="workflow-job" class="spaced">${jobHTML(state.job)}</div><details class="card"><summary class="detail-summary">Node catalog & implementation status</summary><div class="catalog">${nodeCatalog.map(([,label,implemented]) => badge(implemented ? 'implemented' : 'planned', `${t(label)} · ${t(implemented ? 'implemented' : 'planned')}`)).join('')}</div><p>Workflow files are independent of this editor. Execution validation enforces the supported chain and resource bounds.</p></details><details class="card"><summary class="detail-summary">Inspect or edit graph JSON</summary><textarea class="workflow-json" id="workflow-json" spellcheck="false" aria-label="Workflow JSON">${esc(pretty(workflow))}</textarea><button class="secondary-button spaced" data-action="apply-workflow-json">Apply JSON</button></details>`;
}
function nodeConfiguration() {
  const node = state.workflow.nodes.find(item => item.id === state.workflowSelection);
  if (!node) return html('<p class="footnote">Select a node to inspect its configuration. Connections are listed in the graph JSON.</p>');
  return html`<div class="card"><div class="card-head"><h2>${esc(node.config?.label || node.type)}</h2><button class="text-button danger-button" data-action="delete-node">Remove node</button></div><div class="node-config"><label>Node ID<input value="${esc(node.id)}" readonly></label><label>Display label<input id="node-label" value="${esc(node.config?.label || '')}"></label><label style="grid-column:1/-1">Configuration JSON<textarea id="node-json" class="directives">${esc(pretty(node.config || {}))}</textarea></label></div><button class="secondary-button spaced" data-action="save-node">Apply node configuration</button><p class="node-description">Node type: ${esc(node.type)}. Execution settings come from the tuning bounds below. Only the supported chain is executable.</p>${state.workflow.edges.filter(edge => edge.source === node.id || edge.target === node.id).map((edge) => html`<div class="history-row"><span>${esc(edge.source)} → ${esc(edge.target)}</span><button class="text-button" data-remove-edge="${state.workflow.edges.indexOf(edge)}">Remove connection</button></div>`).join('')}</div>`;
}
function drawWorkflow() {
  if (!$('#workflow-nodes')) return;
  $('#workflow-nodes').innerHTML = state.workflow.nodes.map(node => html`<button class="workflow-node ${state.workflowSelection === node.id ? 'selected' : ''} ${nodeCatalog.find(item => item[0] === node.type)?.[2] ? '' : 'planned'}" data-node="${esc(node.id)}" style="left:${Number(node.position?.x) || 0}px;top:${Number(node.position?.y) || 0}px"><span class="node-label">${esc(node.config?.label || node.type)}</span><span class="node-type">${esc(node.type)} · ${esc(node.id)}</span></button>`).join('');
  drawEdges();
  $$('.workflow-node').forEach(element => element.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    const node = state.workflow.nodes.find(item => item.id === element.dataset.node); let moved = false;
    const origin = {x:event.clientX,y:event.clientY,left:node.position?.x || 0,top:node.position?.y || 0};
    element.setPointerCapture(event.pointerId);
    const move = moveEvent => { if (Math.abs(moveEvent.clientX-origin.x)+Math.abs(moveEvent.clientY-origin.y)>3) moved = true; node.position = {x:Math.max(8, Math.min(900, origin.left + moveEvent.clientX-origin.x)),y:Math.max(8, Math.min(450, origin.top + moveEvent.clientY-origin.y))}; element.style.left = `${node.position.x}px`; element.style.top = `${node.position.y}px`; drawEdges(); };
    const end = () => { element.removeEventListener('pointermove',move); element.removeEventListener('pointerup',end); if (!moved) { state.workflowSelection = node.id; $('#node-configuration').innerHTML = nodeConfiguration(); $$('.workflow-node').forEach(item => item.classList.toggle('selected', item.dataset.node === node.id)); } syncWorkflowJSON(); };
    element.addEventListener('pointermove',move); element.addEventListener('pointerup',end,{once:true});
  }));
}
function drawEdges() {
  $('#workflow-edges').innerHTML = html`<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="var(--workflow-marker)"/></marker></defs>${state.workflow.edges.map(edge => { const source = state.workflow.nodes.find(node => node.id === edge.source), target = state.workflow.nodes.find(node => node.id === edge.target); if (!source || !target) return ''; const x1 = (Number(source.position?.x) || 0)+158,y1=(Number(source.position?.y)||0)+37,x2=(Number(target.position?.x)||0)-4,y2=(Number(target.position?.y)||0)+37, bend = Math.max(55,Math.abs(x2-x1)/2); return html`<path d="M ${x1} ${y1} C ${x1+bend} ${y1}, ${x2-bend} ${y2}, ${x2} ${y2}" fill="none" stroke="var(--workflow-edge)" stroke-width="1.5" marker-end="url(#arrow)"/>`; }).join('')}`;
}
function captureWorkflow() {
  if (!$('#workflow-name')) return state.workflow;
  state.workflow.name = $('#workflow-name').value;
  for (const field of ['parameter','metric','netlist']) state.workflow.tuning[field] = $(`#tuning-${field}`).value;
  for (const field of ['minimum','maximum','target','tolerance']) state.workflow.tuning[field] = Number($(`#tuning-${field}`).value);
  state.workflow.limits = {iterations:Number($('#limit-iterations').value),runtime_seconds:Number($('#limit-runtime').value)};
  return state.workflow;
}
function syncWorkflowJSON() { if ($('#workflow-json')) $('#workflow-json').value = pretty(state.workflow); }
function validateWorkflowShape(workflow) {
  if (workflow?.schema_version !== 1 || !Array.isArray(workflow.nodes) || !Array.isArray(workflow.edges) || !workflow.tuning || !workflow.limits) throw new Error(t('Expected workflow schema_version 1 with nodes, edges, tuning, and limits.'));
  if (workflow.nodes.some(node => !node.id || !node.type)) throw new Error(t('Every node needs an ID and a type.'));
  workflow.nodes.forEach((node, index) => { node.position ||= {x:30+(index%3)*200,y:40+Math.floor(index/3)*140}; node.config ||= {}; });
  return workflow;
}
function jobHTML(job, evaluation = false) {
  if (!job) return '';
  const iterations = job.iterations || [];
  return html`<div class="card"><div class="card-head"><h2>${evaluation ? t('Evaluation record') : t('Workflow execution')} <span class="muted mono small">${shortId(job.id)}</span></h2><div class="button-row">${badge(job.status)}${isActive(job) ? html`<button class="secondary-button danger-button" data-action="cancel-${evaluation ? 'evaluation' : 'workflow'}">Stop</button>` : ''}</div></div>${job.error ? html`<div class="callout error">${esc(job.error)}</div>` : ''}${iterations.length ? html`<table class="data-table"><thead><tr><th>Step</th><th>Parameter</th><th>Value</th><th>Metric</th><th>Evidence</th></tr></thead><tbody>${iterations.map((iteration,index) => html`<tr><td>${index+1}</td><td>${esc(iteration.parameter || '—')}</td><td>${esc(number(iteration.value))}</td><td>${esc(number(iteration.metric && typeof iteration.metric === 'object' ? iteration.metric.value : iteration.metric))}</td><td>${iteration.run_id ? html`<button class="run-select-link" data-run-id="${esc(iteration.run_id)}">${shortId(iteration.run_id)} ↗</button>` : '—'}</td></tr>`).join('')}</tbody></table>` : html`<p>${isActive(job) ? t('The engine is executing. Actual results will appear as each step completes.') : t('Inspect the record below for results and diagnostic evidence.')}</p>`}<details><summary class="detail-summary">Full execution record</summary><pre class="evidence-details">${esc(pretty(job))}</pre></details></div>`;
}
function jobHistoryHTML(kind, selected) {
  const jobs = state.jobs.filter(job => job.kind === kind);
  return jobs.length ? html`<label class="spaced">${t('Inspect a recorded {kind} execution', {kind:t(kind === 'evaluation' ? 'evaluation' : 'workflow')})}<select class="job-history" data-job-kind="${kind}"><option value="">Select historical execution</option>${jobs.map(job => html`<option value="${esc(job.id)}" ${job.id === selected?.id ? 'selected' : ''}>${esc(shortId(job.id))} · ${esc(t(job.status))} · ${esc(formatDate(job.created_at))}</option>`).join('')}</select><small>Selecting a record only inspects evidence; it does not rerun the workflow.</small></label>` : '';
}
function evaluationView() {
  return heading(t('EVALUATION ENVIRONMENT'),t('Measure an agent’s decisions.'), t('Collect real circuit trajectories and compare reproducible outcomes in isolated working directories.'), badge('experimental',t('Evaluation available'))) +
    html`<div class="callout">This workspace evaluates circuit parameter search using real simulator runs. It does not update model weights. Evaluation and circuit optimization are separate from reinforcement learning training.</div><div class="grid-2 spaced"><div class="card"><p class="eyebrow">REAL EDA EPISODES</p><h2>Run an evaluation</h2><p>Each episode uses an isolated project copy, a reproducible seed, and evaluator-owned scoring. Inspect the returned trajectory and resource costs.</p><form id="evaluation-form"><div class="form-grid"><label>Episodes<input type="number" name="episodes" value="3" min="1" max="20" required></label><label>Random seed<input type="number" name="seed" value="42" required></label></div><div class="form-actions"><button class="primary-button" type="submit" ${reasonFor('simulate') ? `disabled title="${esc(reasonFor('simulate'))}"` : ''}>▶ Evaluate</button></div>${reasonFor('simulate') ? html`<p class="footnote">${esc(reasonFor('simulate'))}</p>` : ''}</form></div><div class="card"><p class="eyebrow">MODEL TRAINING</p><h2>Weight-updating RL</h2>${badge('unavailable',t('Not integrated'))}<p>No training framework is connected in this release. A trainable model, framework adapter, suitable resources, and verified checkpoint and held-out evaluation evidence are required.</p><button class="secondary-button" disabled title="No executable training framework adapter is integrated.">Start model training</button><p class="footnote">Inference APIs and authenticated agent CLIs are assistance or evaluation backends, not training interfaces.</p></div></div>${jobHistoryHTML('evaluation',state.evaluation)}<div id="evaluation-job" class="spaced">${jobHTML(state.evaluation,true)}</div><div class="card"><h2>What an evaluation can establish</h2><p>Results must preserve observations, parameter actions, tool executions, measurements, score breakdowns, failures, and termination reasons. Inspect the actual job record for the evidence collected by this implementation.</p><div class="button-row">${badge('implemented',t('Isolated episodes'))}${badge('implemented',t('Seeded evaluation'))}${badge('planned',t('Trainable framework adapter'))}${badge('planned',t('Model checkpoints'))}</div></div>`;
}
function settingsView() {
  const settings = state.settings;
  return heading(t('ENVIRONMENT & CONNECTIONS'),t('Know what is ready.'), t('Detect local dependencies and configure the tools and optional model endpoint used by this workspace.'), html('<button class="secondary-button" data-action="detect-tools">↻ Recheck tools</button>')) +
    preferencesHTML() +
    html`<div class="card"><div class="card-head"><h2>Detected toolchain</h2><small>Actual local probes</small></div><div class="tool-grid">${['ngspice','xschem','magic','netgen'].map(name => html`<div class="tool-card"><div><strong>${name}</strong>${badge(tool(name).available ? 'available' : 'unavailable', tool(name).available ? t('Detected') : t('Unavailable'))}</div><p>${esc(tool(name).path || t('Executable not found'))}<br>${esc(tool(name).version || tool(name).diagnostic || t('Version unknown'))}</p></div>`).join('')}</div><div class="callout ${pdk().available ? '' : 'warning'}"><strong>Prepared PDK · ${esc(pdk().variant || 'SKY130')}</strong><br>${esc(pdk().root || pdk().diagnostic || t('No PDK installation configured.'))}</div><details><summary class="detail-summary">Desktop session & capability diagnostics</summary><pre class="evidence-details">${esc(pretty({desktop:state.status?.toolchain?.desktop,capabilities:state.status?.capabilities,pdk:pdk()}))}</pre></details></div>` +
    html`<form id="settings-form"><div class="card"><h2>Local tool paths & process kit</h2><p>Leave a tool path empty to search the local PATH. A raw PDK source checkout is not a prepared installation.</p><div class="form-grid">${['ngspice','xschem','magic','netgen','codex','claude'].map(name => html`<label>${name} executable<input name="tool-${name}" value="${esc(settings.tools?.[name] || '')}" placeholder="Discover on PATH"></label>`).join('')}<label>PDK root<input name="pdk_root" value="${esc(settings.pdk_root || '')}" placeholder="/opt/pdk"></label><label>PDK variant<input name="pdk_variant" value="${esc(settings.pdk_variant || 'sky130A')}"></label></div></div>${providerSettingsHTML()}<div class="form-actions"><button type="submit" class="primary-button">Save configuration</button><button type="button" class="secondary-button" data-action="test-provider">Save and test connection</button><button type="button" class="secondary-button" data-action="export-settings">↓ Export safe config</button><button type="button" class="secondary-button" data-action="import-settings">↑ Import config</button><input type="file" id="settings-file" accept=".json,application/json" hidden></div></form><p class="footnote">Agent CLIs use their existing local login. Choose a backend and permissions in the assistant sidebar. Manual circuit tools remain independent of model configuration.</p>`;
}

function providerSettingsHTML() {
  const provider = state.settings.provider || {};
  return html`<section class="card" id="provider-settings" aria-labelledby="provider-heading"><div class="card-head"><h2 id="provider-heading">Model API</h2>${badge('implemented',t('Project agent'))}</div><p>Enter your endpoint, model, and API key here to connect the design assistant.</p><div class="form-grid"><label>Provider name (optional)<input id="provider-name" name="provider_name" value="${esc(provider.name || '')}" placeholder="My model provider"></label><label>Protocol<select name="protocol"><option value="openai-compatible" ${provider.protocol === 'openai-compatible' ? 'selected' : ''}>OpenAI-compatible chat API</option><option value="ollama" ${provider.protocol === 'ollama' ? 'selected' : ''}>Ollama native API</option></select></label><label class="full-width">Base URL<input name="base_url" value="${esc(provider.base_url || '')}" placeholder="https://api.example.com/v1" autocomplete="url" spellcheck="false"><small>Include /v1 if your OpenAI-compatible provider requires it. Ollama defaults to http://127.0.0.1:11434.</small></label><label>Model<input id="provider-model" name="model" value="${esc(provider.model || '')}" placeholder="Enter a model name" list="provider-models" autocomplete="off"><datalist id="provider-models">${providerModelOptions()}</datalist></label><label>Request timeout (seconds)<input type="number" min="1" max="300" name="timeout" value="${Number(provider.timeout) || 60}"></label><label class="full-width">API key<input id="provider-api-key" name="api_key" type="password" autocomplete="new-password" spellcheck="false" autocapitalize="none" aria-describedby="provider-key-help provider-key-status"><small id="provider-key-help">Leave blank to keep the key for the current endpoint. Enter a new key when changing the endpoint or protocol.</small></label></div><p id="provider-key-status" class="credential-status" role="status">${esc(providerKeyStatus())}</p><div class="provider-key-controls"><label class="check-label" for="provider-remember-key"><input id="provider-remember-key" name="remember_api_key" type="checkbox" ${provider.api_key_storage === 'session' ? '' : 'checked'}>Remember API key on this server</label><button id="provider-clear-key" type="button" class="secondary-button danger-button" data-action="clear-provider-key" ${['local','session'].includes(provider.api_key_storage) ? '' : 'disabled'}>Clear saved key</button></div><p class="footnote">When checked, the key is saved in a file readable and writable only by the current server user; it is not encrypted. Otherwise, it lasts until the workspace server stops. Keys are excluded from configuration exports.</p><details><summary class="detail-summary">Advanced: environment variable</summary><label>API key environment variable<input name="api_key_env" value="${esc(provider.api_key_env || '')}" placeholder="ANALOG_FACTORY_API_KEY" autocomplete="off" spellcheck="false"><small>Optional fallback when no key is saved here. Enter a variable name, not the key itself. Clearing the saved key keeps this fallback.</small></label></details><p class="footnote">The connection test fetches the model list without sending project contents. You can also enter a model name manually; a connection test does not verify chat support.</p><div id="provider-test-result" class="provider-result" role="status" aria-live="polite"></div></section>`;
}
function providerKeyStatus() {
  const provider = state.settings.provider || {};
  if (!provider.api_key_configured) return t('No API key configured. Local providers may work without a key.');
  return t({local:'API key saved on this server.', session:'API key available until the workspace server stops.', environment:'API key available from the configured environment variable.'}[provider.api_key_storage] || 'API key configured.');
}
function providerModelOptions() {
  return state.providerModels.map(model => `<option value="${esc(model)}"></option>`).join('');
}
function updateProviderFeedback() {
  const status = $('#provider-key-status');
  if (status) status.textContent = providerKeyStatus();
  const result = $('#provider-test-result');
  if (!result) return;
  const test = state.providerTest;
  result.classList.toggle('error-inline', test?.status === 'error');
  result.dataset.status = test?.status || '';
  result.textContent = !test ? '' : test.status === 'saving' ? t('Saving API configuration…')
    : test.status === 'testing' ? t('Testing connection…')
    : test.status === 'success' ? (state.providerModels.length ? `${t('Connection successful. Available models:')}\n${state.providerModels.join('\n')}` : t('Connection successful. No models were returned; enter a model name manually.'))
    : `${t(test.saved ? 'Configuration saved, but the connection test failed.' : 'Could not save API configuration. Your input has been kept.')} ${test.error}`;
  const models = $('#provider-models');
  if (models) models.innerHTML = providerModelOptions();
}
function updateProviderControls() {
  $$('#settings-form input, #settings-form select, #settings-form button').forEach(element => { element.disabled = providerSaving; });
  const clear = $('#provider-clear-key');
  if (clear) clear.disabled = providerSaving || !['local','session'].includes(state.settings.provider?.api_key_storage);
  $('#settings-form')?.setAttribute('aria-busy', String(providerSaving));
}

function preferencesHTML() {
  const ui = preferences.current();
  return html`<section class="card preferences-card" aria-labelledby="preferences-heading"><div class="card-head"><h2 id="preferences-heading">User interface</h2></div><div class="form-grid"><label for="ui-language">Language<select id="ui-language" ${preferencesSaving ? 'disabled' : ''}><option value="en" ${ui.language === 'en' ? 'selected' : ''}>English</option><option value="zh-CN" ${ui.language === 'zh-CN' ? 'selected' : ''}>简体中文</option></select></label><label for="ui-theme">Appearance<select id="ui-theme" ${preferencesSaving ? 'disabled' : ''}><option value="light" ${ui.theme === 'light' ? 'selected' : ''}>Light</option><option value="dark" ${ui.theme === 'dark' ? 'selected' : ''}>Dark</option></select></label></div><p class="footnote">Changes apply immediately and are saved for this workspace.</p><p id="ui-preferences-status" class="footnote" role="status" aria-live="polite">${preferencesSaving ? t('Saving preferences…') : ''}</p></section>`;
}
function captureViewDraft() {
  const main = $('#main-content');
  const controls = $$('input, select, textarea', main).filter(element => element.type !== 'file' && !['ui-language', 'ui-theme'].includes(element.id));
  return {
    scrollTop:main.scrollTop,
    details:$$('details', main).map(element => element.open),
    fields:controls.map((element, index) => ({
      id:element.id, name:element.name, formId:element.form?.id, index,
      tag:element.tagName, type:element.type, trace:element.dataset.trace,
      value:element.value, checked:element.checked,
      selected:element.multiple ? [...element.selectedOptions].map(option => option.value) : null,
      focused:element === document.activeElement,
      start:element.selectionStart, end:element.selectionEnd,
      scrollTop:element.scrollTop, scrollLeft:element.scrollLeft
    }))
  };
}
function restoreViewDraft(draft) {
  const main = $('#main-content');
  const controls = $$('input, select, textarea', main).filter(element => element.type !== 'file' && !['ui-language', 'ui-theme'].includes(element.id));
  $$('details', main).forEach((element, index) => { if (index < draft.details.length) element.open = draft.details[index]; });
  for (const field of draft.fields) {
    const element = field.id ? controls.find(control => control.id === field.id)
      : field.formId && field.name ? controls.find(control => control.form?.id === field.formId && control.name === field.name)
      : field.trace ? controls.find(control => control.dataset.trace === field.trace)
      : controls[field.index];
    if (!element || element.tagName !== field.tag || element.type !== field.type) continue;
    if (field.selected) [...element.options].forEach(option => { option.selected = field.selected.includes(option.value); });
    else element.value = field.value;
    if (['checkbox', 'radio'].includes(element.type)) element.checked = field.checked;
    if (field.focused) element.focus({preventScroll:true});
    if (field.start != null && element.setSelectionRange) element.setSelectionRange(field.start, field.end);
    element.scrollTop = field.scrollTop; element.scrollLeft = field.scrollLeft;
  }
  main.scrollTop = draft.scrollTop;
}
function applyPreferences(ui, preserveDraft = true) {
  // A delayed save can settle after navigation. Preserve the currently visible
  // drafts, including graph JSON and verification overrides, without applying them.
  const draft = preserveDraft ? captureViewDraft() : null;
  preferences.apply(ui);
  window.AFI18n.applyDOM();
  updateChrome(); renderTree(); updateLog(); renderChat();
  render();
  if (draft) restoreViewDraft(draft);
}
async function savePreferences() {
  if (preferencesSaving) return;
  const previous = preferences.current();
  const focusId = ['ui-language', 'ui-theme'].includes(document.activeElement?.id) ? document.activeElement.id : null;
  let focusMoved = false;
  const trackFocus = event => { if (event.target !== document.body && event.target.id !== focusId) focusMoved = true; };
  document.addEventListener('focusin', trackFocus);
  const requested = {language:$('#ui-language').value, theme:$('#ui-theme').value};
  preferencesSaving = true;
  applyPreferences(requested);
  try {
    state.settings = await api('/api/settings', {method:'PUT', body:{ui:requested}});
    preferences.apply(state.settings.ui);
    if ($('#ui-preferences-status')) $('#ui-preferences-status').textContent = t('Preferences saved.');
  } catch (error) {
    applyPreferences(previous);
    const message = t('Could not save preferences. Your previous settings have been restored.');
    if ($('#ui-preferences-status')) $('#ui-preferences-status').textContent = message;
    toast(`${message} ${error.message}`, true);
  } finally {
    preferencesSaving = false;
    for (const id of ['ui-language', 'ui-theme']) if ($(`#${id}`)) $(`#${id}`).disabled = false;
    document.removeEventListener('focusin', trackFocus);
    if (focusId && !focusMoved && state.tab === 'settings' && document.activeElement === document.body) $(`#${focusId}`)?.focus({preventScroll:true});
  }
}

async function refreshProject(redraw = true) {
  if (!state.project) return;
  const id = state.project.id;
  const [data, jobs] = await Promise.all([api(projectURL()), api(projectURL('/jobs'))]);
  if (state.project?.id !== id) return;
  state.project = data.project; state.files = data.files || []; state.runs = data.runs || [];
  state.jobs = asArray(jobs, 'jobs');
  if (state.run) state.run = state.runs.find(run => run.id === state.run.id) || state.run;
  renderTree(); updateChrome(); updateLog(); if (redraw) render();
}
async function refreshAgentProject(id) {
  if (state.project?.id !== id || state.loadingProject) return;
  await refreshProject(false);
  if (state.project?.id !== id || state.dirty || !state.file) return;
  const path = state.file, request = state.fileRequest;
  const data = await api(`/api/projects/${encoded(id)}/files?path=${encoded(path)}`);
  // The user may begin typing or open another file while the refresh is pending.
  if (state.project?.id !== id || state.dirty || state.file !== path || state.fileRequest !== request) return;
  state.content = data.content; state.savedContent = data.content; state.history = null;
  const editor = $('#code-editor');
  if (editor) {
    const start = editor.selectionStart, end = editor.selectionEnd, scroll = editor.scrollTop;
    editor.value = data.content; editor.setSelectionRange(start, end); editor.scrollTop = scroll;
    $('.line-numbers').textContent = lineNumbers(data.content);
    $('#file-history').innerHTML = '';
  }
}
async function startRun(kind, options = {}) {
  const reason = reasonFor(kind); if (reason) throw new Error(reason);
  const editor = ['xschem','magic'].includes(kind);
  if (state.dirty) await saveFile();
  if (kind === 'simulate' && !options.netlist) options.netlist = 'testbenches/pre.cir';
  if (['drc','lvs','extract','magic'].includes(kind)) {
    options = {...options,layout:$('#verification-layout')?.value || 'layout/amplifier.mag',layout_netlist:$('#verification-extracted')?.value || 'netlists/extracted.spice',schematic_netlist:$('#verification-schematic')?.value || 'netlists/schematic.spice',cell:$('#verification-cell')?.value || 'amplifier'};
  }
  if (editor) options = {...options,create_missing:true};
  const sourceKey = kind === 'xschem' ? 'schematic' : 'layout';
  const source = options[sourceKey] || (kind === 'xschem' ? 'schematics/amplifier.sch' : 'layout/amplifier.mag');
  const projectId = state.project.id;
  const launchKey = `${projectId}:${kind}:${source}`;
  if (editor && editorLaunches.has(launchKey)) return;
  if (editor) editorLaunches.add(launchKey);
  try {
    if (editor) {
      await refreshProject(false);
      if (state.project?.id !== projectId) return;
      const existing = state.runs.find(run => run.kind === kind && isActive(run) && run.metadata?.sources?.[sourceKey] === source);
      if (existing) {
        await selectRun(existing.id, false);
        await setTab('desktop');
        toast(t('{tool} is already open. Continue in the drawing desktop.', {tool:kind}));
        return;
      }
    }
    const run = await api(projectURL('/runs'), {method:'POST',body:{kind,options,timeout:editor ? 3600 : 60}});
    if (state.project?.id !== projectId) return;
    state.runs.unshift(run); state.run = run; state.waveform = null; state.log = '';
    if (editor) { state.tab = 'desktop'; history.replaceState(null, '', '#desktop'); }
    else if (kind === 'simulate') state.tab = 'simulation'; else if (['drc','lvs','extract'].includes(kind)) state.tab = 'verification';
    render(); $('#main-content').scrollTop = 0; updateLog(); updateChrome();
    toast(editor ? t('Opening {tool} in the drawing desktop…', {tool:kind}) : t('Started {kind} · {id}', {kind:t(kind), id:shortId(run.id)}));
    await selectRun(run.id, false);
    if (editor) await refreshProject();
  } finally {
    if (editor) editorLaunches.delete(launchKey);
  }
}
async function selectRun(id, navigate = true) {
  const request = ++state.runRequest;
  const run = await api(projectURL(`/runs/${encoded(id)}`));
  if (request !== state.runRequest) return;
  state.run = run; state.waveform = null; state.zoom = [0,1]; state.cursor = null;
  const existing = state.runs.findIndex(item => item.id === run.id); if (existing >= 0) state.runs[existing] = run; else state.runs.unshift(run);
  const results = await Promise.allSettled([api(projectURL(`/runs/${encoded(id)}/log`)), run.kind === 'simulate' && !isActive(run) ? api(projectURL(`/runs/${encoded(id)}/waveforms`)) : Promise.resolve(null)]);
  if (request !== state.runRequest) return;
  state.log = results[0].status === 'fulfilled' ? results[0].value.content || '' : t('Log unavailable: {message}', {message:results[0].reason.message});
  if (results[1].status === 'fulfilled' && results[1].value?.points?.length) { state.waveform = results[1].value; state.traces = new Set(state.waveform.variables.slice(1).map((_, index) => index+1)); }
  if (navigate) { state.tab = run.kind === 'simulate' ? 'simulation' : ['drc','lvs','extract','magic'].includes(run.kind) ? 'verification' : state.tab; history.replaceState(null,'',`#${state.tab}`); }
  updateLog(); if (navigate || ['simulation','verification'].includes(state.tab)) render();
}
function updateLog() {
  agentUI.render();
  $('#log-label').textContent = state.run ? `${runName(state.run)}${state.run.stale ? t(t(' · STALE')) : ''}` : t('No run selected');
  const log = $('#execution-log'), nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
  log.textContent = state.log || (state.run ? (isActive(state.run) ? t('Waiting for tool output…') : t('No log output was recorded.')) : t('Run a simulation or select an execution to inspect its actual tool output.'));
  if (nearBottom) log.scrollTop = log.scrollHeight;
  $('#run-cancel').hidden = !isActive(state.run); $('#run-retry').hidden = !state.run || isActive(state.run);
}
let pollCount = 0;
async function poll() {
  if (state.polling || document.hidden || state.loadingProject) return;
  state.polling = true;
  try {
    if (++pollCount % 12 === 0 || !state.connected) { state.status = await api('/api/status'); state.connected = true; updateChrome(); }
    if (!state.project) return;
    if (state.run && isActive(state.run)) {
      const id = state.run.id;
      const results = await Promise.allSettled([api(projectURL(`/runs/${encoded(id)}`)), api(projectURL(`/runs/${encoded(id)}/log`))]);
      if (state.run?.id !== id) return;
      if (results[0].status === 'fulfilled') {
        const wasActive = isActive(state.run); state.run = results[0].value;
        const index = state.runs.findIndex(run => run.id === id); if (index >= 0) state.runs[index] = state.run;
        if (wasActive && !isActive(state.run)) { await refreshProject(false); await selectRun(id, false); toast(`${t(state.run.kind)} ${t(state.run.status)}`, ['failed','error','timeout'].includes(state.run.status)); if (state.tab === 'overview') render(); }
      }
      if (results[1].status === 'fulfilled') state.log = results[1].value.content || '';
      updateLog();
    }
    for (const [key, selector] of [['job','#workflow-job'],['evaluation','#evaluation-job']]) {
      if (isActive(state[key])) { const id = state[key].id; state[key] = await api(projectURL(`/jobs/${encoded(id)}`)); if ($(selector)) $(selector).innerHTML = jobHTML(state[key],key === 'evaluation'); if (!isActive(state[key])) { await refreshProject(false); toast(`${key === 'job' ? t('Workflow') : t('Evaluation')} ${t(state[key].status)}`, state[key].status === 'failed'); } }
    }
  } catch (error) { if (/fetch|network|connect/i.test(error.message)) { state.connected = false; updateChrome(); } }
  finally { state.polling = false; }
}
function bindView() {
  $$('.job-history').forEach(select => select.addEventListener('change', event => {
    if (!event.target.value) return;
    safely(async () => {
      const evaluation = event.target.dataset.jobKind === 'evaluation';
      const key = evaluation ? 'evaluation' : 'job';
      state[key] = await api(projectURL(`/jobs/${encoded(event.target.value)}`));
      $(evaluation ? '#evaluation-job' : '#workflow-job').innerHTML = jobHTML(state[key], evaluation);
    });
  }));
  if (state.tab === 'editor') {
    $('#code-editor')?.addEventListener('input', event => { state.content = event.target.value; state.dirty = state.content !== state.savedContent; $('.line-numbers').textContent = lineNumbers(state.content); $('#file-save-status').textContent = state.dirty ? t('Unsaved changes · save with Ctrl / ⌘ S') : t('Saved locally'); });
    $('#code-editor')?.addEventListener('scroll', event => { $('.line-numbers').scrollTop = event.target.scrollTop; });
    $('#code-editor')?.addEventListener('keydown', event => { if (event.key === 'Tab') { event.preventDefault(); const editor = event.target, start = editor.selectionStart, end = editor.selectionEnd; editor.setRangeText('  ',start,end,'end'); editor.dispatchEvent(new Event('input')); } });
    $('#editor-file')?.addEventListener('change', event => safely(() => openFile(event.target.value,false)));
  }
  if (state.tab === 'simulation') {
    $('#waveform-run')?.addEventListener('change', event => event.target.value && safely(() => selectRun(event.target.value,false)));
    $('#analysis-select')?.addEventListener('change', event => { $('#analysis-parameters').innerHTML = analysisFields(event.target.value); });
    $('#simulation-form')?.addEventListener('submit', event => { event.preventDefault(); safely(async () => { const form = new FormData(event.target), parameters = {}; for (const [key,value] of form.entries()) if (key.startsWith('param-')) parameters[key.slice(6)] = value; const result = await api(projectURL('/simulation'),{method:'POST',body:{analysis:form.get('analysis'),parameters,directives:form.get('directives'),netlist:form.get('netlist')}}); await refreshProject(false); const path = result.path || result.netlist || form.get('netlist'); toast(t('Testbench saved: {path}', {path})); if (event.submitter?.value === 'run') await startRun('simulate',{netlist:path}); }); });
    for (const [id,index] of [['compare-a',0],['compare-b',1]]) $( `#${id}`)?.addEventListener('change', event => { state.compare[index] = event.target.value; $('#comparison-results').innerHTML = comparisonResults(); });
    setupWaveform();
  }
  if (state.tab === 'workflows') {
    drawWorkflow();
    $('#workflow-file')?.addEventListener('change', event => safely(async () => { const file = event.target.files[0]; if (!file) return; state.workflow = validateWorkflowShape(JSON.parse(await file.text())); state.workflowSelection = null; render(); toast(t('Workflow imported for review.')); }));
    $('#workflow-name')?.addEventListener('change', () => { captureWorkflow(); syncWorkflowJSON(); });
    $$('[id^="tuning-"],[id^="limit-"]').forEach(element => element.addEventListener('change', () => { captureWorkflow(); syncWorkflowJSON(); }));
  }
  if (state.tab === 'evaluation') $('#evaluation-form')?.addEventListener('submit', event => { event.preventDefault(); safely(async () => { const form = new FormData(event.target); state.evaluation = await api(projectURL('/evaluate'),{method:'POST',body:{episodes:Number(form.get('episodes')),seed:Number(form.get('seed'))}}); $('#evaluation-job').innerHTML = jobHTML(state.evaluation,true); toast(t('Evaluation started with isolated real-simulation episodes.')); }); });
  if (state.tab === 'settings') {
    for (const id of ['ui-language', 'ui-theme']) $(`#${id}`)?.addEventListener('change', () => safely(savePreferences));
    $('#settings-form')?.addEventListener('submit', event => { event.preventDefault(); safely(saveSettings); });
    $('#settings-file')?.addEventListener('change', event => safely(async () => { const file = event.target.files[0]; if (!file || providerSaving) return; const settings = JSON.parse(await file.text()); if (!settings || typeof settings !== 'object' || Array.isArray(settings)) throw new Error(t('Expected a configuration JSON object.')); const provider = settings.provider || {}; const safeSettings = {...(settings.ui ? {ui:settings.ui} : {}),tools:{...state.settings.tools,...(settings.tools || {})},pdk_root:settings.pdk_root || '',pdk_variant:settings.pdk_variant || 'sky130A',provider:{name:provider.name || '',protocol:provider.protocol || 'openai-compatible',base_url:provider.base_url || '',model:provider.model || '',api_key_env:provider.api_key_env || '',timeout:provider.timeout || 60}}; state.settings = await api('/api/settings',{method:'PUT',body:safeSettings}); state.providerTest = null; state.providerModels = []; state.status = await api('/api/status'); applyPreferences(state.settings.ui, false); agentUI.refreshCapabilities(); toast(t('Configuration imported.')); }));
    $('#provider-settings')?.addEventListener('input', event => {
      if (providerSaving) return;
      state.providerTest = null;
      if (['protocol','base_url'].includes(event.target.name)) state.providerModels = [];
      updateProviderFeedback();
    });
    updateProviderFeedback(); updateProviderControls();
  }
}
async function saveSettings({test = false} = {}) {
  const element = $('#settings-form');
  if (providerSaving || !element || !element.reportValidity()) return;
  const form = new FormData(element);
  const provider = {name:form.get('provider_name'),protocol:form.get('protocol'),base_url:form.get('base_url'),model:form.get('model'),api_key_env:form.get('api_key_env'),timeout:Number(form.get('timeout')),api_key_storage:form.has('remember_api_key') ? 'local' : 'session'};
  const previous = state.settings.provider || {};
  const endpointChanged = String(provider.base_url).trim().replace(/\/+$/, '') !== String(previous.base_url || '').replace(/\/+$/, '') || provider.protocol !== previous.protocol;
  // An inherited environment credential must not silently follow a new endpoint.
  if (endpointChanged && provider.api_key_env === (previous.api_key_env || '')) delete provider.api_key_env;
  const key = String(form.get('api_key') || '');
  if (key.trim()) provider.api_key = key;
  const settings = {tools:{...state.settings.tools,...Object.fromEntries(['ngspice','xschem','magic','netgen','codex','claude'].map(name => [name,form.get(`tool-${name}`)]))},pdk_root:form.get('pdk_root'),pdk_variant:form.get('pdk_variant'),provider};
  providerSaving = true;
  state.providerTest = test ? {status:'saving'} : null;
  updateProviderControls(); updateProviderFeedback();
  let saved = false;
  try {
    state.settings = await api('/api/settings',{method:'PUT',body:settings});
    saved = true;
    agentUI.refreshCapabilities();
    // Never repopulate a secret from server responses or retain a submitted key.
    if ($('#provider-api-key')) $('#provider-api-key').value = '';
    const environment = $('#settings-form [name=api_key_env]');
    if (environment) environment.value = state.settings.provider?.api_key_env || '';
    state.providerModels = [];
    updateChrome(); updateProviderFeedback();
    try { state.status = await api('/api/status'); updateChrome(); }
    catch (_) { toast(t('Configuration saved. Tool status could not be refreshed.'), true); }
    if (test) {
      state.providerTest = {status:'testing'}; updateProviderFeedback();
      const result = await api('/api/provider/test',{method:'POST',body:{},long:true});
      state.providerModels = [...new Set((Array.isArray(result.models) ? result.models : []).filter(model => typeof model === 'string' && model.trim()))];
      state.providerTest = {status:'success'};
    } else toast(t('Local configuration saved.'));
  } catch (error) {
    state.providerTest = {status:'error', saved, error:error.message};
    throw error;
  } finally {
    providerSaving = false;
    updateProviderControls(); updateProviderFeedback();
  }
}
async function clearProviderKey() {
  if (providerSaving) return;
  providerSaving = true; updateProviderControls();
  try {
    state.settings = await api('/api/settings',{method:'PUT',body:{provider:{clear_api_key:true}}});
    if ($('#provider-api-key')) $('#provider-api-key').value = '';
    state.providerTest = null; state.providerModels = [];
    updateChrome();
    toast(t('Saved API key cleared.'));
  } finally {
    providerSaving = false; updateProviderControls(); updateProviderFeedback();
  }
}
function safely(action) { return Promise.resolve().then(action).catch(error => { toast(error.message || String(error),true); }); }
function bindGlobal() {
  $('#close-file-dialog').addEventListener('click', () => $('#new-file-dialog').close());
  $('#new-file-import').addEventListener('change', event => safely(async () => { const file = event.target.files[0]; if (!file) return; if (file.size > 2*1024*1024) throw new Error(t('The editor supports text files up to 2 MiB.')); $('#new-file-content').value = await file.text(); if (!$('#new-file-path').value) $('#new-file-path').value = file.name; }));
  $('#new-file-form').addEventListener('submit', event => { event.preventDefault(); safely(async () => { const form = new FormData(event.target), path = String(form.get('path')).trim(); if (state.files.some(file => file.path === path)) throw new Error(t('This file already exists. Open it from the explorer to edit it.')); if (state.dirty) await saveFile(); await api(projectURL(`/files?path=${encoded(path)}`),{method:'PUT',body:{content:form.get('content')}}); await refreshProject(false); $('#new-file-dialog').close(); event.target.reset(); await openFile(path); toast(t('Project file created.')); }); });
  $('#new-project').addEventListener('click', () => $('#new-project-dialog').showModal());
  $('#close-project-dialog').addEventListener('click', () => $('#new-project-dialog').close());
  $('#new-project-form').addEventListener('submit', event => { event.preventDefault(); const button = $('button[type="submit"]',event.target); button.disabled = true; safely(async () => { const form = new FormData(event.target); const project = await api('/api/projects',{method:'POST',body:{name:form.get('name'),template:form.get('template')}}); state.projects.push(project.project || project); $('#new-project-dialog').close(); await selectProject((project.project || project).id); toast(t('Project created locally.')); }).finally(() => { button.disabled = false; }); });
  $('#project-select').addEventListener('change', event => safely(() => selectProject(event.target.value)));
  $('#refresh-project').addEventListener('click', () => safely(() => refreshProject()));
  $('#run-primary').addEventListener('click', () => safely(() => startRun('simulate')));
  $('#toggle-tree').addEventListener('click', () => document.body.classList.toggle('show-tree'));
  $('#toggle-ai').addEventListener('click', () => { if (innerWidth <= 1000) document.body.classList.toggle('show-ai'); else document.body.classList.toggle('hide-ai'); requestAnimationFrame(drawWaveform); });
  $('#toggle-logs').addEventListener('click', () => $('.log-panel').classList.toggle('collapsed'));
  $('#copy-log').addEventListener('click', () => safely(async () => { await navigator.clipboard.writeText(state.log || $('#execution-log').textContent); toast(t('Execution log copied.')); }));
  $('#run-cancel').addEventListener('click', () => safely(async () => { state.run = await api(projectURL(`/runs/${encoded(state.run.id)}/cancel`),{method:'POST',body:{}}); updateLog(); toast(t('Cancellation requested.')); }));
  $('#run-retry').addEventListener('click', () => safely(async () => { const run = await api(projectURL(`/runs/${encoded(state.run.id)}/retry`),{method:'POST',body:{}}); state.runs.unshift(run); await selectRun(run.id); toast(t('Retry started as a new execution record.')); }));
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-tab],[data-action],[data-file],[data-run-kind],[data-run-id],[data-metric],[data-remove-context],[data-restore],[data-remove-edge],[data-workflow-load]');
    if (!button || button.disabled) return;
    safely(async () => {
      if (button.dataset.tab) { event.preventDefault(); await setTab(button.dataset.tab); }
      else if (button.dataset.file) await openFile(button.dataset.file);
      else if (button.dataset.runKind) await startRun(button.dataset.runKind);
      else if (button.dataset.runId) await selectRun(button.dataset.runId);
      else if (button.dataset.removeContext) { state.context.delete(button.dataset.removeContext); renderTree(); updateContext(); }
      else if (button.dataset.metric) { const name = button.dataset.metric; const metric = metricEntries().find(([key]) => key === name)?.[1]; $('#metric-evidence').innerHTML = html`<pre class="evidence-details spaced">${esc(pretty({metric:name,...(typeof metric === 'object' ? metric : {value:metric}),...state.run.metric_provenance?.[name],run_id:state.run.id,definition:metric?.definition || state.run.metric_provenance?.[name]?.definition || 'See input snapshot and raw simulator log.',conditions:metric?.conditions || state.run.metric_provenance?.[name]?.conditions || state.run.metadata?.conditions || 'See execution record.'}))}</pre>`; $('.log-panel').classList.remove('collapsed'); }
      else if (button.dataset.restore) { await api(projectURL('/restore'),{method:'POST',body:{path:state.file,revision:button.dataset.restore}}); state.dirty = false; await openFile(state.file,false); await refreshProject(false); toast(t('Revision restored.')); }
      else if (button.dataset.removeEdge !== undefined) { captureWorkflow(); state.workflow.edges.splice(Number(button.dataset.removeEdge),1); render(); }
      else if (button.dataset.workflowLoad !== undefined) { const workflows = state.savedWorkflows || []; const entry = workflows[Number(button.dataset.workflowLoad)]; let workflow = entry.workflow || entry; if (typeof entry === 'string' || (!workflow.nodes && entry.path)) { const file = await api(projectURL(`/files?path=${encoded(typeof entry === 'string' ? entry : entry.path)}`)); workflow = JSON.parse(file.content); } state.workflow = validateWorkflowShape(workflow); state.workflowSelection = null; render(); toast(t('Saved workflow loaded.')); }
      else if (button.dataset.action) await handleAction(button.dataset.action);
    });
  });
  document.addEventListener('change', event => { if (event.target.dataset.context) { const path = event.target.dataset.context; event.target.checked ? state.context.add(path) : state.context.delete(path); updateContext(); } });
  document.addEventListener('keydown', event => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's' && state.tab === 'editor') { event.preventDefault(); safely(saveFile); } });
  window.addEventListener('beforeunload', event => { if (state.dirty) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('resize', () => requestAnimationFrame(drawWaveform));
  agentUI.bind();
  $('#chat-form').addEventListener('submit', event => { event.preventDefault(); safely(sendChat); });
  $('#chat-input').addEventListener('keydown', event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); safely(sendChat); } });
}
async function handleAction(action) {
  switch (action) {
    case 'reload-desktop': state.status = await api('/api/status'); renderDesktop(true); break;
    case 'new-project': $('#new-project-dialog').showModal(); break;
    case 'new-file': $('#new-file-dialog').showModal(); break;
    case 'export': location.href = projectURL('/export'); break;
    case 'save-file': await saveFile(); break;
    case 'history': state.history = asArray(await api(projectURL(`/history?path=${encoded(state.file)}`)),'revisions'); $('#file-history').innerHTML = historyHTML(); break;
    case 'reset-waveform': state.zoom = [0,1]; drawWaveform(); break;
    case 'export-waveform': if (state.waveform) download([state.waveform.variables.map(variable => `${variable.name}${variable.unit ? ` (${variable.unit})` : ''}`).join(','), ...state.waveform.points.map(point => point.join(','))].join('\n'),`waveform-${shortId(state.run?.id)}.csv`,'text/csv'); break;
    case 'save-workflow': captureWorkflow(); await api(projectURL('/workflows'),{method:'POST',body:{workflow:state.workflow}}); await refreshProject(false); toast(t('Workflow saved to project.')); break;
    case 'load-workflow': state.savedWorkflows = asArray(await api(projectURL('/workflows')),'workflows'); $('#saved-workflows').innerHTML = state.savedWorkflows.length ? html`<div class="card">${state.savedWorkflows.map((entry,index) => html`<div class="history-row"><span>${esc(entry.name || entry.workflow?.name || entry.path || entry)}</span><button class="secondary-button" data-workflow-load="${index}">Load</button></div>`).join('')}</div>` : html('<div class="callout">No saved workflows yet. Save the current graph to add one.</div>'); break;
    case 'export-workflow': download(pretty(captureWorkflow()),'analog-factory-workflow.json'); break;
    case 'import-workflow': $('#workflow-file').click(); break;
    case 'apply-workflow-json': state.workflow = validateWorkflowShape(JSON.parse($('#workflow-json').value)); state.workflowSelection = null; render(); break;
    case 'add-node': { captureWorkflow(); const type = $('#new-node-type').value; const label = nodeCatalog.find(item => item[0] === type)?.[1] || type; const id = `n${Date.now().toString(36)}`; state.workflow.nodes.push({id,type,position:{x:45,y:365},config:{label}}); state.workflowSelection = id; render(); break; }
    case 'add-edge': { captureWorkflow(); const source = $('#edge-from').value,target = $('#edge-to').value; if (source === target) throw new Error(t('Connect two distinct nodes.')); if (state.workflow.edges.some(edge => edge.source === source && edge.target === target)) throw new Error(t('This connection already exists.')); state.workflow.edges.push({source,target}); drawEdges(); syncWorkflowJSON(); break; }
    case 'save-node': { const node = state.workflow.nodes.find(item => item.id === state.workflowSelection); const config = JSON.parse($('#node-json').value); if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error(t('Node configuration must be a JSON object.')); node.config = {...config,label:$('#node-label').value}; drawWorkflow(); syncWorkflowJSON(); toast(t('Node configuration applied.')); break; }
    case 'delete-node': captureWorkflow(); state.workflow.nodes = state.workflow.nodes.filter(node => node.id !== state.workflowSelection); state.workflow.edges = state.workflow.edges.filter(edge => edge.source !== state.workflowSelection && edge.target !== state.workflowSelection); state.workflowSelection = null; render(); break;
    case 'run-workflow': captureWorkflow(); if (state.dirty) await saveFile(); state.job = await api(projectURL('/workflows/run'),{method:'POST',body:{workflow:state.workflow}}); $('#workflow-job').innerHTML = jobHTML(state.job); toast(t('Bounded workflow started.')); break;
    case 'cancel-workflow': state.job = await api(projectURL(`/jobs/${encoded(state.job.id)}/cancel`),{method:'POST',body:{}}); $('#workflow-job').innerHTML = jobHTML(state.job); break;
    case 'cancel-evaluation': state.evaluation = await api(projectURL(`/jobs/${encoded(state.evaluation.id)}/cancel`),{method:'POST',body:{}}); $('#evaluation-job').innerHTML = jobHTML(state.evaluation,true); break;
    case 'detect-tools': state.status = await api('/api/status'); updateChrome(); render(); toast(t('Local toolchain checked.')); break;
    case 'test-provider': await saveSettings({test:true}); break;
    case 'clear-provider-key': await clearProviderKey(); break;
    case 'export-settings': download(pretty(state.settings),'analog-factory-safe-config.json'); break;
    case 'import-settings': $('#settings-file').click(); break;
  }
}
async function sendChat() { await agentUI.send(); }
function renderChat() { agentUI.render(); }
function setupWaveform() {
  const canvas = $('#waveform'); if (!canvas) return;
  const waveform = state.waveform;
  $('#trace-controls').innerHTML = waveform?.variables?.slice(1).map((variable,index) => html`<label style="color:${traceColors[index%traceColors.length]}"><input type="checkbox" data-trace="${index+1}" ${state.traces.has(index+1) ? 'checked' : ''}>${esc(variable.name)}${variable.unit ? ` [${esc(variable.unit)}]` : ''}</label>`).join('') || '';
  $$('[data-trace]').forEach(input => input.addEventListener('change', () => { input.checked ? state.traces.add(Number(input.dataset.trace)) : state.traces.delete(Number(input.dataset.trace)); drawWaveform(); }));
  let drag = null;
  canvas.addEventListener('wheel', event => {
    if (!state.waveform?.points?.length) return;
    event.preventDefault(); const rect = canvas.getBoundingClientRect(), position = Math.max(0,Math.min(1,(event.clientX-rect.left-58)/(rect.width-81)));
    const oldWidth = state.zoom[1]-state.zoom[0], width = Math.min(1,Math.max(0.001,oldWidth * (event.deltaY > 0 ? 1.25 : 0.8)));
    const center = state.zoom[0]+position*oldWidth, start = Math.max(0,Math.min(1-width,center-position*width)); state.zoom = [start,start+width]; drawWaveform();
  },{passive:false});
  canvas.addEventListener('pointerdown', event => { drag = {x:event.clientX,zoom:[...state.zoom]}; canvas.setPointerCapture(event.pointerId); });
  canvas.addEventListener('pointermove', event => {
    const rect = canvas.getBoundingClientRect();
    if (drag) { const width = drag.zoom[1]-drag.zoom[0], difference = (event.clientX-drag.x)/(rect.width-81)*width, start = Math.max(0,Math.min(1-width,drag.zoom[0]-difference)); state.zoom = [start,start+width]; }
    state.cursor = {x:event.clientX-rect.left,y:event.clientY-rect.top}; drawWaveform();
  });
  canvas.addEventListener('pointerup', () => { drag = null; });
  canvas.addEventListener('pointercancel', () => { drag = null; });
  canvas.addEventListener('pointerleave', () => { if (!drag) { state.cursor = null; drawWaveform(); } });
  requestAnimationFrame(drawWaveform);
}
function axisNumber(value) {
  if (!Number.isFinite(value)) return '—';
  const absolute = Math.abs(value);
  if (absolute >= 1e9) return `${number(value/1e9)}G`;
  if (absolute >= 1e6) return `${number(value/1e6)}M`;
  if (absolute >= 1e3) return `${number(value/1e3)}k`;
  if (absolute > 0 && absolute < 1e-6) return `${number(value/1e-9)}n`;
  if (absolute > 0 && absolute < 1e-3) return `${number(value/1e-6)}µ`;
  if (absolute > 0 && absolute < 1) return `${number(value/1e-3)}m`;
  return number(value);
}
function drawWaveform() {
  const canvas = $('#waveform'); if (!canvas) return;
  const width = canvas.clientWidth, height = canvas.clientHeight; if (!width || !height) return;
  const ratio = devicePixelRatio || 1; canvas.width = Math.round(width*ratio); canvas.height = Math.round(height*ratio);
  const traceColors = Array.from({length:6}, (_, index) => themeColor(`--trace-${index + 1}`));
  const context = canvas.getContext('2d'); context.scale(ratio,ratio); context.clearRect(0,0,width,height);
  const waveform = state.waveform; if (!waveform?.points?.length || waveform.points.length < 2) return;
  const points = waveform.points.filter(point => Number.isFinite(point[0])); if (points.length < 2) return;
  const margin = {left:58,right:23,top:27,bottom:40}, plotW=width-margin.left-margin.right,plotH=height-margin.top-margin.bottom;
  const fullMin=points[0][0],fullMax=points[points.length-1][0], range=fullMax-fullMin || 1;
  const minX=fullMin+range*state.zoom[0],maxX=fullMin+range*state.zoom[1], visible=points.filter(point => point[0]>=minX && point[0]<=maxX);
  let minY=Infinity,maxY=-Infinity;
  for (const point of visible) for (const index of state.traces) if (Number.isFinite(point[index])) { minY=Math.min(minY,point[index]); maxY=Math.max(maxY,point[index]); }
  if (!Number.isFinite(minY)) { minY=-1; maxY=1; }
  const pad=(maxY-minY)*0.1 || Math.abs(maxY)*0.1 || 1; minY-=pad; maxY+=pad;
  const x = value => margin.left+(value-minX)/(maxX-minX)*plotW, y = value => margin.top+(maxY-value)/(maxY-minY)*plotH;
  context.font='9px ui-monospace, monospace'; context.lineWidth=1;
  for (let index=0;index<=5;index++) { const gx=margin.left+plotW*index/5,gy=margin.top+plotH*index/5; context.strokeStyle=themeColor('--chart-grid'); context.beginPath(); context.moveTo(gx,margin.top); context.lineTo(gx,margin.top+plotH); context.stroke(); context.beginPath(); context.moveTo(margin.left,gy); context.lineTo(margin.left+plotW,gy); context.stroke(); context.fillStyle=themeColor('--chart-text'); context.textAlign='center'; context.fillText(axisNumber(minX+(maxX-minX)*index/5),gx,height-21); context.textAlign='right'; context.fillText(axisNumber(maxY-(maxY-minY)*index/5),margin.left-9,gy+3); }
  context.fillStyle=themeColor('--chart-axis'); context.textAlign='left'; context.fillText(waveform.variables?.[0]?.unit || waveform.variables?.[0]?.name || 'x',width-margin.right-13,height-7);
  const units=[...new Set([...state.traces].map(index => waveform.variables?.[index]?.unit).filter(Boolean))]; context.fillText(units.join(' / ') || t('signal'),margin.left,15);
  context.save(); context.beginPath(); context.rect(margin.left,margin.top,plotW,plotH); context.clip();
  const stride=Math.max(1,Math.floor(points.length/(plotW*4)));
  for (const index of state.traces) { context.strokeStyle=traceColors[(index-1)%traceColors.length]; context.lineWidth=1.7; context.beginPath(); let started=false; for (let n=0;n<points.length;n+=stride) { const point=points[n]; if (!Number.isFinite(point[index])) { started=false; continue; } if (!started) { context.moveTo(x(point[0]),y(point[index])); started=true; } else context.lineTo(x(point[0]),y(point[index])); } context.stroke(); }
  if (state.cursor && state.cursor.x>=margin.left && state.cursor.x<=width-margin.right) {
    const target=minX+(state.cursor.x-margin.left)/plotW*(maxX-minX);
    let low=0,high=points.length-1; while (low<high) { const middle=Math.floor((low+high)/2); if (points[middle][0]<target) low=middle+1; else high=middle; }
    const point=points[low],cx=x(point[0]); context.strokeStyle=themeColor('--chart-crosshair'); context.setLineDash([3,4]); context.beginPath(); context.moveTo(cx,margin.top); context.lineTo(cx,margin.top+plotH); context.stroke(); context.setLineDash([]);
    for (const index of state.traces) { if (!Number.isFinite(point[index])) continue; context.fillStyle=traceColors[(index-1)%traceColors.length]; context.beginPath(); context.arc(cx,y(point[index]),3,0,Math.PI*2); context.fill(); }
    $('#waveform-cursor').textContent=`${axisNumber(point[0])}${waveform.variables?.[0]?.unit || ''} · ${[...state.traces].map(index => `${waveform.variables?.[index]?.name}: ${axisNumber(point[index])}${waveform.variables?.[index]?.unit || ''}`).join(' · ')}`;
  } else if ($('#waveform-cursor')) $('#waveform-cursor').textContent = t('{count} visible samples', {count:number(visible.length)});
  context.restore();
}

initialize().catch(error => { $('#main-content').innerHTML = html`<div class="empty-state"><span class="empty-symbol">!</span><h1>The workspace could not open.</h1><p>${esc(error.message)}</p><button class="secondary-button" id="retry-connection">Retry connection</button></div>`; toast(error.message,true); $('#retry-connection')?.addEventListener('click', () => location.reload()); }).finally(() => { document.body.inert = false; document.body.removeAttribute('aria-busy'); });
