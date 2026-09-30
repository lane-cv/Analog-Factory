// Keyboard and responsive chart regressions against a disposable workspace.
// Waveform/job responses are explicit HTTP fixtures so no EDA installation is needed.
const assert = require('node:assert/strict');
const path = require('node:path');
const readline = require('node:readline');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {chromium} = require('playwright');

const harness = String.raw`
import json, sys, tempfile, threading
from pathlib import Path
from analog_factory.server import create_server
from analog_factory.service import Workspace
with tempfile.TemporaryDirectory(prefix='af-browser-workspace-ui-') as root:
    workspace = Workspace(Path(root))
    project = workspace.projects.create('Workspace keyboard validation')
    server = create_server(workspace, port=0, desktop_url='')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(json.dumps({'url':f'http://127.0.0.1:{server.server_port}', 'project':project}), flush=True)
    try: sys.stdin.readline()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
`;

(async () => {
  const child = spawn('python3', ['-u', '-c', harness], {cwd:path.resolve(__dirname, '..'), stdio:['pipe','pipe','pipe']});
  let stderr = '', browser;
  child.stderr.on('data', chunk => { stderr += chunk; });
  const lines = readline.createInterface({input:child.stdout});
  try {
    const local = await new Promise((resolve,reject) => {
      const timer = setTimeout(() => reject(new Error(`Workspace did not start: ${stderr}`)),15000);
      lines.once('line', line => { clearTimeout(timer); resolve(JSON.parse(line)); });
      child.once('exit', code => { clearTimeout(timer); reject(new Error(`Workspace exited ${code}: ${stderr}`)); });
    });
    browser = await chromium.launch({headless:true,args:['--no-sandbox']});
    const page = await browser.newPage({viewport:{width:1500,height:1050}}), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const run = {id:'1234567890abcdef1234567890abcdef',kind:'simulate',status:'succeeded',metrics:{},created_at:'2026-01-01T00:00:00Z'};
    const waveform = {title:'Keyboard test fixture',variables:[{name:'time',unit:'s'},{name:'v(out)',unit:'V'},{name:'v(in)',unit:'V'}],points:Array.from({length:101},(_,index) => [index/100,Math.sin(index/10),Math.cos(index/10)])};
    await page.route(`**/api/projects/${local.project.id}`,async route => {
      const response = await route.fetch(), data = await response.json();
      await route.fulfill({response,json:{...data,runs:[run]}});
    });
    await page.route(`**/api/projects/${local.project.id}/runs/${run.id}**`,async route => {
      const pathname = new URL(route.request().url()).pathname;
      await route.fulfill({json:pathname.endsWith('/waveforms') ? waveform : pathname.endsWith('/log') ? {content:'Explicit browser test fixture'} : run});
    });
    await page.route(`**/api/projects/${local.project.id}/jobs`,route => route.fulfill({json:[{id:'abcdef1234567890abcdef1234567890',kind:'tuning',status:'succeeded',iterations:[{parameter:'RD',value:3300,metric:1.5}]}]}));
    await page.goto(local.url);
    await page.waitForFunction(() => !document.body.inert);
    assert.equal(await page.locator('#log-run-status').isVisible(),true,'Selected execution status is visible in the log header');
    const folder = page.locator('details[data-folder="testbenches"]');
    await folder.locator('summary').focus();
    await page.keyboard.press('Enter');
    assert.equal(await folder.getAttribute('open'),null,'Keyboard collapses explorer folder');
    // Simulate a background refresh without changing the current keyboard focus.
    await page.evaluate(() => refreshProject(false));
    assert.equal(await folder.getAttribute('open'),null,'Refresh preserves collapsed folders');
    assert.equal(await page.evaluate(() => document.activeElement.closest('[data-folder]')?.dataset.folder),'testbenches','Refresh preserves focused summary');
    await page.keyboard.press('Enter');
    const file = page.locator('[data-file="testbenches/pre.cir"]');
    await file.focus();
    await page.keyboard.press('Enter');
    await page.locator('#code-editor').waitFor();
    assert.equal(await file.getAttribute('aria-current'),'true','Selected file exposes current state');
    assert.equal(await page.evaluate(() => document.activeElement.dataset.file),'testbenches/pre.cir','Opening a file keeps explorer keyboard focus');
    const editor = page.locator('#code-editor'), original = await editor.inputValue();
    await editor.focus();
    await page.keyboard.press('Tab');
    assert.notEqual(await page.evaluate(() => document.activeElement.id),'code-editor','Tab exits the source editor');
    assert.equal(await editor.inputValue(),original,'Tab does not edit source');
    await editor.focus();
    await page.keyboard.press('Control+]');
    assert.equal((await editor.inputValue()).length,original.length+2,'Explicit indentation shortcut inserts spaces');
    await editor.fill(original);

    await page.locator('.tabs [data-tab="simulation"]').click();
    const canvas = page.locator('#waveform');
    await page.waitForFunction(() => document.querySelector('#waveform-summary')?.textContent.includes('101'));
    assert.equal(await canvas.getAttribute('tabindex'),'0');
    assert.match(await canvas.getAttribute('aria-describedby'),/waveform-keyboard-hint/);
    assert.equal(await page.locator('[data-action="zoom-out-waveform"]').isDisabled(),true);
    assert.equal(await page.locator('#trace-controls svg').count(),2,'Legend supplies visible line patterns');
    await canvas.focus();
    await page.keyboard.press('+');
    assert.match(await page.locator('#waveform-summary').innerText(),/81 visible samples/);
    await page.keyboard.press('Shift+ArrowRight');
    assert.match(await page.locator('#waveform-summary').innerText(),/200m to 1 s/);
    await page.keyboard.press('ArrowRight');
    const firstSample = await page.locator('#waveform-cursor').innerText();
    assert.match(firstSample,/v\(out\):/);
    await page.keyboard.press('ArrowRight');
    assert.notEqual(await page.locator('#waveform-cursor').innerText(),firstSample,'Arrow keys inspect recorded samples');
    assert.equal(await page.locator('#waveform-cursor').getAttribute('aria-live'),'polite');
    await page.keyboard.press('Home');
    assert.match(await page.locator('#waveform-summary').innerText(),/101 visible samples/);
    await page.locator('[data-action="zoom-in-waveform"]').click();
    assert.match(await page.locator('#waveform-summary').innerText(),/81 visible samples/);
    await page.locator('[data-action="next-sample"]').click();
    assert.match(await page.locator('#waveform-cursor').innerText(),/v\(in\):/);
    await page.locator('[data-action="reset-waveform"]').click();
    const beforeWidth = await canvas.evaluate(element => element.width);
    // A container-only resize does not fire window.resize: the observer must redraw.
    await canvas.evaluate(element => { element.parentElement.style.width = '70%'; });
    await page.waitForFunction(previous => document.querySelector('#waveform').width < previous,beforeWidth);
    assert.ok(await canvas.evaluate(element => Math.abs(element.width-element.clientWidth*devicePixelRatio) <= 1));
    await canvas.evaluate(element => { element.parentElement.style.width = ''; });

    await page.locator('.tabs [data-tab="workflows"]').click();
    const node = page.locator('[data-node="n1"]');
    await node.focus();
    const beforeLeft = await node.evaluate(element => parseFloat(element.style.left));
    await page.keyboard.press('ArrowRight');
    assert.equal(await node.evaluate(element => parseFloat(element.style.left)),beforeLeft+10);
    await page.keyboard.press('Enter');
    assert.equal(await node.getAttribute('aria-pressed'),'true');
    assert.equal(await page.evaluate(() => document.activeElement.id),'node-label','Keyboard activation opens and focuses node configuration');
    await page.locator('#node-position-x').fill('120');
    await page.locator('[data-action="save-node"]').click();
    assert.equal(await node.evaluate(element => parseFloat(element.style.left)),120,'Position field provides a pointer alternative to drag');
    await node.focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#node-position-x').inputValue(),'130','Position field tracks keyboard movement');
    await page.locator('[data-action="save-node"]').click();
    assert.equal(await node.evaluate(element => parseFloat(element.style.left)),130,'Applying configuration preserves the moved position');
    assert.match(await page.locator('#workflow-job .job-progress').innerText(),/1 recorded iterations/);
    assert.equal(await page.locator('#workflow-job .badge svg[aria-hidden="true"]').count(),1,'Status badge supplies a non-color symbol');
    await page.setViewportSize({width:375,height:812});
    assert.equal(await page.locator('#main-content').evaluate(element => element.scrollWidth <= element.clientWidth),true,'Recorded job tables scroll inside the card on narrow screens');
    assert.deepEqual(errors,[]);
    console.log('PASS: explorer state/focus, editor Tab navigation, waveform controls/sample text/container resizing, workflow keyboard/position controls, and evidenced task status');
  } finally {
    await browser?.close();
    lines.close();
    if (child.exitCode === null) { child.stdin.end('\n'); await once(child,'exit'); }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
