// Native edit scheduled after a registered read handler but before its outer envelope.
'use strict';
const assert=require('node:assert/strict'),path=require('node:path'),readline=require('node:readline');
const {spawn}=require('node:child_process'),{once}=require('node:events'),{chromium}=require('playwright');
const harness=String.raw`
import json,sys,tempfile,threading
from dataclasses import replace
from pathlib import Path
from analog_factory.service import Workspace
from analog_factory.server import create_server
with tempfile.TemporaryDirectory(prefix='af-editor-version-race-') as temp:
    ws=Workspace(Path(temp)); cases={}
    for kind in ('open','preview','large','refresh','save'):
        pid=ws.projects.create('Editor '+kind+' version race')['id']
        source='a\n'*40000 if kind=='large' else 'opened base\n'
        filename='large.txt' if kind=='large' else 'notes.txt'
        ws.projects.write(pid,filename,source)
        cases[pid]={'kind':kind,'path':filename,'source':source,'reads':0,'fired':False}
    read=ws.operations.operations['file.read'];preview=ws.operations.operations['file.change.preview'];write=ws.operations.operations['file.write']
    def read_race(context,params):
        data=read.handler(context,params);case=cases[params['project_id']]
        if params['path']==case['path']:
            case['reads']+=1
            if not case['fired'] and ((case['kind']=='open' and case['reads']==1) or (case['kind'] in ('large','refresh') and case['reads']==2)):
                case['fired']=True
                (ws.projects.path(params['project_id'])/params['path']).write_text('native unreviewed edit\n')
        return data
    def preview_race(context,params):
        data=preview.handler(context,params);case=cases[params['project_id']]
        if case['kind']=='preview' and not case['fired']:
            case['fired']=True
            (ws.projects.path(params['project_id'])/params['path']).write_text('native unreviewed edit\n')
        return data
    def write_race(context,params):
        data=write.handler(context,params);case=cases[params['project_id']]
        if case['kind']=='save' and not case['fired']:
            case['fired']=True
            (ws.projects.path(params['project_id'])/params['path']).write_text('native unreviewed edit\n')
        return data
    ws.operations.operations['file.read']=replace(read,handler=read_race)
    ws.operations.operations['file.change.preview']=replace(preview,handler=preview_race)
    ws.operations.operations['file.write']=replace(write,handler=write_race)
    server=create_server(ws,0,desktop_url='');threading.Thread(target=server.serve_forever,daemon=True).start()
    print(json.dumps({'url':f'http://127.0.0.1:{server.server_port}','cases':[{'project_id':pid,**case} for pid,case in cases.items()]}),flush=True)
    try:sys.stdin.readline()
    finally:
        ws.agents.close();server.shutdown();server.server_close();ws.operations.close()
`;
(async()=>{
 const child=spawn('python3',['-u','-c',harness],{cwd:path.resolve(__dirname,'..'),stdio:['pipe','pipe','pipe']});
 let browser,stderr='';child.stderr.on('data',data=>{stderr+=data;});const lines=readline.createInterface({input:child.stdout});
 try {
  const info=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Fixture did not start: '+stderr)),15000);lines.once('line',line=>{clearTimeout(timer);resolve(JSON.parse(line));});child.once('exit',code=>{clearTimeout(timer);reject(new Error('Fixture exited '+code+': '+stderr));});});
  browser=await chromium.launch({channel:'chromium',headless:true,args:['--no-sandbox']});
  for(const fixture of info.cases){
   const context=await browser.newContext(),page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
   await page.goto(info.url);await page.waitForFunction(()=>AFOperations.status().client_id);
   await page.evaluate(async fixture=>{
    await AFOperations.dispatch('ui.workspace.select_project',{project_id:fixture.project_id});
    await AFOperations.dispatch('ui.editor.open',{path:fixture.path});
    if(fixture.kind==='refresh')await refreshAgentProject(fixture.project_id);
    await AFOperations.dispatch('ui.editor.set_content',{content:fixture.kind==='large'?'b\n'.repeat(40000):'human draft\n'});
   },fixture);
   if(['preview','large'].includes(fixture.kind)){
    const inspected=await page.evaluate(()=>AFOperations.dispatch('ui.editor_recovery.inspect'));
    assert.equal(inspected.latest,fixture.source,'inspection retains the text actually checked by its handler');
    await page.evaluate(async id=>AFOperations.dispatch('ui.editor_recovery.resolve',{inspection_id:id,content:'reviewed human merge\n'}),inspected.inspection_id);
   }
   const save=await page.evaluate(async()=>{try{await AFOperations.dispatch('ui.editor.save');return {ok:true};}catch(error){return {ok:false,message:error.message};}});
   assert.equal(save.ok,false,fixture.kind+' must reject the version added only by the outer envelope');
   const actual=await (await context.request.get(info.url+`/api/projects/${fixture.project_id}/files?path=${fixture.path}`)).json();
   assert.equal(actual.content,'native unreviewed edit\n',fixture.kind+' preserves the external writer');assert.deepEqual(errors,[]);
   await context.close();
  }
  console.log('Editor stable read version: actual open, preview, large-file, clean refresh and post-save handler→envelope races preserve native edits PASS.');
 }finally{if(browser)await browser.close();child.stdin.end('\n');await once(child,'exit');lines.close();}
})().catch(error=>{console.error(error);process.exit(1);});
