// Bounded File.slice upload and browser-managed streaming downloads, on a disposable server.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const {chromium}=require('playwright');
const base=process.env.AF_URL||'http://127.0.0.1:8765';
(async()=>{
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'af-transfer-browser-'));
  const zip=path.join(temp,'source.zip');
  execFileSync('python3',['-c',`import sys,zipfile,os
with zipfile.ZipFile(sys.argv[1],'w',zipfile.ZIP_STORED) as z:
 z.writestr('README.txt','Large artifact import fixture')
 with z.open('large.bin','w') as f:
  for i in range(5):f.write(os.urandom(1024*1024))
`,zip]);
  const browser=await chromium.launch({channel:'chromium',headless:true,args:['--no-sandbox']});
  try{
    const page=await browser.newPage({viewport:{width:1500,height:1000},acceptDownloads:true}),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(base);await page.locator('body:not([inert])').waitFor();
    await page.waitForFunction(()=>AFOperations.names().includes('ui.lifecycle.open'));
    await page.evaluate(()=>{
      File.prototype.arrayBuffer=function(){throw new Error('Whole-file archive read is forbidden');};
      window.transferChunks=[];
      const invoke=AFOperations.invoke;
      AFOperations.invoke=async function(name,parameters,options){
        if(name==='project.import.append')window.transferChunks.push(parameters.content_base64.length);
        return invoke(name,parameters,options);
      };
    });
    await page.evaluate(()=>AFOperations.dispatch('ui.lifecycle.open',{action:'import'}));
    const dialog=page.locator('#project-lifecycle-dialog');
    await dialog.locator('[name=name]').fill('Large imported browser artifact');
    await dialog.locator('[name=archive]').setInputFiles(zip);
    await page.waitForFunction(()=>AFOperations.snapshot().lifecycle.archive_loaded);
    await dialog.locator('button[type=submit]').click();
    await page.waitForFunction(()=>{
      const snapshot=AFOperations.snapshot(),selected=document.querySelector('#project-select')?.selectedOptions[0];
      return !snapshot.lifecycle.open && !snapshot.lifecycle.busy && Boolean(snapshot.project_id) &&
        selected?.value===snapshot.project_id && selected.textContent.trim()==='Large imported browser artifact';
    },null,{timeout:30000});
    const pid=await page.evaluate(()=>AFOperations.snapshot().project_id);
    const chunks=await page.evaluate(()=>window.transferChunks);
    assert.ok(chunks.length>20);assert.ok(chunks.every(size=>size<=349528));
    const metadata=await page.evaluate(pid=>AFOperations.invoke('artifact.stat',{project_id:pid,kind:'file',path:'large.bin'}),pid);
    assert.equal(metadata.data.size,5*1024*1024);
    const editorError=await page.evaluate(async pid=>{try{await AFOperations.invoke('file.read',{project_id:pid,path:'large.bin'});return '';}catch(e){return e.message;}},pid);
    assert.match(editorError,/2 MiB editor limit/);
    const downloaded=page.waitForEvent('download');
    await page.evaluate(()=>AFOperations.dispatch('ui.lifecycle.backup'));
    const download=await downloaded,output=path.join(temp,'backup.zip');await download.saveAs(output);
    execFileSync('python3',['-c',`import sys,zipfile,hashlib
with zipfile.ZipFile(sys.argv[1]) as before,zipfile.ZipFile(sys.argv[2]) as after:
 assert 'af-backup.json' in after.namelist()
 def digest(z):
  with z.open('large.bin') as f:return hashlib.file_digest(f,'sha256').hexdigest()
 assert digest(before)==digest(after)
`,zip,output]);
    const artifactDownload=page.waitForEvent('download');
    await page.evaluate(pid=>{const a=document.createElement('a');a.href=`/api/artifacts/download?project_id=${pid}&kind=file&path=large.bin`;a.click();},pid);
    const artifact=await artifactDownload;await artifact.saveAs(path.join(temp,'large.bin'));
    assert.equal(fs.statSync(path.join(temp,'large.bin')).size,5*1024*1024);
    assert.deepEqual(errors,[]);
    console.log('Large transfer browser: 5 MiB File.slice upload, unchanged editor bound, disk-backed backup and direct streamed artifact download passed.');
  }finally{await browser.close();fs.rmSync(temp,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exit(1);});
