'use strict';

window.AFDrcNavigation={
  create({state,toast,t=value=>value,onRunSelected}){
    const operations=window.AFOperations;
    const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    let pid=null,runID=null,sourceID=null,index=1,draftProject=null,draftSource=null,busy=false,result=null,error='',timer=null;
    function draftIndex(){return state.project?.id===draftProject&&selected()?.id===draftSource?index:1;}
    function currentFocus(){return state.project?.id===pid&&selected()?.id===sourceID&&draftIndex()===result?.error_index;}
    function snapshot(){const same=state.project?.id===pid;return {project_id:state.project?.id||null,run_id:same?runID:null,
      source_run_id:selected()?.id||null,error_index:draftIndex(),busy,focused:currentFocus()?Boolean(result?.focused):false,
      status:same?result?.status||null:null,box:currentFocus()?result?.box||null:null,error:same?error:''};}
    function selected(){return state.run?.kind==='drc'?state.run:state.runs.find(row=>row.id===state.run?.metadata?.drc_focus?.source_run_id);}
    function html(){
      const report=selected();if(!report)return '';
      const eligible=report.status==='succeeded'&&report.verification?.error_count>0&&!report.stale&&!report.imported_evidence;
      const message=state.project?.id===pid?t(error||(currentFocus()?result?.reason:'')||''):'';
      return `<div class="card spaced" data-drc-navigation><h2>${esc(t('DRC error navigation'))}</h2><p>${esc(t('Choose a Magic error tile index from the selected local DRC execution. Magic rechecks the layout and reports the actual focused box.'))}</p><div class="button-row"><label>${esc(t('Error index'))}<input type="number" data-drc-error-index min="1" max="${Number(report.verification?.error_count)||1}" value="${draftIndex()}" ${busy?'disabled':''}></label><button type="button" class="secondary-button" data-drc-focus ${!eligible||busy?'disabled':''}>${esc(t('Find error in Magic'))}</button><button type="button" class="secondary-button" data-drc-stop ${!runID||state.project?.id!==pid||!['running','queued'].includes(result?.status)?'disabled':''}>${esc(t('Stop navigation window'))}</button></div><p data-drc-focus-status role="status">${esc(message||(!eligible?t('Select a current completed local DRC execution with errors.'):t('Focus is confirmed only after Magic acknowledges the command.')))}</p></div>`;
    }
    function update(){
      const node=document.querySelector('[data-drc-focus-status]');
      if(node)node.textContent=t(error||(currentFocus()?result?.reason:'')||(busy?'Waiting for Magic focus acknowledgment…':'Focus is confirmed only after Magic acknowledges the command.'));
      const input=document.querySelector('[data-drc-error-index]');if(input)input.disabled=busy;
      const stop=document.querySelector('[data-drc-stop]');if(stop)stop.disabled=state.project?.id!==pid||!['running','queued'].includes(result?.status);
      const button=document.querySelector('[data-drc-focus]');if(button)button.disabled=busy||selected()?.status!=='succeeded'||Boolean(selected()?.stale)||Boolean(selected()?.imported_evidence)||!selected()?.verification?.error_count;
    }
    async function refresh(){
      if(!pid||!runID||state.project?.id!==pid)throw new Error(t('Select the project that owns this navigation window.'));
      const project=pid,identifier=runID;
      const response=await operations.invoke('verification.drc.focus_status',{project_id:project,run_id:identifier});
      if(state.project?.id===project&&runID===identifier){result=response.data;update();}
      if(timer)clearTimeout(timer);timer=null;
      if(state.project?.id===project&&runID===identifier&&!result?.focused&&['running','queued'].includes(result?.status))timer=setTimeout(()=>report(refresh()),250);
      return snapshot();
    }
    function setIndex({error_index}={}){
      if(busy)throw new Error(t('Wait for the current navigation request.'));
      const report=selected(),input=document.querySelector('[data-drc-error-index]');
      if(!state.project?.id||state.tab!=='verification'||!report||!input)throw new Error(t('Select a DRC execution in Verification first.'));
      if(!Number.isInteger(error_index)||error_index<1||error_index>100000||error_index>Number(report.verification?.error_count||0))throw new Error(t('Error index must be within the selected report count.'));
      draftProject=state.project.id;draftSource=report.id;index=error_index;input.value=String(index);error='';update();
      return snapshot();
    }
    async function focus({error_index=draftIndex()}={}){
      if(busy)throw new Error(t('Wait for the current navigation request.'));
      if(state.dirty)throw new Error(t('Save the editor draft before navigating DRC errors.'));
      const report=selected(),project=state.project?.id;
      if(!project||state.tab!=='verification'||!report)throw new Error(t('Select a DRC execution in Verification first.'));
      setIndex({error_index});
      busy=true;pid=project;sourceID=report.id;index=error_index;runID=null;result=null;error='';update();
      try{
        const revision=await operations.invoke('project.detail',{project_id:project});
        if(state.project?.id!==project||selected()?.id!==report.id||state.dirty)throw new Error(t('The selected project or DRC execution changed.'));
        const response=await operations.invoke('verification.drc.focus',{project_id:project,run_id:report.id,error_index},
          {expected_version:revision.version,idempotency_key:`drc-focus-${crypto.randomUUID()}`});
        if(state.project?.id===project){runID=response.data.id;result={status:response.data.status,focused:false};
          await onRunSelected?.(runID,false);await refresh();}
        return snapshot();
      }finally{busy=false;update();}
    }
    async function stop(){
      if(state.project?.id!==pid||!runID)throw new Error(t('Select the project that owns this navigation window.'));
      await operations.invoke('run.cancel',{project_id:pid,run_id:runID});
      return refresh();
    }
    function report(promise){return Promise.resolve(promise).catch(failure=>{error=failure.message;update();toast?.(error,true);});}
    document.addEventListener('change',event=>{
      if(!event.target.matches('[data-drc-error-index]'))return;
      try{setIndex({error_index:Number(event.target.value)});}catch(failure){event.target.value=String(draftIndex());error=failure.message;update();toast?.(error,true);}
    });
    document.addEventListener('click',event=>{
      const button=event.target.closest('button');if(!button||button.disabled)return;
      if(button.hasAttribute('data-drc-focus'))report(focus({error_index:Number(document.querySelector('[data-drc-error-index]')?.value)}));
      if(button.hasAttribute('data-drc-stop'))report(stop());
    });
    return {html,snapshot,commands:{set_index:setIndex,focus,refresh,stop}};
  }
};
