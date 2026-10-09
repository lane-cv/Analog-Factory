// Run against tests/operation_test_server.py. No agent-specific example handler.
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
(async () => {
  const base = process.env.AF_URL || 'http://127.0.0.1:8878';
  const browser = await chromium.launch({headless:true, args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base);
    await page.locator('body:not([inert])').waitFor();
    await page.waitForFunction(() => Boolean(window.AFOperations?.invoke));
    const projectResponse = await page.request.post(base + '/api/operations/invoke', {data:{operation_id:'project.create',parameters:{name:'Registry extension'}}});
    const project = (await projectResponse.json()).data;
    const discovered = await page.evaluate(() => AFOperations.list({module:'example'}));
    assert.equal(discovered.operations[0].operation_id, 'example.project_summary');
    const description = await page.evaluate(pid => AFOperations.describe('example.project_summary', {project_id:pid}), project.id);
    assert.equal(description.input_schema.properties.project_id.type, 'string');
    // A real UI button invokes the shared client, with no new route or transport.
    await page.evaluate(pid => {
      const button = document.createElement('button');
      const output = document.createElement('output'); output.id = 'extension-output';
      button.id = 'extension-example'; button.textContent = 'Count project sources';
      button.onclick = async () => { window.extensionResult = await AFOperations.invoke('example.project_summary', {project_id:pid}); output.textContent = String(window.extensionResult.data.file_count); };
      document.body.append(button, output);
    }, project.id);
    await page.locator('#extension-example').click();
    await page.waitForFunction(() => Boolean(window.extensionResult));
    const uiResult = await page.evaluate(() => window.extensionResult);
    const agentResult = await (await page.request.post(base + '/api/operations/invoke', {data:{operation_id:'example.project_summary',parameters:{project_id:project.id}}})).json();
    assert.equal(agentResult.ok, true);
    assert.deepEqual(uiResult.data, agentResult.data);
    assert.equal(await page.locator('#extension-output').innerText(), String(agentResult.data.file_count));
    assert.ok(agentResult.data.file_count > 0);
    assert.deepEqual(errors, []);
    console.log('Newly registered example: UI button and agent use identical handler and result.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
