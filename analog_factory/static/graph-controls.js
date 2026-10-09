'use strict';

// Schema-v2 editor fragments and live job controls. The main workspace owns
// draft selection/rendering; every execution command goes through the registry.
window.AFGraphControls = (() => {
  const {t, html} = window.AFI18n;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pretty = value => JSON.stringify(value, null, 2);
  const catalog = [
    ['specification','Design specification',true], ['parameter','Parameter value',true],
    ['simulate','ngspice simulation',true], ['measure','Metric extraction',true],
    ['condition','Conditional branch',true], ['report','Result report',true],
    ['input','Workflow input',true], ['project','Project retrieval',true],
    ['model','Model inference',true], ['netlist','Netlist processing',true],
    ['drc','DRC',true], ['lvs','LVS',true], ['extract','Parasitic extraction',true],
    ['loop','Bounded loop',true], ['human_review','Human review',true],
    ['source_patch','Reviewed source patch',true]
  ];
  function newWorkflow() {
    return {schema_version:2,name:'Simulation and measurement',inputs:{},outputs:{},
      nodes:[
        {id:'simulate',type:'simulate',config:{options:{netlist:'testbenches/pre.cir'}},inputs:{},position:{x:40,y:50}},
        {id:'measure',type:'measure',config:{metric:'vout_mean'},inputs:{result:{node:'simulate',port:'result'}},position:{x:285,y:50}},
        {id:'report',type:'report',config:{title:'Measured circuit result'},inputs:{value:{node:'measure',port:'value'},run_id:{node:'simulate',port:'run_id'}},position:{x:530,y:50}}
      ],edges:[{source:'simulate',target:'measure'},{source:'measure',target:'report'}],
      limits:{runtime_seconds:120,max_node_executions:1000,max_iterations:100},debug:{step:false}};
  }
  function objectField(root, id) {
    const element = root.querySelector(`#${id}`);
    if (!element) return undefined;
    const value = JSON.parse(element.value || '{}');
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Mappings must be JSON objects.');
    return value;
  }
  function nodeFields(node) {
    return html`<div class="form-grid spaced"><label>Input mappings<textarea id="node-inputs" class="directives" spellcheck="false">${esc(pretty(node.inputs || {}))}</textarea></label><label>Output aliases<textarea id="node-outputs" class="directives" spellcheck="false">${esc(pretty(node.outputs || {}))}</textarea></label><label>Retries<input id="node-retries" type="number" min="0" max="5" value="${Number(node.retries || 0)}"></label><label>Timeout (seconds)<input id="node-timeout" type="number" min="0.1" max="3600" step="any" value="${Number(node.timeout_seconds || 60)}"></label><label>On failure<select id="node-on-error"><option value="fail" ${node.on_error !== 'continue' ? 'selected' : ''}>Stop unless a failure edge handles it</option><option value="continue" ${node.on_error === 'continue' ? 'selected' : ''}>Continue and retain failure evidence</option></select></label></div><p class="footnote">Map a value with {"value": ...} or connect an output with {"node": "simulate", "port": "metrics"}. Output aliases select JSON paths and declare a type.</p>`;
  }
  function applyNodeFields(node, root = document) {
    const inputs = objectField(root, 'node-inputs'), outputs = objectField(root, 'node-outputs');
    if (inputs !== undefined) node.inputs = inputs;
    if (outputs !== undefined) node.outputs = outputs;
    if (root.querySelector('#node-retries')) node.retries = Number(root.querySelector('#node-retries').value);
    if (root.querySelector('#node-timeout')) node.timeout_seconds = Number(root.querySelector('#node-timeout').value);
    if (root.querySelector('#node-on-error')) node.on_error = root.querySelector('#node-on-error').value;
    return node;
  }
  function limitsHTML(flow) {
    const limits = flow.limits || {};
    return html`<div class="card"><h2>Execution limits</h2><div class="form-grid"><label>Runtime (seconds)<input id="limit-runtime" type="number" min="0.1" max="3600" value="${Number(limits.runtime_seconds || 120)}"></label><label>Maximum node attempts<input id="limit-node-executions" type="number" min="1" max="10000" value="${Number(limits.max_node_executions || 1000)}"></label><label>Total loop iterations<input id="limit-graph-iterations" type="number" min="1" max="1000" value="${Number(limits.max_iterations || 100)}"></label><label>Model cost limit (USD, optional)<input id="limit-cost" type="number" min="0" step="any" value="${esc(limits.cost_usd ?? '')}"></label><label><input id="workflow-debug-step" type="checkbox" ${flow.debug?.step ? 'checked' : ''}> Pause before the first node</label></div><p class="footnote">A required cost limit blocks model calls when provider cost accounting is unavailable. Pauses, loops and retries consume the same runtime budget.</p></div>`;
  }
  function captureLimits(flow, root = document) {
    if (flow.schema_version !== 2) return flow;
    const fields = [['runtime_seconds','limit-runtime'],['max_node_executions','limit-node-executions'],['max_iterations','limit-graph-iterations']];
    flow.limits ||= {};
    for (const [key,id] of fields) if (root.querySelector(`#${id}`)) flow.limits[key] = Number(root.querySelector(`#${id}`).value);
    const cost = root.querySelector('#limit-cost');
    if (cost) { if (cost.value.trim() === '') delete flow.limits.cost_usd; else flow.limits.cost_usd = Number(cost.value); }
    const step = root.querySelector('#workflow-debug-step');
    if (step) flow.debug = {step:step.checked};
    return flow;
  }
  function edgeFields() {
    return html('<label>Branch<select id="edge-when"><option value="">Any result</option><option value="true">True / accepted</option><option value="false">False / rejected</option></select></label><label>After<select id="edge-on"><option value="success">Success</option><option value="failure">Failure</option><option value="always">Any completed outcome</option></select></label>');
  }
  function captureEdge(edge, root = document) {
    const when = root.querySelector('#edge-when')?.value;
    const on = root.querySelector('#edge-on')?.value;
    if (when === 'true' || when === 'false') edge.when = when === 'true';
    if (on && on !== 'success') edge.on = on;
    return edge;
  }
  function jobHTML(job) {
    if (!job || (job.schema_version !== 2 && job.workflow?.schema_version !== 2)) return '';
    const active = ['queued','running'].includes(job.status), review = job.pending_review;
    const controls = active ? html`<div class="workflow-toolbar" aria-label="Graph execution controls"><button class="secondary-button" data-graph-action="step">Step one node</button><button class="secondary-button" data-graph-action="continue">Continue</button><button class="secondary-button" data-graph-action="pause">Pause at next node</button></div>` : '';
    const patch = review?.kind === 'source_patch';
    const reviewEvidence = patch ? html`<p>${esc(review.value.path)}</p><p class="footnote">${esc(review.value.current_sha256)} → ${esc(review.value.candidate_sha256)}</p><pre class="evidence-details">${esc(review.value.diff)}</pre><details><summary>Inspect complete proposal</summary><pre class="evidence-details">${esc(pretty(review.value))}</pre></details>` : html`<pre class="evidence-details">${esc(pretty(review?.value))}</pre>`;
    const reviewButtons = active && review ? html`<button class="primary-button" data-graph-action="review" data-graph-node="${esc(review.node_id)}" ${patch ? `data-graph-review-token="${esc(review.review_token)}"` : ''} data-graph-approved="true">${t(patch ? 'Apply reviewed change' : 'Accept')}</button> <button class="secondary-button" data-graph-action="review" data-graph-node="${esc(review.node_id)}" ${patch ? `data-graph-review-token="${esc(review.review_token)}"` : ''} data-graph-approved="false">Reject</button>` : '';
    const checkpoint = review ? html`<div class="callout"><h3>Human review · ${esc(review.node_id)}</h3><p>${esc(review.prompt)}</p>${reviewEvidence}${reviewButtons}</div>` : '';
    const nodes = Object.entries(job.nodes || {}).map(([id, record]) => html`<tr><td>${esc(id)}</td><td>${esc(t(record.status))}</td><td>${Number(record.attempts || 0)}</td><td><details><summary>Inspect node evidence</summary><pre class="evidence-details">${esc(pretty({inputs:record.inputs,outputs:record.outputs,error:record.error,logs:record.logs,child:record.child,cached:record.cached}))}</pre>${record.live_log ? html`<pre class="evidence-details">${esc(record.live_log)}</pre>` : ''}</details></td></tr>`).join('');
    return html`<section class="card spaced" data-graph-job="${esc(job.id)}"><div class="card-head"><h2>Graph execution</h2><span role="status">${esc(job.debug_state === 'paused' && active ? t('Paused before {node}', {node:job.active_node}) : t(job.status))}</span></div>${controls}${checkpoint}<p>${t(active ? 'Live execution' : 'Historical execution')} · ${Number(job.node_executions || 0)} node attempts · ${Number(job.loop_iterations || 0)} loop iterations · ${esc(job.termination || '')}</p><div class="table-wrap"><table class="data-table"><thead><tr><th>Node</th><th>State</th><th>Attempts</th><th>Evidence</th></tr></thead><tbody>${nodes}</tbody></table></div>${job.error ? html`<p class="callout warning">${esc(job.error)}</p>` : ''}<p class="footnote">${t(job.cost_accounting === 'unavailable' ? 'Model monetary cost is unavailable.' : 'No model monetary cost recorded.')} Inspecting this record does not rerun it.</p><p data-graph-error role="alert"></p></section>`;
  }
  function bind(root, {projectId, onUpdate, onError} = {}) {
    if (!root || root.dataset.graphBound) return;
    root.dataset.graphBound = 'true';
    root.addEventListener('click', async event => {
      const button = event.target.closest('[data-graph-action]');
      if (!button || !root.contains(button) || button.disabled) return;
      const section = button.closest('[data-graph-job]');
      const pid = typeof projectId === 'function' ? projectId() : projectId;
      if (!section || !pid) return;
      event.preventDefault();
      const parameters = {project_id:pid,job_id:section.dataset.graphJob,action:button.dataset.graphAction};
      if (parameters.action === 'review') Object.assign(parameters, {node_id:button.dataset.graphNode,approved:button.dataset.graphApproved === 'true'});
      if (parameters.action === 'review' && button.dataset.graphReviewToken) parameters.review_token = button.dataset.graphReviewToken;
      button.disabled = true;
      try {
        await window.AFOperations.invoke('job.control', parameters);
        const result = await window.AFOperations.invoke('job.get', {project_id:pid,job_id:parameters.job_id});
        if (typeof projectId !== 'function' || projectId() === pid) await onUpdate?.(result.data);
      } catch (error) {
        const target = section.querySelector('[data-graph-error]');
        if (target) target.textContent = error.message;
        onError?.(error);
      } finally { button.disabled = false; }
    });
  }
  return {catalog,newWorkflow,nodeFields,applyNodeFields,limitsHTML,captureLimits,edgeFields,captureEdge,jobHTML,bind};
})();
