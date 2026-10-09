// Actual sidebar, streaming provider fixture, image payload and scoped file effect.
const assert = require('node:assert/strict');
const path = require('node:path');
const readline = require('node:readline');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {chromium} = require('playwright');
const harness = String.raw`
import json,sys,tempfile,threading,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from analog_factory.service import Workspace
from analog_factory.server import create_server
class Endpoint(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_POST(self):
        payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
        def send(delta,finish=None):
            self.wfile.write(('data: '+json.dumps({'choices':[{'index':0,'delta':delta,'finish_reason':finish}]})+'\n\n').encode());self.wfile.flush()
        messages=payload['messages']
        if any('scope notes' in str(item.get('content','')) for item in messages if item['role']=='user'):
            if messages[-1]['role']!='tool':
                send({'tool_calls':[{'index':0,'id':'write-1','type':'function','function':{'name':'write_file','arguments':json.dumps({'path':'notes/approved.txt','content':'actual scoped browser write'})}}]},'tool_calls')
            else: send({'content':'Scoped note saved.'},'stop')
        else:
            content=messages[-1]['content']
            if not isinstance(content,list) or not any(item.get('type')=='image_url' for item in content):
                send({'content':'MISSING IMAGE'},'stop')
            else:
                send({'content':'Live image '});time.sleep(2);send({'content':'review complete.'},'stop')
        self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()
with tempfile.TemporaryDirectory(prefix='af-agent-capabilities-') as root:
    provider=ThreadingHTTPServer(('127.0.0.1',0),Endpoint)
    threading.Thread(target=provider.serve_forever,daemon=True).start()
    workspace=Workspace(Path(root));project=workspace.projects.create('Agent capabilities')
    workspace.save_settings({'provider':{'base_url':f'http://127.0.0.1:{provider.server_port}','model':'local-fixture','stream':True,'capabilities':{'streaming':True,'images':True,'tools':True}}})
    server=create_server(workspace,port=0,desktop_url='');threading.Thread(target=server.serve_forever,daemon=True).start()
    print(json.dumps({'url':f'http://127.0.0.1:{server.server_port}','project':project['id']}),flush=True)
    try:sys.stdin.readline()
    finally:
        workspace.agents.close();workspace.operations.close();server.shutdown();server.server_close();provider.shutdown();provider.server_close()
`;
(async () => {
  const child = spawn('python3',['-u','-c',harness],{cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
  let stderr = '', browser;
  child.stderr.on('data',data => {stderr += data;});
  const lines = readline.createInterface({input:child.stdout});
  try {
    const info = await new Promise((resolve,reject) => {
      const timer = setTimeout(() => reject(new Error(stderr || 'Server timeout')),15000);
      lines.once('line',line => {clearTimeout(timer);resolve(JSON.parse(line));});
      child.once('exit',code => {clearTimeout(timer);reject(new Error(`Server ${code}: ${stderr}`));});
    });
    browser = await chromium.launch({channel:'chromium',headless:true,args:['--no-sandbox']});
    const page = await browser.newPage({viewport:{width:1500,height:1050}}), errors = [];
    page.on('pageerror',error => errors.push(error.message));
    await page.goto(info.url);
    await page.waitForFunction(() => window.AFOperations?.status().client_id && !document.body.inert);
    await page.evaluate(async project => AFOperations.dispatch('ui.workspace.select_project',{project_id:project}),info.project);
    await page.locator('#chat-input').fill('Inspect image');
    const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jvWQAAAAASUVORK5CYII=','base64');
    await page.locator('#agent-file-input').setInputFiles({name:'plot.png',mimeType:'image/png',buffer:png});
    await page.locator('#agent-attachments').getByText('Image: plot.png').waitFor();
    await page.locator('#chat-send').click();
    await page.locator('#chat-messages').getByText('Live image', {exact:true}).waitFor({timeout:5000});
    await page.locator('#chat-messages').getByText('Live image review complete.',{exact:true}).waitFor({timeout:5000});
    await page.waitForFunction(() => AFOperations.snapshot().agent.status === 'succeeded');
    await page.locator('#agent-new').click();
    await page.locator('#agent-mode').selectOption('automatic');
    await page.locator('#agent-auto-scope summary').click();
    await page.locator('[data-scope-paths]').fill('notes/'); await page.locator('[data-scope-paths]').press('Tab');
    await page.locator('[data-scope-actions]').fill('1'); await page.locator('[data-scope-actions]').press('Tab');
    await page.locator('[data-scope-runs]').fill('0'); await page.locator('[data-scope-runs]').press('Tab');
    await page.locator('[data-scope-operation][value="run.start"]').uncheck();
    await page.locator('#chat-input').fill('Write scope notes');
    await page.locator('#chat-send').click();
    await page.waitForFunction(() => AFOperations.snapshot().agent.status === 'succeeded');
    const receipt = await page.evaluate(async pid => {
      const file = await AFOperations.invoke('file.read',{project_id:pid,path:'notes/approved.txt'});
      const session = await AFOperations.invoke('agent.session.get',{project_id:pid,session_id:AFOperations.snapshot().agent.session_id});
      return {file:file.data,session:session.data};
    },info.project);
    assert.equal(receipt.file.content,'actual scoped browser write');
    assert.equal(receipt.session.scope.max_actions,1);
    assert.equal(receipt.session.scope.max_runs,0);
    assert.deepEqual(receipt.session.scope.paths,['notes/']);
    assert.equal(await page.locator('#agent-approval').isVisible(),false);
    assert.deepEqual(errors,[]);
    console.log('PASS: real sidebar streamed image response and bounded human-scope file write');
  } finally {
    if (browser) await browser.close();
    child.stdin.end('\n');
    await once(child,'exit');
    lines.close();
    if (stderr) process.stderr.write(stderr);
  }
})().catch(error => {console.error(error);process.exitCode=1;});
