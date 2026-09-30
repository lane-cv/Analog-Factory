// Isolated browser regression: fake API/desktop, real workspace JavaScript and DOM.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');

(async () => {
  const project = {id:'desktop-test', name:'Desktop test', template:'sky130'};
  const sources = {xschem:{schematic:'schematics/amplifier.sch'}, magic:{layout:'layout/amplifier.mag'}};
  const makeRun = kind => ({id:`${kind}-test`, kind, status:'running', metadata:{sources:sources[kind]}});
  const runs = [makeRun('xschem')];
  const settings = {ui:{language:'en', theme:'dark'}, provider:{}, tools:{}};
  let launches = 0, desktopLoads = 0;
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, 'http://localhost');
    const json = data => { response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify(data)); };
    const body = async () => { let text = ''; for await (const chunk of request) text += chunk; return text ? JSON.parse(text) : {}; };
    if (url.pathname === '/api/status') return json({toolchain:{tools:Object.fromEntries(['ngspice','xschem','magic','netgen'].map(name => [name,{available:true}])),pdk:{available:true,variant:'sky130A'},desktop:{available:true,browser_url:'/desktop/vnc.html?autoconnect=true&resize=scale&path=desktop/websockify'}}});
    if (url.pathname === '/api/settings') { if (request.method === 'PUT') Object.assign(settings, await body()); return json(settings); }
    if (url.pathname === '/api/projects') return json([project]);
    if (url.pathname === `/api/projects/${project.id}`) return json({project,files:Object.values(sources).map(source => ({path:Object.values(source)[0]})),runs});
    if (url.pathname.endsWith('/jobs')) return json([]);
    if (url.pathname.endsWith('/runs') && request.method === 'POST') {
      const requestBody = await body();
      assert.equal(requestBody.options.create_missing, true);
      launches++;
      const run = makeRun(requestBody.kind); runs.unshift(run); return json(run);
    }
    if (url.pathname.endsWith('/log')) return json({content:'Editor is running.'});
    if (url.pathname.includes('/runs/')) return json(runs.find(run => url.pathname.endsWith(`/${run.id}`)));
    if (url.pathname === '/desktop/vnc.html') {
      desktopLoads++;
      response.setHeader('Content-Type', 'text/html');
      return response.end('<!doctype html><title>Test desktop</title><p id="desktop-ready">Desktop loaded</p><div id="noVNC_status"></div><div id="noVNC_credentials_dlg"><input id="noVNC_password_input" type="password"></div>');
    }
    try {
      const relative = url.pathname === '/' ? 'index.html' : url.pathname.slice(1);
      if (relative.includes('..')) throw new Error('Invalid path');
      const content = await fs.readFile(path.join(__dirname, '../analog_factory/static', relative));
      response.setHeader('Content-Type', {'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff2':'font/woff2'}[path.extname(relative)] || 'application/octet-stream');
      response.end(content);
    } catch (_) { response.writeHead(404); response.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({headless:true,args:['--no-sandbox']});
    const page = await browser.newPage({viewport:{width:1500,height:1050}});
    // Prior noVNC settings must not send the embedded viewer to another endpoint.
    await page.addInitScript(() => {
      localStorage.setItem('host', 'old-desktop.invalid');
      localStorage.setItem('port', '5901');
      localStorage.setItem('encrypt', 'true');
    });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.locator('.stage-list [data-run-kind=xschem]').click();
    await page.locator('#desktop-panel').waitFor({state:'visible'});
    await page.frameLocator('#desktop-frame').locator('#desktop-ready').waitFor();
    const viewer = new URL(await page.locator('#desktop-frame').getAttribute('src'));
    assert.equal(viewer.searchParams.get('host'), '127.0.0.1');
    assert.equal(viewer.searchParams.get('port'), String(server.address().port));
    assert.equal(viewer.searchParams.get('encrypt'), 'false');
    assert.equal(viewer.searchParams.get('path'), 'desktop/websockify');
    await page.frameLocator('#desktop-frame').locator('#noVNC_credentials_dlg').evaluate(element => {
      element.classList.add('noVNC_open');
      element.querySelector('input').value = 'private-test-password';
    });
    await page.waitForFunction(() => document.querySelector('#desktop-status').textContent.includes('Enter your existing desktop password'));
    assert(!(await page.locator('#desktop-status').textContent()).includes('private-test-password'));
    await page.frameLocator('#desktop-frame').locator('#noVNC_status').evaluate(element => {
      element.className = 'noVNC_open noVNC_status_error';
      element.innerHTML = '<strong>Failed to connect to server</strong>';
    });
    await page.waitForFunction(() => document.querySelector('#desktop-status').textContent.includes('Failed to connect to server'));
    assert.equal(await page.locator('#desktop-status strong').count(), 0, 'Connection errors are rendered as plain text');
    await page.frameLocator('#desktop-frame').locator('#noVNC_status').evaluate(element => {
      element.classList.remove('noVNC_open');
      element.ownerDocument.querySelector('#noVNC_credentials_dlg').classList.remove('noVNC_open');
      element.ownerDocument.documentElement.classList.add('noVNC_connected');
    });
    await page.waitForFunction(() => document.querySelector('#desktop-status').textContent.includes('Desktop connected'));
    assert.equal(launches, 0, 'Existing editor must not start a duplicate process');
    await page.evaluate(() => { window.initialDesktopFrame = document.querySelector('#desktop-frame'); });
    const assertStableFrame = async () => {
      assert(await page.evaluate(() => window.initialDesktopFrame === document.querySelector('#desktop-frame')), 'Keep the iframe mounted');
      assert.equal(desktopLoads, 1, 'Keep the desktop connection alive');
    };
    const refreshed = page.waitForResponse(response => response.url().endsWith('/jobs'));
    await page.locator('#refresh-project').click();
    await refreshed;
    await assertStableFrame();
    await page.locator('.tabs [data-tab=overview]').click();
    await page.locator('.tabs [data-tab=desktop]').click();
    await assertStableFrame();
    await page.locator('#desktop-toolbar [data-run-kind=magic]').click();
    await page.waitForFunction(() => document.querySelector('#log-label').textContent.includes('magic'));
    assert.equal(launches, 1, 'New editor must start once');
    await page.waitForResponse(response => response.url().endsWith('/magic-test/log'));
    await page.locator('#desktop-toolbar [data-run-kind=magic]').click();
    await page.locator('.toast').filter({hasText:'magic is already open'}).waitFor();
    assert.equal(launches, 1, 'Repeated editor click must reuse the active process');
    await assertStableFrame();
    await page.locator('.tabs [data-tab=settings]').click();
    await page.locator('#ui-language').selectOption('zh-CN');
    await page.waitForFunction(() => !document.querySelector('#ui-language').disabled);
    await page.locator('.tabs [data-tab=desktop]').click();
    assert.equal(await page.locator('#desktop-frame').getAttribute('title'), '图形桌面');
    assert((await page.locator('#desktop-status').textContent()).includes('桌面已连接'));
    await assertStableFrame();
    const reloaded = page.waitForResponse(response => response.url().includes('/desktop/vnc.html'));
    await page.locator('[data-action=reload-desktop]').click();
    await reloaded;
    assert.equal(desktopLoads, 2, 'Explicit reconnect reloads the desktop');
    await page.frameLocator('#desktop-frame').locator('#desktop-ready').waitFor();
    await page.frameLocator('#desktop-frame').locator('#noVNC_status').evaluate(element => {
      element.className = 'noVNC_open noVNC_status_error';
      element.textContent = 'Connection closed after reconnect';
    });
    await page.waitForFunction(() => document.querySelector('#desktop-status').textContent.includes('桌面连接失败：Connection closed after reconnect'));
    assert.deepEqual(errors, []);
    // Route an HTTPS browser origin entirely to the fake server; no real VNC or TLS endpoint is contacted.
    const securePage = await browser.newPage();
    await securePage.addInitScript(() => {
      localStorage.setItem('host', 'old-desktop.invalid');
      localStorage.setItem('port', '80');
      localStorage.setItem('encrypt', 'false');
    });
    await securePage.route('https://workspace.test:9443/**', async route => {
      const requested = new URL(route.request().url());
      const response = await securePage.request.get(`http://127.0.0.1:${server.address().port}${requested.pathname}${requested.search}`);
      await route.fulfill({response});
    });
    await securePage.goto('https://workspace.test:9443/#desktop');
    await securePage.frameLocator('#desktop-frame').locator('#desktop-ready').waitFor();
    const secureViewer = new URL(await securePage.locator('#desktop-frame').getAttribute('src'));
    assert.equal(secureViewer.origin, 'https://workspace.test:9443');
    assert.equal(secureViewer.searchParams.get('host'), 'workspace.test');
    assert.equal(secureViewer.searchParams.get('port'), '9443');
    assert.equal(secureViewer.searchParams.get('encrypt'), 'true');
    await securePage.close();
    console.log('PASS inline desktop endpoint overrides saved settings on HTTP/HTTPS, connection and credential status, editor reuse, persistent connection, explicit reconnect');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
