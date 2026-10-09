/* Shared visual provider fields. Validation authority remains the registry. */
((root) => {
  'use strict';
  const parameters=['temperature','top_p','max_tokens','seed','stop','reasoning_effort'];
  const publicNames=['name','protocol','base_url','model','api_key_env','timeout','retries','concurrency','stream','capabilities','model_capabilities','generation'];
  function publicConfig(value) {
    return Object.fromEntries(publicNames.filter(key=>Object.hasOwn(value,key)).map(key=>[key,structuredClone(value[key])]));
  }
  function advanced(provider={}, {prefix='',esc,t=value=>value}={}) {
    const capabilities=provider.capabilities || {}, generation=provider.generation || {};
    const supported=new Set(capabilities.generation_parameters || []);
    const check=(name,label,checked)=>`<label class="check-label"><input type="checkbox" name="${prefix+name}" ${checked?'checked':''}>${esc(t(label))}</label>`;
    const numeric=(name,label,value,min,max)=>`<label>${esc(t(label))}<input name="${prefix+name}" type="number" min="${min}" max="${max}" step="any" value="${esc(value??'')}"></label>`;
    const controls=parameters.map(name=>{
      const key=prefix+'generation_'+name, disabled=supported.has(name)?'':'disabled';
      const input=name==='reasoning_effort' ? `<select name="${key}" ${disabled}><option value="">${esc(t('Provider default'))}</option>${['low','medium','high'].map(level=>`<option ${generation[name]===level?'selected':''}>${level}</option>`).join('')}</select>` :
        name==='stop' ? `<textarea name="${key}" rows="2" ${disabled}>${esc((generation.stop||[]).join('\n'))}</textarea>` :
        `<input name="${key}" type="number" step="any" value="${esc(generation[name]??'')}" ${disabled}>`;
      return `<div>${check('cap_'+name,`Supports ${name}`,supported.has(name))}<label>${esc(name==='stop'?t('Stop sequences (one per line)'):name)}${input}</label></div>`;
    }).join('');
    return `<fieldset class="spaced" data-provider-advanced="${esc(prefix)}"><legend>${esc(t('Model capabilities and request controls'))}</legend><p class="footnote">${esc(t('Declare only features documented by this provider for the selected model. Unsupported settings are rejected before inference.'))}</p><div class="form-grid">${numeric('retries','Transient HTTP retries',provider.retries??0,0,3)}${numeric('concurrency','Maximum concurrent requests',provider.concurrency??4,1,4)}</div><div class="button-row">${check('cap_streaming','Supports streaming',capabilities.streaming)}${check('cap_tools','Supports tool calls',capabilities.tools!==false)}${check('cap_images','Supports images',capabilities.images)}${check('stream','Enable streaming',provider.stream)}</div><div class="form-grid">${controls}</div><details class="spaced"><summary>${esc(t('Capabilities for other model identifiers'))}</summary><label>${esc(t('Per-model capability declarations (JSON object)'))}<textarea name="${prefix}model_capabilities" rows="5" spellcheck="false">${esc(JSON.stringify(provider.model_capabilities||{},null,2))}</textarea></label></details></fieldset>`;
  }
  function readAdvanced(form,prefix='') {
    const result={retries:Number(form.get(prefix+'retries')||0),concurrency:Number(form.get(prefix+'concurrency')||4),
      stream:form.has(prefix+'stream'),capabilities:{streaming:form.has(prefix+'cap_streaming'),tools:form.has(prefix+'cap_tools'),images:form.has(prefix+'cap_images'),generation_parameters:[]},generation:{}};
    for(const name of parameters) {
      if(!form.has(prefix+'cap_'+name))continue;
      result.capabilities.generation_parameters.push(name);
      const value=String(form.get(prefix+'generation_'+name)||'').trim();
      if(!value)continue;
      result.generation[name]=name==='stop'?value.split('\n').map(item=>item.trim()).filter(Boolean):name==='reasoning_effort'?value:Number(value);
      if(typeof result.generation[name]==='number'&&!Number.isFinite(result.generation[name]))throw new Error('Generation parameter must be finite.');
    }
    result.model_capabilities=JSON.parse(String(form.get(prefix+'model_capabilities')||'{}'));
    if(!result.model_capabilities||typeof result.model_capabilities!=='object'||Array.isArray(result.model_capabilities))throw new Error('Model capabilities must be a JSON object.');
    return result;
  }
  function update(host) {
    for (const box of host?.querySelectorAll('[data-provider-advanced]') || []) {
      const prefix=box.dataset.providerAdvanced;
      for(const name of parameters) {
        const input=box.querySelector(`[name="${prefix}generation_${name}"]`);
        input.disabled=!box.querySelector(`[name="${prefix}cap_${name}"]`).checked;
      }
    }
  }
  function bind(host) {
    host?.addEventListener('change',()=>update(host));
    update(host);
  }
  const api={advanced,readAdvanced,publicConfig,bind,update};
  if(typeof module==='object'&&module.exports)module.exports=api;else root.AFProviderFields=api;
})(typeof window==='undefined'?globalThis:window);
