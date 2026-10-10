'use strict';
const assert=require('node:assert/strict');

// Poll actual records in Node: the bundled browser waitForFunction treats an
// async predicate's Promise as truthy before its result has been inspected.
module.exports=async function waitForSuccessfulRun(invoke,project_id,run_id){
  const deadline=Date.now()+45000;
  let run;
  do{
    run=(await invoke('run.get',{project_id,run_id})).data;
    if(['succeeded','failed','cancelled','timed_out','unavailable'].includes(run.status)){
      assert.equal(run.status,'succeeded',JSON.stringify({run_id,status:run.status,error:run.error}));
      return run;
    }
    assert(['queued','starting','running'].includes(run.status),`Unexpected run status: ${run.status}`);
    await new Promise(resolve=>setTimeout(resolve,50));
  }while(Date.now()<deadline);
  throw new Error(`Run ${run_id} did not finish; last status: ${run?.status}`);
};
