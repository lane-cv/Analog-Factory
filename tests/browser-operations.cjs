// Run against a disposable workspace: this creates a project and source revisions.
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const baseURL = process.env.AF_URL || 'http://127.0.0.1:8765';

(async () => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  try {
    const context = await browser.newContext({viewport:{width:1500,height:1000}});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(baseURL);
    await page.locator('body:not([inert])').waitFor();
    await page.waitForFunction(() => window.AFOperations?.status().client_id);
    const client = await page.evaluate(() => AFOperations.status().client_id);
    const request = context.request;
    const call = async (operation_id, parameters = {}, ok = true) => {
      const response = await request.post(`${baseURL}/api/operations/invoke`, {data:{operation_id,parameters:{client_id:client,...parameters}}});
      const result = await response.json();
      assert.equal(result.ok, ok, `${operation_id}: ${JSON.stringify(result.error)}`);
      return result;
    };
    const catalog = await (await request.get(`${baseURL}/api/operations`)).json();
    const browserNames = await page.evaluate(() => AFOperations.names());
    assert.deepEqual(catalog.operations.filter(op => op.kind === 'frontend' && op.frontend_adapter === 'browser').map(op => op.operation_id).sort(), browserNames);
    const nativeNames = catalog.operations.filter(op => op.kind === 'frontend' && op.frontend_adapter === 'native').map(op => op.operation_id);
    assert.ok(nativeNames.includes('native.canvas.move'));
    assert.ok(nativeNames.every(name => !browserNames.includes(name)), 'Native windows acknowledge their own registered adapter; browser commands have a distinct target');
    assert.ok(browserNames.length >= 75);
    const sharedClient = await page.evaluate(async () => {
      const result = await AFOperations.invoke('project.list');
      const listed = await AFOperations.list({keyword:'project.list'});
      const description = await AFOperations.describe('project.list');
      let failure;
      try { await AFOperations.invoke('missing.operation'); } catch (error) { failure = error.code; }
      return {ok:result.ok,listed:listed.operations.some(item => item.operation_id === 'project.list'),description:description.operation_id,failure};
    });
    assert.deepEqual(sharedClient, {ok:true,listed:true,description:'project.list',failure:'NOT_FOUND'});
    const sessions = await (await request.get(`${baseURL}/api/operations/ui.sessions`)).json();
    assert.ok(sessions.data.sessions.some(item => item.client_id === client));

    const create = await request.post(`${baseURL}/api/projects`, {data:{name:'Operation browser verification',template:'amplifier'}});
    assert.equal(create.status(), 201);
    const created = await create.json(), project = created.project || created;
    const beforeSelection = await call('ui.workspace.read');
    await call('ui.workspace.select_project', {project_id:project.id,ui_version:beforeSelection.data.version});
    const readiness = await (await request.get(`${baseURL}/api/status?project_id=${project.id}`)).json();
    const runOperation = await (await request.get(`${baseURL}/api/operations/run.start?project_id=${project.id}&kind=simulate`)).json();
    assert.equal(readiness.project_id, project.id);
    assert.equal(readiness.operation_availability.simulate.available, runOperation.availability.available);
    assert.equal(await page.locator('#run-primary').isDisabled(), !readiness.operation_availability.simulate.available, 'UI readiness comes from the same operation availability as agents');
    await call('ui.editor.open', {path:'testbenches/pre.cir'});
    assert.equal(await page.locator('.tabs [data-tab=editor]').getAttribute('aria-selected'), 'true');
    const original = (await call('ui.editor.read')).data.data.content;
    const source = original + '\n* registry draft verification\n';
    await call('ui.editor.set_content', {content:source});
    assert.equal(await page.locator('#code-editor').inputValue(), source);
    await call('ui.editor.save');
    const saved = await (await request.get(`${baseURL}/api/projects/${project.id}/files?path=testbenches/pre.cir`)).json();
    assert.equal(saved.content, source);
    await page.locator('#code-editor').fill(source + '* manual UI input\n');
    assert.equal((await call('ui.editor.read')).data.data.content, source + '* manual UI input\n');

    // Server changes are visible without overwriting a browser draft, and polling
    // must not replace the draft's original If-Match token with a newer token.
    const file = await request.get(`${baseURL}/api/projects/${project.id}/files?path=testbenches/pre.cir`);
    const remote = source + '* external source edit\n';
    const written = await request.put(`${baseURL}/api/projects/${project.id}/files?path=testbenches/pre.cir`, {headers:{'If-Match':file.headers().etag},data:{content:remote}});
    assert.equal(written.status(), 200);
    await page.waitForTimeout(2300);
    assert.equal(await page.locator('#code-editor').inputValue(), source + '* manual UI input\n');
    const conflict = await call('ui.editor.save', {}, false);
    assert.match(conflict.error.message, /changed|version|failed/i);
    const stillRemote = await (await request.get(`${baseURL}/api/projects/${project.id}/files?path=testbenches/pre.cir`)).json();
    assert.equal(stillRemote.content, remote);
    await call('ui.editor.set_content', {content:source}); // Explicitly discard only this disposable test draft.
    await call('ui.editor.open', {path:'testbenches/pre.cir'});
    assert.equal(await page.locator('#code-editor').inputValue(), remote);

    // An older open-file response must acknowledge a conflict when a newer
    // manual file selection wins while the remote command is awaiting HTTP.
    let releaseSelection, selectionRequested;
    const selectionGate = new Promise(resolve => { releaseSelection = resolve; });
    const selectionStarted = new Promise(resolve => { selectionRequested = resolve; });
    const selectionRoute = `**/api/projects/${project.id}/files?path=project.json`;
    await page.route(selectionRoute, async route => { selectionRequested(); await selectionGate; await route.continue(); });
    const superseded = call('ui.editor.open', {path:'project.json'}, false);
    await selectionStarted;
    const manualSelection = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/files') && new URL(response.url()).searchParams.get('path') === 'testbenches/pre.cir');
    await page.locator('[data-file="testbenches/pre.cir"]').click();
    await manualSelection;
    releaseSelection();
    assert.equal((await superseded).error.code, 'VERSION_CONFLICT');
    assert.equal((await call('ui.editor.read')).data.data.path, 'testbenches/pre.cir');
    await page.unroute(selectionRoute);

    await call('ui.workspace.set_context', {paths:['testbenches/pre.cir']});
    assert.equal(await page.locator('[data-context="testbenches/pre.cir"]').isChecked(), true);
    await call('ui.workspace.navigate', {tab:'workflows'});
    const graph = (await call('ui.workflow.read')).data.data;
    await call('ui.workflow.select_node', {node_id:graph.nodes[0].id});
    await call('ui.workflow.update_node', {node_id:graph.nodes[0].id,position:{x:123,y:234},config:{label:'Remote semantic node'}});
    assert.equal(await page.locator('#node-position-x').inputValue(), '123');
    assert.match(await page.locator(`[data-node="${graph.nodes[0].id}"]`).innerText(), /Remote semantic node/);
    await call('ui.workflow.save');
    const disclosures = (await call('ui.workspace.disclosures')).data.data;
    assert.ok(disclosures.some(item => item.id === 'workflow.json'));
    await call('ui.workspace.set_disclosure', {id:'workflow.json',open:true});
    assert.equal(await page.locator('#workflow-json').isVisible(), true);
    await call('ui.workspace.scroll', {panel:'workspace',position:'end'});
    await call('ui.form.set', {form:'workflow',values:{'tuning-target':'2.5'}});
    assert.equal((await call('ui.workflow.read')).data.data.tuning.target, 2.5);

    await call('ui.layout.set_panel', {panel:'tree',visible:false});
    assert.equal(await page.locator('body').evaluate(element => element.classList.contains('hide-tree')), true);
    const resized = await call('ui.layout.resize', {panel:'ai',size:450});
    assert.equal(resized.data.state.layout.sizes.ai, 450);
    await call('ui.layout.resize', {panel:'ai',size:400,ui_version:resized.data.version - 1}, false);
    await call('ui.layout.reset');
    await call('ui.agent.set_draft', {message:'Draft through semantic API'});
    assert.equal(await page.locator('#chat-input').inputValue(), 'Draft through semantic API');
    await call('ui.agent.add_attachments', {files:[{name:'notes.txt',content:'Explicit test attachment'}]});
    const draft = (await call('ui.agent.read_draft')).data.data;
    assert.equal(draft.attachments[0].name, 'notes.txt');
    await call('ui.agent.remove_attachment', {attachment_id:draft.attachments[0].id});
    await call('ui.agent.prompt', {name:'review'});
    assert.match(await page.locator('#chat-input').inputValue(), /Review/);
    await call('ui.agent.manage_quick', {open:true});
    assert.equal(await page.locator('#agent-quick-message-dialog').isVisible(), true);
    await call('ui.agent.manage_quick', {open:false});

    await call('ui.workspace.navigate', {tab:'settings'});
    await page.locator('#provider-api-key').fill('operation-test-private-secret-never-publish');
    await call('ui.settings.set_draft', {values:{model:'draft-only-model'}});
    const snapshot = await call('ui.workspace.read');
    assert.equal(JSON.stringify(snapshot).includes('operation-test-private-secret-never-publish'), false);
    assert.equal(await page.locator('#provider-api-key').inputValue(), 'operation-test-private-secret-never-publish');
    await call('ui.settings.set_draft', {values:{api_key:'forbidden'}}, false);
    assert.equal(await page.locator('#provider-api-key').inputValue(), 'operation-test-private-secret-never-publish');
    const settingsResponse = await request.get(`${baseURL}/api/settings`), originalSettings = await settingsResponse.json();
    const theme = originalSettings.ui.theme === 'dark' ? 'light' : 'dark';
    const preferenceWrite = await request.put(`${baseURL}/api/settings`, {headers:{'If-Match':settingsResponse.headers().etag},data:{ui:{...originalSettings.ui,theme}}});
    assert.equal(preferenceWrite.status(), 200);
    await page.waitForFunction(theme => document.documentElement.dataset.theme === theme, theme);
    assert.equal(await page.locator('#provider-model').inputValue(), 'draft-only-model');
    assert.equal(await page.locator('#provider-api-key').inputValue(), 'operation-test-private-secret-never-publish');
    const staleSettings = page.waitForResponse(response => new URL(response.url()).pathname === '/api/settings' && response.request().method() === 'PUT');
    await page.locator('#settings-form button[type=submit]').click();
    assert.equal((await staleSettings).status(), 409, 'Settings retain their original draft ETag across background preference refresh');
    const latestSettings = await request.get(`${baseURL}/api/settings`);
    assert.equal((await latestSettings.json()).provider.model, originalSettings.provider.model);
    assert.equal((await request.put(`${baseURL}/api/settings`, {headers:{'If-Match':latestSettings.headers().etag},data:{ui:originalSettings.ui}})).status(), 200);

    // A second browser is a distinct target; commands must not broadcast UI changes.
    const otherContext = await browser.newContext({viewport:{width:1500,height:1000}});
    const other = await otherContext.newPage();
    await other.goto(baseURL);
    await other.locator('body:not([inert])').waitFor();
    await other.waitForFunction(() => window.AFOperations?.status().client_id);
    const otherClient = await other.evaluate(() => AFOperations.status().client_id);
    assert.notEqual(otherClient, client);
    await call('ui.workspace.navigate', {client_id:otherClient,tab:'evaluation'});
    assert.equal(await page.locator('.tabs [data-tab=settings]').getAttribute('aria-selected'), 'true');
    assert.equal(await other.locator('.tabs [data-tab=evaluation]').getAttribute('aria-selected'), 'true');
    await call('ui.workspace.navigate', {client_id:'disconnected-client',tab:'overview'}, false);
    await otherContext.close();
    assert.deepEqual(errors, []);
    console.log('PASS: 75 browser operations match discovery; actual acknowledgements, source concurrency, redaction, shared drafts, graph, layout, agent controls, disclosures, and per-browser targeting');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
