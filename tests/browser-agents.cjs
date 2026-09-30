// Exercise the actual browser UI against an isolated workspace. Agent endpoints
// are controlled at the HTTP boundary to cover running/approval/race states.
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
with tempfile.TemporaryDirectory(prefix='af-browser-agents-') as root:
    workspace = Workspace(Path(root))
    projects = [workspace.projects.create('Agent project A'), workspace.projects.create('Agent project B')]
    recorded = workspace.runner.start(workspace.projects.path(projects[0]['id']), 'inspection', [sys.executable, '-c', 'print("Recorded fixture output")'])
    workspace.runner.wait(workspace.projects.path(projects[0]['id']), recorded['id'])
    server = create_server(workspace, port=0, desktop_url='')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(json.dumps({'url':f'http://127.0.0.1:{server.server_port}', 'projects':projects,'run_id':recorded['id']}), flush=True)
    try: sys.stdin.readline()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
`;
(async () => {
  const child = spawn('python3', ['-u', '-c', harness], {cwd:path.resolve(__dirname, '..'), stdio:['pipe','pipe','pipe']});
  let stderr = '';
  child.stderr.on('data', chunk => { stderr += chunk; });
  const lines = readline.createInterface({input:child.stdout});
  let browser;
  try {
    const local = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`Workspace did not start: ${stderr}`)), 15000);
      lines.once('line', line => { clearTimeout(timer); resolve(JSON.parse(line)); });
      child.once('exit', code => { clearTimeout(timer); reject(new Error(`Workspace exited ${code}: ${stderr}`)); });
    });
    browser = await chromium.launch({headless:true, args:['--no-sandbox']});
    const page = await browser.newPage({viewport:{width:1500,height:1150}});
    const errors = [], requests = [], sessions = new Map();
    page.on('pageerror', error => errors.push(error.message));
    const backends = [
      {id:'api',name:'Model API',available:true,modes:['read-only','confirm']},
      {id:'codex',name:'Codex CLI',available:true,modes:['read-only','workspace-write']},
      {id:'claude',name:'Claude CLI',available:false,diagnostic:'Claude executable not found',modes:['read-only','workspace-write']},
    ];
    let sequence = 0, delaySession = null, releaseDelayed = null, rejectMessage = false;
    await page.route('**/api/agents*', route => route.fulfill({json:{backends}}));
    await page.route('**/api/projects/*/agent/sessions**', async route => {
      const request = route.request(), parts = new URL(request.url()).pathname.split('/');
      const project = parts[3], id = parts[6], action = parts[7];
      const body = request.method() === 'POST' ? request.postDataJSON() : null;
      requests.push({project,id,action,body,method:request.method()});
      if (!id && request.method() === 'GET') return route.fulfill({json:[...sessions.values()].filter(session => session.project_id === project)});
      if (!id) {
        const session = {id:`session-${++sequence}`,project_id:project,backend:body.backend,model:body.model || '',mode:body.mode,status:'idle',created_at:new Date().toISOString(),turns:[],pending_approval:null,usage:null};
        sessions.set(session.id, session);
        return route.fulfill({status:201,json:session});
      }
      const session = sessions.get(id);
      if (request.method() === 'GET') {
        if (id === delaySession) {
          delaySession = null;
          const snapshot = JSON.parse(JSON.stringify(session));
          await new Promise(resolve => { releaseDelayed = resolve; });
          return route.fulfill({json:snapshot});
        }
        return route.fulfill({json:session});
      }
      if (action === 'messages') {
        if (rejectMessage) { rejectMessage = false; return route.fulfill({status:409,json:{error:'Previous turn is still shutting down'}}); }
        session.status = 'running';
        session.turns.push({id:`turn-${session.turns.length+1}`,message:body.message,status:'running',events:[{type:'status',text:'Inspecting project files'}],reply:'',usage:null,error:null});
        return route.fulfill({status:202,json:session});
      }
      if (action === 'approval') {
        assert.equal(body.approval_id, session.pending_approval.id);
        session.pending_approval = null;
        session.status = 'succeeded';
        Object.assign(session.turns.at(-1), {status:'succeeded',reply:body.approved ? 'Change approved' : 'Change denied'});
        session.turns.at(-1).events.push({type:'message',text:session.turns.at(-1).reply});
      }
      if (action === 'cancel') {
        session.status = 'cancelled'; session.pending_approval = null;
        session.turns.at(-1).status = 'cancelled';
      }
      return route.fulfill({json:session});
    });
    await page.goto(local.url);
    await page.waitForFunction(() => !document.body.inert);
    assert.equal(await page.locator('#agent-backend').count(), 1, 'Users can choose an agent backend');
    await page.locator('#project-select').selectOption(local.projects[0].id);
    await page.waitForFunction(() => !document.querySelector('#chat-send').disabled);
    assert.equal(await page.locator('#agent-mode').inputValue(), 'read-only');
    assert.notEqual(await page.locator('#agent-backend option[value=claude]').getAttribute('disabled'), null, 'Unavailable backends cannot be selected');
    await page.locator('[data-context="testbenches/pre.cir"]').check();
    await page.locator('#agent-add-context').click();
    await page.locator('#agent-attach-run').check();
    await page.keyboard.press('Escape');
    await page.locator('#chat-input').fill('Inspect <img src=x onerror="window.agentXss=1">');
    await page.locator('#chat-send').click();
    await page.waitForFunction(() => document.querySelector('#agent-status').textContent.includes('running'));
    const first = [...sessions.values()][0];
    assert.equal(first.backend, 'api');
    assert.equal(first.mode, 'read-only');
    assert.deepEqual(requests.find(request => request.action === 'messages').body.files, ['testbenches/pre.cir']);
    assert.equal(requests.find(request => request.action === 'messages').body.run_id, local.run_id);
    assert.equal(await page.locator('#agent-backend').isDisabled(), true);
    assert.equal(await page.locator('#agent-new').isDisabled(), true);
    assert.equal(await page.locator('#chat-messages img').count(), 0, 'User text remains escaped');
    await page.locator('.tabs [data-tab=settings]').click();
    await page.locator('#provider-api-key').fill('unsaved-key-draft');
    first.turns[0].events.push({type:'tool_call',tool:'read_file',arguments:{path:'testbenches/pre.cir'},text:'Reading selected circuit'});
    await page.locator('.agent-tool > summary').click();
    await page.locator('#chat-messages').getByText('Reading selected circuit').waitFor();
    assert.match(await page.locator('.agent-tool-body').innerText(), /testbenches\/pre.cir/, 'Tool arguments remain visible alongside their status text');
    assert.equal(await page.locator('#provider-api-key').inputValue(), 'unsaved-key-draft', 'Agent polling preserves settings drafts');
    await page.locator('#agent-stop').click();
    await page.waitForFunction(() => document.querySelector('#agent-status').textContent.includes('cancelled'));
    await page.reload();
    await page.waitForFunction(() => document.querySelector('#agent-status')?.textContent.includes('cancelled'));
    assert.match(await page.locator('#chat-messages').innerText(), /Inspect <img/);
    await page.locator('#agent-new').click();
    await page.locator('#agent-mode').selectOption('confirm');
    await page.locator('#chat-input').fill('Propose a circuit change');
    await page.locator('#chat-send').click();
    await page.waitForFunction(() => document.querySelector('#agent-status').textContent.includes('running'));
    const second = [...sessions.values()][1];
    second.status = 'waiting_approval';
    second.pending_approval = {id:'proposal-1',tool:'write_file',arguments:{path:'testbenches/pre.cir',content:'Complete replacement <script>window.agentXss=1</script>\nLast replacement line',diff:'--- old\n+++ new\n-previous\n+<script>window.agentXss=1</script>'}};
    await page.locator('#agent-approval').waitFor();
    assert.match(await page.locator('#agent-approval').innerText(), /testbenches\/pre.cir/);
    await page.locator('#agent-proposed-file summary').click();
    assert.match(await page.locator('#agent-proposed-content').innerText(), /Complete replacement/);
    assert.match(await page.locator('#agent-proposed-content').innerText(), /Last replacement line/);
    assert.equal(await page.locator('#agent-approval script').count(), 0);
    assert.match(await page.locator('#agent-approval-details').innerText(), /--- old\n\+\+\+ new/, 'Approval diffs preserve actual line breaks');
    await page.screenshot({path:'/tmp/analog-factory-agent-desktop.png'});
    await page.setViewportSize({width:390,height:844});
    await page.locator('#toggle-ai').click();
    await page.locator('#agent-deny').evaluate(element => element.scrollIntoView({block:'center'}));
    const approvalBounds = await page.locator('#agent-deny').boundingBox();
    await page.screenshot({path:'/tmp/analog-factory-agent-mobile.png'});
    assert(approvalBounds.y >= 67 && approvalBounds.y + approvalBounds.height <= 844, `Mobile approval controls are reachable: ${JSON.stringify(approvalBounds)}`);
    await page.locator('#chat-input').evaluate(element => element.scrollIntoView({block:'center'}));
    const composerBounds = await page.locator('#chat-input').boundingBox();
    assert(composerBounds.y >= 67 && composerBounds.y + composerBounds.height <= 844, 'Mobile composer is reachable');
    await page.locator('#toggle-ai').click();
    await page.setViewportSize({width:1500,height:1150});
    await page.locator('#agent-deny').click();
    await page.locator('#chat-messages').getByText('Change denied', {exact:true}).first().waitFor();
    assert.equal(await page.locator('#chat-messages').getByText('Change denied', {exact:true}).count(), 1, 'Final reply appears only once');
    assert.equal(requests.find(request => request.action === 'approval').body.approved, false);
    second.usage = {total_tokens:999};
    rejectMessage = true;
    await page.locator('#chat-input').fill('Keep this draft on failure');
    await page.locator('#chat-send').click();
    await page.locator('#agent-diagnostic').getByText('Previous turn is still shutting down').waitFor();
    assert.equal(await page.locator('#chat-input').inputValue(), 'Keep this draft on failure');
    await page.locator('#chat-send').click();
    await page.waitForFunction(() => document.querySelector('#agent-status').textContent.includes('running'));
    assert.equal(await page.locator('#assistant-usage').innerText(), '', 'Active turn does not show prior usage');
    second.status = 'waiting_approval';
    second.pending_approval = {id:'proposal-2',tool:'simulate',arguments:{netlist:'testbenches/pre.cir'}};
    await page.locator('#agent-approval').waitFor();
    await page.locator('#agent-allow').click();
    await page.locator('#chat-messages').getByText('Change approved', {exact:true}).waitFor();
    assert.equal(requests.filter(request => request.action === 'approval').at(-1).body.approved, true);
    await page.locator('#agent-backend').selectOption('codex');
    assert.equal(await page.locator('#agent-session').inputValue(), '', 'Changing backend starts a fresh conversation');
    assert.equal(await page.locator('#agent-mode').inputValue(), 'read-only');
    await page.locator('#agent-mode').selectOption('workspace-write');
    await page.locator('#agent-model-toggle').click();
    assert.match(await page.locator('#agent-permission-note').innerText(), /without.*approval|without.*confirmation/i);
    await page.locator('#agent-model').fill('cli-model-override');
    await page.locator('#agent-model-apply').click();
    await page.locator('#agent-add-context').click();
    assert.match(await page.locator('#agent-context-note').innerText(), /project/i);
    await page.keyboard.press('Escape');
    await page.locator('#chat-input').fill('CLI task');
    await page.locator('#chat-send').click();
    await page.waitForFunction(() => document.querySelector('#agent-status').textContent.includes('running'));
    const third = [...sessions.values()][2];
    assert.equal(third.backend, 'codex'); assert.equal(third.model, 'cli-model-override'); assert.equal(third.mode, 'workspace-write');
    third.turns[0].events.push({type:'message',text:'LATE PROJECT A EVENT'});
    delaySession = third.id;
    await page.waitForTimeout(1200);
    assert(releaseDelayed, 'An active session polls incrementally');
    await page.locator('#project-select').selectOption(local.projects[1].id);
    await page.waitForFunction(id => document.querySelector('#footer-project').textContent.includes('Agent project B') && document.querySelector('#agent-session').value === '', local.projects[1].id);
    releaseDelayed();
    await page.waitForTimeout(100);
    assert.doesNotMatch(await page.locator('#chat-messages').innerText(), /LATE PROJECT A|CLI task/, 'Late output cannot cross projects');
    await page.locator('#project-select').selectOption(local.projects[0].id);
    await page.waitForFunction(() => document.querySelector('#agent-status').textContent.includes('running'));
    await page.locator('#agent-stop').click();
    await page.waitForFunction(() => document.querySelector('#agent-status').textContent.includes('cancelled'));
    // A poll already in flight must not restore the running state after Stop.
    await page.locator('#chat-input').fill('Second CLI turn');
    await page.locator('#chat-send').click();
    await page.waitForFunction(() => document.querySelector('#agent-status').textContent.includes('running'));
    releaseDelayed = null; delaySession = third.id;
    await page.waitForTimeout(1200);
    assert(releaseDelayed);
    await page.locator('#agent-stop').click();
    await page.waitForFunction(() => document.querySelector('#agent-status').textContent.includes('cancelled'));
    releaseDelayed();
    await page.waitForTimeout(200);
    assert.match(await page.locator('#agent-status').innerText(), /cancelled/, 'Late polls cannot undo cancellation');

    // A completed agent turn refreshes saved sources, but preserves editor drafts.
    await page.locator('[data-file="testbenches/pre.cir"]').click();
    const savedCircuit = await page.locator('#code-editor').inputValue();
    await page.locator('#chat-input').fill('Edit saved source');
    await page.locator('#chat-send').click();
    await page.waitForFunction(() => document.querySelector('#agent-status').textContent.includes('running'));
    const sourceURL = `${local.url}/api/projects/${local.projects[0].id}/files?path=testbenches%2Fpre.cir`;
    await page.request.put(sourceURL, {data:{content:savedCircuit+'\n* Agent edit one\n'}});
    third.status = 'succeeded'; third.turns.at(-1).status = 'succeeded';
    await page.waitForFunction(() => document.querySelector('#code-editor').value.includes('Agent edit one'));
    const dirtyCircuit = (await page.locator('#code-editor').inputValue())+'\n* Local unsaved draft\n';
    await page.locator('#code-editor').fill(dirtyCircuit);
    await page.locator('#chat-input').fill('Do not discard my editor draft');
    await page.locator('#chat-send').click();
    await page.waitForFunction(() => document.querySelector('#agent-status').textContent.includes('running'));
    await page.request.put(sourceURL, {data:{content:savedCircuit+'\n* Agent edit two\n'}});
    third.status = 'succeeded'; third.turns.at(-1).status = 'succeeded';
    await page.waitForFunction(() => document.querySelector('#agent-status').textContent.includes('succeeded'));
    assert.equal(await page.locator('#code-editor').inputValue(), dirtyCircuit);
    await page.locator('#agent-history-toggle').click();
    await page.locator(`#agent-history-list [data-agent-session="${second.id}"]`).click();
    await page.locator('#chat-messages').getByText('Change denied', {exact:true}).first().waitFor();
    assert.equal(await page.locator('#chat-messages').getByText('Change denied', {exact:true}).count(), 1, 'Final reply appears only once');
    await page.locator('.tabs [data-tab=settings]').click();
    await page.locator('#settings-form [name=tool-codex]').fill('/tmp/example-codex');
    await page.locator('#settings-form [name=tool-claude]').fill('/tmp/example-claude');
    const saved = page.waitForResponse(response => new URL(response.url()).pathname === '/api/settings' && response.request().method() === 'PUT');
    await page.locator('#settings-form button[type=submit]').click();
    assert((await saved).ok());
    const settings = await (await page.request.get(`${local.url}/api/settings`)).json();
    assert.equal(settings.tools.codex, '/tmp/example-codex');
    assert.equal(settings.tools.claude, '/tmp/example-claude');
    const imported = page.waitForResponse(response => new URL(response.url()).pathname === '/api/settings' && response.request().method() === 'PUT');
    await page.locator('#settings-file').setInputFiles({name:'partial-settings.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({tools:{ngspice:''}}))});
    assert((await imported).ok());
    await page.waitForFunction(() => document.querySelector('#settings-form [name=tool-codex]').value === '/tmp/example-codex');
    const preserved = await (await page.request.get(`${local.url}/api/settings`)).json();
    assert.equal(preserved.tools.codex, '/tmp/example-codex', 'Importing another tool preserves the CLI path');
    assert.equal(preserved.tools.claude, '/tmp/example-claude');
    await page.locator('#ui-language').selectOption('zh-CN');
    await page.waitForFunction(() => !document.querySelector('#ui-language').disabled);
    assert.match(await page.locator('#agent-new').getAttribute('aria-label'), /[\u4e00-\u9fff]/, 'Icon-only new conversation control has a translated accessible label');
    assert.match(await page.locator('#chat-messages').innerText(), /Change denied/, 'Authored output is never translated');
    await page.locator('#ui-theme').selectOption('light');
    await page.waitForFunction(() => !document.querySelector('#ui-theme').disabled);
    await page.setViewportSize({width:390,height:844});
    await page.locator('#toggle-ai').click();
    assert(await page.locator('.ai-sidebar').isVisible());
    assert.equal(await page.evaluate(() => window.agentXss), undefined);
    assert.deepEqual(errors, []);
    console.log('Agent browser checks passed: sessions, polling, permissions, cancellation, escaping, project isolation, settings, localization.');
  } finally {
    if (browser) await browser.close();
    child.stdin.end('stop\n');
    const timer = setTimeout(() => child.kill('SIGKILL'), 7000);
    if (child.exitCode === null) await once(child, 'exit');
    clearTimeout(timer); lines.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
