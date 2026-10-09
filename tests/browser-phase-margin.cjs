// Human loop-convention selection through the main UI to real ngspice evidence.
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const readline=require('node:readline');
const {chromium}=require('playwright');
const harness=String.raw`
import json,sys,tempfile,threading
from pathlib import Path
from analog_factory.service import Workspace
from analog_factory.server import create_server
from tests.test_phase_margin import LOOP_DECK
with tempfile.TemporaryDirectory(prefix='af-phase-margin-') as root:
    workspace=Workspace(Path(root));project=workspace.projects.create('Loop margin')
    workspace.projects.write(project['id'],'testbenches/pre.cir',LOOP_DECK)
    server=create_server(workspace,port=0,desktop_url='');threading.Thread(target=server.serve_forever,daemon=True).start()
    print(json.dumps({'url':f'http://127.0.0.1:{server.server_port}','project':project['id']}),flush=True)
    try:sys.stdin.readline()
    finally:server.shutdown();server.server_close();workspace.operations.close()
`;
(async()=>{
  const child=spawn('python3',['-u','-c',harness],{stdio:['pipe','pipe','pipe']});
  const lines=readline.createInterface({input:child.stdout});let browser,stderr='';child.stderr.on('data',v=>stderr+=v);
  try {
    const config=JSON.parse((await once(lines,'line'))[0]);
    browser=await chromium.launch({channel:'chromium',headless:true,args:['--no-sandbox']});
    const page=await browser.newPage({viewport:{width:1500,height:1100}}),errors=[];
    page.on('pageerror',error=>errors.push(error.message));await page.goto(config.url);
    await page.locator('body:not([inert])').waitFor();
    await page.evaluate(pid=>AFOperations.dispatch('ui.workspace.select_project',{project_id:pid}),config.project);
    await page.evaluate(()=>AFOperations.dispatch('ui.workspace.navigate',{tab:'simulation'}));
    await page.locator('[data-batch-open]').click();
    const dialog=page.locator('#simulation-batch-dialog');
    await dialog.locator('[data-batch-field="measurements.0.kind"]').selectOption('phase_margin');
    assert.equal(await dialog.locator('[data-batch-field="measurements.0.loop_gain_convention"]').inputValue(),'');
    await dialog.locator('[data-batch-action=preview]').click();
    await page.waitForFunction(()=>document.querySelector('[data-batch-error]')?.textContent.includes('loop_gain_convention'));
    assert.equal(await page.evaluate(()=>AFOperations.snapshot().simulation_batch.preview_current),false);
    await dialog.locator('[data-batch-field="measurements.0.loop_gain_convention"]').selectOption('negative_feedback_L');
    await dialog.locator('[data-batch-field="analysis.parameters.points"]').fill('200');
    await dialog.locator('[data-batch-action=preview]').click();
    await page.waitForFunction(()=>AFOperations.snapshot().simulation_batch.preview_current);
    await dialog.locator('[data-batch-action=start]').click();
    await page.waitForFunction(()=>AFOperations.snapshot().simulation_batch.status==='succeeded',null,{timeout:30000});
    const result=await page.evaluate(async()=>{
      const value=AFOperations.snapshot().simulation_batch;
      return (await AFOperations.invoke('job.get',{project_id:value.project_id,job_id:value.job_id})).data;
    });
    const margin=result.iterations[0].metrics.phase_margin_deg;
    assert.ok(margin>19 && margin<21,JSON.stringify(result));
    await dialog.locator('[data-batch-inspect]').first().click();
    await page.waitForFunction(()=>document.querySelector('[data-batch-job]')?.textContent.includes('unity_frequency_metric'));
    assert.deepEqual(errors,[]);console.log(`PASS: declared loop-gain UI measured ${margin} degrees with unity-crossing provenance`);
  } finally {
    if(browser)await browser.close();child.stdin.end('\n');await once(child,'exit');lines.close();if(stderr)process.stderr.write(stderr);
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
