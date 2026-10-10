// First-use guidance uses the real disposable server and simulator.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const waitForSuccessfulRun = require('./fixtures/browser-run-wait.cjs');
const base = process.env.AF_URL || 'http://127.0.0.1:8765';
const evidence = process.env.AF_BROWSER_ARTIFACTS || '/tmp/analog-factory-first-user-evidence';
fs.mkdirSync(evidence,{recursive:true});
(async () => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const waitRun=async run_id=>waitForSuccessfulRun(
      (name,parameters)=>page.evaluate(({name,parameters})=>AFOperations.invoke(name,parameters),{name,parameters}),
      await page.evaluate(()=>AFOperations.snapshot().workspace.project_id),run_id);
    await page.goto(base);
    await page.locator('body:not([inert])').waitFor();
    await page.locator('#new-project').click();
    await page.locator('#project-readiness [data-readiness=ngspice]').waitFor({timeout:5000});
    await page.locator('#new-project-form [name=template]').selectOption('ota5');
    await page.waitForFunction(() => document.querySelector('#project-readiness [data-readiness=ota5_assets]'));
    // The disposable server has no explicitly configured prepared OTA assets.
    assert.equal(await page.locator('#new-project-form [type=submit]').isDisabled(), true);
    await page.screenshot({path:path.join(evidence,'first-user-preflight.png'),fullPage:true});
    await page.locator('#project-use-amplifier').click();
    await page.locator('#new-project-form [name=name]').fill('First-user acceptance');
    let releaseDetail, seenDetail, delayedDetail=false;
    const detailSeen=new Promise(resolve=>{seenDetail=resolve;});
    const detailRelease=new Promise(resolve=>{releaseDetail=resolve;});
    const detailPattern=/\/api\/projects\/[a-f0-9]{32}$/;
    await page.route(detailPattern,async route=>{
      const response=await route.fetch();
      if(!delayedDetail&&route.request().method()==='GET'&&(await response.json()).project?.name==='First-user acceptance'){
        delayedDetail=true;seenDetail();await detailRelease;
      }
      await route.fulfill({response});
    });
    await page.locator('#new-project-form [type=submit]').click();
    await detailSeen;
    try { assert.equal(await page.locator('#new-project-dialog').evaluate(node=>node.open),true,
      'Keep the dialog open until the new project is selected, so Run cannot target the previous project'); }
    finally { releaseDetail(); }
    await page.waitForFunction(()=>!document.querySelector('#new-project-dialog').open&&
      document.querySelector('#project-summary strong')?.textContent==='First-user acceptance');
    await page.unroute(detailPattern);
    await page.locator('[data-design-stage=pre]').waitFor();
    assert.equal(await page.locator('[data-design-stage=pre]').getAttribute('data-stage-status'), 'not_run');
    const readiness=await page.evaluate(async()=>{
      const id=AFOperations.snapshot().workspace.project_id;
      return (await fetch(`/api/status?project_id=${id}`)).json();
    });
    assert.equal(await page.locator('[data-design-stage=drc]').getAttribute('data-stage-status'),
      readiness.operation_availability.drc.available?'not_run':'blocked',
      'Stage readiness must reflect the actual shared workspace tool configuration');
    await page.locator('#run-primary').click();
    await page.waitForFunction(() => AFOperations.snapshot().runs.selected);
    const id = await page.evaluate(() => AFOperations.snapshot().runs.selected);
    await waitRun(id);
    await page.locator('#refresh-project').click();
    await page.locator('.tabs [data-tab=overview]').click();
    await page.waitForFunction(() => document.querySelector('[data-design-stage=pre]')?.dataset.stageStatus === 'completed');
    await page.locator('[data-file="testbenches/pre.cir"]').click();
    const editor = page.locator('#code-editor');
    await editor.fill((await editor.inputValue()) + '\n* First-user source edit\n');
    await page.locator('#save-file').click();
    await page.waitForFunction(() => document.querySelector('#file-save-status')?.textContent.includes('Saved locally'));
    await page.locator('.tabs [data-tab=overview]').click();
    await page.waitForFunction(() => document.querySelector('[data-design-stage=pre]')?.dataset.stageStatus === 'stale');
    // Create fresh evidence, then mutate through a separate HTTP caller while
    // overview stays open. Only the event subscription may update this view.
    await page.locator('#run-primary').click();
    await page.waitForFunction(previous => AFOperations.snapshot().runs.selected && AFOperations.snapshot().runs.selected !== previous,id);
    const refreshedRun = await page.evaluate(() => AFOperations.snapshot().runs.selected);
    await waitRun(refreshedRun);
    await page.locator('.tabs [data-tab=overview]').click();
    await page.waitForFunction(() => document.querySelector('[data-design-stage=pre]')?.dataset.stageStatus === 'completed',{},{timeout:45000});
    await page.screenshot({path:path.join(evidence,'first-user-overview-completed.png'),fullPage:true});
    const project_id=await page.evaluate(() => AFOperations.snapshot().workspace.project_id);
    const external=async(operation_id,parameters,options={})=>{
      const response=await page.request.post(`${base}/api/operations/invoke`,{data:{operation_id,parameters,...options}});
      const result=await response.json();assert.equal(response.ok(),true,JSON.stringify(result));assert.equal(result.ok,true,JSON.stringify(result));return result;
    };
    const source=await external('file.read',{project_id,path:'testbenches/pre.cir'});
    await external('file.write',{project_id,path:'testbenches/pre.cir',content:source.data.content+'\n* External first-user source edit\n'},
      {expected_version:source.version,idempotency_key:`first-user-external-${project_id}`});
    await page.waitForFunction(() => AFOperations.snapshot().workspace.tab==='overview' &&
      document.querySelector('[data-design-stage=pre]')?.dataset.stageStatus==='stale',{},{timeout:15000});
    assert.match(await page.locator('.design-next strong').innerText(),/Pre-layout simulation/,'Next-step guidance updates without navigating or manually refreshing');
    assert.equal(await page.evaluate(() => AFOperations.snapshot().workspace.project_id),project_id);
    await page.screenshot({path:path.join(evidence,'first-user-overview-external-stale.png'),fullPage:true});
    // Remote opening and form editing must use the same preflight as manual controls.
    await page.evaluate(() => AFOperations.dispatch('ui.workspace.dialog',{name:'new_project',open:true}));
    await page.evaluate(() => AFOperations.dispatch('ui.form.set',{form:'new_project',values:{template:'ota5'}}));
    await page.waitForFunction(() => document.querySelector('#project-readiness [data-readiness=ota5_assets]'));
    assert.equal(await page.locator('#new-project-form [type=submit]').isDisabled(), true);
    await page.locator('#project-configure').click();
    assert.equal(await page.locator('#new-project-dialog').evaluate(node => node.open), false);
    await page.waitForFunction(() => AFOperations.snapshot().workspace.tab === 'settings');
    assert.equal(await page.evaluate(() => AFOperations.snapshot().workspace.tab), 'settings');
    // A delayed create response must not close a newer dialog or discard its draft.
    const selected = await page.evaluate(() => AFOperations.snapshot().workspace.project_id);
    let releaseCreate, seenCreate;
    const sent = new Promise(resolve => { seenCreate = resolve; });
    const release = new Promise(resolve => { releaseCreate = resolve; });
    await page.route('**/api/projects', async route => {
      if (route.request().method() !== 'POST') return route.continue();
      const response = await route.fetch(); seenCreate(); await release;
      await route.fulfill({response});
    });
    await page.locator('#new-project').click();
    await page.locator('#project-use-amplifier').click();
    await page.locator('#new-project-form [name=name]').fill('Slow created project');
    await page.locator('#new-project-form [type=submit]').click();
    await sent;
    await page.locator('#close-project-dialog').click();
    await page.locator('#new-project').click();
    await page.locator('#new-project-form [name=name]').fill('Keep this newer draft');
    releaseCreate();
    await page.waitForFunction(() => !document.querySelector('#new-project-form [type=submit]').disabled);
    assert.equal(await page.locator('#new-project-dialog').evaluate(node => node.open), true);
    assert.equal(await page.locator('#new-project-form [name=name]').inputValue(), 'Keep this newer draft');
    assert.equal(await page.evaluate(() => AFOperations.snapshot().workspace.project_id), selected);
    await page.screenshot({path:path.join(evidence,'first-user-preserved-draft.png'),fullPage:true});
    await page.locator('#close-project-dialog').click();
    await page.locator('#ui-language').selectOption('zh-CN');
    await page.waitForFunction(() => !document.querySelector('#ui-language').disabled && document.documentElement.lang==='zh-CN');
    await page.locator('#new-project').click();
    await page.locator('#new-project-form [name=template]').selectOption('ota5');
    await page.waitForFunction(() => document.querySelector('#project-readiness [data-readiness=ota5_assets]'));
    assert.equal(await page.locator('#project-readiness h3').innerText(),'环境检查');
    assert.equal(await page.locator('#project-check-refresh').innerText(),'重新检查');
    assert.equal(await page.locator('#project-configure').innerText(),'配置所需环境');
    assert.equal(await page.locator('#project-use-amplifier').innerText(),'使用教学示例');
    assert.equal(await page.locator('#new-project-form .callout').isVisible(),false);
    await page.screenshot({path:path.join(evidence,'first-user-preflight-zh.png'),fullPage:true});
    await page.locator('#close-project-dialog').click();
    await page.locator('#ui-language').selectOption('en');
    await page.waitForFunction(() => !document.querySelector('#ui-language').disabled && document.documentElement.lang==='en');
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({preflight:true,real_simulation:true,source_staleness:true,external_overview_update:true,remote_parity:true,screenshots:evidence}));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
