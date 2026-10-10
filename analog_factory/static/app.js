'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
const encoded = value => encodeURIComponent(value);
const {t, html} = window.AFI18n;
const preferences = window.AFPreferences;
let preferencesSaving = false;
let providerSaving = false;
let waveformResizeObserver = null;
const treeFolders = new Map();
const draftVersions = new Map();
let browserDraftRevision = 0, workflowDraftRevision = 0;
let navigationRequest = 0;
let designNavigationRequest = 0;
let designNavigationPending = false;
let newProjectStatus = null, newProjectCheck = 0, newProjectChecking = false, newProjectSubmitting = false;
const state = {
  status: null, settings: {}, projects: [], project: null, projectConfiguration: {}, files: [], runs: [], tab: 'overview',
  file: null, content: '', savedContent: '', dirty: false, context: new Set(), run: null, log: '',
  waveform: null, waveformSource: null, waveformOptions: {}, traces: new Set(), zoom: [0, 1], cursor: null, workflow: null, workflowSelection: null,
  job: null, evaluation: null, jobs: [], chatting: false, chat: [], connected: false, polling: false,
  compare: ['', ''], assistantUsage: null, history: null, fileRequest: 0, runRequest: 0, loadingProject: false,
  providerTest: null, providerModels: [], projectRequest: 0, fileVersion:null, designProgress:[]
};
const traceColors = Array.from({length:6}, (_, index) => `var(--trace-${index + 1})`);
const traceDashes = [[], [8,4], [2,3], [10,3,2,3], [5,3], [12,5]];
const waveformMargin = {left:84, right:34, top:27, bottom:40};
const themeColor = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const terminalStatuses = new Set(['succeeded', 'completed', 'success', 'passed', 'failed', 'cancelled', 'canceled', 'timeout', 'timed_out', 'error', 'unavailable']);
const editorLaunches = new Set();
const isActive = run => run && !terminalStatuses.has(run.status) && ['queued', 'running', 'starting', 'pending'].includes(run.status);
const projectURL = suffix => `/api/projects/${encoded(state.project.id)}${suffix || ''}`;
const statusURL = (id = state.project?.id) => `/api/status${id ? `?project_id=${encoded(id)}` : ''}`;
const formatDate = value => value ? new Date(value).toLocaleString(window.AFI18n.language(), {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '—';
const shortId = value => String(value || '').slice(0, 8);
const pretty = value => JSON.stringify(value, null, 2);
const number = value => typeof value === 'number' && Number.isFinite(value) ? (Math.abs(value) >= 100000 || (value !== 0 && Math.abs(value) < 0.001) ? value.toExponential(3) : Number(value.toPrecision(6)).toLocaleString(window.AFI18n.language(), {maximumFractionDigits:6})) : String(value ?? '—');
const runTime = run => run.started_at || run.created_at || run.created;
const runName = run => `${t(run.kind || 'run')} · ${shortId(run.id)} · ${t(run.status || 'unknown')}`;
const agentUI = window.AFAgents.create({state, api, esc, t, html, formatDate, number, onSettled:refreshAgentProject, onContextChange:()=>{renderTree();updateContext();}});
const lifecycleUI = window.AFProjectControls.create({state,esc,t,toast,refreshProject,selectProject,openFile,onProjectsChanged:()=>updateChrome()});
lifecycleUI.mount($('#project-lifecycle-controls'));
const nativeTerminalUI = window.AFNativeTerminal.create({state,toast,t});
nativeTerminalUI.mount($('#native-terminal-controls'));
const nativeCanvasUI = window.AFNativeCanvas.create({state,toast,t,
  onUpdate:async data => {
    const projectId = data.project_id || data.state?.project_id;
    if (state.project?.id === projectId) await refreshAgentProject(projectId);
  }});
nativeCanvasUI.mount($('#native-canvas-controls'));
const drcNavigationUI = window.AFDrcNavigation.create({state,toast,t,onRunSelected:selectRun});
const editorRecoveryUI = window.AFEditorRecovery.create({state,t,notify:toast,
  readLatest:async captured => {
    let version;
    const data = await api(`/api/projects/${encoded(captured.project_id)}/files?path=${encoded(captured.path)}`,
      {onVersion:value=>{version=value;}});
    if (new TextEncoder().encode(captured.content).length <= 64000 && new TextEncoder().encode(data.content).length <= 64000) {
      const digest = await crypto.subtle.digest('SHA-256',new TextEncoder().encode(data.content));
      const sha256 = [...new Uint8Array(digest)].map(value=>value.toString(16).padStart(2,'0')).join('');
      try {
        const preview = await window.AFOperations.invoke('file.change.preview',
          {project_id:captured.project_id,path:captured.path,content:captured.content,expected_sha256:sha256});
        return {content:preview.data.current_content,version:preview.data.project_version,preview:preview.data};
      } catch (error) {
        if (!error.message.includes('Complete source proposal exceeds the 256 KiB limit')) throw error;
        // Full three-way text remains available within the editor's 2 MiB bound.
      }
    }
    return {content:data.content,version:data.source_version ?? version};
  },
  applyDraft:({content,base,version})=>{state.savedContent=base;state.fileVersion=version;setEditorDraft(content,false);}
});
const editorRecoveryHost = document.createElement('div');
editorRecoveryHost.id = 'editor-recovery-controls';
editorRecoveryUI.mount(editorRecoveryHost);
const simulationBatchUI = window.AFSimulationBatches.create({state,toast,t,
  onUpdate: async job => {
    if (!state.project || job.project_id && job.project_id !== state.project.id) return;
    const index = state.jobs.findIndex(item => item.id === job.id);
    if (index < 0) state.jobs.unshift(job); else state.jobs[index] = job;
  }, onRunSelected:id=>selectRun(id,true)});
const simulationBatchHost = document.createElement('div');
simulationBatchHost.id = 'simulation-batch-controls';
simulationBatchUI.mount(simulationBatchHost);
const characterizationUI = window.AFCharacterization.create({state,toast,t,
  onUpdate: async job => {
    if (!job || !state.project || job.project_id && job.project_id !== state.project.id) return;
    const index = state.jobs.findIndex(item => item.id === job.id);
    if (index < 0) state.jobs.unshift(job); else state.jobs[index] = job;
  }, onRunSelected:id=>selectRun(id,true)});
const characterizationHost = document.createElement('div');
characterizationHost.id = 'characterization-controls';
characterizationUI.mount(characterizationHost);
const trainingUI = window.AFTrainingControls.create({state,toast,t,
  onUpdate: async job => {
    if (!job || !state.project) return;
    const index = state.jobs.findIndex(item => item.id === job.id);
    if (index < 0) state.jobs.unshift(job); else state.jobs[index] = job;
  }});
const trainingHost = document.createElement('div');
trainingHost.id = 'training-controls';
trainingUI.mount(trainingHost);
const agentCLIUI = window.AFAgentCLIControls.create({state,toast,t,
  onUpdate:async ({project_id})=>{
    if(state.project?.id===project_id){await refreshProject(false);await refreshStatus();await agentUI.refreshCapabilities();}
  }});
const agentCLIHost = document.createElement('div');
agentCLIHost.id = 'agent-cli-controls';
agentCLIUI.mount(agentCLIHost);
const tool = name => state.status?.toolchain?.tools?.[name] || {available:false, diagnostic:t('Tool detection has not completed.')};
const pdk = () => state.status?.toolchain?.pdk || {available:false, diagnostic:t('No prepared PDK detected.')};
function badge(status, label) {
  const s = String(status || 'unknown');
  const type = ['succeeded', 'completed', 'success', 'passed', 'available', 'implemented'].includes(s) ? 'ok' : ['failed','error','unavailable','timed_out','timeout'].includes(s) ? 'error' : ['running','queued','starting'].includes(s) ? 'running' : 'warning';
  const paths = {ok:'M3 8l3 3 7-7',error:'M4 4l8 8M12 4l-8 8',running:'M8 3v5l3 2',warning:'M8 3v6M8 12h.01'};
  return html`<span class="badge ${type}"><svg class="badge-icon" width="12" height="12" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="7"/><path d="${paths[type]}"/></svg>${esc(label || t(s).replaceAll('_', ' '))}</span>`;
}
function reasonFor(kind) {
  if (!state.project) return t('Create or select a project first.');
  const availability = state.status?.project_id === state.project.id ? state.status.operation_availability?.[kind] : null;
  if (!availability) return t('Tool detection has not completed.');
  return availability.available ? '' : (availability.reasons || []).map(reason => t(reason)).join(' ') || t('Unavailable');
}
async function refreshStatus() {
  const id = state.project?.id || null, value = await api(statusURL(id));
  if ((state.project?.id || null) === id) { state.status = value; state.connected = true; }
  return value;
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
    const headers = {...(options.body === undefined ? {} : {'Content-Type':'application/json'}), ...(options.expectedVersion ? {'If-Match':options.expectedVersion} : {})};
    const response = await (window.AFOperations?.request || fetch)(path, {method:options.method || 'GET', headers, body:options.body === undefined ? undefined : JSON.stringify(options.body), signal:controller.signal, cache:'no-store'});
    if (response.ok) options.onVersion?.(response.headers.get('ETag'));
    const type = response.headers.get('content-type') || '';
    const data = type.includes('json') ? await response.json() : await response.text();
    if (!response.ok) {
      const failure = new Error(typeof data === 'object' ? data.error?.message || data.error || data.message || t('Request failed ({status})', {status:response.status}) : data || t('Request failed ({status})', {status:response.status}));
      failure.status=response.status;failure.code=data?.error?.code;throw failure;
    }
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
const stageLabels = {ready:'Ready to inspect',missing:'Source missing',not_run:'Not run',running:'Running',
  stale:'Results stale',unknown:'Evidence unknown',failed:'Failed',passed:'Verified pass',completed:'Tool completed',
  publication_required:'Publish output',published:'Output published',blocked:'Prerequisite missing'};
function designStages() {
  const tools = {schematic:'xschem',pre:'simulate',layout:'magic',drc:'drc',extract:'extract',lvs:'lvs',post:'simulate'};
  return state.designProgress.map(stage => {
    const reason = tools[stage.id] ? reasonFor(tools[stage.id]) : '';
    return reason && ['not_run','missing','ready'].includes(stage.status) ? {...stage,status:'blocked',reason} : stage;
  });
}
function designNextStep(stages) {
  const candidates = stages.filter(stage => !['specification','layout','archive'].includes(stage.id) &&
    !['completed','passed','published'].includes(stage.status) && (state.project.template !== 'amplifier' || stage.id !== 'schematic'));
  const next = candidates.find(stage => stage.status !== 'blocked') || candidates[0];
  const latestCharacterization = state.jobs.find(job => job.kind === 'ota-characterization');
  return html`<div class="card design-next"><div><strong>${t('Next step')}: ${next ? esc(t(next.title)) : t('Review results and export evidence')}</strong><p>${next ? esc(t(next.reason)) : t('Inspect the recorded conditions and target checks before exporting.')}</p></div><div class="button-row">${next ? html`<button type="button" class="secondary-button" data-continue-design="${esc(next.id)}" data-design-intent="${esc(designIntent(next.id))}">${designAction(next.id).type === 'settings' ? t('Configure requirements') : t('Continue design')}</button>` : html`<button class="secondary-button" data-action="export">Export project</button>`}${latestCharacterization ? html`<button type="button" class="secondary-button" data-characterization-job="${esc(latestCharacterization.id)}">Open characterization report</button>` : ''}</div></div>`;
}
const designAction = stage => window.AFDesignNavigation.resolve(state,stage);
const designIntent = stage => window.AFDesignNavigation.fingerprint(designAction(stage));
async function continueDesign({stage}) {
  if (designNavigationPending) throw new Error(t('A design action is already in progress.'));
  designNavigationPending = true;
  try { return await enterDesignStage(stage); }
  finally { designNavigationPending = false; }
}
async function enterDesignStage(stage) {
  if (!state.project || state.loadingProject) throw new Error(t('Create or select a project first.'));
  const action = designAction(stage), intent = window.AFDesignNavigation.fingerprint(action);
  const projectId = state.project.id, projectRequest = state.projectRequest, request = ++designNavigationRequest;
  const conflict = message => { const error = new Error(t(message)); error.code='VERSION_CONFLICT'; throw error; };
  const checkProject = () => {
    if (state.project?.id !== projectId || state.projectRequest !== projectRequest || state.loadingProject || request !== designNavigationRequest)
      conflict('Selected project changed before the design action completed.');
  };
  if ($('#new-file-dialog').open || action.type==='create_file' && ($('#new-file-path').value || $('#new-file-content').value))
    throw new Error(t('Finish the existing file draft before continuing design.'));
  if (state.dirty) await saveFile();
  checkProject();
  if (state.dirty) throw new Error(t('Save or review the editor draft before continuing design.'));
  const draft = browserDraftRevision, fileRequest = state.fileRequest, navigation = navigationRequest;
  await Promise.all([refreshProject(false),refreshStatus()]);
  checkProject();
  const checkIntent = () => {
    checkProject();
    if (intent !== designIntent(stage) || draft !== browserDraftRevision || fileRequest !== state.fileRequest || navigation !== navigationRequest || state.dirty)
      conflict('Design requirements changed. Review the next step and try again.');
  };
  checkIntent();
  if (action.type === 'open_file') {
    await openFile(action.path,true,() => {
      checkProject();
      if (draft !== browserDraftRevision || state.dirty || navigation !== navigationRequest || state.fileRequest !== fileRequest+1)
        conflict('Design requirements changed. Review the next step and try again.');
    });
    if (state.file!==action.path || state.tab!=='editor') conflict('Design requirements changed. Review the next step and try again.');
  }
  else if (action.type === 'create_file') {
    // Reuse the existing versioned new-file dialog; task entry does not write a blank source.
    await window.AFOperations.dispatch('ui.workspace.dialog',{name:'new_file',open:true});
    checkIntent();
    $('#new-file-path').value=action.path;
    $('#new-file-content').focus();
  } else if (action.type === 'settings') await setTab('settings',() => {
    checkProject();
    if (draft !== browserDraftRevision || state.dirty || fileRequest !== state.fileRequest || navigationRequest !== navigation+1)
      conflict('Design requirements changed. Review the next step and try again.');
  });
  else if (action.type === 'export') await handleAction('export');
  else {
    let runId=action.run_id;
    if (action.type === 'run') {
      const result=await window.AFOperations.invoke('run.start',{project_id:projectId,kind:action.kind,options:action.options,
        timeout:action.tab==='desktop'?3600:state.project.template==='ota5'?180:60},
        {expected_version:window.AFOperations.version(`/api/projects/${encoded(projectId)}`),idempotency_key:`design-${crypto.randomUUID()}`});
      checkProject();runId=result.data.id;
      // Do not redraw the editor while a user can still begin typing during launch.
      if (draft !== browserDraftRevision || state.dirty) conflict('Design requirements changed. Review the next step and try again.');
      await refreshProject(false);checkProject();
    }
    checkProject();
    if (draft !== browserDraftRevision || state.dirty || navigation !== navigationRequest || fileRequest !== state.fileRequest)
      conflict('Design requirements changed. Review the next step and try again.');
    const selectedRequest = state.runRequest+1;
    await selectRun(runId,false,() => {
      checkProject();
      if (draft !== browserDraftRevision || state.dirty || navigation !== navigationRequest || fileRequest !== state.fileRequest || state.runRequest !== selectedRequest)
        conflict('Design requirements changed. Review the next step and try again.');
    });checkProject();
    if (draft !== browserDraftRevision || state.dirty || navigation !== navigationRequest || fileRequest !== state.fileRequest || state.runRequest !== selectedRequest || state.run?.id !== runId)
      conflict('Design requirements changed. Review the next step and try again.');
    await setTab(action.tab);checkProject();
    if (action.type==='publish') $('[data-action="reference-publish"]')?.focus();
  }
  checkProject();
  return {stage,action,state:window.AFOperations.snapshot()};
}
function renderProjectReadiness() {
  const host = $('#project-readiness');
  if (!host) return;
  const assessment = window.AFProjectReadiness.assess($('#new-project-form [name=template]').value,newProjectStatus);
  host.innerHTML = html`<h3>Environment check</h3><p role="status">${newProjectChecking ? t('Checking workspace requirements…') : esc(t(assessment.summary))}</p><ul class="readiness-list">${assessment.checks.map(check => html`<li data-readiness="${esc(check.id)}" data-readiness-status="${esc(check.status)}"><div><strong>${esc(t(check.label))}</strong> <span class="muted">${esc(t({ready:'Ready',missing:'Missing',unknown:'unknown',action_required:'Action required'}[check.status]))}</span>${check.required ? html` <small>Required to create</small>` : ''}</div>${check.status !== 'ready' ? html`<small>${esc(t(check.reason))}</small>` : ''}</li>`).join('')}</ul>`;
  $('#new-project-form [type=submit]').disabled = newProjectSubmitting || newProjectChecking || !assessment.canCreate;
  $('#project-check-refresh').disabled = newProjectSubmitting || newProjectChecking;
  $('#project-use-amplifier').hidden = assessment.template === 'amplifier';
  $('#new-project-form .callout').hidden = assessment.template !== 'amplifier';
}
async function refreshProjectReadiness() {
  const ticket = ++newProjectCheck;
  newProjectChecking = true; newProjectStatus = null; renderProjectReadiness();
  try {
    const status = await api('/api/status');
    if (ticket === newProjectCheck) newProjectStatus = status;
  } catch (error) {
    if (ticket === newProjectCheck) newProjectStatus = {error:error.message};
  } finally {
    if (ticket === newProjectCheck) { newProjectChecking = false; renderProjectReadiness(); }
  }
}
async function openProjectDialog() {
  const dialog = $('#new-project-dialog');
  if (!dialog.open) dialog.showModal();
  await refreshProjectReadiness();
}
function closeProjectDialog() {
  ++newProjectCheck; newProjectChecking = false; $('#new-project-dialog').close();
}
async function submitNewProject(form) {
  if (newProjectSubmitting) return;
  newProjectSubmitting = true; renderProjectReadiness();
  const name = form.elements.name.value, template = form.elements.template.value;
  const selectedId = state.project?.id;
  try {
    const readiness = refreshProjectReadiness(), ticket = newProjectCheck;
    await readiness;
    const ownsDialog = () => ticket === newProjectCheck && $('#new-project-dialog').open &&
      form.elements.name.value === name && form.elements.template.value === template;
    if (!ownsDialog())
      throw new Error(t('Project details changed. Review them before creating.'));
    const assessment = window.AFProjectReadiness.assess(template,newProjectStatus);
    if (!assessment.canCreate) throw new Error(t(assessment.summary));
    const project = await api('/api/projects',{method:'POST',body:{name,template}});
    state.projects.push(project.project || project);
    if (ownsDialog() && state.project?.id === selectedId) {
      await selectProject((project.project || project).id);
      if (ownsDialog()) closeProjectDialog();
    } else updateChrome();
    toast(t('Project created locally.'));
  } finally { newProjectSubmitting = false; renderProjectReadiness(); }
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
  registerWorkspaceOperations();
  window.AFOperations?.start(refreshOperationChanges);
  if (!state.connected) toast(t('Cannot reach the local workspace server. Check that Analog-factory is running.'), true);
  setInterval(poll, 2500);
}
function asArray(data, key) { return Array.isArray(data) ? data : data?.[key] || []; }
function updateChrome() {
  updatePreferenceControls();
  lifecycleUI.render();
  nativeTerminalUI.render();
  simulationBatchUI.render();
  characterizationUI.render();
  trainingUI.render();
  agentCLIUI.render();
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
  if ($('#new-project-dialog').open) renderProjectReadiness();
}
function renderTree() {
  const tree = $('#file-tree'), active = document.activeElement;
  const focus = tree.contains(active) ? {file:active.dataset.file, context:active.dataset.context, folder:active.closest('[data-folder]')?.dataset.folder} : null;
  const previousProject = tree.dataset.project;
  if (previousProject) treeFolders.set(previousProject, new Map($$('details[data-folder]',tree).map(folder => [folder.dataset.folder,folder.open])));
  tree.dataset.project = state.project?.id || '';
  if (!state.project) { tree.innerHTML = html('<p class="empty-small">Your design files will appear here.</p>'); return; }
  const folders = new Map(), expanded = treeFolders.get(state.project.id) || new Map();
  for (const file of state.files) { const split = file.path.lastIndexOf('/'); const folder = split < 0 ? '' : file.path.slice(0, split); if (!folders.has(folder)) folders.set(folder, []); folders.get(folder).push(file); }
  tree.innerHTML = [...folders.entries()].map(([folder, files]) => html`<details class="tree-folder" data-folder="${esc(folder)}" ${expanded.get(folder) === false ? '' : 'open'}><summary>${esc(folder || t('Project root'))}</summary>${files.map(file => html`<div class="file-row ${state.file === file.path ? 'selected' : ''}"><button data-file="${esc(file.path)}" ${state.file === file.path ? 'aria-current="true"' : ''} title="${esc(file.path)}"><span class="file-symbol" aria-hidden="true">${/\.(cir|spice|sp)$/.test(file.path) ? '∿' : /\.(sch|mag)$/.test(file.path) ? '◇' : '≡'}</span>${esc(file.path.split('/').pop())}</button><input type="checkbox" data-context="${esc(file.path)}" ${state.context.has(file.path) ? 'checked' : ''} aria-label="${esc(t('Include {path} in AI context', {path:file.path}))}"></div>`).join('')}</details>`).join('') || html('<p class="empty-small">This project has no source files.</p>');
  if (focus && previousProject === state.project.id) {
    const restored = focus.file !== undefined ? $$('[data-file]',tree).find(item => item.dataset.file === focus.file) : focus.context !== undefined ? $$('[data-context]',tree).find(item => item.dataset.context === focus.context) : $$('[data-folder]',tree).find(item => item.dataset.folder === focus.folder)?.querySelector('summary');
    restored?.focus({preventScroll:true});
  }
}

function updateContext() {
  $('#context-count').textContent = t('{count} files', {count:number(state.context.size)});
  $('#context-files').innerHTML = state.context.size ? [...state.context].map(path => html`<span class="context-chip" title="${esc(path)}">${esc(path.split('/').pop())}<button data-remove-context="${esc(path)}" aria-label="${esc(t('Remove {path} from context', {path}))}">×</button></span>`).join('') : html('<small>No files selected. Use the explorer checkboxes.</small>');
}
async function selectProject(id) {
  if (state.dirty) await saveFile();
  draftVersions.clear();
  const request = ++state.projectRequest;
  ++state.fileRequest; ++state.runRequest;
  agentUI.selectProject(null);
  if (!id) { state.loadingProject = false; state.project = null; state.projectConfiguration = {}; state.files = []; state.runs = []; state.designProgress = []; state.run = null; state.file = null; state.context.clear(); state.log = ''; state.chat = []; await refreshStatus(); if (request !== state.projectRequest) return; renderTree(); updateChrome(); renderChat(); updateLog(); render(); return; }
  state.loadingProject = true;
  try {
    const data = await api(`/api/projects/${encoded(id)}`);
    if (request !== state.projectRequest) return;
    state.project = data.project; state.projectConfiguration = data.configuration || {}; state.files = data.files || []; state.runs = data.runs || [];
    state.designProgress = data.design_progress || [];
    state.file = null; state.content = ''; state.savedContent = ''; state.dirty = false; state.context.clear(); state.run = null; state.waveform = null; state.waveformSource = null; state.waveformOptions = {}; state.job = null; state.evaluation = null; state.chat = []; state.log = ''; state.history = null;
    agentUI.selectProject(id);
    state.workflow = defaultWorkflow(); state.workflowSelection = null;
    const [jobsData, status] = await Promise.all([api(projectURL('/jobs')), api(statusURL(id))]);
    if (request !== state.projectRequest) return;
    state.jobs = asArray(jobsData, 'jobs'); state.status = status; state.connected = true;
    state.job = state.jobs.find(job => ['tuning','workflow'].includes(job.kind)) || null;
    state.evaluation = state.jobs.find(job => job.kind === 'evaluation') || null;
    try { localStorage.setItem('af-project', id); } catch (_) { /* Optional browser cache. */ } renderTree(); updateChrome(); renderChat(); render(); updateLog();
    if (state.runs.length) await selectRun(state.runs[0].id, false);
  } finally { if (request === state.projectRequest) state.loadingProject = false; }
}
function render() {
  waveformResizeObserver?.disconnect();
  nativeCanvasUI.render();
  if (!draftVersions.has(state.tab)) draftVersions.set(state.tab, window.AFOperations?.version(state.tab === 'settings' ? '/api/settings' : state.project ? projectURL() : '/api/settings'));
  const simulationForm = state.tab === 'simulation' && $('#simulation-form') ? Object.fromEntries(new FormData($('#simulation-form'))) : null;
  $$('.tabs button').forEach(button => { const active = button.dataset.tab === state.tab; button.classList.toggle('active', active); if (active) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current'); });
  const drawing = state.tab === 'desktop';
  $('#main-content').hidden = drawing;
  $('#desktop-panel').hidden = !drawing;
  if (drawing) { renderDesktop(); return; }
  const views = {overview:overviewView, editor:editorView, simulation:simulationView, verification:verificationView, workflows:workflowView, evaluation:evaluationView, settings:settingsView};
  $('#main-content').innerHTML = !state.project && state.tab !== 'settings' ? noProject() : views[state.tab]();
  bindView();
  if (state.tab === 'editor' && state.project) {
    $('#main-content').append(editorRecoveryHost);editorRecoveryUI.render();
  }
  if (state.tab === 'simulation' && $('#simulation-batch-slot')) {
    $('#simulation-batch-slot').append(simulationBatchHost);
    $('#simulation-batch-slot').append(characterizationHost);
    simulationBatchUI.render();
    characterizationUI.render();
  }
  if (state.tab === 'evaluation' && $('#training-controls-slot')) {
    $('#training-controls-slot').append(trainingHost);
    trainingUI.render();
  }
  if (state.tab === 'settings' && $('#agent-cli-controls-slot')) {
    $('#agent-cli-controls-slot').append(agentCLIHost);
    agentCLIUI.render();
  }
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
async function setTab(tab, beforeApply = null) {
  const request = ++navigationRequest;
  if (state.tab === 'editor' && state.dirty && tab !== 'editor') await saveFile();
  if (request !== navigationRequest) return;
  if (tab === 'settings' && state.tab !== 'settings' && state.project) {
    const projectId = state.project.id;
    const detail = await api(projectURL());
    beforeApply?.();
    if (request !== navigationRequest || state.project?.id !== projectId) return;
    state.project = detail.project; state.projectConfiguration = detail.configuration || {};
    draftVersions.delete(`project_provider:${projectId}`);
    draftVersions.delete(`project_environment:${projectId}`);
  }
  beforeApply?.();
  if (tab !== state.tab && tab !== 'workflows') draftVersions.delete(tab);
  state.tab = tab; history.replaceState(null, '', `#${tab}`); render(); $('#main-content').scrollTop = 0;
  if (tab === 'editor' && !state.file && state.files.length) await openFile(state.files.find(file => /pre\.cir$/.test(file.path))?.path || state.files[0].path, false);
}
function overviewView() {
  const completed = state.runs.filter(run => ['succeeded','completed','success','passed'].includes(run.status));
  const tools = ['ngspice','xschem','magic','netgen'].filter(name => tool(name).available).length;
  const latest = state.runs[0];
  const stages = designStages();
  return heading(t('PROJECT WORKSPACE'), esc(state.project.name), t('From the first netlist to the evidence behind every result.'), html`<button data-action="export" class="secondary-button">↗ Export project</button>`) +
    html`<div class="hero-card card"><p class="eyebrow">DESIGN. SIMULATE. UNDERSTAND.</p><h2>Your circuit, with a clear trail.</h2><p>${state.project.template === 'ota5' ? t('A five-transistor differential OTA is ready in Xschem and Magic. Run each tool to obtain your own simulation and verification evidence.') : state.project.template === 'sky130' ? t('This SKY130 workspace connects design sources to your prepared PDK. Tool and process readiness is checked before execution.') : t('A common-source amplifier is ready to explore. Edit its parameters, run ngspice, and inspect the measurements and raw waveform.')}</p><div class="button-row">${actionButton('simulate',t('▶ Run pre-layout simulation'),'primary-button')}<button class="secondary-button" data-tab="editor">⌘ Open netlist</button></div>${reasonFor('simulate') ? html`<p class="reason">${esc(reasonFor('simulate'))}</p>` : ''}</div>` +
    html`<div class="grid-3"><div class="metric-card"><small>Design sources</small><div class="metric-value">${state.files.length}<span class="muted small"> files</span></div><p>Versioned when saved</p></div><div class="metric-card"><small>Recorded executions</small><div class="metric-value">${state.runs.length}<span class="muted small"> runs</span></div><p>${t('{completed} completed · {stale} stale', {completed:number(completed.length), stale:number(state.runs.filter(run => run.stale).length)})}</p></div><div class="metric-card"><small>Toolchain readiness</small><div class="metric-value">${tools}<span class="muted"> / 4</span></div><p>${pdk().available ? t('Prepared PDK detected') : t('PDK not configured')}</p></div></div>` +
    html`<div class="section-heading"><h2>The design path</h2><span>Manual workflow · AI optional</span></div>${designNextStep(stages)}<div class="stage-list">${stages.map((stage,index) => html`<button class="stage" data-design-stage="${esc(stage.id)}" data-continue-design="${esc(stage.id)}" data-design-intent="${esc(designIntent(stage.id))}" data-stage-status="${esc(stage.status)}"><span class="stage-number">${String(index+1).padStart(2,'0')} · ${esc(t(stageLabels[stage.status] || 'unknown'))}</span><strong>${esc(t(stage.title))}</strong><small>${esc(t(stage.reason))}</small></button>`).join('')}</div>` +
    html`<p class="footnote">Open Xschem or Magic to draw in the Drawing desktop tab. A blank design file is created on first open if needed.</p>` +
    html`<div class="section-heading"><h2>Recent executions</h2><span>${latest ? `${t('Latest')} ${esc(formatDate(runTime(latest)))}` : t('No tool executions yet')}</span></div><div class="card" style="padding:0">${runsTable(state.runs.slice(0, 5))}</div>` +
    html`<p class="footnote">${state.project.template !== 'amplifier' ? t('DRC and LVS success require report evidence. Extraction coverage is tool and PDK dependent; a completed process is not tapeout signoff.') : t('Educational model · This example uses a level-1 MOS model, not foundry-qualified SKY130 devices. Verification results appear only after actual tool execution.')}</p>`;
}
function runsTable(runs) {
  if (!runs.length) return html('<div class="empty-state" style="min-height:140px"><span class="empty-symbol">∿</span><p>No executions yet. Start a run to create a reproducible record.</p></div>');
  return html`<div class="table-wrap"><table class="data-table"><thead><tr><th>Execution</th><th>State</th><th>Inputs</th><th>Started</th></tr></thead><tbody>${runs.map(run => html`<tr class="clickable" data-run-id="${esc(run.id)}"><td><button class="run-select-link" data-run-id="${esc(run.id)}">${esc(t(run.kind || 'run'))} <span class="muted">${shortId(run.id)}</span></button></td><td>${badge(run.status)}</td><td>${run.stale ? badge('warning',t('stale')) : html('<span class="muted">Current</span>')}</td><td class="muted">${esc(formatDate(runTime(run)))}</td></tr>`).join('')}</tbody></table></div>`;
}
function editorView() {
  return heading(t('DESIGN SOURCES'), t('A direct line to your circuit.'), t('Edit project files locally. Every save records a revision; changes invalidate previous results.')) +
    html`<div class="editor-toolbar"><select id="editor-file" aria-label="File to edit"><option value="">Select a file</option>${state.files.map(file => html`<option value="${esc(file.path)}" ${file.path === state.file ? 'selected' : ''}>${esc(file.path)}</option>`).join('')}</select><button class="secondary-button" data-action="new-file">＋ Add file</button><button class="secondary-button" data-action="history" ${!state.file ? 'disabled' : ''}>◷ History</button><button id="save-file" class="primary-button" data-action="save-file" ${!state.file || state.file === 'project.json' ? 'disabled' : ''}>Save file <span class="small">⌘S</span></button></div>` +
    `${state.file ? html`<div class="editor-surface"><pre class="line-numbers" aria-hidden="true">${lineNumbers(state.content)}</pre><textarea id="code-editor" class="code-editor" ${state.file === 'project.json' ? 'readonly' : ''} aria-label="${esc(t('Source editor for {path}', {path:state.file}))}" aria-describedby="editor-keyboard-hint" spellcheck="false" autocomplete="off" autocapitalize="off">${esc(state.content)}</textarea></div><p id="editor-keyboard-hint" class="editor-keyboard-hint">Tab moves to the next control. Ctrl+] inserts two spaces.</p><div class="editor-status"><span id="file-save-status">${state.file === 'project.json' ? t('Project metadata · read only') : state.dirty ? t('Unsaved changes') : t('Saved locally')}</span><span>${esc(state.file)} · UTF-8</span></div><div id="file-history">${historyHTML()}</div>` : html('<div class="empty-state"><span class="empty-symbol">⌘</span><p>Select a source file from the explorer.</p></div>')}`;
}
function lineNumbers(content) { return Array.from({length:content.split('\n').length}, (_, index) => index + 1).join('\n'); }
function historyHTML() {
  if (!state.history) return '';
  return html`<div class="card spaced"><div class="card-head"><h2>File revisions</h2><small>Restoring creates a recoverable edit</small></div>${state.history.length ? state.history.map(revision => { const id = revision.id || revision.revision || revision.sha256 || revision.name; return html`<div class="history-row"><span>${esc(shortId(id))} · ${esc(formatDate(revision.created_at || revision.timestamp || revision.time))}</span><button class="secondary-button" data-restore="${esc(id)}">Restore revision</button></div>`; }).join('') : html('<p>No previous saved revisions for this file.</p>')}</div>`;
}
async function openFile(path, switchTab = true, beforeApply = null) {
  if (state.dirty) await saveFile();
  const request = ++state.fileRequest;
  let version;
  const data = await api(projectURL(`/files?path=${encoded(path)}`), {onVersion:value => { version = value; }});
  beforeApply?.();
  if (request !== state.fileRequest) return;
  state.file = path; state.content = data.content; state.savedContent = data.content; state.dirty = false; state.history = null; state.fileVersion = data.source_version ?? version;
  if (switchTab) { state.tab = 'editor'; history.replaceState(null, '', '#editor'); }
  renderTree(); render(); $('#main-content').scrollTop = 0; document.body.classList.remove('show-tree');
}
async function saveFile() {
  if (!state.file || !state.dirty) return;
  const content = state.content, path = state.file, projectId = state.project.id,
    openedVersion=state.fileVersion,openedBase=state.savedContent,fileRequest=state.fileRequest,projectRequest=state.projectRequest;
  let version,committed=false;
  const selected=()=>state.project?.id===projectId&&state.file===path&&state.fileRequest===fileRequest&&state.projectRequest===projectRequest;
  try {
    await api(projectURL(`/files?path=${encoded(path)}`), {method:'PUT', body:{content}, expectedVersion:openedVersion});
    committed=true;
    const verified=await api(`/api/projects/${encoded(projectId)}/files?path=${encoded(path)}`,{onVersion:value=>{version=value;}});
    version=verified.source_version ?? version;
    if(verified.content!==content||typeof version!=='string'||!version)throw new Error(t('Saved source differs from the submitted text or its checked version is unavailable.'));
    if(!selected()||state.savedContent!==openedBase||state.fileVersion!==openedVersion)throw new Error(t('Editor selection or base changed while verifying the save.'));
  } catch (failure) {
    const error=committed?Object.assign(new Error(t('Save could not be verified. Draft retained; inspect disk before retrying.')+' '+failure.message),{code:'OUTCOME_UNKNOWN'}):failure;
    if(state.project?.id===projectId&&state.file===path)editorRecoveryUI.saveFailed(error);throw error;
  }
  state.savedContent = content; state.dirty = state.content !== content; state.fileVersion = version;
  await editorRecoveryUI.saved();
  if(!selected()){const error=new Error(t('Editor selection changed before the save completed. Inspect disk to confirm the outcome.'));error.code='VERSION_CONFLICT';throw error;}
  $('#file-save-status')?.replaceChildren(document.createTextNode(state.dirty ? t('Unsaved changes') : t('Saved locally · revision recorded')));
  await refreshProject(false); toast(t('File saved. Previous results will be checked against the new inputs.'));
  if(!selected()){const error=new Error(t('Editor selection changed before the save completed. Inspect disk to confirm the outcome.'));error.code='VERSION_CONFLICT';throw error;}
}
function waveformControls() {
  if (!state.waveform) return '';
  const available = AFWaveforms.plots(state.waveformSource), options = state.waveformOptions;
  const complex = Boolean(available[options.plot]?.complex_points);
  const sampling = available[options.plot]?.display_sampling;
  return html`<div class="simulation-toolbar waveform-display-controls"><label>${t('Plot')}<select id="waveform-plot">${available.map((plot,index) => html`<option value="${index}" ${index === options.plot ? 'selected' : ''}>${esc(plot.plot || plot.title || String(index+1))}</option>`).join('')}</select></label><label>${t('Component')}<select id="waveform-component">${[['magnitude','Value / magnitude'],['db','Magnitude (dB)'],['phase','Phase'],['real','Real part'],['imaginary','Imaginary part']].map(([value,label]) => html`<option value="${value}" ${options.component === value ? 'selected' : ''} ${!complex && ['phase','imaginary'].includes(value) ? 'disabled' : ''}>${t(label)}</option>`).join('')}</select></label><label>${t('Horizontal scale')}<select id="waveform-x-scale"><option value="linear" ${options.x_scale === 'linear' ? 'selected' : ''}>${t('Linear')}</option><option value="log" ${options.x_scale === 'log' ? 'selected' : ''}>${t('Logarithmic')}</option></select></label><label>${t('Reference trace')}<select id="waveform-reference"><option value="">${t('None (absolute values)')}</option>${available[options.plot].variables.slice(1).map((variable,index) => html`<option value="${index+1}" ${options.reference === index+1 ? 'selected' : ''}>${esc(variable.name)}</option>`).join('')}</select></label><label class="checkbox-label"><input id="waveform-unwrap" type="checkbox" ${options.unwrap ? 'checked' : ''} ${options.component !== 'phase' ? 'disabled' : ''}>${t('Unwrap phase')}</label></div>${sampling ? html`<p class="callout" id="waveform-sampling">${t('Display samples')}: ${number(sampling.returned_points)} / ${number(sampling.original_points)}. ${esc(t(sampling.description))}</p>` : ''}${state.waveform.undefined_values ? html`<p class="footnote" role="status">${t('Undefined values are shown as gaps and exported as empty cells.')}</p>` : ''}`;
}
function setWaveformDisplay(options) {
  if (!state.waveformSource) throw new Error('Load a waveform first.');
  const candidate = {...state.waveformOptions,...options};
  const waveform = AFWaveforms.view(state.waveformSource,candidate);
  AFWaveforms.axis(waveform.points.map(row => row[0]),candidate.x_scale);
  const changedPlot = candidate.plot !== state.waveformOptions.plot;
  state.waveformOptions = candidate; state.waveform = waveform;
  if (changedPlot) { state.traces = new Set(waveform.variables.slice(1).map((_,index) => index+1)); state.zoom = [0,1]; state.cursor = null; }
  if (state.tab === 'simulation') render();
}
function selectWaveformPlot(index) {
  setWaveformDisplay({plot:index,component:'magnitude',reference:null,x_scale:'linear',unwrap:false});
}
function simulationView() {
  return heading(t('SIMULATION LAB'), t('See how your circuit behaves.'), t('Configure an analysis, inspect real output, and compare measurements with their source evidence.')) +
    html`<div class="card"><div class="card-head"><h2>Waveform viewer</h2><small id="waveform-title">${esc(state.waveform?.title || t('Actual simulator output'))}</small></div><div class="simulation-toolbar"><select id="waveform-run" aria-label="Execution for waveform"><option value="">Select a simulation execution</option>${state.runs.filter(run => run.kind === 'simulate').map(run => html`<option value="${esc(run.id)}" ${run.id === state.run?.id ? 'selected' : ''}>${esc(runName(run))}${run.stale ? t(' · stale inputs') : ''}</option>`).join('')}</select><button class="secondary-button" data-action="zoom-in-waveform" ${!state.waveform?.points?.length ? 'disabled' : ''}>Zoom in +</button><button class="secondary-button" data-action="zoom-out-waveform" ${!state.waveform?.points?.length ? 'disabled' : ''}>Zoom out −</button><button class="secondary-button" data-action="reset-waveform" ${!state.waveform?.points?.length ? 'disabled' : ''}>Reset zoom</button><button class="secondary-button" data-action="export-waveform" ${!state.waveform?.points?.length ? 'disabled' : ''}>↓ CSV</button></div>${waveformControls()}<div class="waveform-wrap"><canvas id="waveform" tabindex="0" aria-label="Measured waveforms" aria-describedby="waveform-keyboard-hint waveform-summary" role="img"></canvas><div id="waveform-empty" class="waveform-empty" ${state.waveform?.points?.length ? 'hidden' : ''}><span class="empty-symbol">∿</span><strong>No waveform loaded</strong><span>Select a completed simulation with recorded waveform data.</span></div></div><div id="trace-controls" class="trace-controls"></div><div class="waveform-navigation" role="group" aria-label="Waveform navigation"><button class="secondary-button" data-action="pan-left-waveform" ${!state.waveform?.points?.length ? 'disabled' : ''}>Pan left</button><button class="secondary-button" data-action="pan-right-waveform" ${!state.waveform?.points?.length ? 'disabled' : ''}>Pan right</button><button class="secondary-button" data-action="previous-sample" ${!state.waveform?.points?.length ? 'disabled' : ''}>Previous sample</button><button class="secondary-button" data-action="next-sample" ${!state.waveform?.points?.length ? 'disabled' : ''}>Next sample</button></div><p id="waveform-keyboard-hint" class="waveform-hint">Chart keys: +/− zoom · ←/→ inspect samples · Shift+←/→ pan · Home reset. Scroll to zoom, drag to pan.</p><p id="waveform-summary" class="waveform-summary"></p><div class="waveform-hint"><span id="waveform-cursor" role="status" aria-live="polite" aria-atomic="true">No cursor</span></div>${state.run?.stale ? html('<div class="callout warning">These results use earlier inputs. Run the current design to obtain up-to-date evidence.</div>') : ''}</div>` +
    html`<div id="run-metrics">${metricsHTML()}</div>` +
    html`<details class="card" open data-operation-disclosure="simulation.analysis"><summary class="detail-summary">ANALYSIS CONFIGURATION <span class="muted">· ngspice</span></summary><form id="simulation-form" class="spaced"><div class="form-grid"><label>Analysis<select name="analysis" id="analysis-select"><option value="tran">Transient</option><option value="op">Operating point</option><option value="dc">DC sweep</option><option value="ac">AC frequency</option><option value="noise">Noise</option></select></label><label>Output testbench<input name="netlist" value="testbenches/configured.cir" required></label><div id="analysis-parameters" class="full-width form-grid">${analysisFields('tran')}</div><label class="full-width">Additional SPICE directives<textarea name="directives" class="directives" placeholder=".meas tran vout_mean AVG v(out) FROM=1m TO=2m"></textarea><small>Directives are written into the testbench. Review the generated file in the editor.</small></label></div><div class="form-actions"><button class="secondary-button" type="submit" name="action" value="save">Save testbench</button><button class="primary-button" type="submit" name="action" value="run" ${reasonFor('simulate') ? `disabled title="${esc(reasonFor('simulate'))}"` : ''}>Save & run ▶</button></div></form></details>` +
    html`<div class="card"><div class="card-head"><h2>Compare recorded results</h2><small>Pre / post-layout or two design iterations</small></div>${comparisonHTML()}</div>` +
    html`<section class="card"><h2>Parameter, PVT & statistical analysis</h2><p>Configure parameter sweeps, temperatures, supply values, model corners and measurements. Each scenario records its conditions and actual simulator evidence.</p><div id="simulation-batch-slot"></div><p class="footnote">Statistical modes require qualification of the installed model's seed behavior and device correlation. Engineering parameter variation is recorded separately.</p></section><div class="section-heading"><h2>Execution history</h2><span>Click an execution for raw logs and provenance</span></div>${runsTable(state.runs.filter(run => run.kind === 'simulate'))}`;
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
  return html`<div class="card"><div class="card-head"><h2>Measured results</h2>${badge(state.run.status)}</div>${entries.length ? html`<div class="metric-list">${entries.map(([name,metric]) => html`<button class="metric-item" data-metric="${esc(name)}"><span>${esc(name)}</span><strong>${esc(number(metricValue(metric)))} <span style="display:inline;font-size:10px">${esc(metric?.unit || '')}</span></strong><small>Inspect definition and source ↗</small></button>`).join('')}</div>` : html('<p>This execution has no parsed measurements. Inspect the log for simulator output and measurement definitions.</p>')}<div id="metric-evidence"></div><details class="spaced" data-operation-disclosure="run.record"><summary class="detail-summary">Execution record & artifacts · ${shortId(state.run.id)}</summary>${runDetailsHTML(state.run)}</details></div>`;
}
function runDetailsHTML(run) {
  const artifacts = Array.isArray(run.artifacts) ? run.artifacts : Object.values(run.artifacts || {});
  return html`${run.provenance?.complete === false ? html`<div class="callout warning">Input provenance is incomplete. ${esc((run.provenance.limitations || []).join(" "))} Current project hashes do not establish complete external dependency reproducibility.</div>` : ''}<div class="run-details-grid spaced"><div><strong>Started</strong><span>${esc(formatDate(runTime(run)))}</span></div><div><strong>Finished</strong><span>${esc(formatDate(run.finished_at || run.ended_at))}</span></div><div><strong>Exit code</strong><span>${esc(run.exit_code ?? t('pending / unknown'))}</span></div><div><strong>Input validity</strong><span>${run.stale ? t('Stale — current sources differ') : t('Current project source hashes')}</span></div></div>${run.error || run.diagnostic ? html`<p class="callout error">${esc(run.error || run.diagnostic)}</p>` : ''}<pre class="evidence-details">${esc(pretty(run))}</pre>${artifacts.length ? html`<div class="artifact-links">${artifacts.map(artifact => { const path = typeof artifact === 'string' ? artifact : artifact.path || artifact.name; return path ? html`<a href="/api/artifacts/download?project_id=${encoded(state.project.id)}&amp;kind=run&amp;run_id=${encoded(run.id)}&amp;path=${encoded(path)}">↓ ${esc(path.split('/').pop())}</a>` : ''; }).join('')}</div>` : ''}`;
}
function comparisonHTML() {
  const completed = window.AFResultComparison.eligibleRuns(state.runs);
  if (completed.length < 2) return html('<p>Complete at least two real simulations to compare metrics. No post-layout results are fabricated; run an extracted testbench when available.</p>');
  const options = selected => html`<option value="">Choose an execution</option>${completed.map(run => html`<option value="${esc(run.id)}" ${selected === run.id ? 'selected' : ''}>${esc(runName(run))}${run.stale ? t(' · stale') : ''}</option>`).join('')}`;
  return html`<div class="comparison-controls"><select id="compare-a" aria-label="Baseline execution">${options(state.compare[0])}</select><span>→</span><select id="compare-b" aria-label="Comparison execution">${options(state.compare[1])}</select></div><div id="comparison-results">${comparisonResults()}</div>`;
}
function comparisonResults() {
  const a = state.runs.find(run => run.id === state.compare[0]), b = state.runs.find(run => run.id === state.compare[1]);
  if (!a || !b) return html('<p>Choose a baseline and comparison run. Each result retains its original inputs and conditions.</p>');
  const comparison = window.AFResultComparison.compare(a,b);
  return html`${a.stale || b.stale ? html('<div class="callout warning">One or both runs use older source files. This is a historical comparison.</div>') : ''}${a.id === b.id ? html('<p class="callout warning">Both selectors refer to the same execution.</p>') : ''}${comparison.differences.length ? html`<div class="callout warning"><strong>Comparability needs review</strong><ul>${comparison.differences.map(reason=>html`<li>${esc(t(reason))}</li>`).join('')}</ul></div>` : html`<p class="callout">Recorded conditions and measurement definitions match.</p>`}<div class="table-wrap"><table class="data-table"><thead><tr><th>Metric</th><th>Unit</th><th>Baseline</th><th>Comparison</th><th>Δ</th><th>Δ %</th><th>Comparability</th></tr></thead><tbody>${comparison.rows.map(row=>html`<tr data-comparable="${row.comparable}"><td>${esc(row.name)}</td><td>${esc(row.unit || t('unknown'))}</td><td>${esc(number(row.a))}</td><td>${esc(number(row.b))}</td><td>${row.delta === null ? '—' : esc(number(row.delta))}</td><td>${row.percent === null ? '—' : `${esc(number(row.percent))}%`}</td><td>${row.comparable ? t('Comparable') : esc(row.reasons.map(reason=>t(reason)).join(' '))}</td></tr>`).join('')}</tbody></table></div><details><summary>Recorded comparison conditions</summary><div class="comparison-evidence"><pre>${esc(pretty({run_id:a.id,conditions:a.metadata?.measurement_evidence?.conditions || a.metadata?.conditions || {},declared_conditions:a.metadata?.conditions || {},limitations:a.metadata?.measurement_evidence?.limitations || [],definitions:a.metric_provenance || {}}))}</pre><pre>${esc(pretty({run_id:b.id,conditions:b.metadata?.measurement_evidence?.conditions || b.metadata?.conditions || {},declared_conditions:b.metadata?.conditions || {},limitations:b.metadata?.measurement_evidence?.limitations || [],definitions:b.metric_provenance || {}}))}</pre></div></details><p class="footnote">Differences are calculated only when recorded units, definitions and conditions agree. Percentage changes are omitted for dB values.</p>`;
}
function designDefaults() {
  return {schematic:'schematics/amplifier.sch',layout:'layout/amplifier.mag',netlist:'testbenches/pre.cir',post_netlist:'testbenches/post.cir',layout_netlist:'netlists/extracted.spice',schematic_netlist:'netlists/schematic.spice',cell:'amplifier',...(state.project?.design || {})};
}
function verificationView() {
  const design = designDefaults();
  const publishable = state.run?.status === 'succeeded' && ['xschem','extract'].includes(state.run?.kind) && !state.run?.stale && !state.run?.imported_evidence;
  const actions = [
    ['magic','◇',t('Layout editor'),t('Open Magic in the Drawing desktop tab. A blank layout is created if the file does not exist.'),t('Open magic')],
    ['drc','⌗',t('Design rule check'),t('Run magic against the configured process rules. Success is determined from the recorded DRC report.'),t('Run DRC')],
    ['lvs','⇄',t('Layout versus schematic'),t('Compare the extracted layout netlist with the schematic source through netgen. Inspect the report to assess the match.'),t('Run LVS')],
    ['extract','⊞',t('Parasitic extraction'),t('Extract layout connectivity and supported parasitics. Review the extraction coverage before post-layout simulation.'),t('Run extraction')]
  ];
  return heading(t('PHYSICAL VERIFICATION'),t('Follow the evidence.'), t('Coordinate native layout tools and inspect their reports, inputs, and execution records.')) +
    html`<div class="callout ${pdk().available ? '' : 'warning'}">${pdk().available ? html`Prepared PDK detected: ${esc(pdk().root)} · ${esc(pdk().variant || '')}. Every verification result still requires its actual tool report.` : html`Verification needs a prepared PDK installation. ${esc(pdk().diagnostic || t('Configure PDK_ROOT in Settings.'))}`}</div><div class="grid-2 spaced">${actions.map(([kind,icon,title,description,label]) => html`<div class="card verification-card"><span class="verification-icon">${icon}</span><div><h2>${title}</h2><p>${description}</p>${actionButton(kind,label)}${reasonFor(kind) ? html`<p class="reason spaced">${esc(reasonFor(kind))}</p>` : ''}</div></div>`).join('')}</div>` +
    html`<div class="card"><h2>Execution inputs</h2><p>These values are passed to the next verification execution. They must refer to actual project sources.</p><div class="form-grid"><label>Layout source<input id="verification-layout" value="${esc(design.layout)}"></label><label>Extracted layout netlist<input id="verification-extracted" value="${esc(design.layout_netlist)}"></label><label>Schematic netlist<input id="verification-schematic" value="${esc(design.schematic_netlist)}"></label><label>Top cell<input id="verification-cell" value="${esc(design.cell)}"></label></div></div>` +
    (state.project.template === 'ota5' ? html`<div class="card"><h2>Five-transistor reference</h2><p>Netlist the schematic, then select its successful execution and publish the actual output. Run extraction and publish that output before LVS and post-layout simulation. Publication preserves all five devices and records source hashes.</p><div class="button-row"><button class="secondary-button" data-action="reference-netlist" ${!tool('xschem').available ? 'disabled' : ''}>Netlist schematic</button><button class="secondary-button" data-action="reference-publish" ${!publishable ? 'disabled' : ''}>Publish selected tool output</button><button class="secondary-button" data-action="reference-post" ${reasonFor('simulate') || !state.files.some(file => file.path === 'netlists/extracted.spice') ? 'disabled' : ''}>Run post-layout simulation</button></div></div>` : '') +
    html`<div class="section-heading"><h2>Verification executions</h2><span>Process completion ≠ verification pass</span></div>${runsTable(state.runs.filter(run => ['drc','lvs','extract','magic','xschem'].includes(run.kind)))}` +
    drcNavigationUI.html() +
    html`${state.run && ['drc','lvs','extract','magic','xschem'].includes(state.run.kind) ? html`<div class="card spaced"><div class="card-head"><h2>Selected execution</h2>${badge(state.run.status)}</div>${runDetailsHTML(state.run)}</div>` : ''}<p class="footnote">A working flow is not tapeout signoff. Extraction accuracy and RC coverage depend on the installed tool and PDK configuration. No unexecuted check is shown as passing.</p>`;
}
const nodeCatalog = [
  ['specification','Design specification',true],['parameter','Parameter adjustment',true],['simulate','ngspice simulation',true],['measure','Metric extraction',true],['condition','Target condition',true],['report','Result report',true],
  ...window.AFGraphControls.catalog.slice(6)
];
function defaultWorkflow() {
  return {schema_version:1,name:'Amplifier bias tuning',nodes:nodeCatalog.slice(0,6).map(([type,label], index) => ({id:`n${index + 1}`,type,position:{x:35 + (index % 3) * 210,y:55 + Math.floor(index / 3) * 160},config:{label}})),edges:[['n1','n2'],['n2','n3'],['n3','n4'],['n4','n5'],['n5','n6']].map(([source,target]) => ({source,target})),limits:{iterations:8,runtime_seconds:120},tuning:{parameter:'RD',minimum:1000,maximum:10000,target:1.5,tolerance:0.05,metric:'vout_mean',netlist:'testbenches/pre.cir'}};
}
function workflowView() {
  if (!state.workflow) state.workflow = defaultWorkflow();
  const workflow = state.workflow;
  if (workflow.schema_version === 2) return graphView(workflow);
  return heading(t('VISUAL WORKFLOWS'),t('Connect a repeatable design loop.'), t('Build a versioned graph and execute bounded parameter tuning with real simulation evidence.'), badge('implemented',t('Experimental execution'))) +
    html`<div class="workflow-toolbar"><input id="workflow-name" value="${esc(workflow.name)}" aria-label="Workflow name" style="max-width:210px"><button class="secondary-button" data-action="new-graph">New graph</button><button class="secondary-button" data-action="save-workflow">Save</button><button class="secondary-button" data-action="load-workflow">Load saved</button><button class="secondary-button" data-action="export-workflow">↓ JSON</button><button class="secondary-button" data-action="export-workflow-yaml">↓ YAML</button><button class="secondary-button" data-action="import-workflow">↑ Import</button><input type="file" id="workflow-file" accept=".json,.yaml,.yml,application/json,application/yaml,text/yaml" hidden><button class="primary-button" data-action="run-workflow" ${reasonFor('simulate') ? `disabled title="${esc(reasonFor('simulate'))}"` : ''}>▶ Run loop</button></div>` +
    html`<div id="saved-workflows"></div><div class="workflow-board"><div id="workflow-plane" class="workflow-plane"><svg class="workflow-edges" id="workflow-edges" aria-hidden="true"></svg><div id="workflow-nodes"></div></div></div><p id="workflow-keyboard-hint" class="waveform-hint">Drag or use arrow keys to arrange nodes. Press Enter to configure. Shift+arrow moves farther.</p>` +
    html`<div class="workflow-toolbar spaced"><select id="new-node-type" aria-label="Node type to add">${nodeCatalog.slice(0,6).map(([type,label,implemented]) => html`<option value="${type}">${t(label)}${implemented ? '' : t(' (planned)')}</option>`).join('')}</select><button class="secondary-button" data-action="add-node">＋ Add node</button><select id="edge-from" aria-label="Connection source">${workflow.nodes.map(node => html`<option value="${esc(node.id)}">From: ${esc(node.config?.label || node.type)}</option>`).join('')}</select><select id="edge-to" aria-label="Connection destination">${workflow.nodes.map(node => html`<option value="${esc(node.id)}">To: ${esc(node.config?.label || node.type)}</option>`).join('')}</select><button class="secondary-button" data-action="add-edge">Connect →</button></div>` +
    html`<div id="node-configuration" class="workflow-configuration">${nodeConfiguration()}</div><div class="callout">Executable graph: specification → parameter → simulate → measure → condition → report. The engine runs a bounded resistor search. Create a schema-v2 graph for general nodes and control flow.</div>` +
    html`<div class="card"><div class="card-head"><h2>Search bounds & stopping conditions</h2><small>Circuit optimization · no model weight updates</small></div><div class="form-grid"><label>Parameter<input id="tuning-parameter" value="${esc(workflow.tuning.parameter)}"></label><label>Measurement<input id="tuning-metric" value="${esc(workflow.tuning.metric)}"></label><label>Minimum<input type="number" id="tuning-minimum" value="${workflow.tuning.minimum}" step="any"></label><label>Maximum<input type="number" id="tuning-maximum" value="${workflow.tuning.maximum}" step="any"></label><label>Target value<input type="number" id="tuning-target" value="${workflow.tuning.target}" step="any"></label><label>Absolute tolerance<input type="number" id="tuning-tolerance" value="${workflow.tuning.tolerance}" min="0" step="any"></label><label>Maximum iterations<input type="number" id="limit-iterations" value="${workflow.limits.iterations}" min="3" max="100"></label><label>Runtime limit (seconds)<input type="number" id="limit-runtime" value="${workflow.limits.runtime_seconds}" min="1" max="3600"></label><label class="full-width">Testbench<input id="tuning-netlist" value="${esc(workflow.tuning.netlist)}"></label></div></div>` +
    html`${jobHistoryHTML('tuning',state.job)}<div id="workflow-job" class="spaced">${jobHTML(state.job)}</div><details class="card" data-operation-disclosure="workflow.catalog"><summary class="detail-summary">Node catalog & implementation status</summary><div class="catalog">${nodeCatalog.map(([,label,implemented]) => badge(implemented ? 'implemented' : 'planned', `${t(label)} · ${t(implemented ? 'implemented' : 'planned')}`)).join('')}</div><p>Workflow files are independent of this editor. Execution validation enforces the supported chain and resource bounds.</p></details><details class="card" data-operation-disclosure="workflow.json"><summary class="detail-summary">Inspect or edit graph JSON</summary><textarea class="workflow-json" id="workflow-json" spellcheck="false" aria-label="Workflow JSON">${esc(pretty(workflow))}</textarea><button class="secondary-button spaced" data-action="apply-workflow-json">Apply JSON</button></details>`;
}
function graphView(workflow) {
  const nodeOptions = direction => workflow.nodes.map(node => html`<option value="${esc(node.id)}">${t(direction)}: ${esc(node.config?.label || node.type)} · ${esc(node.id)}</option>`).join('');
  return heading(t('VISUAL WORKFLOWS'),t('Build a bounded execution graph.'),t('Connect typed inputs, real EDA operations, conditions and human review.'),badge('implemented',t('Schema v2'))) +
    html`<div class="workflow-toolbar"><input id="workflow-name" value="${esc(workflow.name)}" aria-label="Workflow name"><button class="secondary-button" data-action="new-graph">New graph</button><button class="secondary-button" data-action="new-tuning">New v1 tuning chain</button><button class="secondary-button" data-action="save-workflow">Save</button><button class="secondary-button" data-action="load-workflow">Load saved</button><button class="secondary-button" data-action="export-workflow">↓ JSON</button><button class="secondary-button" data-action="export-workflow-yaml">↓ YAML</button><button class="secondary-button" data-action="import-workflow">↑ Import</button><input type="file" id="workflow-file" accept=".json,.yaml,.yml,application/json,application/yaml,text/yaml" hidden><button class="primary-button" data-action="run-workflow">Run graph</button></div>` +
    html`<div id="saved-workflows"></div><div class="workflow-board"><div id="workflow-plane" class="workflow-plane"><svg class="workflow-edges" id="workflow-edges" aria-hidden="true"></svg><div id="workflow-nodes"></div></div></div><p id="workflow-keyboard-hint" class="waveform-hint">Drag or use arrow keys to arrange nodes. Press Enter to configure. Shift+arrow moves farther.</p><div class="workflow-toolbar spaced"><select id="new-node-type" aria-label="Node type to add">${nodeCatalog.map(([type,label]) => html`<option value="${type}">${t(label)}</option>`).join('')}</select><button class="secondary-button" data-action="add-node">＋ Add node</button><select id="edge-from" aria-label="Connection source">${nodeOptions('From')}</select><select id="edge-to" aria-label="Connection destination">${nodeOptions('To')}</select>${window.AFGraphControls.edgeFields()}<button class="secondary-button" data-action="add-edge">Connect →</button></div><div id="node-configuration" class="workflow-configuration">${nodeConfiguration()}</div>` +
    window.AFGraphControls.limitsHTML(workflow) +
    html`<details class="card" data-operation-disclosure="workflow.inputs"><summary class="detail-summary">Workflow inputs and outputs</summary><div class="form-grid"><label>Workflow inputs<textarea id="workflow-inputs" class="directives" spellcheck="false">${esc(pretty(workflow.inputs || {}))}</textarea></label><label>Workflow output mappings<textarea id="workflow-outputs" class="directives" spellcheck="false">${esc(pretty(workflow.outputs || {}))}</textarea></label></div></details>${jobHistoryHTML('workflow',state.job)}<div id="workflow-job" class="spaced">${jobHTML(state.job)}</div><details class="card" data-operation-disclosure="workflow.json"><summary class="detail-summary">Inspect or edit graph JSON</summary><textarea class="workflow-json" id="workflow-json" spellcheck="false" aria-label="Workflow JSON">${esc(pretty(workflow))}</textarea><button class="secondary-button spaced" data-action="apply-workflow-json">Apply JSON</button></details>`;
}
function nodeConfiguration() {
  const node = state.workflow.nodes.find(item => item.id === state.workflowSelection);
  if (!node) return html('<p class="footnote">Select a node to inspect its configuration. Connections are listed in the graph JSON.</p>');
  return html`<div class="card"><div class="card-head"><h2>${esc(node.config?.label || node.type)}</h2><button class="text-button danger-button" data-action="delete-node">Remove node</button></div><div class="node-config"><label>Node ID<input value="${esc(node.id)}" readonly></label><label>Display label<input id="node-label" value="${esc(node.config?.label || '')}"></label><label>Horizontal position<input id="node-position-x" type="number" min="8" max="900" value="${Number(node.position?.x) || 0}"></label><label>Vertical position<input id="node-position-y" type="number" min="8" max="450" value="${Number(node.position?.y) || 0}"></label><label style="grid-column:1/-1">Configuration JSON<textarea id="node-json" class="directives">${esc(pretty(node.config || {}))}</textarea></label></div>${state.workflow.schema_version === 2 ? window.AFGraphControls.nodeFields(node) : ''}<button class="secondary-button spaced" data-action="save-node">Apply node configuration</button><p class="node-description">Node type: ${esc(node.type)}. ${state.workflow.schema_version === 2 ? t('Inputs, outputs and limits are validated before execution.') : t('Execution settings come from the tuning bounds below.')}</p>${state.workflow.edges.filter(edge => edge.source === node.id || edge.target === node.id).map((edge) => html`<div class="history-row"><span>${esc(edge.source)} → ${esc(edge.target)}${edge.when === undefined ? '' : ' · ' + esc(String(edge.when))}${edge.on ? ' · ' + esc(edge.on) : ''}</span><button class="text-button" data-remove-edge="${state.workflow.edges.indexOf(edge)}">Remove connection</button></div>`).join('')}</div>`;
}
function drawWorkflow() {
  if (!$('#workflow-nodes')) return;
  $('#workflow-nodes').innerHTML = state.workflow.nodes.map(node => html`<button class="workflow-node ${state.workflowSelection === node.id ? 'selected' : ''} ${nodeCatalog.find(item => item[0] === node.type)?.[2] ? '' : 'planned'}" data-node="${esc(node.id)}" aria-pressed="${state.workflowSelection === node.id}" aria-describedby="workflow-keyboard-hint" style="left:${Number(node.position?.x) || 0}px;top:${Number(node.position?.y) || 0}px"><span class="node-label">${esc(node.config?.label || node.type)}</span><span class="node-type">${esc(node.type)} · ${esc(node.id)}</span></button>`).join('');
  drawEdges();
  $$('.workflow-node').forEach(element => {
    element.addEventListener('click', event => {
      const dragged = element.dataset.dragged === 'true'; delete element.dataset.dragged;
      if (dragged && event.detail !== 0) return;
      safely(async () => { await window.AFOperations.dispatch('ui.workflow.select_node', {node_id:element.dataset.node}); if (event.detail === 0) $('#node-label')?.focus(); });
    });
    element.addEventListener('keydown', event => {
      if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      const node = state.workflow.nodes.find(item => item.id === element.dataset.node), step = event.shiftKey ? 40 : 10;
      node.position = {x:Math.max(8,Math.min(900,(Number(node.position?.x) || 0) + (event.key === 'ArrowRight' ? step : event.key === 'ArrowLeft' ? -step : 0))),y:Math.max(8,Math.min(450,(Number(node.position?.y) || 0) + (event.key === 'ArrowDown' ? step : event.key === 'ArrowUp' ? -step : 0)))};
      element.style.left = `${node.position.x}px`; element.style.top = `${node.position.y}px`;
      syncNodePositionFields(node); element.scrollIntoView({block:'nearest',inline:'nearest'}); drawEdges(); syncWorkflowJSON();
    });
    element.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    const node = state.workflow.nodes.find(item => item.id === element.dataset.node); let moved = false;
    const origin = {x:event.clientX,y:event.clientY,left:node.position?.x || 0,top:node.position?.y || 0};
    element.setPointerCapture(event.pointerId);
    const move = moveEvent => { if (Math.abs(moveEvent.clientX-origin.x)+Math.abs(moveEvent.clientY-origin.y)>3) moved = true; node.position = {x:Math.max(8, Math.min(900, origin.left + moveEvent.clientX-origin.x)),y:Math.max(8, Math.min(450, origin.top + moveEvent.clientY-origin.y))}; element.style.left = `${node.position.x}px`; element.style.top = `${node.position.y}px`; syncNodePositionFields(node); drawEdges(); };
    const end = () => { element.removeEventListener('pointermove',move); element.removeEventListener('pointerup',end); element.removeEventListener('pointercancel',end); element.dataset.dragged = String(moved); syncWorkflowJSON(); };
    element.addEventListener('pointermove',move); element.addEventListener('pointerup',end,{once:true}); element.addEventListener('pointercancel',end,{once:true});
    });
  });
}
function syncNodePositionFields(node) {
  if (state.workflowSelection !== node.id) return;
  if ($('#node-position-x')) $('#node-position-x').value = node.position.x;
  if ($('#node-position-y')) $('#node-position-y').value = node.position.y;
}
function drawEdges() {
  workflowDraftRevision++;
  $('#workflow-edges').innerHTML = html`<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="var(--workflow-marker)"/></marker></defs>${state.workflow.edges.map(edge => { const source = state.workflow.nodes.find(node => node.id === edge.source), target = state.workflow.nodes.find(node => node.id === edge.target); if (!source || !target) return ''; const x1 = (Number(source.position?.x) || 0)+158,y1=(Number(source.position?.y)||0)+37,x2=(Number(target.position?.x)||0)-4,y2=(Number(target.position?.y)||0)+37, bend = Math.max(55,Math.abs(x2-x1)/2); return html`<path d="M ${x1} ${y1} C ${x1+bend} ${y1}, ${x2-bend} ${y2}, ${x2} ${y2}" fill="none" stroke="var(--workflow-edge)" stroke-width="1.5" marker-end="url(#arrow)"/>`; }).join('')}`;
}
function captureWorkflow() {
  if (!$('#workflow-name')) return state.workflow;
  state.workflow.name = $('#workflow-name').value;
  if (state.workflow.schema_version === 2) {
    window.AFGraphControls.captureLimits(state.workflow);
    for (const field of ['inputs','outputs']) {
      const element = $(`#workflow-${field}`);
      if (element) { const value = JSON.parse(element.value || '{}'); if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(t('Workflow mappings must be objects.')); state.workflow[field] = value; }
    }
    return state.workflow;
  }
  for (const field of ['parameter','metric','netlist']) state.workflow.tuning[field] = $(`#tuning-${field}`).value;
  for (const field of ['minimum','maximum','target','tolerance']) state.workflow.tuning[field] = Number($(`#tuning-${field}`).value);
  state.workflow.limits = {iterations:Number($('#limit-iterations').value),runtime_seconds:Number($('#limit-runtime').value)};
  return state.workflow;
}
function syncWorkflowJSON() { if ($('#workflow-json')) $('#workflow-json').value = pretty(state.workflow); }
function validateWorkflowShape(workflow) {
  if (![1,2].includes(workflow?.schema_version) || !Array.isArray(workflow.nodes) || !Array.isArray(workflow.edges) || (workflow.schema_version === 1 && (!workflow.tuning || !workflow.limits))) throw new Error(t('Expected a versioned workflow with nodes and edges.'));
  if (workflow.nodes.some(node => !node.id || !node.type)) throw new Error(t('Every node needs an ID and a type.'));
  workflow.nodes.forEach((node, index) => { node.position ||= {x:30+(index%3)*200,y:40+Math.floor(index/3)*140}; node.config ||= {}; if (workflow.schema_version === 2) { node.inputs ||= {}; node.outputs ||= {}; } });
  if (workflow.schema_version === 2) { workflow.inputs ||= {}; workflow.outputs ||= {}; workflow.limits ||= {runtime_seconds:120,max_node_executions:1000,max_iterations:100}; }
  return workflow;
}
async function importWorkflowDocument(content, format) {
  if (state.tab !== 'workflows') throw new Error(t('Open Workflows first.'));
  const projectId = state.project?.id, draft = state.workflow, revision = workflowDraftRevision;
  const result = await window.AFOperations.invoke('workflow.parse', {content,format});
  if (state.project?.id !== projectId || state.tab !== 'workflows' || state.workflow !== draft || workflowDraftRevision !== revision) throw new Error(t('Workflow changed during import. Import the document again.'));
  state.workflow = validateWorkflowShape(result.data.workflow);
  state.workflowSelection = null; render();
}
async function exportWorkflowDocument(format) {
  if (state.tab !== 'workflows') throw new Error(t('Open Workflows first.'));
  const result = await window.AFOperations.invoke('workflow.export', {workflow:captureWorkflow(),format});
  download(result.data.content,result.data.filename,result.data.content_type);
  return {filename:result.data.filename,format};
}
function jobHTML(job, evaluation = false) {
  if (!job) return '';
  if (!evaluation && (job.schema_version === 2 || job.workflow?.schema_version === 2)) return window.AFGraphControls.jobHTML(job) + (isActive(job) ? html`<button class="secondary-button danger-button" data-action="cancel-workflow">Stop</button>` : '');
  const iterations = job.iterations || [];
  return html`<div class="card"><div class="card-head"><h2>${evaluation ? t('Evaluation record') : t('Workflow execution')} <span class="muted mono small">${shortId(job.id)}</span></h2><div class="button-row">${badge(job.status)}${isActive(job) ? html`<button class="secondary-button danger-button" data-action="cancel-${evaluation ? 'evaluation' : 'workflow'}">Stop</button>` : ''}</div></div><p class="job-progress" role="status" aria-live="polite" aria-atomic="true">${esc(t(evaluation ? '{count} recorded episodes' : '{count} recorded iterations', {count:number(iterations.length)}))}</p>${job.error ? html`<div class="callout error">${esc(job.error)}</div>` : ''}${iterations.length ? html`<div class="table-wrap"><table class="data-table"><thead><tr><th>Step</th><th>Parameter</th><th>Value</th><th>Metric</th><th>Evidence</th></tr></thead><tbody>${iterations.map((iteration,index) => html`<tr><td>${index+1}</td><td>${esc(iteration.parameter || '—')}</td><td>${esc(number(iteration.value))}</td><td>${esc(number(iteration.metric && typeof iteration.metric === 'object' ? iteration.metric.value : iteration.metric))}</td><td>${iteration.run_id ? html`<button class="run-select-link" data-run-id="${esc(iteration.run_id)}">${shortId(iteration.run_id)} ↗</button>` : '—'}</td></tr>`).join('')}</tbody></table></div>` : html`<p>${isActive(job) ? t('The engine is executing. Actual results will appear as each step completes.') : t('Inspect the record below for results and diagnostic evidence.')}</p>`}<details data-operation-disclosure="${evaluation ? 'evaluation.record' : 'workflow.record'}"><summary class="detail-summary">Full execution record</summary><pre class="evidence-details">${esc(pretty(job))}</pre></details></div>`;
}
function jobHistoryHTML(kind, selected) {
  const jobs = state.jobs.filter(job => kind === 'evaluation' ? job.kind === kind : ['workflow','tuning'].includes(job.kind));
  return jobs.length ? html`<label class="spaced">${t('Inspect a recorded {kind} execution', {kind:t(kind === 'evaluation' ? 'evaluation' : 'workflow')})}<select class="job-history" data-job-kind="${kind}"><option value="">Select historical execution</option>${jobs.map(job => html`<option value="${esc(job.id)}" ${job.id === selected?.id ? 'selected' : ''}>${esc(shortId(job.id))} · ${esc(t(job.status))} · ${esc(formatDate(job.created_at))}</option>`).join('')}</select><small>Selecting a record only inspects evidence; it does not rerun the workflow.</small></label>` : '';
}
function evaluationView() {
  return heading(t('EVALUATION ENVIRONMENT'),t('Measure an agent’s decisions.'), t('Collect real circuit trajectories and compare reproducible outcomes in isolated working directories.'), badge('experimental',t('Evaluation available'))) +
    html`<div class="callout">Circuit parameter search and language-model weight training have separate controls and evidence. Both use independent scoring from real simulator runs.</div><div class="grid-2 spaced"><div class="card"><p class="eyebrow">REAL EDA EPISODES</p><h2>Run an evaluation</h2><p>Each episode uses an isolated project copy, a reproducible seed, and evaluator-owned scoring. Inspect the returned trajectory and resource costs.</p><form id="evaluation-form"><div class="form-grid"><label>Episodes<input type="number" name="episodes" value="3" min="1" max="20" required></label><label>Random seed<input type="number" name="seed" value="42" required></label></div><div class="form-actions"><button class="primary-button" type="submit" ${reasonFor('simulate') ? `disabled title="${esc(reasonFor('simulate'))}"` : ''}>▶ Evaluate</button></div>${reasonFor('simulate') ? html`<p class="footnote">${esc(reasonFor('simulate'))}</p>` : ''}</form></div><div class="card"><p class="eyebrow">MODEL TRAINING</p><h2>Weight-updating RL</h2>${badge('implemented',t('Optional local trainer'))}<p>Train a local pretrained language model with TRL GRPO and real ngspice rewards. Inspect CPU resources, fixed task sets, curves, saved checkpoints and held-out comparisons.</p><div id="training-controls-slot"></div><p class="footnote">Inference APIs and authenticated agent CLIs are assistance or evaluation backends, not training interfaces.</p></div></div>${jobHistoryHTML('evaluation',state.evaluation)}<div id="evaluation-job" class="spaced">${jobHTML(state.evaluation,true)}</div><div class="card"><h2>What an evaluation can establish</h2><p>Results must preserve observations, parameter actions, tool executions, measurements, score breakdowns, failures, and termination reasons. Inspect the actual job record for the evidence collected by this implementation.</p><div class="button-row">${badge('implemented',t('Isolated episodes'))}${badge('implemented',t('Seeded evaluation'))}${badge('implemented',t('Optional TRL adapter'))}${badge('implemented',t('Model checkpoints'))}</div></div>`;
}
function settingsView() {
  const settings = state.settings;
  return heading(t('ENVIRONMENT & CONNECTIONS'),t('Know what is ready.'), t('Detect local dependencies and configure the tools and optional model endpoint used by this workspace.'), html('<button class="secondary-button" data-action="detect-tools">↻ Recheck tools</button>')) +
    html`<div class="card"><div class="card-head"><h2>Detected toolchain</h2><small>Actual local probes</small></div><div class="tool-grid">${['ngspice','xschem','magic','netgen'].map(name => html`<div class="tool-card"><div><strong>${name}</strong>${badge(tool(name).available ? 'available' : 'unavailable', tool(name).available ? t('Detected') : t('Unavailable'))}</div><p>${esc(tool(name).path || t('Executable not found'))}<br>${esc(tool(name).version || tool(name).diagnostic || t('Version unknown'))}</p></div>`).join('')}</div><div class="callout ${pdk().available ? '' : 'warning'}"><strong>Prepared PDK · ${esc(pdk().variant || 'SKY130')}</strong><br>${esc(pdk().root || pdk().diagnostic || t('No PDK installation configured.'))}</div><details data-operation-disclosure="settings.diagnostics"><summary class="detail-summary">Desktop session & capability diagnostics</summary><pre class="evidence-details">${esc(pretty({desktop:state.status?.toolchain?.desktop,capabilities:state.status?.capabilities,pdk:pdk()}))}</pre></details></div>` +
    html`<section class="card spaced"><h2>Agent CLIs</h2><p>Inspect CLI status, project paths and official update tracking.</p><div id="agent-cli-controls-slot"></div></section>` +
    html`<form id="settings-form"><div class="card"><h2>Local tool paths & process kit</h2><p>Leave a tool path empty to search the local PATH. A raw PDK source checkout is not a prepared installation.</p><div class="form-grid">${['ngspice','xschem','magic','netgen','codex','claude'].map(name => html`<label>${name} executable<input name="tool-${name}" value="${esc(settings.tools?.[name] || '')}" placeholder="Discover on PATH"></label>`).join('')}<label>PDK root<input name="pdk_root" value="${esc(settings.pdk_root || '')}" placeholder="/opt/pdk"></label><label>PDK variant<input name="pdk_variant" value="${esc(settings.pdk_variant || 'sky130A')}"></label></div></div>${providerSettingsHTML()}<div class="form-actions"><button type="submit" class="primary-button">Save configuration</button><button type="button" class="secondary-button" data-action="test-provider">Save and test connection</button><button type="button" class="secondary-button" data-action="export-settings">↓ Export safe config</button><button type="button" class="secondary-button" data-action="import-settings">↑ Import config</button><input type="file" id="settings-file" accept=".json,application/json" hidden></div></form><p class="footnote">Agent CLIs use their existing local login. Choose a backend and permissions in the assistant sidebar. Manual circuit tools remain independent of model configuration.</p>` + projectEnvironmentHTML() + projectProviderHTML();
}

function providerSettingsHTML() {
  const provider = state.settings.provider || {};
  return html`<section class="card" id="provider-settings" aria-labelledby="provider-heading"><div class="card-head"><h2 id="provider-heading">Model API</h2>${badge('implemented',t('Project agent'))}</div><p>Enter your endpoint, model, and API key here to connect the design assistant.</p><div class="form-grid"><label>Provider name (optional)<input id="provider-name" name="provider_name" value="${esc(provider.name || '')}" placeholder="My model provider"></label><label>Protocol<select name="protocol"><option value="openai-compatible" ${provider.protocol === 'openai-compatible' ? 'selected' : ''}>OpenAI-compatible chat API</option><option value="ollama" ${provider.protocol === 'ollama' ? 'selected' : ''}>Ollama native API</option></select></label><label class="full-width">Base URL<input name="base_url" value="${esc(provider.base_url || '')}" placeholder="https://api.example.com/v1" autocomplete="url" spellcheck="false"><small>Include /v1 if your OpenAI-compatible provider requires it. Ollama defaults to http://127.0.0.1:11434.</small></label><label>Model<input id="provider-model" name="model" value="${esc(provider.model || '')}" placeholder="Enter a model name" list="provider-models" autocomplete="off"><datalist id="provider-models">${providerModelOptions()}</datalist></label><label>Request timeout (seconds)<input type="number" min="1" max="300" name="timeout" value="${Number(provider.timeout) || 60}"></label><label class="full-width">API key<input id="provider-api-key" name="api_key" type="password" autocomplete="new-password" spellcheck="false" autocapitalize="none" aria-describedby="provider-key-help provider-key-status"><small id="provider-key-help">Leave blank to keep the key for the current endpoint. Enter a new key when changing the endpoint or protocol.</small></label></div><p id="provider-key-status" class="credential-status" role="status">${esc(providerKeyStatus())}</p><div class="provider-key-controls"><label class="check-label" for="provider-remember-key"><input id="provider-remember-key" name="remember_api_key" type="checkbox" ${provider.api_key_storage === 'session' ? '' : 'checked'}>Remember API key on this server</label><button id="provider-clear-key" type="button" class="secondary-button danger-button" data-action="clear-provider-key" ${['local','session'].includes(provider.api_key_storage) ? '' : 'disabled'}>Clear saved key</button></div><p class="footnote">When checked, the key is saved in a file readable and writable only by the current server user; it is not encrypted. Otherwise, it lasts until the workspace server stops. Keys are excluded from configuration exports.</p><details data-operation-disclosure="settings.environment"><summary class="detail-summary">Advanced: environment variable</summary><label>API key environment variable<input name="api_key_env" value="${esc(provider.api_key_env || '')}" placeholder="ANALOG_FACTORY_API_KEY" autocomplete="off" spellcheck="false"><small>Optional fallback when no key is saved here. Enter a variable name, not the key itself. Clearing the saved key keeps this fallback.</small></label></details><p class="footnote">The connection test fetches the model list without sending project contents. You can also enter a model name manually; a connection test does not verify chat support.</p>${window.AFProviderFields.advanced(provider,{esc,t})}<div id="provider-test-result" class="provider-result" role="status" aria-live="polite"></div></section>`;
}
function providerKeyStatus() {
  const provider = state.settings.provider || {};
  if (!provider.api_key_configured) return t('No API key configured. Local providers may work without a key.');
  return t({local:'API key saved on this server.', session:'API key available until the workspace server stops.', environment:'API key available from the configured environment variable.'}[provider.api_key_storage] || 'API key configured.');
}
function projectProviderHTML() {
  if (!state.project) return '';
  const provider = state.projectConfiguration.provider || {};
  const fields = [['name','Provider name'],['base_url','Base URL'],['model','Model'],['api_key_env','API key environment variable']];
  const revisionKey = `project_provider:${state.project.id}`;
  if (!draftVersions.has(revisionKey)) draftVersions.set(revisionKey,window.AFOperations?.version(projectURL()));
  return html`<form id="project-provider-form" class="card spaced"><h2>Project model override</h2><p>Save explicit provider settings for this project. Workspace defaults apply until an override is saved. Keys remain in controlled workspace storage or an explicitly named environment variable.</p><div class="form-grid">${fields.map(([key,label])=>html`<label>${t(label)}<input name="project_${key}" value="${esc(provider[key] || '')}" autocomplete="off"></label>`).join('')}<label>Protocol<select name="project_protocol"><option value="openai-compatible" ${provider.protocol==='ollama'?'':'selected'}>OpenAI-compatible chat API</option><option value="ollama" ${provider.protocol==='ollama'?'selected':''}>Ollama native API</option></select></label><label>Request timeout (seconds)<input name="project_timeout" type="number" min="1" max="300" value="${provider.timeout || 60}"></label></div>${window.AFProviderFields.advanced(provider,{prefix:'project_',esc,t})}<div class="form-actions"><button type="submit" class="primary-button">Save project override</button><button type="button" class="secondary-button" id="project-provider-reset">Use workspace defaults</button></div></form>`;
}
const projectEdaTools = ['ngspice','xschem','magic','netgen'];
const projectPdkFields = ['pdk_root','pdk_variant','pdk_version'];
function projectEnvironmentHTML() {
  if (!state.project) return '';
  const config=state.project.config || {};
  const revisionKey=`project_environment:${state.project.id}`;
  if (!draftVersions.has(revisionKey)) draftVersions.set(revisionKey,window.AFOperations?.version(projectURL()));
  return html`<form id="project-environment-form" class="card spaced"><h2>Project tools and PDK</h2><p>Override tool paths and the prepared process kit for this project. Empty fields inherit workspace defaults. Changing a model-library include in a testbench still requires editing that source file.</p><div class="form-grid">${projectEdaTools.map(name=>html`<label>${name} executable<input name="project_tool_${name}" value="${esc(config.tools?.[name] || '')}" placeholder="${esc(state.settings.tools?.[name] || t('Discover on PATH'))}"></label>`).join('')}${projectPdkFields.map(name=>html`<label>${t({pdk_root:'PDK root',pdk_variant:'PDK variant',pdk_version:'PDK version'}[name])}<input name="project_${name}" value="${esc(config[name] || '')}" placeholder="${esc(state.settings[name] || (name==='pdk_variant'?'sky130A':''))}" ${name==='pdk_variant'?'pattern="[A-Za-z0-9_-]+"':''}></label>`).join('')}</div><div class="form-actions"><button type="submit" class="primary-button">Save project environment</button><button type="button" class="secondary-button" id="project-environment-reset">Use workspace defaults</button></div></form>`;
}
async function saveProjectEnvironment(reset=false) {
  const form=$('#project-environment-form');
  if (!form || !state.project) throw new Error(t('Open project settings first.'));
  if (!reset && !form.reportValidity()) throw new Error(t('Correct the invalid environment fields.'));
  const projectId=state.project.id,key=`project_environment:${projectId}`,config=structuredClone(state.project.config || {}),values=new FormData(form);
  config.tools={...(config.tools || {})};
  for (const name of projectEdaTools) {
    const value=reset?'':String(values.get('project_tool_'+name) || '').trim();
    if (value) config.tools[name]=value; else delete config.tools[name];
  }
  if (!Object.keys(config.tools).length) delete config.tools;
  for (const name of projectPdkFields) {
    const value=reset?'':String(values.get('project_'+name) || '').trim();
    if (value) config[name]=value; else delete config[name];
  }
  const result=await AFOperations.invoke('project.configure',{project_id:projectId,config},{expected_version:draftVersions.get(key)});
  draftVersions.set(key,result.version);
  if (state.project?.id===projectId) {
    state.project=result.data; await refreshProject(false); await refreshStatus(); updateChrome();
    if (reset && $('#project-environment-form')===form) for (const field of form.querySelectorAll('input')) field.value='';
  }
  toast(t(reset?'Project uses workspace environment defaults.':'Project environment saved.'));
  return result.data;
}
async function saveProjectProvider(reset=false) {
  const form = $('#project-provider-form');
  if (!form || !state.project) throw new Error(t('Open project settings first.'));
  if (!reset && !form.reportValidity()) throw new Error(t('Correct the invalid provider fields.'));
  const projectId=state.project.id, key=`project_provider:${projectId}`, config=structuredClone(state.project.config || {});
  if (reset) delete config.provider;
  else {
    const values=new FormData(form);
    config.provider={...Object.fromEntries(['name','protocol','base_url','model','api_key_env'].map(name=>[name,String(values.get('project_'+name)||'')])),
      timeout:Number(values.get('project_timeout')),...window.AFProviderFields.readAdvanced(values,'project_')};
    const inherited=state.settings.provider || {};
    if (config.provider.api_key_env === (inherited.api_key_env || '') && (config.provider.protocol !== inherited.protocol || config.provider.base_url.replace(/\/+$/,'') !== (inherited.base_url || '').replace(/\/+$/,''))) delete config.provider.api_key_env;
  }
  const result=await window.AFOperations.invoke('project.configure',{project_id:projectId,config},{expected_version:draftVersions.get(key)});
  draftVersions.set(key,result.version);
  if (state.project?.id === projectId) { state.project=result.data; await refreshProject(false); await refreshStatus(); await agentUI.refreshCapabilities(); }
  toast(t(reset?'Project uses workspace provider defaults.':'Project provider override saved.'));
  return result.data;
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
  if (!providerSaving) window.AFProviderFields.update($('#settings-form'));
}

function updatePreferenceControls() {
  const ui = preferences.current();
  $('#ui-language').value = ui.language;
  $('#ui-theme').value = ui.theme;
  for (const id of ['ui-language', 'ui-theme']) $(`#${id}`).disabled = preferencesSaving;
  $('.topbar-preferences').setAttribute('aria-busy', String(preferencesSaving));
}
function captureViewDraft() {
  const main = $('#main-content');
  const controls = $$('input, select, textarea', main).filter(element => element.type !== 'file');
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
  const controls = $$('input, select, textarea', main).filter(element => element.type !== 'file');
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
  $('#ui-preferences-status').textContent = t('Saving preferences…');
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
    updatePreferenceControls();
    document.removeEventListener('focusin', trackFocus);
    if (focusId && !focusMoved && document.activeElement === document.body) $(`#${focusId}`).focus({preventScroll:true});
  }
}

async function refreshProject(redraw = true) {
  if (!state.project) return;
  const id = state.project.id;
  const [data, jobs] = await Promise.all([api(projectURL()), api(projectURL('/jobs'))]);
  if (state.project?.id !== id) return;
  state.project = data.project; state.projectConfiguration = data.configuration || {}; state.files = data.files || []; state.runs = data.runs || [];
  state.designProgress = data.design_progress || [];
  state.jobs = asArray(jobs, 'jobs');
  if (state.run) state.run = state.runs.find(run => run.id === state.run.id) || state.run;
  renderTree(); updateChrome(); updateLog(); if (redraw || state.tab === 'overview') render();
}
async function refreshAgentProject(id) {
  if (state.project?.id !== id || state.loadingProject) return;
  await refreshProject(false);
  if (state.project?.id !== id || state.dirty || !state.file) return;
  const path = state.file, request = state.fileRequest;
  let version;
  const data = await api(`/api/projects/${encoded(id)}/files?path=${encoded(path)}`, {onVersion:value => { version = value; }});
  // The user may begin typing or open another file while the refresh is pending.
  if (state.project?.id !== id || state.dirty || state.file !== path || state.fileRequest !== request) return;
  state.content = data.content; state.savedContent = data.content; state.history = null; state.fileVersion = data.source_version ?? version;
  const editor = $('#code-editor');
  if (editor) {
    const start = editor.selectionStart, end = editor.selectionEnd, scroll = editor.scrollTop;
    editor.value = data.content; editor.setSelectionRange(start, end); editor.scrollTop = scroll;
    $('.line-numbers').textContent = lineNumbers(data.content);
    $('#file-history').innerHTML = '';
  }
}
async function startRun(kind, options = {}) {
  const reason = kind === 'xschem' && options.netlist_only ? (tool('xschem').available ? '' : tool('xschem').diagnostic) : reasonFor(kind); if (reason) throw new Error(reason);
  const design = designDefaults();
  const editor = kind === 'magic' || (kind === 'xschem' && !options.netlist_only);
  if (state.dirty) await saveFile();
  if (kind === 'simulate' && !options.netlist) options.netlist = design.netlist;
  if (['drc','lvs','extract','magic'].includes(kind)) {
    options = {layout:$('#verification-layout')?.value || design.layout,layout_netlist:$('#verification-extracted')?.value || design.layout_netlist,schematic_netlist:$('#verification-schematic')?.value || design.schematic_netlist,cell:$('#verification-cell')?.value || design.cell,...options};
  }
  if (kind === 'xschem') options = {schematic:design.schematic,...options};
  if (editor) options = {...options,create_missing:true};
  const sourceKey = kind === 'xschem' ? 'schematic' : 'layout';
  const source = options[sourceKey] || design[sourceKey];
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
    const run = await api(projectURL('/runs'), {method:'POST',body:{kind,options,timeout:editor ? 3600 : state.project.template === 'ota5' ? 180 : 60}});
    if (state.project?.id !== projectId) return;
    state.runs.unshift(run); state.run = run; state.waveform = null; state.log = '';
    if (editor) { state.tab = 'desktop'; history.replaceState(null, '', '#desktop'); }
    else if (kind === 'simulate') state.tab = 'simulation'; else if (['drc','lvs','extract','xschem'].includes(kind)) state.tab = 'verification';
    render(); $('#main-content').scrollTop = 0; updateLog(); updateChrome();
    toast(editor ? t('Opening {tool} in the drawing desktop…', {tool:kind}) : t('Started {kind} · {id}', {kind:t(kind), id:shortId(run.id)}));
    await selectRun(run.id, false);
    if (editor) await refreshProject();
  } finally {
    if (editor) editorLaunches.delete(launchKey);
  }
}
async function deriveReferenceNetlists({run_id} = {}) {
  const projectId = state.project?.id;
  const runId = run_id || state.run?.id;
  if (!projectId || !runId) throw new Error('Select a successful reference tool execution first.');
  const detail = await window.AFOperations.invoke('project.detail',{project_id:projectId});
  if (state.project?.id !== projectId) throw new Error('Selected project changed.');
  const result = await window.AFOperations.invoke('reference.derive_netlists',{project_id:projectId,run_id:runId},{expected_version:detail.version});
  await refreshProject(false); render();
  toast(t('Actual tool output published with its source hash.'));
  return result.data;
}
async function selectRun(id, navigate = true, beforeApply = null) {
  const request = ++state.runRequest;
  const run = await api(projectURL(`/runs/${encoded(id)}`));
  beforeApply?.();
  if (request !== state.runRequest) return;
  state.run = run; state.waveform = null; state.waveformSource = null; state.waveformOptions = {}; state.zoom = [0,1]; state.cursor = null;
  const existing = state.runs.findIndex(item => item.id === run.id); if (existing >= 0) state.runs[existing] = run; else state.runs.unshift(run);
  const results = await Promise.allSettled([api(projectURL(`/runs/${encoded(id)}/log`)), run.kind === 'simulate' && !isActive(run) ? api(projectURL(`/runs/${encoded(id)}/waveforms`)) : Promise.resolve(null)]);
  beforeApply?.();
  if (request !== state.runRequest) return;
  state.log = results[0].status === 'fulfilled' ? results[0].value.content || '' : t('Log unavailable: {message}', {message:results[0].reason.message});
  if (results[1].status === 'fulfilled' && results[1].value?.points?.length) { state.waveformSource = results[1].value; selectWaveformPlot(0); }
  if (navigate) { state.tab = run.kind === 'simulate' ? 'simulation' : ['drc','lvs','extract','magic','xschem'].includes(run.kind) ? 'verification' : state.tab; history.replaceState(null,'',`#${state.tab}`); }
  updateLog(); if (navigate || ['simulation','verification'].includes(state.tab)) render();
}
function updateLog() {
  agentUI.render();
  $('#log-label').textContent = state.run ? `${runName(state.run)}${state.run.stale ? t(t(' · STALE')) : ''}` : t('No run selected');
  if ($('#log-run-status')) { $('#log-run-status').hidden = !state.run; $('#log-run-status').innerHTML = state.run ? badge(state.run.status) : ''; }
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
    if (++pollCount % 12 === 0 || !state.connected) { await refreshStatus(); updateChrome(); }
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
    $('#code-editor')?.addEventListener('input', event => setEditorDraft(event.target.value));
    $('#code-editor')?.addEventListener('scroll', event => { $('.line-numbers').scrollTop = event.target.scrollTop; });
    $('#code-editor')?.addEventListener('keydown', event => { if (event.ctrlKey && event.key === ']' && !event.target.readOnly) { event.preventDefault(); const editor = event.target, start = editor.selectionStart, end = editor.selectionEnd; editor.setRangeText('  ',start,end,'end'); editor.dispatchEvent(new Event('input')); } });
    $('#editor-file')?.addEventListener('change', event => safely(() => openFile(event.target.value,false)));
  }
  if (state.tab === 'simulation') {
    $('#waveform-run')?.addEventListener('change', event => event.target.value && safely(() => selectRun(event.target.value,false)));
    $('#analysis-select')?.addEventListener('change', event => { $('#analysis-parameters').innerHTML = analysisFields(event.target.value); });
    $('#simulation-form')?.addEventListener('submit', event => { event.preventDefault(); safely(async () => { const form = new FormData(event.target), parameters = {}; for (const [key,value] of form.entries()) if (key.startsWith('param-')) parameters[key.slice(6)] = value; const result = await api(projectURL('/simulation'),{method:'POST',expectedVersion:draftVersions.get('simulation'),onVersion:value => draftVersions.set('simulation',value),body:{analysis:form.get('analysis'),parameters,directives:form.get('directives'),netlist:form.get('netlist')}}); await refreshProject(false); const path = result.path || result.netlist || form.get('netlist'); toast(t('Testbench saved: {path}', {path})); if (event.submitter?.value === 'run') await startRun('simulate',{netlist:path}); }); });
    for (const [id,index] of [['compare-a',0],['compare-b',1]]) $( `#${id}`)?.addEventListener('change', event => { state.compare[index] = event.target.value; $('#comparison-results').innerHTML = comparisonResults(); });
    setupWaveform();
  }
  if (state.tab === 'workflows') {
    drawWorkflow();
    const workflowRoot = $('#main-content');
    if (!workflowRoot.dataset.workflowDraftBound) {
      workflowRoot.dataset.workflowDraftBound = 'true';
      workflowRoot.addEventListener('input', event => {
        if (state.tab === 'workflows' && /^(workflow-|tuning-|limit-|node-|new-node-|edge-)/.test(event.target.id || '')) workflowDraftRevision++;
      });
    }
    window.AFGraphControls.bind($('#main-content'), {projectId:()=>state.project?.id,
      onUpdate:job=>{ if (state.job?.id === job.id) { state.job = job; if ($('#workflow-job')) $('#workflow-job').innerHTML = jobHTML(job); } },
      onError:error=>toast(error.message,true)});
    $('#workflow-file')?.addEventListener('change', event => safely(async () => { const file = event.target.files[0]; if (!file) return; if (file.size > 2*1024*1024) throw new Error(t('Workflow documents must be no larger than 2 MiB.')); await importWorkflowDocument(await file.text(), /\.ya?ml$/i.test(file.name) ? 'yaml' : 'json'); toast(t('Workflow imported for review.')); }));
    $('#workflow-name')?.addEventListener('change', () => { captureWorkflow(); syncWorkflowJSON(); });
    $$('[id^="tuning-"],[id^="limit-"],#workflow-debug-step,#workflow-inputs,#workflow-outputs').forEach(element => element.addEventListener('change', () => safely(() => { captureWorkflow(); syncWorkflowJSON(); })));
  }
  if (state.tab === 'evaluation') $('#evaluation-form')?.addEventListener('submit', event => { event.preventDefault(); safely(async () => { const form = new FormData(event.target); state.evaluation = await api(projectURL('/evaluate'),{method:'POST',body:{episodes:Number(form.get('episodes')),seed:Number(form.get('seed'))}}); $('#evaluation-job').innerHTML = jobHTML(state.evaluation,true); toast(t('Evaluation started with isolated real-simulation episodes.')); }); });
  if (state.tab === 'settings') {
    window.AFProviderFields.bind($('#settings-form'));
    $('#project-environment-form')?.addEventListener('submit',event=>{event.preventDefault();safely(()=>saveProjectEnvironment());});
    $('#project-environment-reset')?.addEventListener('click',()=>safely(()=>saveProjectEnvironment(true)));
    window.AFProviderFields.bind($('#project-provider-form'));
    $('#project-provider-form')?.addEventListener('submit',event=>{event.preventDefault();safely(()=>saveProjectProvider());});
    $('#project-provider-reset')?.addEventListener('click',()=>safely(async()=>{await saveProjectProvider(true);render();}));
    $('#settings-form')?.addEventListener('submit', event => { event.preventDefault(); safely(saveSettings); });
    $('#settings-file')?.addEventListener('change', event => safely(async () => { const file = event.target.files[0]; if (!file || providerSaving) return; const settings = JSON.parse(await file.text()); if (!settings || typeof settings !== 'object' || Array.isArray(settings)) throw new Error(t('Expected a configuration JSON object.')); const provider = settings.provider || {}; const safeSettings = {...(settings.ui ? {ui:settings.ui} : {}),tools:{...state.settings.tools,...(settings.tools || {})},pdk_root:settings.pdk_root || '',pdk_variant:settings.pdk_variant || 'sky130A',provider:window.AFProviderFields.publicConfig({name:'',protocol:'openai-compatible',base_url:'',model:'',api_key_env:'',timeout:60,...provider})}; state.settings = await api('/api/settings',{method:'PUT',body:safeSettings}); state.providerTest = null; state.providerModels = []; await refreshStatus(); applyPreferences(state.settings.ui, false); agentUI.refreshCapabilities(); toast(t('Configuration imported.')); }));
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
  const provider = {name:form.get('provider_name'),protocol:form.get('protocol'),base_url:form.get('base_url'),model:form.get('model'),api_key_env:form.get('api_key_env'),timeout:Number(form.get('timeout')),api_key_storage:form.has('remember_api_key') ? 'local' : 'session',...window.AFProviderFields.readAdvanced(form)};
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
    state.settings = await api('/api/settings',{method:'PUT',body:settings,expectedVersion:draftVersions.get('settings'),onVersion:value => draftVersions.set('settings',value)});
    saved = true;
    agentUI.refreshCapabilities();
    // Never repopulate a secret from server responses or retain a submitted key.
    if ($('#provider-api-key')) $('#provider-api-key').value = '';
    const environment = $('#settings-form [name=api_key_env]');
    if (environment) environment.value = state.settings.provider?.api_key_env || '';
    state.providerModels = [];
    updateChrome(); updateProviderFeedback();
    try { await refreshStatus(); updateChrome(); }
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
  for (const name of ['input','change']) document.addEventListener(name, event => {
    if (event.target.matches('input,select,textarea') && !['password','file'].includes(event.target.type)) browserDraftRevision++;
  });
  for (const id of ['ui-language', 'ui-theme']) $(`#${id}`).addEventListener('change', () => safely(savePreferences));
  $('#close-file-dialog').addEventListener('click', () => $('#new-file-dialog').close());
  $('#new-file-import').addEventListener('change', event => safely(async () => { const file = event.target.files[0]; if (!file) return; if (file.size > 2*1024*1024) throw new Error(t('The editor supports text files up to 2 MiB.')); $('#new-file-content').value = await file.text(); if (!$('#new-file-path').value) $('#new-file-path').value = file.name; }));
  $('#new-file-form').addEventListener('submit', event => { event.preventDefault(); safely(async () => { const form = new FormData(event.target), path = String(form.get('path')).trim(); if (state.files.some(file => file.path === path)) throw new Error(t('This file already exists. Open it from the explorer to edit it.')); if (state.dirty) await saveFile(); await api(projectURL(`/files?path=${encoded(path)}`),{method:'PUT',body:{content:form.get('content')},expectedVersion:draftVersions.get('new_file')}); await refreshProject(false); $('#new-file-dialog').close(); event.target.reset(); await openFile(path); toast(t('Project file created.')); }); });
  const readiness = document.createElement('section'); readiness.id = 'project-readiness';
  const readinessActions = document.createElement('div'); readinessActions.className = 'button-row';
  readinessActions.innerHTML = html`<button type="button" class="secondary-button" id="project-check-refresh" data-i18n="Refresh check">Refresh check</button><button type="button" class="secondary-button" id="project-configure" data-i18n="Configure requirements">Configure requirements</button><button type="button" class="secondary-button" id="project-use-amplifier" data-i18n="Use educational example">Use educational example</button>`;
  $('#new-project-form .dialog-actions').before(readiness,readinessActions);
  $('#new-project').addEventListener('click', () => safely(openProjectDialog));
  $('#close-project-dialog').addEventListener('click', closeProjectDialog);
  $('#new-project-dialog').addEventListener('cancel', () => { ++newProjectCheck; newProjectChecking = false; });
  $('#new-project-form [name=template]').addEventListener('change', renderProjectReadiness);
  $('#project-check-refresh').addEventListener('click', () => safely(refreshProjectReadiness));
  $('#project-configure').addEventListener('click', () => safely(async () => { closeProjectDialog(); await window.AFOperations.dispatch('ui.workspace.navigate',{tab:'settings'}); }));
  $('#project-use-amplifier').addEventListener('click', () => setOperationForm('new_project',{template:'amplifier'}));
  $('#new-project-form').addEventListener('submit', event => { event.preventDefault(); safely(() => submitNewProject(event.target)); });
  $('#project-select').addEventListener('change', event => safely(() => window.AFOperations.dispatch('ui.workspace.select_project', {project_id:event.target.value})));
  $('#refresh-project').addEventListener('click', () => safely(() => refreshProject()));
  $('#run-primary').addEventListener('click', () => safely(() => startRun('simulate')));
  $('#copy-log').addEventListener('click', () => safely(async () => { await navigator.clipboard.writeText(state.log || $('#execution-log').textContent); toast(t('Execution log copied.')); }));
  $('#run-cancel').addEventListener('click', () => safely(async () => { state.run = await api(projectURL(`/runs/${encoded(state.run.id)}/cancel`),{method:'POST',body:{}}); updateLog(); toast(t('Cancellation requested.')); }));
  $('#run-retry').addEventListener('click', () => safely(async () => { const run = await api(projectURL(`/runs/${encoded(state.run.id)}/retry`),{method:'POST',body:{}}); state.runs.unshift(run); await selectRun(run.id); toast(t('Retry started as a new execution record.')); }));
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-continue-design],[data-tab],[data-action],[data-file],[data-run-kind],[data-run-id],[data-metric],[data-remove-context],[data-restore],[data-remove-edge],[data-workflow-load],[data-characterization-job]');
    if (!button || button.disabled) return;
    safely(async () => {
      if (button.dataset.continueDesign) {
        event.preventDefault();
        if (button.dataset.designIntent !== designIntent(button.dataset.continueDesign)) throw new Error(t('Design requirements changed. Review the next step and try again.'));
        await window.AFOperations.dispatch('ui.workspace.continue_design',{stage:button.dataset.continueDesign});
      }
      else if (button.dataset.tab) { event.preventDefault(); await window.AFOperations.dispatch('ui.workspace.navigate', {tab:button.dataset.tab}); }
      else if (button.dataset.characterizationJob) await window.AFOperations.dispatch('ui.characterization.open',{job_id:button.dataset.characterizationJob});
      else if (button.dataset.file) await window.AFOperations.dispatch('ui.editor.open', {path:button.dataset.file});
      else if (button.dataset.runKind) await startRun(button.dataset.runKind);
      else if (button.dataset.runId) await window.AFOperations.dispatch('ui.runs.select', {run_id:button.dataset.runId});
      else if (button.dataset.removeContext) await window.AFOperations.dispatch('ui.agent.set_context', {paths:[...state.context].filter(path => path !== button.dataset.removeContext)});
      else if (button.dataset.metric) { const name = button.dataset.metric; const metric = metricEntries().find(([key]) => key === name)?.[1]; $('#metric-evidence').innerHTML = html`<pre class="evidence-details spaced">${esc(pretty({metric:name,...(typeof metric === 'object' ? metric : {value:metric}),...state.run.metric_provenance?.[name],run_id:state.run.id,definition:metric?.definition || state.run.metric_provenance?.[name]?.definition || 'See input snapshot and raw simulator log.',conditions:state.run.metadata?.measurement_evidence?.conditions || metric?.conditions || state.run.metric_provenance?.[name]?.conditions || state.run.metadata?.conditions || 'See execution record.',declared_conditions:state.run.metadata?.conditions || {},limitations:state.run.metadata?.measurement_evidence?.limitations || []}))}</pre>`; $('.log-panel').classList.remove('collapsed'); }
      else if (button.dataset.restore) { await api(projectURL('/restore'),{method:'POST',body:{path:state.file,revision:button.dataset.restore}}); state.dirty = false; await openFile(state.file,false); await refreshProject(false); toast(t('Revision restored.')); }
      else if (button.dataset.removeEdge !== undefined) { const edge = state.workflow.edges[Number(button.dataset.removeEdge)]; if (edge) await window.AFOperations.dispatch('ui.workflow.disconnect', edge); }
      else if (button.dataset.workflowLoad !== undefined) { const workflows = state.savedWorkflows || []; const entry = workflows[Number(button.dataset.workflowLoad)]; let workflow = entry.workflow || entry; if (typeof entry === 'string' || (!workflow.nodes && entry.path)) { const file = await api(projectURL(`/files?path=${encoded(typeof entry === 'string' ? entry : entry.path)}`)); workflow = JSON.parse(file.content); } state.workflow = validateWorkflowShape(workflow); state.workflowSelection = null; render(); toast(t('Saved workflow loaded.')); }
      else if (button.dataset.action) await handleAction(button.dataset.action);
    });
  });
  document.addEventListener('change', event => { if (event.target.dataset.context) { const paths = new Set(state.context), path = event.target.dataset.context; event.target.checked ? paths.add(path) : paths.delete(path); safely(() => window.AFOperations.dispatch('ui.agent.set_context', {paths:[...paths]})); } });
  document.addEventListener('keydown', event => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's' && state.tab === 'editor') { event.preventDefault(); safely(saveFile); } });
  window.addEventListener('beforeunload', event => { if (state.dirty) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('resize', () => requestAnimationFrame(drawWaveform));
  agentUI.bind();
  $('#chat-form').addEventListener('submit', event => { event.preventDefault(); safely(sendChat); });
}
async function handleAction(action) {
  if (action.endsWith('-waveform') || ['previous-sample','next-sample'].includes(action)) $('#waveform-cursor')?.setAttribute('aria-live','polite');
  switch (action) {
    case 'reference-netlist': return window.AFOperations.dispatch('ui.runs.netlist_reference', {});
    case 'reference-publish': return window.AFOperations.dispatch('ui.runs.derive_reference', {});
    case 'reference-post': return window.AFOperations.dispatch('ui.runs.simulate_reference_post', {});
    case 'new-graph': return window.AFOperations.dispatch('ui.workflow.new_graph', {schema_version:2});
    case 'new-tuning': return window.AFOperations.dispatch('ui.workflow.new_graph', {schema_version:1});
    case 'reload-desktop': await refreshStatus(); renderDesktop(true); break;
    case 'new-project': return openProjectDialog();
    case 'new-file': draftVersions.set('new_file', window.AFOperations?.version(projectURL())); $('#new-file-dialog').showModal(); break;
    case 'export': {
      const pid = state.project?.id;
      if (!pid) throw new Error('Select a project first.');
      const result = await window.AFOperations.invoke('project.archive.prepare', {project_id:pid,mode:'export'});
      const anchor = document.createElement('a');
      anchor.href = `/api/transfers/download?project_id=${encodeURIComponent(pid)}&transfer_id=${encodeURIComponent(result.data.id)}`;
      anchor.download = result.data.filename; anchor.click();
      break;
    }
    case 'save-file': await saveFile(); break;
    case 'history': state.history = asArray(await api(projectURL(`/history?path=${encoded(state.file)}`)),'revisions'); $('#file-history').innerHTML = historyHTML(); break;
    case 'zoom-in-waveform': zoomWaveform(0.8); break;
    case 'zoom-out-waveform': zoomWaveform(1.25); break;
    case 'pan-left-waveform': panWaveform(-0.2); break;
    case 'pan-right-waveform': panWaveform(0.2); break;
    case 'previous-sample': moveWaveformCursor(-1); break;
    case 'next-sample': moveWaveformCursor(1); break;
    case 'reset-waveform': state.zoom = [0,1]; state.cursor = null; drawWaveform(); break;
    case 'export-waveform': if (state.waveform) download(AFWaveforms.csv(state.waveform),`waveform-${shortId(state.run?.id)}${AFWaveforms.plots(state.waveformSource)[state.waveformOptions.plot]?.display_sampling ? '-display' : ''}.csv`,'text/csv'); break;
    case 'save-workflow': captureWorkflow(); await api(projectURL('/workflows'),{method:'POST',body:{workflow:state.workflow},expectedVersion:draftVersions.get('workflows'),onVersion:value => draftVersions.set('workflows',value)}); await refreshProject(false); toast(t('Workflow saved to project.')); break;
    case 'load-workflow': state.savedWorkflows = asArray(await api(projectURL('/workflows')),'workflows'); $('#saved-workflows').innerHTML = state.savedWorkflows.length ? html`<div class="card">${state.savedWorkflows.map((entry,index) => html`<div class="history-row"><span>${esc(entry.name || entry.workflow?.name || entry.path || entry)}</span><button class="secondary-button" data-workflow-load="${index}">Load</button></div>`).join('')}</div>` : html('<div class="callout">No saved workflows yet. Save the current graph to add one.</div>'); break;
    case 'export-workflow': return exportWorkflowDocument('json');
    case 'export-workflow-yaml': return exportWorkflowDocument('yaml');
    case 'import-workflow': $('#workflow-file').click(); break;
    case 'apply-workflow-json': state.workflow = validateWorkflowShape(JSON.parse($('#workflow-json').value)); state.workflowSelection = null; render(); break;
    case 'add-node': { captureWorkflow(); const type = $('#new-node-type').value; const label = nodeCatalog.find(item => item[0] === type)?.[1] || type; const id = `n${Date.now().toString(36)}`; const config = {label}; if (state.workflow.schema_version === 2) { if (type === 'parameter') config.value = 1; if (type === 'project') config.path = 'specifications.json'; if (type === 'measure') config.metric = 'vout_mean'; if (type === 'netlist') config.parameter = 'RD'; if (type === 'input') Object.assign(config,{name:id,type:'any',default:null}); if (type === 'human_review') config.prompt = 'Review the design evidence before continuing.'; if (type === 'loop') Object.assign(config,{max_iterations:3,body:{schema_version:2,nodes:[{id:'value',type:'parameter',config:{value:1}}],edges:[],outputs:{state:{node:'value',port:'value'}}}}); } state.workflow.nodes.push({id,type,position:{x:45,y:365},config,...(state.workflow.schema_version === 2 ? {inputs:{},outputs:{}} : {})}); state.workflowSelection = id; render(); break; }
    case 'add-edge': { captureWorkflow(); const source = $('#edge-from').value,target = $('#edge-to').value; if (source === target) throw new Error(t('Connect two distinct nodes.')); const edge = state.workflow.schema_version === 2 ? window.AFGraphControls.captureEdge({source,target}) : {source,target}; if (state.workflow.edges.some(existing => JSON.stringify(existing) === JSON.stringify(edge))) throw new Error(t('This connection already exists.')); state.workflow.edges.push(edge); drawEdges(); syncWorkflowJSON(); break; }
    case 'save-node': { const node = state.workflow.nodes.find(item => item.id === state.workflowSelection); const config = JSON.parse($('#node-json').value); if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error(t('Node configuration must be a JSON object.')); const updated = state.workflow.schema_version === 2 ? window.AFGraphControls.applyNodeFields(structuredClone(node)) : node; Object.assign(node,updated); node.config = {...config,label:$('#node-label').value}; node.position = {x:Math.max(8,Math.min(900,Number($('#node-position-x').value) || 8)),y:Math.max(8,Math.min(450,Number($('#node-position-y').value) || 8))}; drawWorkflow(); syncWorkflowJSON(); toast(t('Node configuration applied.')); break; }
    case 'delete-node': captureWorkflow(); state.workflow.nodes = state.workflow.nodes.filter(node => node.id !== state.workflowSelection); state.workflow.edges = state.workflow.edges.filter(edge => edge.source !== state.workflowSelection && edge.target !== state.workflowSelection); state.workflowSelection = null; render(); break;
    case 'run-workflow': captureWorkflow(); if (state.dirty) await saveFile(); state.job = await api(projectURL('/workflows/run'),{method:'POST',body:{workflow:state.workflow}}); $('#workflow-job').innerHTML = jobHTML(state.job); toast(t('Bounded workflow started.')); break;
    case 'cancel-workflow': state.job = await api(projectURL(`/jobs/${encoded(state.job.id)}/cancel`),{method:'POST',body:{}}); $('#workflow-job').innerHTML = jobHTML(state.job); break;
    case 'cancel-evaluation': state.evaluation = await api(projectURL(`/jobs/${encoded(state.evaluation.id)}/cancel`),{method:'POST',body:{}}); $('#evaluation-job').innerHTML = jobHTML(state.evaluation,true); break;
    case 'detect-tools': await refreshStatus(); updateChrome(); render(); toast(t('Local toolchain checked.')); break;
    case 'test-provider': await saveSettings({test:true}); break;
    case 'clear-provider-key': await clearProviderKey(); break;
    case 'export-settings': download(pretty(state.settings),'analog-factory-safe-config.json'); break;
    case 'import-settings': $('#settings-file').click(); break;
  }
}
async function sendChat() { await agentUI.send(); }
function renderChat() { agentUI.render(); }
function zoomWaveform(factor, position = 0.5) {
  if (!state.waveform?.points?.length) return;
  const oldWidth = state.zoom[1]-state.zoom[0], width = Math.min(1,Math.max(0.001,oldWidth*factor));
  const center = state.zoom[0]+position*oldWidth, start = Math.max(0,Math.min(1-width,center-position*width));
  state.zoom = [start,start+width]; drawWaveform();
}
function panWaveform(amount) {
  if (!state.waveform?.points?.length) return;
  const width = state.zoom[1]-state.zoom[0], start = Math.max(0,Math.min(1-width,state.zoom[0]+width*amount));
  state.zoom = [start,start+width]; drawWaveform();
}
function moveWaveformCursor(direction) {
  const points = state.waveform?.points?.filter(point => Number.isFinite(point[0]));
  if (!points?.length) return;
  const axis = AFWaveforms.axis(points.map(row => row[0]),state.waveformOptions.x_scale);
  const minimum = axis.bounds[0], range = axis.bounds[1]-minimum || 1;
  const firstVisible = Math.max(0,points.findIndex(point => axis.forward(point[0])>=minimum+state.zoom[0]*range));
  const index = state.cursor?.index === undefined ? firstVisible : Math.max(0,Math.min(points.length-1,state.cursor.index+direction));
  state.cursor = {index};
  const position = (axis.forward(points[index][0])-minimum)/range, width = state.zoom[1]-state.zoom[0];
  if (position < state.zoom[0] || position > state.zoom[1]) { const start = Math.max(0,Math.min(1-width,position-width/2)); state.zoom = [start,start+width]; }
  $('#waveform-cursor')?.setAttribute('aria-live','polite'); drawWaveform();
}
function setupWaveform() {
  const canvas = $('#waveform'); if (!canvas) return;
  const waveform = state.waveform;
  const changeDisplay = values => safely(async () => { try { await AFOperations.dispatch('ui.waveform.set_display',values); } catch(error) { render(); throw error; } });
  $('#waveform-plot')?.addEventListener('change',event => safely(() => AFOperations.dispatch('ui.waveform.select_plot',{index:Number(event.target.value)})));
  $('#waveform-component')?.addEventListener('change',event => changeDisplay({component:event.target.value}));
  $('#waveform-x-scale')?.addEventListener('change',event => changeDisplay({x_scale:event.target.value}));
  $('#waveform-reference')?.addEventListener('change',event => changeDisplay({reference:event.target.value === '' ? null : Number(event.target.value)}));
  $('#waveform-unwrap')?.addEventListener('change',event => changeDisplay({unwrap:event.target.checked}));
  $('#trace-controls').innerHTML = waveform?.variables?.slice(1).map((variable,index) => html`<label><input type="checkbox" data-trace="${index+1}" ${state.traces.has(index+1) ? 'checked' : ''}><svg width="28" height="12" viewBox="0 0 28 12" aria-hidden="true"><path d="M0 6h28" stroke="${traceColors[index%traceColors.length]}" stroke-width="2" stroke-dasharray="${traceDashes[index%traceDashes.length].join(' ')}"/></svg>${esc(variable.name)}${variable.unit ? ` [${esc(variable.unit)}]` : ''}</label>`).join('') || '';
  $$('[data-trace]').forEach(input => input.addEventListener('change', () => { input.checked ? state.traces.add(Number(input.dataset.trace)) : state.traces.delete(Number(input.dataset.trace)); drawWaveform(); }));
  let drag = null;
  canvas.addEventListener('keydown', event => {
    if (event.ctrlKey || event.metaKey || event.altKey || !waveform?.points?.length) return;
    if (!['+','=','-','Home','ArrowLeft','ArrowRight','Escape'].includes(event.key)) return;
    event.preventDefault();
    $('#waveform-cursor').setAttribute('aria-live','polite');
    if (event.key === '+' || event.key === '=') zoomWaveform(0.8);
    else if (event.key === '-') zoomWaveform(1.25);
    else if (event.key === 'Home') { state.zoom = [0,1]; state.cursor = null; drawWaveform(); }
    else if (event.key === 'Escape') { state.cursor = null; drawWaveform(); }
    else if (event.shiftKey) panWaveform(event.key === 'ArrowLeft' ? -0.2 : 0.2);
    else moveWaveformCursor(event.key === 'ArrowLeft' ? -1 : 1);
  });
  canvas.addEventListener('wheel', event => {
    if (!state.waveform?.points?.length) return;
    event.preventDefault(); const rect = canvas.getBoundingClientRect(), position = Math.max(0,Math.min(1,(event.clientX-rect.left-waveformMargin.left)/Math.max(1,rect.width-waveformMargin.left-waveformMargin.right)));
    zoomWaveform(event.deltaY > 0 ? 1.25 : 0.8,position);
  },{passive:false});
  canvas.addEventListener('pointerdown', event => { if (event.button !== 0 || !waveform?.points?.length) return; drag = {x:event.clientX,zoom:[...state.zoom]}; canvas.setPointerCapture(event.pointerId); });
  canvas.addEventListener('pointermove', event => {
    if (!waveform?.points?.length) return;
    const rect = canvas.getBoundingClientRect();
    if (drag) { const width = drag.zoom[1]-drag.zoom[0], difference = (event.clientX-drag.x)/Math.max(1,rect.width-waveformMargin.left-waveformMargin.right)*width, start = Math.max(0,Math.min(1-width,drag.zoom[0]-difference)); state.zoom = [start,start+width]; }
    $('#waveform-cursor').setAttribute('aria-live','off');
    state.cursor = {x:event.clientX-rect.left,y:event.clientY-rect.top}; drawWaveform();
  });
  canvas.addEventListener('pointerup', () => { drag = null; });
  canvas.addEventListener('pointercancel', () => { drag = null; });
  canvas.addEventListener('pointerleave', () => { if (!drag && state.cursor?.index === undefined) { state.cursor = null; drawWaveform(); } });
  waveformResizeObserver?.disconnect();
  if (window.ResizeObserver) { waveformResizeObserver = new ResizeObserver(() => requestAnimationFrame(drawWaveform)); waveformResizeObserver.observe(canvas); }
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
  const waveform = state.waveform; if (!waveform?.points?.length || waveform.points.length < 2) { $('#waveform-summary').textContent = t('No waveform loaded'); return; }
  const points = waveform.points.filter(point => Number.isFinite(point[0])); if (points.length < 2) return;
  const margin = waveformMargin, plotW=width-margin.left-margin.right,plotH=height-margin.top-margin.bottom;
  const axis=AFWaveforms.axis(points.map(row => row[0]),state.waveformOptions.x_scale);
  const [fullMin,fullMax]=axis.bounds, range=fullMax-fullMin || 1;
  const minX=fullMin+range*state.zoom[0],maxX=fullMin+range*state.zoom[1], visible=points.filter(point => axis.forward(point[0])>=minX && axis.forward(point[0])<=maxX);
  let minY=Infinity,maxY=-Infinity;
  for (const point of visible) for (const index of state.traces) if (Number.isFinite(point[index])) { minY=Math.min(minY,point[index]); maxY=Math.max(maxY,point[index]); }
  if (!Number.isFinite(minY)) { minY=-1; maxY=1; }
  const pad=(maxY-minY)*0.1 || Math.abs(maxY)*0.1 || 1; minY-=pad; maxY+=pad;
  const x = value => margin.left+(axis.forward(value)-minX)/(maxX-minX)*plotW, y = value => margin.top+(maxY-value)/(maxY-minY)*plotH;
  $('#waveform-summary').textContent = t('{count} visible samples · {start} to {end} {unit} · {traces}', {count:number(visible.length),start:axisNumber(axis.inverse(minX)),end:axisNumber(axis.inverse(maxX)),unit:waveform.variables?.[0]?.unit || waveform.variables?.[0]?.name || '',traces:[...state.traces].map(index => waveform.variables?.[index]?.name).join(', ') || t('No traces selected')});
  $('[data-action="pan-left-waveform"]').disabled = state.zoom[0] <= 0;
  $('[data-action="pan-right-waveform"]').disabled = state.zoom[1] >= 1;
  $('[data-action="zoom-out-waveform"]').disabled = state.zoom[1]-state.zoom[0] >= 1;
  $('[data-action="zoom-in-waveform"]').disabled = state.zoom[1]-state.zoom[0] <= 0.001;
  context.font='11px ui-monospace, monospace'; context.lineWidth=1;
  const ticks = plotW < 360 ? 3 : 5;
  for (let index=0;index<=ticks;index++) { const gx=margin.left+plotW*index/ticks,gy=margin.top+plotH*index/ticks; context.strokeStyle=themeColor('--chart-grid'); context.beginPath(); context.moveTo(gx,margin.top); context.lineTo(gx,margin.top+plotH); context.stroke(); context.beginPath(); context.moveTo(margin.left,gy); context.lineTo(margin.left+plotW,gy); context.stroke(); context.fillStyle=themeColor('--chart-text'); context.textAlign='center'; context.fillText(axisNumber(axis.inverse(minX+(maxX-minX)*index/ticks)),gx,height-21); context.textAlign='right'; context.fillText(axisNumber(maxY-(maxY-minY)*index/ticks),margin.left-9,gy+3); }
  context.fillStyle=themeColor('--chart-axis'); context.textAlign='left'; context.fillText(waveform.variables?.[0]?.unit || waveform.variables?.[0]?.name || 'x',width-margin.right-13,height-7);
  const units=[...new Set([...state.traces].map(index => waveform.variables?.[index]?.unit).filter(Boolean))]; context.fillText(units.join(' / ') || t('signal'),margin.left,15);
  context.save(); context.beginPath(); context.rect(margin.left,margin.top,plotW,plotH); context.clip();
  // The server already bounds samples and retains extrema. A second uniform
  // stride here would discard narrow peaks preserved by that reduction.
  for (const index of state.traces) { context.strokeStyle=traceColors[(index-1)%traceColors.length]; context.setLineDash(traceDashes[(index-1)%traceDashes.length]); context.lineWidth=1.7; context.beginPath(); let started=false; for (let n=0;n<points.length;n++) { const point=points[n]; if (!Number.isFinite(point[index])) { started=false; continue; } if (!started) { context.moveTo(x(point[0]),y(point[index])); started=true; } else context.lineTo(x(point[0]),y(point[index])); } context.stroke(); }
  context.setLineDash([]);
  const cursorX = state.cursor?.index !== undefined && points[state.cursor.index] ? x(points[state.cursor.index][0]) : state.cursor?.x;
  if (Number.isFinite(cursorX) && cursorX>=margin.left && cursorX<=width-margin.right) {
    const target=axis.inverse(minX+(cursorX-margin.left)/plotW*(maxX-minX));
    let low=0,high=points.length-1; while (low<high) { const middle=Math.floor((low+high)/2); if (points[middle][0]<target) low=middle+1; else high=middle; }
    const point=points[state.cursor?.index ?? low],cx=x(point[0]); context.strokeStyle=themeColor('--chart-crosshair'); context.setLineDash([3,4]); context.beginPath(); context.moveTo(cx,margin.top); context.lineTo(cx,margin.top+plotH); context.stroke(); context.setLineDash([]);
    for (const index of state.traces) { if (!Number.isFinite(point[index])) continue; context.fillStyle=traceColors[(index-1)%traceColors.length]; context.beginPath(); context.arc(cx,y(point[index]),3,0,Math.PI*2); context.fill(); }
    $('#waveform-cursor').textContent=`${axisNumber(point[0])}${waveform.variables?.[0]?.unit || ''} · ${[...state.traces].map(index => `${waveform.variables?.[index]?.name}: ${axisNumber(point[index])}${waveform.variables?.[index]?.unit || ''}`).join(' · ')}`;
  } else if ($('#waveform-cursor')) $('#waveform-cursor').textContent = t('{count} visible samples', {count:number(visible.length)});
  context.restore();
}

// Expose the existing controllers through semantic operations. UI-only drafts
// remain owned by this browser; domain persistence continues through api().
function operationForm(name) {
  const roots = {simulation:'#simulation-form', verification:'#main-content', evaluation:'#evaluation-form', settings:'#settings-form', project_provider:'#project-provider-form', project_environment:'#project-environment-form', new_project:'#new-project-form', new_file:'#new-file-form', workflow:'#main-content'};
  const root = $(roots[name] || '#__unknown_form');
  if (!root || (name === 'verification' && state.tab !== 'verification') || (name === 'workflow' && state.tab !== 'workflows')) throw new Error('Open the requested form first.');
  return $$('input,select,textarea', root).filter(field => field.type !== 'password' && field.type !== 'file' && !field.readOnly && (name !== 'verification' || field.id.startsWith('verification-')) && (name !== 'workflow' || /^(workflow-|tuning-|limit-|node-|new-node-|edge-)/.test(field.id)));
}
function readOperationForm(name) {
  return Object.fromEntries(operationForm(name).map(field => [field.name || field.id, field.type === 'checkbox' ? field.checked : field.value]));
}
function setOperationForm(name, values) {
  if (!values || typeof values !== 'object' || Array.isArray(values)) throw new Error('Expected named form fields.');
  let fields = operationForm(name);
  if (name === 'simulation' && values.analysis !== undefined) {
    if (!['op','tran','dc','ac','noise'].includes(values.analysis)) throw new Error('Unknown analysis.');
    const template = document.createElement('template'); template.innerHTML = analysisFields(values.analysis);
    fields = [...fields.filter(field => !field.name.startsWith('param-')), ...template.content.querySelectorAll('input')];
  }
  for (const [key, value] of Object.entries(values)) {
    const field = fields.find(item => (item.name || item.id) === key);
    if (!field || field.disabled) throw new Error(`Field is unavailable: ${key}`);
    if (field.type === 'checkbox' ? typeof value !== 'boolean' : !['string','number'].includes(typeof value)) throw new Error(`Invalid field value: ${key}`);
    if (field.tagName === 'SELECT' && ![...field.options].some(option => option.value === String(value))) throw new Error(`Unknown option for ${key}`);
  }
  // Analysis changes construct a new set of parameter controls.
  if (name === 'simulation' && values.analysis !== undefined) {
    if (!['op','tran','dc','ac','noise'].includes(values.analysis)) throw new Error('Unknown analysis.');
    $('#analysis-select').value = values.analysis;
    $('#analysis-parameters').innerHTML = analysisFields(values.analysis);
  }
  for (const [key, value] of Object.entries(values)) {
    const field = operationForm(name).find(item => (item.name || item.id) === key);
    if (!field) throw new Error(`Field is unavailable for this analysis: ${key}`);
    if (field.type === 'checkbox') field.checked = value; else field.value = String(value);
    field.dispatchEvent(new Event('input', {bubbles:true}));
    field.dispatchEvent(new Event('change', {bubbles:true}));
  }
  return readOperationForm(name);
}
function setEditorDraft(content, retain = true) {
  if (!state.file || state.file === 'project.json') throw new Error('Select an editable project file first.');
  if (typeof content !== 'string' || new TextEncoder().encode(content).length > 2 * 1024 * 1024) throw new Error('Source text must fit within 2 MiB.');
  if (content !== state.content) browserDraftRevision++;
  state.content = content; state.dirty = state.content !== state.savedContent;
  if ($('#code-editor')) $('#code-editor').value = content;
  if ($('.line-numbers')) $('.line-numbers').textContent = lineNumbers(content);
  if ($('#file-save-status')) $('#file-save-status').textContent = state.dirty ? t('Unsaved changes · save with Ctrl / ⌘ S') : t('Saved locally');
  if (retain) return editorRecoveryUI.edited();
}
async function refreshOperationChanges(events, reset) {
  if (state.loadingProject) return;
  const relevant = reset || events.some(event => !event.project_id || event.project_id === state.project?.id);
  if (!relevant) return;
  // Updating project metadata and the clean editor is safe while form/source
  // drafts are present. Avoid render(), which replaces those controls.
  if (state.project) await refreshAgentProject(state.project.id);
  if (reset || events.some(event => !event.project_id)) {
    const [settings, , projects] = await Promise.all([api('/api/settings'), refreshStatus(), api('/api/projects')]);
    state.settings = settings; state.projects = asArray(projects, 'projects');
    if (!preferencesSaving && JSON.stringify(preferences.current()) !== JSON.stringify(preferences.normalize(settings.ui))) applyPreferences(settings.ui);
    else updateChrome();
    await agentUI.refreshCapabilities();
  } else { await refreshStatus(); updateChrome(); }
  await agentCLIUI.sync(events,reset);
}
function registerWorkspaceOperations() {
  const operations = window.AFOperations;
  if (!operations) return;
  const requireProject = () => { if (!state.project) throw new Error('Select a project first.'); };
  const requireApplied = (condition, selection) => { if (!condition) { const error = new Error(`Frontend state version conflict: ${selection} changed before the command completed.`); error.code = 'VERSION_CONFLICT'; throw error; } };
  const workflowNode = id => { const node = state.workflow?.nodes.find(item => item.id === id); if (!node) throw new Error('Unknown workflow node.'); return node; };
  const workflowViewRequired = (capture = true) => { if (state.tab !== 'workflows') throw new Error('Open Workflows first.'); if (capture) captureWorkflow(); };
  operations.register('workspace', {
    continue_design:continueDesign,
    navigate:async ({tab}) => { if (!['overview','editor','desktop','simulation','verification','workflows','evaluation','settings'].includes(tab)) throw new Error('Unknown workspace view.'); await setTab(tab); requireApplied(state.tab === tab, 'Workspace view'); },
    select_project:async ({project_id}) => { if (project_id && !state.projects.some(item => item.id === project_id)) { state.projects = asArray(await api('/api/projects'), 'projects'); if (!state.projects.some(item => item.id === project_id)) throw new Error('Unknown project.'); } await selectProject(project_id || ''); requireApplied((state.project?.id || '') === (project_id || ''), 'Selected project'); },
    refresh:() => { requireProject(); return refreshProject(); },
    set_context:({paths}) => { requireProject(); if (!Array.isArray(paths) || paths.length > 20 || paths.some(path => !state.files.some(file => file.path === path))) throw new Error('Select at most 20 existing project files.'); state.context = new Set(paths); renderTree(); updateContext(); agentUI.render(); },
    set_folder:({path, open}) => { const folder = $$('[data-folder]', $('#file-tree')).find(item => item.dataset.folder === path); if (!folder) throw new Error('Unknown project folder.'); folder.open = Boolean(open); },
    dialog:async ({name, open}) => { if (name === 'new_project') { if (open) await openProjectDialog(); else closeProjectDialog(); return; } const dialog = name === 'new_file' ? $('#new-file-dialog') : null; if (name === 'new_file' && open && state.project) draftVersions.set('new_file', window.AFOperations?.version(projectURL())); if (!dialog) throw new Error('Unknown dialog.'); if (open && !dialog.open) dialog.showModal(); else if (!open && dialog.open) dialog.close(); },
    set_preferences:async ({language,theme}) => { if (preferencesSaving) throw new Error('A preference save is already running.'); const before = preferences.current(); if (language !== undefined && !['en','zh-CN'].includes(language) || theme !== undefined && !['light','dark'].includes(theme)) throw new Error('Unsupported preference.'); $('#ui-language').value = language ?? before.language; $('#ui-theme').value = theme ?? before.theme; await savePreferences(); if (preferences.current().language !== (language ?? before.language) || preferences.current().theme !== (theme ?? before.theme)) throw new Error('Preference save failed.'); },
    reconnect_desktop:() => handleAction('reload-desktop'),
    disclosures:() => $$('[data-operation-disclosure]').map(element => ({id:element.dataset.operationDisclosure,open:element.open,label:element.querySelector('summary')?.textContent.trim() || '',hidden:element.hidden})),
    set_disclosure:({id,open}) => { const element = $$('[data-operation-disclosure]').find(item => item.dataset.operationDisclosure === id); if (!element || element.hidden) throw new Error('Disclosure is unavailable in the current view.'); element.open = Boolean(open); },
    scroll:({panel,position,axis = 'vertical'}) => { const panels = {workspace:'#main-content',desktop:'#desktop-panel',explorer:'#file-tree',editor:'#code-editor',log:'#execution-log',conversation:'#chat-messages',history:'#agent-history-list',workflow:'.workflow-board'}; const element = $(panels[panel] || '#__unknown_panel'); if (!element || !element.getClientRects().length) throw new Error('Panel is unavailable in the current view.'); if (!['start','end'].includes(position) || !['vertical','horizontal'].includes(axis)) throw new Error('Unknown scroll position or axis.'); if (axis === 'horizontal') element.scrollLeft = position === 'start' ? 0 : element.scrollWidth; else element.scrollTop = position === 'start' ? 0 : element.scrollHeight; element.dispatchEvent(new Event('scroll')); },
    read:() => operations.snapshot()
  }, () => ({project_id:state.project?.id || null, tab:state.tab, files:state.files.map(file => file.path), context:[...state.context], run_id:state.run?.id || null, connected:state.connected, preferences:preferences.current(), draft_revision:browserDraftRevision, disclosures:$$('[data-operation-disclosure]').map(element => ({id:element.dataset.operationDisclosure,open:element.open})), folders:$$('[data-folder]', $('#file-tree')).map(element => ({path:element.dataset.folder,open:element.open})), dialogs:['new_project','new_file'].filter(name => $(name === 'new_project' ? '#new-project-dialog' : '#new-file-dialog').open)}));
  operations.register('form', {read:({form}) => { if (form === 'settings') throw new Error('Use the settings operation.'); return readOperationForm(form); }, set:({form,values}) => { if (form === 'settings') throw new Error('Use ui.settings.set_draft.'); return setOperationForm(form, values); }});
  operations.register('settings', {set_draft:({values}) => setOperationForm('settings',values),
    project_environment_read:()=>readOperationForm('project_environment'),
    project_environment_draft:({values})=>setOperationForm('project_environment',values),
    project_environment_save:()=>saveProjectEnvironment(),project_environment_reset:()=>saveProjectEnvironment(true),
    project_provider_read:()=>readOperationForm('project_provider'),
    project_provider_draft:({values})=>setOperationForm('project_provider',values),
    project_provider_save:()=>saveProjectProvider(),project_provider_reset:()=>saveProjectProvider(true)});
  operations.register('editor', {
    open:async ({path}) => { requireProject(); const projectId = state.project.id; if (!state.files.some(file => file.path === path)) throw new Error('Unknown project file.'); await openFile(path); requireApplied(state.project?.id === projectId && state.file === path && state.tab === 'editor', 'Selected source file'); },
    set_content:({content}) => setEditorDraft(content),
    read:() => ({path:state.file,content:state.content,dirty:state.dirty,readonly:state.file === 'project.json'}),
    save:() => { if (!state.file || state.file === 'project.json') throw new Error('Select an editable file first.'); return saveFile(); },
    history:async () => { requireProject(); if (!state.file) throw new Error('Select a file first.'); await handleAction('history'); return state.history; }
  }, () => ({path:state.file,dirty:state.dirty,readonly:state.file === 'project.json'}));
  operations.register('editor_recovery', editorRecoveryUI.commands, editorRecoveryUI.snapshot);
  operations.register('runs', {
    netlist_reference:() => { requireProject(); if (state.project.template !== 'ota5') throw new Error('Select the OTA reference project.'); return startRun('xschem',{netlist_only:true}); },
    derive_reference:deriveReferenceNetlists,
    simulate_reference_post:() => { requireProject(); if (state.project.template !== 'ota5') throw new Error('Select the OTA reference project.'); return startRun('simulate',{netlist:designDefaults().post_netlist}); },
    select:async ({run_id}) => { requireProject(); const projectId = state.project.id; await selectRun(run_id); requireApplied(state.project?.id === projectId && state.run?.id === run_id, 'Selected run'); },
    compare:({baseline,comparison}) => { const eligible = window.AFResultComparison.eligibleRuns(state.runs); for (const id of [baseline,comparison]) if (!eligible.some(run => run.id === id)) throw new Error('Choose successful simulations with recorded measurements.'); state.compare = [baseline,comparison]; if ($('#comparison-results')) { $('#compare-a').value = baseline; $('#compare-b').value = comparison; $('#comparison-results').innerHTML = comparisonResults(); } return comparisonResults(); },
    inspect_metric:({name}) => { if (!state.run) throw new Error('Select a run first.'); const metric = metricEntries().find(([key]) => key === name); if (!metric) throw new Error('Unknown metric.'); const evidence = {metric:name,value:metric[1],provenance:state.run.metric_provenance?.[name],run_id:state.run.id}; if ($('#metric-evidence')) $('#metric-evidence').textContent = pretty(evidence); $('.log-panel').classList.remove('collapsed'); return evidence; },
    copy_log:async () => { await navigator.clipboard.writeText(state.log || $('#execution-log').textContent); }
  }, () => ({selected:state.run?.id || null, comparison:[...state.compare], job_id:state.job?.id || null,evaluation_id:state.evaluation?.id || null}));
  operations.register('waveform', {
    select_plot:({index}) => selectWaveformPlot(index),
    set_display:options => setWaveformDisplay(options),
    set_traces:({indices}) => { if (!state.waveform) throw new Error('Load a waveform first.'); if (!Array.isArray(indices) || indices.some(index => !Number.isInteger(index) || index < 1 || index >= state.waveform.variables.length)) throw new Error('Unknown waveform trace.'); state.traces = new Set(indices); if ($('#waveform')) setupWaveform(); },
    set_view:({start,end}) => { if (!state.waveform || !Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end > 1 || end-start < .001) throw new Error('Expected normalized waveform bounds within [0, 1].'); state.zoom = [start,end]; drawWaveform(); },
    set_cursor:({index}) => { if (index !== null && (!Number.isInteger(index) || index < 0 || index >= (state.waveform?.points?.length || 0))) throw new Error('Unknown waveform sample.'); state.cursor = index === null ? null : {index}; drawWaveform(); },
    zoom:({factor,position = .5}) => { if (!state.waveform) throw new Error('Load a waveform first.'); if (!Number.isFinite(factor) || factor <= 0 || position < 0 || position > 1) throw new Error('Invalid zoom.'); zoomWaveform(factor,position); },
    pan:({amount}) => { if (!state.waveform) throw new Error('Load a waveform first.'); if (!Number.isFinite(amount)) throw new Error('Invalid pan.'); panWaveform(amount); },
    reset:() => { if (!state.waveform) throw new Error('Load a waveform first.'); return handleAction('reset-waveform'); },
    export:() => { if (!state.waveform) throw new Error('Load a waveform first.'); return handleAction('export-waveform'); }
  }, () => ({loaded:Boolean(state.waveform),...state.waveformOptions,traces:[...state.traces],zoom:[...state.zoom],cursor:state.cursor,sample:state.cursor?.index ?? null}));
  operations.register('lifecycle',lifecycleUI.commands,lifecycleUI.snapshot);
  operations.register('simulation_batch',simulationBatchUI.commands,simulationBatchUI.snapshot);
  operations.register('characterization',characterizationUI.commands,characterizationUI.snapshot);
  operations.register('training',trainingUI.commands,trainingUI.snapshot);
  operations.register('agent_cli',agentCLIUI.commands,agentCLIUI.snapshot);
  operations.register('native_terminal',nativeTerminalUI.commands,nativeTerminalUI.snapshot);
  operations.register('native_canvas',nativeCanvasUI.commands,nativeCanvasUI.snapshot);
  operations.register('drc_navigation',drcNavigationUI.commands,drcNavigationUI.snapshot);
  operations.register('workflow', {
    read:() => { if (state.tab === 'workflows') captureWorkflow(); return state.workflow; },
    new_graph:({schema_version=2}) => { workflowViewRequired(false); if (![1,2].includes(schema_version)) throw new Error('Unknown workflow schema version.'); state.workflow = schema_version === 1 ? defaultWorkflow() : window.AFGraphControls.newWorkflow(); state.workflowSelection = null; render(); },
    set_graph:({workflow}) => { workflowViewRequired(false); state.workflow = validateWorkflowShape(structuredClone(workflow)); state.workflowSelection = null; render(); },
    select_node:({node_id}) => { workflowViewRequired(); workflowNode(node_id); state.workflowSelection = node_id; $('#node-configuration').innerHTML = nodeConfiguration(); drawWorkflow(); },
    add_node:({type}) => { workflowViewRequired(); const available = state.workflow.schema_version === 2 ? nodeCatalog : nodeCatalog.slice(0,6); if (!available.some(item => item[0] === type)) throw new Error('This node type requires a v2 graph.'); $('#new-node-type').value = type; return handleAction('add-node'); },
    update_node:({node_id,config,position,...policy}) => { workflowViewRequired(); const node = workflowNode(node_id), updated = structuredClone(node); if (state.workflow.schema_version !== 2 && Object.keys(policy).length) throw new Error('Typed mappings and node policies require a v2 graph.'); for (const [key,value] of Object.entries({config,...policy})) { if (value === undefined) continue; if (['config','inputs','outputs'].includes(key) && (!value || typeof value !== 'object' || Array.isArray(value))) throw new Error('Node configuration and mappings must be objects.'); updated[key] = structuredClone(value); } if (position) { if (!Number.isFinite(position.x) || !Number.isFinite(position.y)) throw new Error('Invalid node position.'); updated.position = {x:Math.max(8,Math.min(900,position.x)), y:Math.max(8,Math.min(450,position.y))}; } Object.assign(node,updated); drawWorkflow(); $('#node-configuration').innerHTML = nodeConfiguration(); syncWorkflowJSON(); },
    remove_node:({node_id}) => { workflowViewRequired(); workflowNode(node_id); state.workflowSelection = node_id; return handleAction('delete-node'); },
    connect:({source,target,when,on}) => { workflowViewRequired(); workflowNode(source); workflowNode(target); if (state.workflow.schema_version !== 2 && (when !== undefined || on !== undefined)) throw new Error('Conditional connections require a v2 graph.'); $('#edge-from').value = source; $('#edge-to').value = target; if ($('#edge-when')) $('#edge-when').value = when === undefined ? '' : String(when); if ($('#edge-on')) $('#edge-on').value = on || 'success'; return handleAction('add-edge'); },
    disconnect:({source,target,when,on}) => { workflowViewRequired(); const matches = state.workflow.edges.map((edge,index)=>({edge,index})).filter(({edge})=>edge.source === source && edge.target === target && (when === undefined || edge.when === when) && (on === undefined || (edge.on || 'success') === on)); if (!matches.length) throw new Error('Unknown workflow connection.'); if (matches.length > 1) throw new Error('Select the branch condition to remove one of these connections.'); state.workflow.edges.splice(matches[0].index,1); drawWorkflow(); $('#node-configuration').innerHTML = nodeConfiguration(); syncWorkflowJSON(); },
    save:() => { workflowViewRequired(); return handleAction('save-workflow'); },
    list_saved:async () => { workflowViewRequired(); await handleAction('load-workflow'); return state.savedWorkflows; },
    export:({format='json'}={}) => { workflowViewRequired(); return exportWorkflowDocument(format); },
    apply_document:({content,format}) => importWorkflowDocument(content,format),
    apply_json:({content}) => { workflowViewRequired(false); state.workflow = validateWorkflowShape(JSON.parse(content)); state.workflowSelection = null; render(); }
  }, () => ({selected_node:state.workflowSelection,name:state.workflow?.name || null,schema_version:state.workflow?.schema_version || null,nodes:state.workflow?.nodes?.length || 0,draft_revision:workflowDraftRevision}));
}

initialize().catch(error => { $('#main-content').innerHTML = html`<div class="empty-state"><span class="empty-symbol">!</span><h1>The workspace could not open.</h1><p>${esc(error.message)}</p><button class="secondary-button" id="retry-connection">Retry connection</button></div>`; toast(error.message,true); $('#retry-connection')?.addEventListener('click', () => location.reload()); }).finally(() => { document.body.inert = false; document.body.removeAttribute('aria-busy'); });
