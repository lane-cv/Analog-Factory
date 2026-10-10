/* Pure, project-scoped task entry resolution. Navigation never publishes evidence. */
((root) => {
  'use strict';
  const defaults = {schematic:'schematics/amplifier.sch',layout:'layout/amplifier.mag',
    netlist:'testbenches/pre.cir',post_netlist:'testbenches/post.cir',
    schematic_netlist:'netlists/schematic.spice',layout_netlist:'netlists/extracted.spice',cell:'amplifier'};
  const ids = ['specification','schematic','pre','layout','drc','extract','lvs','post','archive'];
  const active = run => ['running','queued','starting','pending'].includes(run.status);
  function resolve(input, requested) {
    if (!ids.includes(requested)) throw new Error('Unknown design stage.');
    if (!input.project) throw new Error('Select a project first.');
    const design = {...defaults,...input.project.design}, ota = input.project.template === 'ota5';
    const paths = new Set((input.files || []).map(file=>file.path));
    const stages = input.designProgress || [], runs = input.runs || [];
    const scoped = input.status?.project_id === input.project.id;
    const action = (type, fields) => ({type,stage:requested,...fields});
    const source = path => action(paths.has(path)?'open_file':'create_file',{path,tab:'editor'});
    const settings = () => action('settings',{tab:'settings'});
    function task(id, visited = new Set()) {
      if (visited.has(id)) return source(id==='schematic'?design.schematic:design.layout);
      visited.add(id);
      if (id==='specification') return source('specifications.json');
      if (id==='archive') return action('export',{});
      const stage = stages.find(item=>item.id===id) || {id,status:'unknown'};
      const native = id==='schematic'||id==='layout';
      const kind = {schematic:'xschem',layout:'magic',pre:'simulate',post:'simulate',drc:'drc',extract:'extract',lvs:'lvs'}[id];
      const tab = ['pre','post'].includes(id)?'simulation':native && !(ota&&id==='schematic')?'desktop':'verification';
      const options = kind==='simulate'?{netlist:id==='post'?design.post_netlist:design.netlist}:
        kind==='xschem'?{schematic:design.schematic,...(ota?{netlist_only:true}:{create_missing:true})}:
        kind==='lvs'?{layout_netlist:design.layout_netlist,schematic_netlist:design.schematic_netlist,cell:design.cell}:
        {layout:design.layout,cell:design.cell,...(id==='layout'?{create_missing:true}:{})};
      const matches = run => run.kind===kind && Object.entries(options).every(([key,value])=>
        ['netlist','schematic','layout','layout_netlist','schematic_netlist'].includes(key)?run.metadata?.sources?.[key]===value:
          key==='cell'&&kind==='lvs'?(run.metadata?.options?.cell || 'amplifier')===value:true);
      const running = runs.find(run=>matches(run)&&active(run));
      if (running) return action('select_run',{run_id:running.id,tab:running.metadata?.gui_edits_original?'desktop':tab});
      const record = runs.find(run=>run.id===stage.run_id);
      if (ota && ['schematic','extract'].includes(id) && stage.status==='publication_required') {
        if (record && matches(record) && record.status==='succeeded' && record.stale===false && !record.imported_evidence)
          return action('publish',{source_stage:id,run_id:record.id,tab:'verification'});
        return record?action('select_run',{run_id:record.id,tab:'verification'}):source(id==='schematic'?design.schematic:design.layout);
      }
      // Unknown or completed evidence calls for inspection, never an inferred rerun.
      if (!['ready','missing','not_run','stale','failed','blocked'].includes(stage.status)) {
        if (record) return action('select_run',{run_id:record.id,tab});
        return source(id==='pre'?design.netlist:id==='post'?design.post_netlist:id==='schematic'?design.schematic:design.layout);
      }
      if (['pre','post'].includes(id) && !paths.has(options.netlist)) return source(options.netlist);
      // Template publication is an explicit task and takes precedence over downstream runs.
      for (const dependency of ota ? (id==='pre'?['schematic']:id==='post'?['extract']:id==='lvs'?['schematic','extract']:[]) : []) {
        if (stages.find(item=>item.id===dependency)?.status!=='published') return task(dependency,visited);
      }
      if (id==='lvs') {
        for (const path of [design.schematic_netlist,design.layout_netlist]) if (!paths.has(path)) return source(path);
      }
      if (['drc','extract'].includes(id) && !paths.has(design.layout)) return task('layout',visited);
      if (id==='schematic' && ota && !paths.has(design.schematic)) return source(design.schematic);
      const available = scoped && (kind==='xschem' && ota ? input.status.toolchain?.tools?.xschem?.available===true :
        input.status.operation_availability?.[kind]?.available===true);
      if (!available) return settings();
      return action('run',{kind,options,tab});
    }
    return task(requested);
  }
  const fingerprint = action => JSON.stringify(action);
  const api = {resolve,fingerprint};
  if (typeof module==='object' && module.exports) module.exports=api;
  else root.AFDesignNavigation=api;
})(typeof window==='undefined'?globalThis:window);
