// Exercise the rendered application with deterministic CLI metadata and session
// responses. Real CLI discovery and command flags have separate Python coverage.
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');
const readline = require('node:readline');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {chromium} = require('playwright');

async function waitUntil(predicate, description) {
  const deadline=Date.now()+8000;
  while (!predicate()) {
    assert(Date.now()<deadline,description);
    await new Promise(resolve => setTimeout(resolve,10));
  }
}

const harness = String.raw`
import json, sys, tempfile, threading
from pathlib import Path
from analog_factory.server import create_server
from analog_factory.service import Workspace
with tempfile.TemporaryDirectory(prefix='af-browser-models-') as root:
    workspace = Workspace(Path(root))
    projects = [workspace.projects.create('Models project A'), workspace.projects.create('Models project B')]
    server = create_server(workspace, port=0, desktop_url='')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(json.dumps({'url':f'http://127.0.0.1:{server.server_port}', 'projects':projects}), flush=True)
    try: sys.stdin.readline()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
`;

(async () => {
  const child = spawn('python3', ['-u', '-c', harness], {cwd:path.resolve(__dirname, '..'), stdio:['pipe','pipe','pipe']});
  let stderr = '', browser;
  child.stderr.on('data', value => { stderr += value; });
  const lines = readline.createInterface({input:child.stdout});
  const releases = new Set();
  try {
    const local = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`Workspace did not start: ${stderr}`)), 15000);
      lines.once('line', line => {clearTimeout(timer); resolve(JSON.parse(line));});
      child.once('exit', code => {clearTimeout(timer); reject(new Error(`Workspace exited ${code}: ${stderr}`));});
    });
    browser = await chromium.launch({headless:true,args:['--no-sandbox']});
    const page = await browser.newPage({viewport:{width:1500,height:1050}});
    page.setDefaultTimeout(8000);
    const errors = [], requests = [], catalogRequests = [], sessions = new Map();
    page.on('pageerror', error => errors.push(error.message));
    const backends = [
      {id:'api',name:'Model API',available:true,modes:['read-only','confirm']},
      {id:'codex',name:'Codex CLI',available:true,modes:['read-only','workspace-write','automatic']},
      {id:'claude',name:'Claude CLI',available:true,modes:['read-only','workspace-write']},
    ];
    const catalogs = {
      codex:{backend:'codex',source:'cli',diagnostic:'',models:[
        {id:'gpt-5.4',label:'GPT-5.4',description:'General coding model',is_default:true,fast_mode:true,effort_levels:['minimal','low','medium','high','xhigh','max','ultra'],default_effort:'medium'},
        {id:'gpt-5.4-mini',label:'GPT-5.4 mini',description:'Small coding model',is_default:false,fast_mode:false,effort_levels:['low','medium','high'],default_effort:'medium'},
      ],fast_mode:{supported:true}},
      claude:{backend:'claude',source:'builtin',diagnostic:'',models:[
        {id:'sonnet',label:'Sonnet',description:'Balanced model',is_default:true,fast_mode:false,effort_levels:[],default_effort:null},
        {id:'opus',label:'Opus',description:'Advanced reasoning model',is_default:false,fast_mode:true,effort_levels:['low','medium','high','max'],default_effort:'high'},
        {id:'haiku',label:'Haiku',description:'Compact model',is_default:false,fast_mode:false,effort_levels:[],default_effort:null},
      ],fast_mode:{supported:true}},
    };
    let sequence = 0, holdCatalog = null, releaseCatalog = null, holdMessage = false, releaseMessage = null, holdEffort = null, releaseEffort = null;
    await page.route('**/api/agents**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname !== '/api/agents/models') return route.fulfill({json:{backends}});
      const backend = url.searchParams.get('backend');
      catalogRequests.push({backend,project:url.searchParams.get('project_id'),refresh:url.searchParams.get('refresh'),model:url.searchParams.get('model')});
      let response = structuredClone(catalogs[backend] || {backend,source:'unavailable',models:[],diagnostic:'No CLI model catalog',fast_mode:{supported:false}});
      const selectedModel = url.searchParams.get('model');
      if (selectedModel) response.selected_model = response.models.find(model => model.id === selectedModel) || {id:selectedModel,effort_levels:selectedModel === 'private-supported-model' ? ['low','high','ultra'] : [],default_effort:null};
      if (selectedModel && selectedModel === holdEffort) {
        holdEffort = null;
        response.selected_model.effort_levels = ['late-effort'];
        await new Promise(resolve => { releaseEffort = resolve; releases.add(resolve); });
      }
      if (holdCatalog && backend === holdCatalog.backend && (holdCatalog.initial || url.searchParams.get('refresh') === '1')) {
        if (holdCatalog.model) response.models = [{id:holdCatalog.model,label:holdCatalog.model,is_default:true,fast_mode:false}];
        holdCatalog = null;
        await new Promise(resolve => {releaseCatalog = resolve; releases.add(resolve);});
      }
      return route.fulfill({json:response});
    });
    await page.route('**/api/projects/*/agent/sessions**', async route => {
      const request = route.request(), parts = new URL(request.url()).pathname.split('/');
      const project = parts[3], id = parts[6], action = parts[7], method = request.method();
      const body = method === 'POST' ? request.postDataJSON() : null;
      requests.push({project,id,action,method,body});
      if (!id && method === 'GET') return route.fulfill({json:[...sessions.values()].filter(session => session.project_id === project)});
      if (!id) {
        const session = {id:`models-session-${++sequence}`,project_id:project,fast_mode:false,...body,title:`Conversation ${sequence}`,status:'idle',created_at:new Date().toISOString(),turns:[],pending_approval:null,usage:null};
        sessions.set(session.id,session);
        return route.fulfill({status:201,json:session});
      }
      const session = sessions.get(id);
      assert(session, `Requested known session ${id}`);
      if (action === 'messages') {
        if (holdMessage) {holdMessage=false; await new Promise(resolve => {releaseMessage=resolve; releases.add(resolve);});}
        session.status='succeeded';
        session.turns.push({id:`turn-${session.turns.length+1}`,...body,status:'succeeded',events:[],reply:'Fixture review complete.',usage:null,error:null});
      }
      return route.fulfill({json:session});
    });
    const openModels = async () => {if (!(await page.locator('#agent-model-picker').isVisible())) await page.locator('#agent-model-toggle').click();};
    const selectModel = async id => {await openModels(); await page.locator(`[data-agent-model="${id}"]`).click();};
    const send = async message => {
      const before = requests.filter(request => request.action === 'messages').length;
      await page.locator('#chat-input').fill(message);
      await page.locator('#chat-send').click();
      await page.waitForFunction(() => document.querySelector('#agent-status').textContent.includes('succeeded'));
      assert.equal(requests.filter(request => request.action === 'messages').length,before+1);
      return [...sessions.values()].at(-1);
    };
    await page.goto(local.url);
    await page.waitForFunction(() => !document.body.inert);
    await page.locator('#project-select').selectOption(local.projects[0].id);
    await page.waitForFunction(() => !document.querySelector('#chat-send').disabled);
    assert.equal(await page.locator('#agent-fast-toggle').count(),1,'The CLI composer exposes a Fast mode control');
    assert.equal(await page.locator('#agent-fast-toggle').isVisible(),false,'Fast is not offered for the Model API backend');
    assert.equal(await page.locator('#agent-effort').count(),1,'The composer exposes a labeled effort selector');
    assert.equal(await page.locator('#agent-effort').inputValue(),'','Model API effort uses the provider default');
    assert.equal(await page.locator('#agent-effort').isDisabled(),true);
    await page.locator('#chat-input').fill('Keep my design draft');
    await page.locator('[data-context="testbenches/pre.cir"]').check();
    holdCatalog={backend:'codex',initial:true}; releaseCatalog=null;
    await page.locator('#agent-backend').selectOption('codex');
    await waitUntil(() => releaseCatalog,'The first Codex catalog request reached the server');
    await page.locator('#agent-backend').selectOption('claude');
    await openModels();
    await page.locator('[data-agent-model="sonnet"]').waitFor();
    await page.keyboard.press('Escape');
    await page.locator('#agent-backend').selectOption('codex');
    await openModels();
    assert.equal(await page.locator('#agent-model-refresh').isDisabled(),true,'The original Codex catalog is still pending after a backend round trip');
    releaseCatalog();
    await page.locator('[data-agent-model="gpt-5.4"]').waitFor();
    assert.equal(await page.locator('#agent-model-refresh').isDisabled(),false,'Returning to a pending backend resolves its catalog instead of leaving it loading');
    assert.doesNotMatch(await page.locator('#agent-model-catalog-status').innerText(),/Loading/);
    assert.equal(await page.locator('[data-agent-model="gpt-5.4-mini"]').count(),1,'Codex model discovery populates the chooser');
    assert.equal(await page.locator('#agent-fast-toggle').isDisabled(),true,'CLI default keeps the configured model unknown instead of inferring the catalog default');
    assert(catalogRequests.some(request => request.backend==='codex' && request.project===local.projects[0].id),'Model discovery is scoped to the selected project');
    await page.locator('#agent-model-refresh').click();
    await page.waitForFunction(() => !document.querySelector('#agent-model-refresh').disabled);
    assert(catalogRequests.some(request => request.backend==='codex' && request.refresh==='1'),'Refresh requests new CLI metadata');
    assert.equal(await page.locator('#chat-input').inputValue(),'Keep my design draft','Refreshing model metadata preserves the composer');
    await selectModel('gpt-5.4');
    await page.locator('#agent-mode').selectOption('automatic');
    assert.equal(await page.locator('#agent-auto-scope').isVisible(),true,'Native automatic mode exposes finite human scope controls');
    assert.match(await page.locator('#agent-permission-note').innerText(),/scope authorizes limited actions/,'Native automatic permission text describes the actual grant');
    await page.locator('#agent-mode').selectOption('read-only');
    assert.deepEqual(await page.locator('#agent-effort option').evaluateAll(options => options.map(option => option.value)),['','minimal','low','medium','high','xhigh','max','ultra'],'Every CLI-reported level is available, including max and ultra');
    assert.equal(await page.locator('#agent-effort').inputValue(),'','Catalog default does not silently set an override');
    await page.locator('#agent-file-input').setInputFiles({name:'effort-context.txt',mimeType:'text/plain',buffer:Buffer.from('Keep this attachment when configuring effort.')});
    await page.locator('#agent-attachments').getByText('effort-context.txt').waitFor();
    await page.locator('#agent-effort').focus();
    await page.keyboard.press('End');
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#agent-effort').inputValue(),'ultra','The native effort control supports keyboard selection');
    await page.locator('#agent-effort').selectOption('high');
    assert.equal(await page.locator('#chat-input').inputValue(),'Keep my design draft','Changing effort preserves the composer');
    assert.equal(await page.locator('[data-context="testbenches/pre.cir"]').isChecked(),true,'Changing effort preserves project context');
    assert.equal(await page.locator('#agent-attachments').getByText('effort-context.txt').count(),1,'Changing effort preserves attachments');
    const unsupported = await page.evaluate(async () => { try { await AFOperations.dispatch('ui.agent.set_config',{effort:'invented'}); return null; } catch (error) { return error.message; } });
    assert.match(unsupported,/not supported/i,'Semantic configuration validates the same reported effort choices');
    assert.equal(await page.locator('#agent-fast-toggle').isDisabled(),false);
    await page.locator('#agent-fast-toggle').click();
    assert.equal(await page.locator('#agent-fast-toggle').getAttribute('aria-pressed'),'true');
    assert.equal(await page.locator('#chat-input').inputValue(),'Keep my design draft','Changing Fast mode preserves the draft');
    assert.equal(await page.locator('[data-context="testbenches/pre.cir"]').isChecked(),true,'Changing Fast mode preserves selected project context');
    const fastSession = await send('Fast circuit review');
    assert.equal(fastSession.backend,'codex');
    assert.equal(fastSession.model,'gpt-5.4');
    assert.equal(fastSession.fast_mode,true,'Fast mode reaches the session creation API');
    assert.equal(fastSession.effort,'high','Effort reaches session creation independently of Fast');
    await page.locator('#chat-input').fill('Next unsent circuit question');
    await page.locator('#agent-effort').selectOption('medium');
    assert.equal(await page.locator('#agent-session').inputValue(),'','Changing effort starts a new conversation');
    assert.equal(await page.locator('#chat-input').inputValue(),'Next unsent circuit question');
    await page.locator('#agent-fast-toggle').click();
    assert.equal(await page.locator('#agent-session').inputValue(),'','Changing Fast mode starts a new conversation');
    assert.equal(await page.locator('#chat-input').inputValue(),'Next unsent circuit question');
    const normalSession = await send('Normal circuit review');
    assert.equal(normalSession.fast_mode,false,'Turning Fast off creates an ordinary session');
    assert.equal(normalSession.effort,'medium');
    await page.locator('#agent-history-toggle').click();
    await page.locator(`#agent-history-list [data-agent-session="${fastSession.id}"]`).click();
    await page.waitForFunction(id => document.querySelector('#agent-session').value===id && !document.querySelector('#agent-backend').disabled,fastSession.id);
    assert.equal(await page.locator('#agent-fast-toggle').getAttribute('aria-pressed'),'true','Restoring a conversation restores its actual Fast mode');
    assert.equal(await page.locator('#agent-effort').inputValue(),'high','History restores the recorded effort');
    await page.locator('#agent-history-toggle').click();
    await page.locator(`#agent-history-list [data-agent-session="${normalSession.id}"]`).click();
    await page.waitForFunction(id => document.querySelector('#agent-session').value===id && !document.querySelector('#agent-backend').disabled,normalSession.id);
    assert.equal(await page.locator('#agent-fast-toggle').getAttribute('aria-pressed'),'false');
    assert.equal(await page.locator('#agent-effort').inputValue(),'medium');
    await selectModel('gpt-5.4-mini');
    assert.equal(await page.locator('#agent-effort').inputValue(),'medium','Changing models retains a supported effort');
    assert.equal(await page.locator('#agent-fast-toggle').isDisabled(),true,'Model capabilities disable unsupported Fast mode');
    await selectModel('gpt-5.4');
    await page.locator('#agent-effort').selectOption('ultra');
    await selectModel('gpt-5.4-mini');
    assert.equal(await page.locator('#agent-effort').inputValue(),'','An incompatible effort resets to Default');
    await selectModel('gpt-5.4');
    await page.locator('#agent-fast-toggle').click();
    holdMessage = true;
    await page.locator('#chat-input').fill('Request pending at the transport');
    await page.locator('#chat-send').click();
    await page.waitForFunction(() => document.querySelector('#agent-backend').disabled);
    assert.equal(await page.locator('#agent-fast-toggle').isDisabled(),true,'Pending requests lock Fast mode');
    assert.equal(await page.locator('#agent-model-toggle').isDisabled(),true,'Pending requests lock model selection');
    assert.equal(await page.locator('#agent-effort').isDisabled(),true,'Pending requests lock effort selection');
    await waitUntil(() => releaseMessage,'The pending message reached the server');
    releaseMessage();
    await page.waitForFunction(() => !document.querySelector('#agent-backend').disabled);
    await page.locator('#agent-backend').selectOption('claude');
    assert.equal(await page.locator('#agent-fast-toggle').isDisabled(),true,'Claude default does not assume the underlying configured model');
    await selectModel('sonnet');
    assert.equal(await page.locator('#agent-effort').isDisabled(),true,'A model without confirmed effort support offers Default only');
    assert.equal(await page.locator('#agent-fast-toggle').isDisabled(),true,'Sonnet cannot enable Opus-only Fast mode');
    assert.equal(await page.locator('#agent-fast-toggle').getAttribute('aria-pressed'),'false','Changing backend clears the previous Fast preference');
    await selectModel('opus');
    assert.deepEqual(await page.locator('#agent-effort option').evaluateAll(options => options.map(option => option.value)),['','low','medium','high','max']);
    await page.locator('#agent-effort').selectOption('max');
    assert.equal(await page.locator('#agent-fast-toggle').isDisabled(),false);
    await page.locator('#agent-fast-toggle').click();
    const claudeSession = await send('Opus fast review');
    assert.equal(claudeSession.backend,'claude');
    assert.equal(claudeSession.model,'opus');
    assert.equal(claudeSession.fast_mode,true,'Claude Fast mode is sent with an eligible model');
    assert.equal(claudeSession.effort,'max');
    await openModels();
    await page.locator('#agent-model').fill('custom-private-model');
    await page.locator('#agent-model-apply').click();
    assert.equal(await page.locator('#assistant-model').innerText(),'custom-private-model','Custom model IDs remain usable');
    assert.equal(await page.locator('#agent-fast-toggle').isDisabled(),true,'An unknown custom model does not claim Fast support');
    assert.equal(await page.locator('#agent-fast-toggle').getAttribute('aria-pressed'),'false');
    assert.equal(await page.locator('#agent-effort').inputValue(),'','Unknown custom models clear an incompatible effort');
    await page.waitForFunction(() => document.querySelector('#agent-effort').title.includes('could not be confirmed'));
    await openModels();
    await page.locator('#agent-model').fill('private-supported-model');
    await page.locator('#agent-model-apply').click();
    await page.waitForFunction(() => !document.querySelector('#agent-effort').disabled);
    await page.locator('#agent-effort').selectOption('ultra');
    assert(catalogRequests.some(request => request.model === 'private-supported-model'),'Custom effort capabilities are resolved by the backend instead of model-name guesses');
    await page.locator('#agent-effort').selectOption('high');
    await selectModel('opus');
    await selectModel('private-supported-model');
    assert.equal(await page.locator('#agent-effort').inputValue(),'high','A cached custom model retains a known-compatible effort');
    holdEffort = 'late-private-model';
    await openModels();
    await page.locator('#agent-model').fill(holdEffort);
    await page.locator('#agent-model-apply').click();
    await waitUntil(() => releaseEffort,'Custom-model capability lookup reached the backend');
    assert.equal(await page.locator('#agent-effort').inputValue(),'','An unverified custom model uses Default during discovery');
    assert.equal(await page.locator('#agent-effort').isDisabled(),true,'Pending capability discovery locks effort');
    await page.locator('#agent-backend').selectOption('api');
    releaseEffort();
    await page.waitForTimeout(100);
    assert.deepEqual(await page.locator('#agent-effort option').evaluateAll(options => options.map(option => option.value)),[''],'Late custom-model metadata cannot change another backend');
    assert.equal(await page.locator('#agent-fast-toggle').isVisible(),false);
    const apiSession = await send('Ordinary Model API request');
    assert.equal(Boolean(apiSession.fast_mode),false,'Model API sessions never inherit CLI Fast mode');
    assert.equal(apiSession.effort,null,'Default is explicitly represented as null without a provider override');

    // A legacy CLI conversation has no recorded speed override. Presenting it
    // as Off would make a false claim about inherited native CLI configuration.
    sessions.set('legacy-cli-session',{id:'legacy-cli-session',project_id:local.projects[0].id,backend:'codex',model:'gpt-5.4',mode:'read-only',title:'Legacy CLI conversation',status:'succeeded',created_at:new Date().toISOString(),turns:[],pending_approval:null,usage:null});
    await page.locator('#agent-history-toggle').click();
    await page.locator('#agent-history-list [data-agent-session="legacy-cli-session"]').click();
    await page.waitForFunction(() => document.querySelector('#agent-session').value==='legacy-cli-session' && !document.querySelector('#agent-backend').disabled);
    assert.equal(await page.locator('#agent-fast-toggle').getAttribute('aria-pressed'),'mixed','Legacy CLI sessions expose inherited speed as unknown');
    assert.equal(await page.locator('#agent-effort').inputValue(),'','Legacy sessions without effort preserve native defaults');
    assert.match(await page.locator('#agent-fast-toggle').getAttribute('title'),/inherit/i,'The inherited speed state is explained accessibly');
    await page.locator('#chat-input').fill('Keep my legacy conversation draft');
    await page.locator('#agent-fast-toggle').click();
    assert.equal(await page.locator('#agent-session').inputValue(),'','Choosing an explicit speed starts a new conversation');
    assert.equal(await page.locator('#agent-fast-toggle').getAttribute('aria-pressed'),'false','The first click from inherited speed explicitly disables Fast');
    assert.equal(await page.locator('#chat-input').inputValue(),'Keep my legacy conversation draft');
    await send('Explicit standard speed after legacy history');
    assert.equal(requests.filter(request => !request.id && request.method==='POST').at(-1).body.fast_mode,false,'A new conversation sends an explicit false speed override');

    // Delayed catalogs from an old backend or project must not replace the
    // current chooser, even when the asynchronous request succeeds later.
    await page.locator('#agent-backend').selectOption('codex');
    await openModels();
    await page.locator('[data-agent-model="gpt-5.4"]').waitFor();
    holdCatalog={backend:'codex',model:'stale-codex-model'}; releaseCatalog=null;
    await page.locator('#agent-model-refresh').click();
    await waitUntil(() => releaseCatalog,'The Codex refresh reached the server');
    await page.keyboard.press('Escape');
    await page.locator('#agent-backend').selectOption('claude');
    await openModels();
    await page.locator('[data-agent-model="sonnet"]').waitFor();
    releaseCatalog();
    await page.waitForTimeout(100);
    assert.equal(await page.locator('[data-agent-model="stale-codex-model"]').count(),0,'A late Codex catalog cannot overwrite Claude models');
    holdCatalog={backend:'claude',model:'stale-project-model'}; releaseCatalog=null;
    await page.locator('#agent-model-refresh').click();
    await waitUntil(() => releaseCatalog,'The Claude refresh reached the server');
    await page.keyboard.press('Escape');
    await page.locator('#project-select').selectOption(local.projects[1].id);
    await page.waitForFunction(() => !document.querySelector('#chat-send').disabled);
    await page.locator('#agent-backend').selectOption('claude');
    await openModels();
    await page.locator('[data-agent-model="sonnet"]').waitFor();
    releaseCatalog();
    await page.waitForTimeout(100);
    assert.equal(await page.locator('[data-agent-model="stale-project-model"]').count(),0,'A late catalog cannot cross projects');
    await selectModel('opus');
    await page.locator('#agent-fast-toggle').click();
    await page.locator('#agent-effort').selectOption('high');
    await page.locator('#chat-input').fill('Persist the effort draft');
    await page.reload();
    await page.waitForFunction(() => !document.querySelector('#agent-effort').disabled);
    assert.equal(await page.locator('#agent-effort').inputValue(),'high','The project draft restores effort after reload');
    assert.equal(await page.locator('#chat-input').inputValue(),'Persist the effort draft');

    // Capture the actual chooser in both themes and screen sizes. Keeping its
    // bounds inside the viewport protects mouse and touch access to the model
    // list and custom model controls.
    const screenshotDir='/tmp/af-agent-models';
    await fs.mkdir(screenshotDir,{recursive:true});
    for (const width of [1500,390]) {
      await page.keyboard.press('Escape');
      await page.setViewportSize({width,height:width===390?844:1050});
      if (width===390 && !(await page.locator('#assistant-panel').isVisible())) await page.locator('#toggle-ai').click();
      for (const theme of ['dark','light']) {
        await page.evaluate(value => document.documentElement.dataset.theme=value,theme);
        await openModels();
        const bounds=await page.locator('#agent-model-picker').boundingBox();
        assert(bounds.x>=0 && bounds.x+bounds.width<=width+1,`Model picker fits ${width}px ${theme} viewport`);
        assert(bounds.y>=0 && bounds.y+bounds.height<=(width===390?844:1050)+1,'Model picker controls remain vertically reachable');
        const effortBounds=await page.locator('#agent-effort').boundingBox();
        assert(effortBounds.x>=0 && effortBounds.x+effortBounds.width<=width+1,'Effort control remains inside the compact viewport');
        await page.screenshot({path:`${screenshotDir}/models-${width}-${theme}.png`});
      }
    }
    await page.keyboard.press('Escape');
    await page.locator('#toggle-ai').click();
    await page.setViewportSize({width:1500,height:1050});
    await page.locator('#agent-expand').click();
    assert.equal(await page.locator('#agent-effort').isVisible(),true,'Effort remains available in the expanded assistant');
    await page.screenshot({path:`${screenshotDir}/effort-expanded.png`});
    await page.locator('#agent-expand').click();
    await page.locator('#agent-effort').click();
    await page.screenshot({path:`${screenshotDir}/effort-choices.png`});
    await page.keyboard.press('Escape');
    await page.locator('.tabs [data-tab=settings]').click();
    await page.locator('#ui-language').selectOption('zh-CN');
    await page.waitForFunction(() => document.documentElement.lang==='zh-CN' && !document.querySelector('#ui-language').disabled);
    await page.locator('#ui-theme').selectOption('light');
    await page.waitForFunction(() => document.documentElement.dataset.theme==='light' && !document.querySelector('#ui-theme').disabled);
    await openModels();
    assert.match(await page.locator('#agent-model-catalog-status').innerText(),/[\u4e00-\u9fff]/,'Model catalog guidance follows the selected language');
    assert.equal(await page.locator('#agent-fast-toggle').getAttribute('aria-pressed'),'true','A language change preserves the selected Fast preference');
    assert.equal(await page.locator('#agent-effort').getAttribute('aria-label'),'思考强度');
    assert.equal(await page.locator('#agent-effort').inputValue(),'high','Localization preserves effort');
    assert.equal(await page.locator('#agent-effort option:checked').innerText(),'高');
    await page.screenshot({path:`${screenshotDir}/models-1500-zh-light.png`});
    assert.deepEqual(errors,[],'Model and Fast interactions do not raise browser errors');
    console.log('Agent models passed: CLI catalogs, model-aware effort/defaults, Fast independence, session/draft persistence, attachments/context, keyboard/localization, pending locks, stale responses, and compact/expanded themes.');
  } finally {
    for (const release of releases) release();
    if (browser) await browser.close();
    lines.close();
    child.stdin.end('\n');
    if (child.exitCode === null) await once(child,'exit');
  }
})().catch(error => {console.error(error);process.exitCode=1;});
