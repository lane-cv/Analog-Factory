/* New projects inherit workspace settings; this check never uses a selected project's overrides. */
((root) => {
  'use strict';
  const labels = {ngspice: 'Circuit simulator (ngspice)', xschem: 'Schematic editor (optional)',
    magic: 'Layout editor (optional)', netgen: 'LVS comparator (optional)', desktop: 'Drawing desktop (optional)'};
  function assess(template, status) {
    const scoped = Boolean(status && status.project_id != null);
    const toolchain = !scoped && status && !status.error ? status.toolchain : null;
    const known = Boolean(toolchain && toolchain.tools && typeof toolchain.tools.ngspice?.available === 'boolean');
    const checks = [];
    function add(id, label, data, required = false) {
      const state = data?.available === true ? 'ready' : data?.available === false ? 'missing' : 'unknown';
      checks.push({id, label, status: state, required,
        reason: typeof data?.diagnostic === 'string' && data.diagnostic ? data.diagnostic :
          state === 'ready' ? 'Detected and ready.' : state === 'missing' ? 'Not configured or unavailable.' :
          scoped ? 'Check workspace settings, without project overrides.' : 'Environment check is unavailable. Refresh to try again.'});
      return state === 'ready';
    }
    const simulatorReady = add('ngspice', labels.ngspice, toolchain?.tools?.ngspice);
    let canCreate = template === 'amplifier' || template === 'sky130';
    let canSimulate = template === 'amplifier' && simulatorReady;
    if (template === 'ota5') {
      let assets = toolchain?.pdk?.ota5;
      // A generic PDK flag does not qualify the distinct OTA combined-model library.
      if (assets?.available === true && !['models', 'xschem', 'magic', 'netgen'].every(key =>
        typeof assets.paths?.[key] === 'string' && assets.paths[key].trim())) assets = null;
      canCreate = add('ota5_assets', 'Prepared SKY130 OTA assets', assets, true);
      const simulationPdkReady = add('pdk', 'Prepared SKY130 PDK', toolchain?.pdk);
      canSimulate = canCreate && simulatorReady && simulationPdkReady;
    } else if (template === 'sky130') {
      add('pdk', 'Prepared SKY130 PDK', toolchain?.pdk);
      checks.push({id: 'model_library', label: 'Project model-library path', status: 'action_required', required: false,
        reason: 'After creating this starter, replace the model-library placeholder in testbenches/pre.cir before simulation.'});
    }
    for (const name of ['xschem', 'magic', 'netgen']) add(name, labels[name], toolchain?.tools?.[name]);
    add('desktop', labels.desktop, toolchain?.desktop);
    const summary = !['amplifier', 'sky130', 'ota5'].includes(template) ? 'Select a supported project template.' :
      !canCreate ? 'Prepare the required PDK assets before creating this OTA project.' :
      canSimulate ? 'Ready to create and simulate. Native editors are optional.' :
      template === 'sky130' ? 'You can create this starter now. Configure its model-library path before simulation.' :
      'You can create this project now. Complete the simulation requirements before running it.';
    return {template, canCreate, canSimulate, known, checks, summary};
  }
  const api = {assess};
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AFProjectReadiness = api;
})(typeof window === 'undefined' ? globalThis : window);
