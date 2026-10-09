// Run against a disposable workspace; this exercises persisted workspace settings.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

const baseURL = process.env.AF_URL || 'http://127.0.0.1:8765';
const evidence = process.env.AF_BROWSER_ARTIFACTS || '/tmp/analog-factory-preferences-evidence';
fs.mkdirSync(evidence, {recursive:true});

(async () => {
  const browser = await chromium.launch({headless:true, args:['--no-sandbox']});
  try {
    const context = await browser.newContext({viewport:{width:1500, height:1050}});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(baseURL);
    await page.waitForLoadState('networkidle');
    await page.locator('body:not([inert])').waitFor();
    assert.equal(await page.locator('.topbar #ui-language').count(), 1, 'Language is available in the header before opening Settings');
    assert.equal(await page.locator('.topbar #ui-theme').count(), 1, 'Appearance is available in the header before opening Settings');
    assert.equal(await page.getByLabel('Language', {exact:true}).count(), 1);
    assert.equal(await page.getByLabel('Appearance', {exact:true}).count(), 1);

    const settings = async () => (await page.request.get(`${baseURL}/api/settings`)).json();
    const choose = async (id, value) => {
      const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/settings' && r.request().method() === 'PUT');
      await page.locator(id).focus();
      await page.locator(id).selectOption(value);
      assert.equal((await response).status(), 200);
      await page.waitForFunction(() => !document.querySelector('#ui-language').disabled && !document.querySelector('#ui-theme').disabled);
      assert.equal(await page.evaluate(() => document.activeElement.id), id.slice(1), 'Keep keyboard focus on the changed preference');
    };
    assert.equal(await page.locator('#ui-language').inputValue(), 'en');
    assert.equal(await page.locator('#ui-theme').inputValue(), 'dark');
    const initial = await settings();
    const darkBackground = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);

    await choose('#ui-theme', 'light');
    await choose('#ui-theme', 'dark');
    await page.locator('.tabs [data-tab=settings]').click();
    assert.equal(await page.locator('#main-content #ui-language, #main-content #ui-theme').count(), 0, 'Settings does not duplicate the global controls');
    await page.locator('#settings-form [name=model]').fill('Unsaved model / Settings / English');
    await choose('#ui-language', 'zh-CN');
    assert.equal(await page.locator('html').getAttribute('lang'), 'zh-CN');
    assert.match(await page.locator('.tabs [data-tab=settings]').innerText(), /[\u4e00-\u9fff]/);
    assert.equal(await page.locator('#settings-form [name=model]').inputValue(), 'Unsaved model / Settings / English');
    await choose('#ui-theme', 'light');
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
    assert.equal(await page.locator('#settings-form [name=model]').inputValue(), 'Unsaved model / Settings / English');
    assert.notEqual(await page.evaluate(() => getComputedStyle(document.body).backgroundColor), darkBackground);
    const changed = await settings();
    assert.deepEqual(changed.ui, {language:'zh-CN', theme:'light'});
    assert.deepEqual(changed.tools, initial.tools);
    assert.deepEqual(changed.provider, initial.provider);
    await page.screenshot({path:path.join(evidence, 'settings-zh-light.png'), fullPage:true});

    await page.reload();
    await page.locator('body:not([inert])').waitFor();
    assert.equal(await page.locator('#ui-language').inputValue(), 'zh-CN');
    assert.equal(await page.locator('#ui-theme').inputValue(), 'light');
    const fresh = await browser.newContext();
    const freshPage = await fresh.newPage();
    await freshPage.goto(`${baseURL}/#overview`);
    await freshPage.locator('body:not([inert])').waitFor();
    assert.equal(await freshPage.locator('#ui-language').inputValue(), 'zh-CN');
    assert.equal(await freshPage.locator('#ui-theme').inputValue(), 'light');
    await fresh.close();

    // A rejected save restores the confirmed selection rather than claiming persistence.
    await page.route('**/api/settings', async route => {
      if (route.request().method() === 'PUT') await route.fulfill({status:500, contentType:'application/json', body:JSON.stringify({error:'Preference save test failure'})});
      else await route.continue();
    });
    await page.locator('#ui-theme').selectOption('dark');
    await page.waitForFunction(() => document.querySelector('#ui-theme').value === 'light' && !document.querySelector('#ui-theme').disabled);
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
    assert.deepEqual((await settings()).ui, {language:'zh-CN', theme:'light'});
    await page.unroute('**/api/settings');

    // Authored content resembling interface labels must stay byte-for-byte intact.
    await page.locator('#new-project').click();
    await page.locator('#new-project-form [name=name]').fill('Settings - Save - Dark - English');
    await page.locator('#new-project-form button[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('#footer-project').textContent.includes('Settings - Save - Dark - English'));
    await page.locator('.tabs [data-tab=overview]').click();
    await page.locator('.hero-card').waitFor();
    assert.match(await page.locator('#footer-project').innerText(), /Settings - Save - Dark - English/);
    await page.locator('[data-file="testbenches/pre.cir"]').click();
    const source = `${await page.locator('#code-editor').inputValue()}\n* Settings Save Dark English 模拟电路\n`;
    await page.locator('#code-editor').fill(source);
    await choose('#ui-language', 'en');
    await choose('#ui-language', 'zh-CN');
    assert.equal(await page.locator('#code-editor').inputValue(), source, 'Global controls preserve unsaved source edits');
    const saved = page.waitForResponse(r => new URL(r.url()).pathname.endsWith('/files') && r.request().method() === 'PUT');
    await page.locator('#save-file').click();
    assert.equal((await saved).status(), 200);
    for (const tab of ['simulation','verification','workflows','evaluation','settings']) {
      const control = page.locator(`.tabs [data-tab=${tab}]`);
      assert.match(await control.innerText(), /[\u4e00-\u9fff]/, `${tab} label should be localized`);
      await control.click();
      assert.equal(await page.locator('.topbar #ui-language').isVisible(), true);
      assert.equal(await page.locator('.topbar #ui-theme').isVisible(), true);
    }

    // Failure arriving after navigation must preserve the current tab's unapplied draft.
    let releaseFailure;
    const delayedFailure = new Promise(resolve => { releaseFailure = resolve; });
    await page.route('**/api/settings', async route => {
      if (route.request().method() !== 'PUT') return route.continue();
      await delayedFailure;
      await route.fulfill({status:500, contentType:'application/json', body:JSON.stringify({error:'Delayed save failure'})});
    });
    await page.locator('#ui-language').selectOption('en');
    await page.locator('.tabs [data-tab=workflows]').click();
    await page.locator('details').filter({has:page.locator('#workflow-json')}).locator('summary').click();
    const graphDraft = '{"unsaved":"Keep this draft / Settings / Save"}';
    await page.locator('#workflow-json').fill(graphDraft);
    releaseFailure();
    await page.waitForFunction(() => document.documentElement.lang === 'zh-CN');
    assert.equal(await page.locator('#workflow-json').inputValue(), graphDraft);
    assert.equal(await page.locator('#workflow-json').isVisible(), true);
    assert.equal(await page.evaluate(() => document.activeElement.id), 'workflow-json');
    await page.unroute('**/api/settings');
    await page.locator('.tabs [data-tab=settings]').click();
    await choose('#ui-language', 'en');
    await choose('#ui-theme', 'dark');
    await page.locator('[data-file="testbenches/pre.cir"]').click();
    assert.equal(await page.locator('#code-editor').inputValue(), source);
    assert.match(await page.locator('#footer-project').innerText(), /Settings - Save - Dark - English/);
    assert.equal(await page.locator('html').getAttribute('lang'), 'en');
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
    await page.locator('.tabs [data-tab=settings]').click();
    const checkHeader = async () => {
      const bounds = await page.evaluate(() => {
        const rect = selector => {
          const {x,y,width,height} = document.querySelector(selector).getBoundingClientRect();
          return {x,y,width,height};
        };
        return {width:innerWidth, overflow:document.documentElement.scrollWidth > innerWidth,
          header:rect('.topbar'), language:rect('#ui-language'), theme:rect('#ui-theme'), workspace:rect('.workspace')};
      });
      assert.equal(bounds.overflow, false, `Page fits ${bounds.width}px viewport`);
      for (const control of [bounds.language, bounds.theme]) {
        assert.ok(control.x >= 0 && control.x + control.width <= bounds.width, 'Preferences fit the viewport');
        assert.ok(control.y >= 0 && control.y + control.height <= bounds.header.height, 'Preferences stay inside the header');
      }
      assert.ok(bounds.theme.x + bounds.theme.width >= bounds.width - 24, 'Appearance is at the right edge');
      assert.ok(bounds.workspace.y >= bounds.header.height, 'Header does not overlap the workspace');
      assert.equal(await page.locator('#ui-language').isVisible(), true);
      assert.equal(await page.locator('#ui-theme').isVisible(), true);
    };
    for (const width of [1500,1280,1024,768,390,375]) {
      await page.setViewportSize({width,height:844});
      await checkHeader();
    }
    await page.screenshot({path:path.join(evidence, 'settings-en-dark-mobile.png'), fullPage:true});
    await choose('#ui-language', 'zh-CN');
    await choose('#ui-theme', 'light');
    await checkHeader();
    await page.screenshot({path:path.join(evidence, 'settings-zh-light-mobile.png'), fullPage:true});
    await choose('#ui-language', 'en');
    await choose('#ui-theme', 'dark');
    assert.deepEqual(errors, []);
    console.log('PASS: global header preferences, language/theme switching, persistence, form/source preservation, failed-save rollback, keyboard focus, and responsive layout');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
