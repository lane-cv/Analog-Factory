// Self-contained browser regression: temporary workspace and local fake provider only.
// Requires Playwright, Chromium, and Python. Never connects to the user's workspace.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {chromium} = require('playwright');

const pythonHarness = String.raw`
import json
import sys
import tempfile
import threading
from pathlib import Path
from analog_factory.server import create_server
from analog_factory.service import Workspace

with tempfile.TemporaryDirectory(prefix='af-browser-api-') as root:
    server = create_server(Workspace(Path(root)), port=0, desktop_url='')
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    print(json.dumps({'url': f'http://127.0.0.1:{server.server_port}'}), flush=True)
    try:
        sys.stdin.readline()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
`;

async function workspace() {
  const child = spawn('python3', ['-u', '-c', pythonHarness], {
    cwd:path.resolve(__dirname, '..'), env:{...process.env, AF_DESKTOP_URL:''},
    stdio:['pipe', 'pipe', 'pipe'],
  });
  let errors = '';
  child.stderr.on('data', chunk => { errors += chunk; });
  const lines = readline.createInterface({input:child.stdout});
  const ready = await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => { child.kill(); reject(new Error('Isolated workspace startup timed out')); }, 15000);
    lines.once('line', line => { clearTimeout(timeout); resolve(JSON.parse(line)); });
    child.once('error', error => { clearTimeout(timeout); reject(error); });
    child.once('exit', code => { clearTimeout(timeout); reject(new Error(`Isolated workspace exited (${code}): ${errors}`)); });
  });
  return {
    url:ready.url,
    async close() {
      child.stdin.end('stop\n');
      const timeout = setTimeout(() => child.kill('SIGKILL'), 7000);
      await once(child, 'exit');
      clearTimeout(timeout);
      lines.close();
      assert.equal(errors, '', 'The isolated server should not log failures or credentials');
    },
  };
}

(async () => {
  const firstKey = 'dummy-browser-local-key';
  const secondKey = 'dummy-browser-session-key';
  const rejectedKey = 'dummy-browser-rejected-draft';
  let expectedKey = firstKey;
  const providerRequests = [];
  const provider = http.createServer((request, response) => {
    providerRequests.push({path:request.url, authorization:request.headers.authorization});
    // Keep the request pending long enough to exercise the disabled test button.
    setTimeout(() => {
      response.setHeader('Content-Type', 'application/json');
      if (request.url !== '/v1/models' || request.headers.authorization !== `Bearer ${expectedKey}`) {
        response.writeHead(401); response.end(JSON.stringify({error:'Invalid test credential'}));
      } else {
        response.end(JSON.stringify({data:[{id:'test-model'}, {id:'another-test-model'}]}));
      }
    }, 150);
  });
  provider.listen(0, '127.0.0.1');
  await once(provider, 'listening');
  const providerURL = `http://127.0.0.1:${provider.address().port}/v1`;
  let local;
  let browser;
  const downloads = fs.mkdtempSync(path.join(os.tmpdir(), 'af-api-download-'));
  try {
    local = await workspace();
    browser = await chromium.launch({headless:true, args:['--no-sandbox']});
    const context = await browser.newContext({viewport:{width:1500, height:1050}, acceptDownloads:true});
    const page = await context.newPage();
    const errors = [];
    const apiRequests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      if (request.method() !== 'GET' && new URL(request.url()).pathname.startsWith('/api/')) {
        apiRequests.push({path:new URL(request.url()).pathname, body:request.postDataJSON()});
      }
    });
    const settings = async () => (await page.request.get(`${local.url}/api/settings`)).json();
    const waitIdle = () => page.waitForFunction(() => document.querySelector('#settings-form')?.getAttribute('aria-busy') === 'false');
    const save = async (success = true) => {
      const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/settings' && r.request().method() === 'PUT');
      await page.locator('#settings-form button[type=submit]').click();
      assert.equal((await response).ok(), success);
      await waitIdle();
    };
    const testConnection = async (status = 'success') => {
      const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/provider/test');
      await page.locator('[data-action=test-provider]').click();
      await response;
      await page.locator(`#provider-test-result[data-status=${status}]`).waitFor();
      await waitIdle();
    };
    const preference = async (id, value) => {
      const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/settings' && r.request().method() === 'PUT');
      await page.locator(id).selectOption(value);
      assert((await response).ok());
      await page.waitForFunction(() => !document.querySelector('#ui-language').disabled && !document.querySelector('#ui-theme').disabled);
    };
    await page.goto(`${local.url}/#settings`);
    await page.locator('#provider-api-key').waitFor();
    assert.equal(await page.locator('#provider-api-key').getAttribute('type'), 'password');
    assert.equal(await page.locator('#provider-api-key').inputValue(), '');
    assert(await page.locator('#provider-remember-key').isChecked());
    assert(await page.locator('#provider-clear-key').isDisabled());
    await page.locator('#provider-name').fill('Browser test provider');
    await page.locator('[name=base_url]').fill(providerURL);
    await page.locator('#provider-model').fill('test-model');
    await page.locator('[name=timeout]').fill('5');
    await page.locator('#provider-api-key').fill(firstKey);
    await preference('#ui-language', 'zh-CN');
    await preference('#ui-theme', 'light');
    assert.equal(await page.locator('#provider-api-key').inputValue(), firstKey, 'Language and theme must retain the key draft');
    assert.equal(await page.locator('[name=base_url]').inputValue(), providerURL);
    assert.equal(await page.locator('#provider-model').inputValue(), 'test-model');
    assert(!(await page.locator('body').innerText()).includes(firstKey), 'The key must remain masked');

    const requestStart = apiRequests.length;
    const tested = page.waitForResponse(r => new URL(r.url()).pathname === '/api/provider/test');
    await page.locator('[data-action=test-provider]').click();
    assert(await page.locator('[data-action=test-provider]').isDisabled());
    await page.locator('[data-action=test-provider]').evaluate(button => button.click());
    await tested;
    await page.locator('#provider-test-result[data-status=success]').waitFor();
    await waitIdle();
    assert.deepEqual(apiRequests.slice(requestStart).map(request => request.path), ['/api/settings', '/api/provider/test']);
    assert.equal(providerRequests.length, 1, 'Repeated clicks must not start another request');
    assert.equal(providerRequests[0].authorization, `Bearer ${firstKey}`, 'Test must use the freshly saved draft key');
    let publicSettings = await settings();
    assert.equal(publicSettings.provider.name, 'Browser test provider');
    assert.equal(publicSettings.provider.api_key_configured, true);
    assert.equal(publicSettings.provider.api_key_storage, 'local');
    assert(!JSON.stringify(publicSettings).includes(firstKey));
    assert(!Object.hasOwn(publicSettings.provider, 'api_key'));
    assert.equal(await page.locator('#provider-api-key').inputValue(), '');
    assert.deepEqual(await page.locator('#provider-models option').evaluateAll(options => options.map(option => option.value)), ['test-model', 'another-test-model']);
    assert((await page.locator('#provider-test-result').innerText()).includes('test-model'));

    const downloadPromise = page.waitForEvent('download');
    await page.locator('[data-action=export-settings]').click();
    const download = await downloadPromise;
    const exportPath = path.join(downloads, download.suggestedFilename());
    await download.saveAs(exportPath);
    const exported = fs.readFileSync(exportPath, 'utf8');
    assert(!exported.includes(firstKey), 'Configuration export must exclude secrets');
    assert(!Object.hasOwn(JSON.parse(exported).provider, 'api_key'));
    await page.reload();
    await page.locator('#provider-api-key').waitFor();
    assert.equal(await page.locator('#provider-api-key').inputValue(), '', 'Reload must never retrieve the saved key');
    assert(!(await page.locator('#provider-clear-key').isDisabled()));
    assert.match(await page.locator('#provider-key-status').innerText(), /已保存/);

    await page.locator('#provider-model').fill('another-test-model');
    const callsBeforeSave = providerRequests.length;
    await save();
    assert.equal(providerRequests.length, callsBeforeSave, 'Plain Save must not contact the provider');
    assert(!Object.hasOwn(apiRequests.at(-1).body.provider, 'api_key'), 'A blank field must omit the credential');
    assert.equal((await settings()).provider.api_key_storage, 'local');
    await testConnection();
    assert.equal(providerRequests.at(-1).authorization, `Bearer ${firstKey}`);

    expectedKey = secondKey;
    await page.locator('#provider-api-key').fill(secondKey);
    await page.locator('#provider-remember-key').uncheck();
    await testConnection();
    assert.equal((await settings()).provider.api_key_storage, 'session');
    assert.equal(providerRequests.at(-1).authorization, `Bearer ${secondKey}`);
    await page.locator('#provider-remember-key').check();
    await save();
    assert.equal((await settings()).provider.api_key_storage, 'local', 'Storage mode can change without re-entering the key');

    await page.locator('[name=base_url]').fill(`${providerURL}?invalid=1`);
    await page.locator('#provider-api-key').fill(rejectedKey);
    await save(false);
    assert.equal(await page.locator('#provider-api-key').inputValue(), rejectedKey, 'Rejected settings must retain the draft key');
    assert.equal((await settings()).provider.base_url, providerURL, 'A failed save must preserve the previous endpoint');
    await preference('#ui-language', 'en');
    await preference('#ui-theme', 'dark');
    assert.equal(await page.locator('#provider-api-key').inputValue(), rejectedKey);
    await page.locator('[name=base_url]').fill(providerURL);
    await testConnection('error');
    assert.match(await page.locator('#provider-test-result').innerText(), /Configuration saved, but the connection test failed/);
    assert.match(await page.locator('#provider-test-result').innerText(), /HTTP 401/);
    assert.equal(await page.locator('#provider-api-key').inputValue(), '', 'A saved key must be cleared from the input even if connection testing fails');
    assert(!(await page.locator('body').innerText()).includes(rejectedKey));

    const cleared = page.waitForResponse(r => new URL(r.url()).pathname === '/api/settings' && r.request().method() === 'PUT');
    await page.locator('#provider-clear-key').click();
    assert((await cleared).ok());
    await waitIdle();
    publicSettings = await settings();
    assert.equal(publicSettings.provider.api_key_configured, false);
    assert.equal(publicSettings.provider.api_key_storage, 'none');
    assert(await page.locator('#provider-clear-key').isDisabled());
    await testConnection('error');
    assert.equal(providerRequests.at(-1).authorization, undefined, 'Clearing the key must remove Authorization from later requests');
    assert.equal((await settings()).provider.api_key_configured, false);
    const browserStorage = await page.evaluate(() => JSON.stringify({...localStorage}));
    for (const key of [firstKey, secondKey, rejectedKey]) assert(!browserStorage.includes(key), 'API keys must never be stored in localStorage');
    assert.deepEqual(errors, []);
    console.log('PASS browser API configuration: save/test, authentication, storage, draft preservation, clear, and safe export');
  } finally {
    if (browser) await browser.close();
    if (local) await local.close();
    await new Promise(resolve => provider.close(resolve));
    fs.rmSync(downloads, {recursive:true, force:true});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
