// Run against an isolated workspace with native tools, PDK, and DISPLAY configured.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({headless:true, args:['--no-sandbox']});
  const page = await browser.newPage();
  const base = process.env.AF_URL;
  assert(base, 'AF_URL must point to the isolated test workspace');
  const errors = [];
  const websockets = [];
  page.on('websocket', socket => websockets.push(socket.url()));
  const launched = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base);
    await page.locator('.stage-list').waitFor();
    const project = await page.locator('#project-select').inputValue();
    for (const [kind, source] of [['xschem', 'schematics/amplifier.sch'], ['magic', 'layout/amplifier.mag']]) {
      await page.locator('.tabs [data-tab=overview]').click();
      const response = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/runs'));
      await page.locator(`.stage-list [data-run-kind=${kind}]`).click();
      const result = await response;
      assert(result.ok(), await result.text());
      const run = await result.json();
      launched.push({project, id:run.id});
      assert.equal(run.metadata.options.create_missing, true);
      assert.equal(run.metadata.editor_source_created, true);
      assert(['queued', 'running'].includes(run.status), JSON.stringify(run));
      await page.locator(`[data-file="${source}"]`).waitFor();
      await page.locator('#desktop-panel').waitFor({state:'visible'});
      await page.frameLocator('#desktop-frame').locator('#noVNC_credentials_dlg').waitFor({state:'visible'});
      assert(websockets.some(url => new URL(url).pathname === '/desktop/websockify'), 'Desktop must connect through the workspace origin');
      const file = await page.request.get(`${base}/api/projects/${project}/files?path=${encodeURIComponent(source)}`);
      assert(file.ok());
      const text = (await file.json()).content;
      assert(kind === 'xschem' ? text.includes('file_version=1.2') : text.startsWith('magic\ntech sky130A\n'));
      console.log(`PASS page button opened ${kind} and refreshed ${source}`);
    }
    await page.screenshot({path:'.tools/desktop/validation/embedded-desktop-login.png'});
    assert.deepEqual(errors, []);
  } finally {
    for (const run of launched) {
      await page.request.post(`${base}/api/projects/${run.project}/runs/${run.id}/cancel`, {data:{}});
    }
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
