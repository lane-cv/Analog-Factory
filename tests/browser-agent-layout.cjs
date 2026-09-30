// Actual app and CSS against a disposable server. Agent transcripts below are
// controlled display fixtures, not claims about a live model or EDA result.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
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
with tempfile.TemporaryDirectory(prefix='af-agent-layout-') as root:
    workspace = Workspace(Path(root))
    project = workspace.projects.create('Amplifier design')
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
  const child = spawn('python3', ['-u','-c',harness], {cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
  const lines = readline.createInterface({input:child.stdout});
  let stderr='', browser, page;
  child.stderr.on('data',chunk => {stderr+=chunk;});
  await fs.mkdir('/tmp/af-agent-ui',{recursive:true});
  try {
    const local=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error(`Server startup failed: ${stderr}`)),15000);
      lines.once('line',line=>{clearTimeout(timer);resolve(JSON.parse(line));});
      child.once('exit',code=>{clearTimeout(timer);reject(new Error(`Server exited ${code}: ${stderr}`));});
    });
    browser=await chromium.launch({headless:true,args:['--no-sandbox']});
    page=await browser.newPage({viewport:{width:1500,height:1000},deviceScaleFactor:1});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    const fixture={id:'layout-fixture',project_id:local.project.id,title:'Amplifier topology review · fixture',backend:'api',model:'circuit-review-model',mode:'read-only',status:'succeeded',created_at:'2026-09-30T09:30:00Z',updated_at:'2026-09-30T09:33:00Z',turns:[{
      id:'layout-turn',status:'succeeded',created_at:'2026-09-30T09:30:00Z',message:'Review the amplifier topology and the next verification steps. (Layout test fixture)',files:['testbenches/pre.cir'],events:[
        {type:'tool_call',tool:'read_file',tool_call_id:'read-circuit',arguments:{path:'testbenches/pre.cir'}},
        {type:'tool_result',tool:'read_file',tool_call_id:'read-circuit',text:'* Educational common-source amplifier\nVDD vdd 0 1.8\nR1 vdd out 10k\nM1 out in 0 0 nmos W=10u L=1u'},
        {type:'tool_call',tool:'list_runs',tool_call_id:'list-runs',arguments:{}},
        {type:'tool_result',tool:'list_runs',tool_call_id:'list-runs',text:'[]'}
      ],reply:'### A clear starting point\nThe selected testbench describes a **common-source amplifier**. The drain resistor converts the transistor current into an output voltage.\n\nBefore changing device sizes:\n1. Check the DC operating point and headroom.\n2. Sweep the small-signal gain and bandwidth.\n3. Inspect the transient response for clipping.\n\n```spice\n.op\n.ac dec 40 10 100Meg\n```\n\nThese are display fixtures; no simulation was executed.',usage:{input_tokens:842,output_tokens:184,total_tokens:1026},error:null
    }],pending_approval:null};
    const sessions=[fixture,{...fixture,id:'layout-fixture-two',title:'Bias point checklist · fixture',turns:[{id:'other-turn',message:'Which operating-point checks matter?',reply:'Inspect bias current and device headroom.',status:'succeeded'}]}];
    await page.route('**/api/agents*',route=>route.fulfill({json:{backends:[{id:'api',name:'Model API',available:true,modes:['read-only','confirm']},{id:'codex',name:'Codex CLI',available:true,modes:['read-only','workspace-write']}]}}));
    await page.route('**/api/projects/*/agent/sessions**',route=>{
      const url=new URL(route.request().url()),id=url.pathname.split('/')[6];
      return route.fulfill({json:id ? sessions.find(item=>item.id===id) : sessions});
    });
    await page.goto(local.url);
    await page.waitForFunction(()=>!document.body.inert && !document.querySelector('#chat-send').disabled);
    const visible=selector=>page.locator(selector).isVisible();
    const box=selector=>page.locator(selector).boundingBox();
    const screenshot=name=>page.screenshot({path:`/tmp/af-agent-ui/${name}.png`});
    const inside=async(selector,label)=>{await page.waitForFunction(selector=>{const bounds=document.querySelector(selector)?.getBoundingClientRect();return bounds && bounds.x>=0 && bounds.y>=0 && bounds.right<=innerWidth+1 && bounds.bottom<=innerHeight+1;},selector,{timeout:2000}).catch(()=>{});const bounds=await box(selector),viewport=page.viewportSize();assert(bounds && bounds.x>=0 && bounds.y>=0 && bounds.x+bounds.width<=viewport.width+1 && bounds.y+bounds.height<=viewport.height+1,`${label} remains within viewport: ${JSON.stringify(bounds)}`);};
    const assertNoInert=async()=>assert.equal(await page.locator('.central-workspace').evaluate(e=>e.inert),false,'Closed/expanded panel restores workspace interactivity');
    assert(await visible('#assistant-panel'),'Default desktop shows the agent sidebar');
    assert(await visible('.central-workspace'),'Default desktop keeps design workspace visible');
    assert.equal(Math.round((await box('.agent-chat-header')).height),32,'Conversation header follows the compact 32 px reference');
    assert((await box('#assistant-panel')).width<600,'Default assistant starts as a sidebar');
    await page.locator('#agent-history-toggle').click();
    await page.locator('#agent-history-list [data-agent-session="layout-fixture"]').click();
    await page.locator('#chat-input').fill('Keep this draft while changing the workspace view.');
    await page.evaluate(()=>{window.agentLayoutInput=document.querySelector('#chat-input');});
    await screenshot('sidebar-dark');
    await page.locator('#agent-expand').click();
    await page.waitForFunction(()=>document.body.classList.contains('agent-expanded'));
    assert.equal(await visible('.central-workspace'),false,'Expanded agent replaces the main design view');
    assert.equal(await page.evaluate(()=>document.querySelector('#chat-input')===window.agentLayoutInput),true,'Expansion reuses the composer DOM');
    assert.equal(await page.locator('#chat-input').inputValue(),'Keep this draft while changing the workspace view.');
    assert.equal(await page.locator('#agent-expand').getAttribute('aria-expanded'),'true');
    await inside('#chat-input','Expanded composer');
    await screenshot('workspace-dark');
    await page.locator('#agent-expand').click();
    assert(await visible('.central-workspace'));
    assert.equal(await page.evaluate(()=>document.querySelector('#chat-input')===window.agentLayoutInput),true);
    const handle=page.locator('#agent-composer-resizer');
    const height=()=>page.locator('#chat-form').evaluate(e=>Math.round(e.getBoundingClientRect().height));
    await handle.focus();const initial=await height();
    await page.keyboard.press('ArrowUp');assert(await height()>initial,'ArrowUp increases composer height');
    await page.keyboard.press('Home');assert.equal(await height(),Number(await handle.getAttribute('aria-valuemin')),'Home reaches minimum composer height');
    await page.keyboard.press('End');assert.equal(await height(),Number(await handle.getAttribute('aria-valuemax')),'End reaches maximum composer height');
    await handle.dblclick();
    const dragBounds=await handle.boundingBox(),startHeight=await height();
    await page.mouse.move(dragBounds.x+dragBounds.width/2,dragBounds.y+dragBounds.height/2);
    await page.mouse.down();await page.mouse.move(dragBounds.x+dragBounds.width/2,dragBounds.y-60,{steps:4});await page.mouse.up();
    assert(await height()>startHeight+40,'Pointer drag resizes the composer');
    assert.equal(await page.locator('body').evaluate(e=>e.classList.contains('resizing-panels')),false,'Pointer release completes resizing');
    await handle.dblclick();
    await page.locator('#agent-history-toggle').click();
    assert(await visible('#agent-history'));
    assert.equal(await visible('#chat-form'),false,'History replaces the composer');
    assert.equal(await visible('#chat-messages'),false,'History replaces the message list');
    await screenshot('history');
    await page.locator('#agent-history-close').click();
    assert.equal(await page.locator('#chat-input').inputValue(),'Keep this draft while changing the workspace view.');
    await page.locator('#agent-expand').click();
    await page.locator('[data-file="testbenches/pre.cir"]').click();
    await page.waitForFunction(()=>!document.body.classList.contains('agent-expanded') && Boolean(document.querySelector('#code-editor')));
    assert(await visible('.central-workspace'),'Selecting a source file leaves expanded chat and reveals the editor');
    assert.equal(await page.locator('#chat-input').inputValue(),'Keep this draft while changing the workspace view.');
    await page.locator('#agent-expand').click();
    await page.locator('.brand').click();
    await page.waitForFunction(()=>!document.body.classList.contains('agent-expanded'));
    assert(await visible('.central-workspace'),'Home navigation leaves expanded chat');
    await page.locator('.tabs [data-tab=settings]').click();
    await page.locator('#ui-theme').selectOption('light');
    await page.waitForFunction(()=>document.documentElement.dataset.theme==='light' && !document.querySelector('#ui-theme').disabled);
    await page.locator('#ui-language').selectOption('zh-CN');
    await page.waitForFunction(()=>document.documentElement.lang==='zh-CN' && !document.querySelector('#ui-language').disabled);
    assert.match(await page.locator('#agent-new').getAttribute('aria-label'),/[\u4e00-\u9fff]/);
    assert.match(await page.locator('#chat-input').getAttribute('placeholder'),/[\u4e00-\u9fff]/);
    assert.match(await page.locator('#chat-messages').innerText(),/common-source amplifier/,'Language change preserves authored content');
    await page.locator('#agent-expand').click();
    await screenshot('workspace-light');
    await page.locator('#agent-expand').click();
    await page.locator('#ui-language').selectOption('en');
    await page.waitForFunction(()=>!document.querySelector('#ui-language').disabled);
    await page.locator('#ui-theme').selectOption('dark');
    await page.waitForFunction(()=>!document.querySelector('#ui-theme').disabled);
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>document.querySelector('#toggle-ai').getAttribute('aria-expanded')==='false');
    await page.locator('#toggle-ai').click();
    assert(await visible('#panel-backdrop'));
    assert.equal(await page.locator('#assistant-panel').getAttribute('aria-modal'),'true');
    assert.equal(await page.locator('.central-workspace').evaluate(e=>e.inert),true);
    assert.equal(await page.locator('#assistant-panel').evaluate(e=>e.contains(document.activeElement)),true,'Opening mobile panel moves focus inside');
    await page.locator('#chat-send').focus();
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'agent-expand','Mobile Tab wraps to the first panel control');
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'chat-send','Mobile Shift+Tab wraps to the last panel control');
    await inside('#chat-input','Mobile composer');await inside('#chat-send','Mobile send action');
    await screenshot('mobile');
    await page.locator('#agent-model-toggle').click();
    assert(await visible('#agent-model-picker'));
    await page.keyboard.press('Escape');
    assert.equal(await visible('#agent-model-picker'),false,'Escape first dismisses the model menu');
    assert(await visible('#assistant-panel'),'Escape from a menu leaves mobile panel open');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'agent-model-toggle');
    await page.locator('#chat-input').fill('/');
    await page.locator('[data-agent-manage-quick]').click();
    const dialog=page.locator('#agent-quick-message-dialog');
    assert(await dialog.isVisible());
    assert.equal(await dialog.evaluate(e=>e.contains(document.activeElement)),true,'Native quick-message dialog receives focus');
    await dialog.locator('button[type=submit]').focus();
    await page.keyboard.press('Tab');
    assert.equal(await dialog.evaluate(e=>e.contains(document.activeElement) || document.activeElement === document.body),true,'Native dialog does not move focus into the underlying workspace');
    if (await page.evaluate(()=>document.activeElement===document.body)) await page.keyboard.press('Tab');
    assert.equal(await dialog.evaluate(e=>e.contains(document.activeElement)),true,'Native dialog resumes at its first field after browser chrome');
    await page.keyboard.press('Escape');
    assert.equal(await dialog.isVisible(),false,'Escape closes the native dialog');
    assert(await visible('#assistant-panel'),'Closing a native dialog keeps mobile panel open');
    await page.waitForFunction(()=>document.querySelector('#assistant-panel').contains(document.activeElement) && Boolean(document.activeElement.getClientRects().length),null,{timeout:2000});
    assert.equal(await page.evaluate(()=>document.activeElement.id),'chat-input','Closing the dialog restores visible composer focus inside the mobile panel');
    await page.locator('#agent-expand').click();
    assert.equal(await visible('#panel-backdrop'),false,'Expanded mobile workspace has no modal backdrop');
    await assertNoInert();
    assert.equal(await page.locator('#assistant-panel').getAttribute('aria-modal'),null);
    await inside('#chat-input','Expanded mobile composer');
    await page.locator('#agent-expand').click();
    assert(await visible('#panel-backdrop'),'Collapse returns to the mobile overlay');
    assert.equal(await page.locator('.central-workspace').evaluate(e=>e.inert),true);
    await page.locator('#agent-close').click();
    assert.equal(await visible('#assistant-panel'),false);await assertNoInert();
    assert.equal(await page.evaluate(()=>document.activeElement.id),'toggle-ai','Closing returns focus to the mobile trigger');
    await page.locator('#toggle-ai').click();
    await page.keyboard.press('Escape');
    assert.equal(await visible('#assistant-panel'),false,'Escape closes the overlay when no child menu is open');await assertNoInert();
    await page.locator('#toggle-ai').click();
    await page.locator('#chat-input').fill('Check the DC operating point next.');
    await page.setViewportSize({width:844,height:390});
    await inside('#chat-input','Landscape composer');await inside('#chat-send','Landscape send action');
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Landscape does not overflow horizontally');
    await screenshot('mobile-landscape');
    await page.locator('#agent-expand').click();
    await page.locator('#agent-close').click();
    assert.equal(await visible('#assistant-panel'),false,'Close works directly from the expanded mobile workspace');await assertNoInert();
    assert.deepEqual(errors,[]);
    console.log('Agent layout passed: desktop/sidebar expansion, preserved DOM and drafts, pointer/keyboard composer resize, history replacement, theme/language, mobile menus/dialog focus, landscape, and inert cleanup. Screenshots: /tmp/af-agent-ui/');
  } catch (error) {
    if (page) await page.screenshot({path:'/tmp/af-agent-ui/failure.png'}).catch(()=>{});
    throw error;
  } finally {
    if(browser)await browser.close();child.stdin.end('stop\n');
    const timer=setTimeout(()=>child.kill('SIGKILL'),7000);if(child.exitCode===null)await once(child,'exit');clearTimeout(timer);lines.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
