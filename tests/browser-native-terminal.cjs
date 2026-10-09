// Real browser + PTY fixture. Never sends a paid model turn to an installed CLI.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {chromium}=require('playwright');
const base=process.env.AF_URL||'http://127.0.0.1:8765';
(async()=>{
  const fixtureDir=fs.mkdtempSync(path.join(os.tmpdir(),'af-native-browser-'));
  const executable=path.join(fixtureDir,'native-fixture');
  fs.writeFileSync(executable,`#!/usr/bin/env python3
import sys,os,json
if '--help' in sys.argv:
 print('--sandbox --ask-for-approval --no-alt-screen --no-daemon --model');sys.exit(0)
if '--version' in sys.argv:
 print('local-browser-fixture 1.0');sys.exit(0)
print('\\x1b[32mPTY READY\\x1b[0m tty='+str(os.isatty(0)),flush=True)
print('Human approval? [y/N]',flush=True)
for line in sys.stdin:
 line=line.rstrip('\\n')
 if line=='quit':break
 if line=='size':print('SIZE '+str(os.get_terminal_size(0)),flush=True)
 else:print('HUMAN '+line,flush=True)
`,{mode:0o700});
  const browser=await chromium.launch({channel:'chromium',headless:true,args:['--no-sandbox']});
  let page,project;
  try {
    page=await browser.newPage({viewport:{width:1500,height:1200}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(base);await page.locator('body:not([inert])').waitFor();
    await page.waitForFunction(()=>AFOperations.names().includes('ui.native_terminal.open'));
    const invoke=(operation_id,parameters={},options={})=>page.evaluate(({operation_id,parameters,options})=>AFOperations.invoke(operation_id,parameters,options),{operation_id,parameters,options});
    const dispatch=(name,parameters={})=>page.evaluate(({name,parameters})=>AFOperations.dispatch(name,parameters),{name,parameters});
    project=(await invoke('project.create',{name:'Native terminal browser fixture'})).data;
    const revision=await invoke('project.detail',{project_id:project.id});
    await invoke('project.configure',{project_id:project.id,config:{tools:{codex:executable}}},{expected_version:revision.version});
    await page.reload();await page.locator('body:not([inert])').waitFor();
    await dispatch('ui.workspace.select_project',{project_id:project.id});
    await page.locator('[data-native-terminal-open]').click();
    const dialog=page.locator('#native-terminal-dialog');
    await dialog.locator('[data-terminal-action=start]').click();
    const screen=dialog.locator('.xterm-rows');
    await screen.getByText('PTY READY tty=True',{exact:false}).waitFor();
    assert.match(await screen.innerText(),/Human approval\? \[y\/N\]/);
    assert.doesNotMatch(await screen.innerText(),/HUMAN /,'Native approvals must await a human keystroke');
    const terminalID=await page.evaluate(()=>AFOperations.snapshot().native_terminal.terminal_id);
    const tty=dialog.locator('.xterm-helper-textarea');
    await tty.focus();await page.keyboard.type('n');await page.keyboard.press('Enter');
    await screen.getByText('HUMAN n',{exact:true}).waitFor();
    await page.keyboard.type('browser-secret-free-input');await page.keyboard.press('Enter');
    await screen.getByText('HUMAN browser-secret-free-input',{exact:true}).waitFor();
    const snap=await page.evaluate(()=>AFOperations.snapshot());
    assert.doesNotMatch(JSON.stringify(snap),/browser-secret-free-input|PTY READY|Human approval/);
    await dialog.locator('[data-terminal-option=cols]').fill('110');
    await dialog.locator('[data-terminal-option=cols]').press('Tab');
    await page.waitForFunction(()=>AFOperations.snapshot().native_terminal.options.cols===110);
    await tty.focus();await page.keyboard.type('size');await page.keyboard.press('Enter');
    await screen.getByText('SIZE os.terminal_size(columns=110, lines=30)',{exact:true}).waitFor();
    await dialog.locator('[data-terminal-action=close]').first().click();
    assert.equal((await invoke('native.terminal.get',{project_id:project.id,terminal_id:terminalID})).data.status,'running');
    await page.locator('[data-native-terminal-open]').click();
    await screen.getByText('HUMAN browser-secret-free-input',{exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>AFOperations.snapshot().native_terminal.terminal_id),terminalID);
    assert.equal((await invoke('native.terminal.list',{project_id:project.id})).data.terminals.length,1);

    // Owner-origin UI commands use the same acknowledged controller; an agent
    // cannot invoke these approve-capable commands (covered by Python tests).
    const remote=async(operation_id,parameters={})=>{
      let ready;
      for(let attempt=0;attempt<100;attempt++){
        ready=await page.evaluate(async()=>{
          const result=await AFOperations.invoke('ui.sessions');
          const session=result.data.sessions.find(item=>item.client_id===AFOperations.status().client_id);
          return session&&JSON.stringify(session.state)===JSON.stringify(AFOperations.snapshot())?{client_id:session.client_id,ui_version:session.version}:null;
        });
        if(ready)break;await page.waitForTimeout(100);
      }
      assert.ok(ready,'Terminal state must be acknowledged before remote UI command');
      return invoke(operation_id,{...ready,...parameters});
    };
    await remote('ui.native_terminal.input',{text:'acknowledged-human-input\n'});
    await screen.getByText('HUMAN acknowledged-human-input',{exact:true}).waitFor();
    await remote('ui.native_terminal.resize',{cols:100,rows:28});
    assert.equal(await page.evaluate(()=>AFOperations.snapshot().native_terminal.options.rows),28);
    await remote('ui.native_terminal.stop');
    assert.equal((await invoke('native.terminal.get',{project_id:project.id,terminal_id:terminalID})).data.status,'cancelled');
    await dispatch('ui.native_terminal.close');
    assert.deepEqual(errors,[]);
    console.log('Native terminal browser: actual PTY, ANSI renderer, human approval input, resize, detach/reattach, private snapshot and acknowledged UI input/stop passed.');
  } finally {
    if(page&&project)await page.evaluate(async pid=>{
      const result=await AFOperations.invoke('native.terminal.list',{project_id:pid});
      for(const row of result.data.terminals)if(['queued','running'].includes(row.status))await AFOperations.invoke('native.terminal.stop',{project_id:pid,terminal_id:row.id});
    },project.id).catch(()=>{});
    await browser.close();fs.rmSync(fixtureDir,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error);process.exit(1);});
