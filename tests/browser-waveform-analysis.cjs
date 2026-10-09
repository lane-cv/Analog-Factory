// Isolated real run; controlled raw-data fixture exercises complex UI math.
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const baseURL = process.env.AF_URL || 'http://127.0.0.1:8883';
(async () => {
  const browser = await chromium.launch({channel:'chromium',headless:true,args:['--no-sandbox']});
  try {
    const context = await browser.newContext({viewport:{width:1500,height:1000}});
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(baseURL); await page.locator('body:not([inert])').waitFor();
    await page.waitForFunction(() => window.AFOperations?.status().client_id);
    const client_id = await page.evaluate(() => AFOperations.status().client_id);
    const call = async (operation_id,parameters = {},ok = true) => {
      const response = await context.request.post(`${baseURL}/api/operations/invoke`,{data:{operation_id,parameters:{client_id,...parameters}}});
      const result = await response.json(); assert.equal(result.ok,ok,JSON.stringify(result.error)); return result;
    };
    const project = await (await context.request.post(`${baseURL}/api/projects`,{data:{name:'Waveform display verification',template:'amplifier'}})).json();
    await call('ui.workspace.select_project',{project_id:project.id});
    const started = await (await context.request.post(`${baseURL}/api/projects/${project.id}/runs`,{data:{kind:'simulate'}})).json();
    for (let attempt=0;attempt<200;attempt++) {
      const run = await (await context.request.get(`${baseURL}/api/projects/${project.id}/runs/${started.id}`)).json();
      if (!['queued','running'].includes(run.status)) { assert.equal(run.status,'succeeded'); break; }
      await new Promise(resolve => setTimeout(resolve,50));
      if (attempt===199) throw new Error('Fixture simulation timed out');
    }
    const ac = {title:'AC fixture',plot:'AC Analysis',variables:[{name:'frequency',unit:'Hz',type:'frequency'},{name:'v(out)',unit:'V',type:'voltage'},{name:'v(in)',unit:'V',type:'voltage'}],points:[[1,2,1],[10,Math.SQRT2,1],[100,0.2,1]],complex_points:[[[1,0],[2,0],[1,0]],[[10,0],[1,-1],[1,0]],[[100,0],[0,-.2],[1,0]]]};
    const tran = {title:'Transient fixture',plot:'Transient Analysis',variables:[{name:'time',unit:'s',type:'time'},{name:'v(out)',unit:'V',type:'voltage'}],points:[[0,0],[.1,-1],[.2,1]]};
    const pulse = {...tran,title:'Retained narrow pulse',points:Array.from({length:16001},(_,i)=>[i,i===1?9:0]),
      display_sampling:{method:'bucket_extrema',returned_points:16001,original_points:1000000,
        description:'Display samples preserve bucket extrema; the original raw artifact is authoritative.'}};
    await page.route('**/runs/*/waveforms', route => route.fulfill({json:{...ac,plots:[ac,tran,pulse]}}));
    await call('ui.runs.select',{run_id:started.id});
    await page.locator('#waveform-component').waitFor({timeout:4000});
    await page.locator('#waveform-component').selectOption('phase');
    await call('ui.waveform.set_cursor',{index:1});
    assert.match(await page.locator('#waveform-cursor').textContent(),/-45°/);
    const applied = await call('ui.waveform.set_display',{component:'db',x_scale:'log',reference:2});
    assert.equal(applied.data.state.waveform.component,'db');
    assert.equal(await page.locator('#waveform-component').inputValue(),'db');
    assert.equal(await page.locator('#waveform-x-scale').inputValue(),'log');
    await call('ui.waveform.set_cursor',{index:0});
    assert.match(await page.locator('#waveform-cursor').textContent(),/6\.02.*dB/);
    const downloadPromise = page.waitForEvent('download');
    await page.locator('[data-action="export-waveform"]').click();
    const download = await downloadPromise;
    const stream = await download.createReadStream(), chunks = [];
    for await (const chunk of stream) chunks.push(chunk);
    const csv = Buffer.concat(chunks).toString();
    assert.match(csv,/v\(out\) \/ v\(in\) \[dB\]/);
    assert.match(csv,/6\.020599/);
    await call('ui.waveform.select_plot',{index:1});
    assert.equal(await page.locator('#waveform-plot').inputValue(),'1');
    assert.equal(await page.locator('#waveform-x-scale').inputValue(),'linear');
    await call('ui.waveform.set_display',{x_scale:'log'},false);
    await call('ui.waveform.set_display',{component:'phase'},false);
    assert.equal(await page.locator('#waveform-component').inputValue(),'magnitude');
    await page.locator('#waveform-plot').selectOption('0');
    assert.equal((await call('ui.workspace.read')).data.state.waveform.plot,0);
    // Inspect actual painted trace geometry: a second uniform stride must not
    // discard the narrow peak retained by the bounded rawfile parser.
    await page.evaluate(()=>{
      window.paintedPulse=false;
      const lineTo=CanvasRenderingContext2D.prototype.lineTo;
      CanvasRenderingContext2D.prototype.lineTo=function(x,y){
        if(this.canvas.id==='waveform'&&Math.abs(this.lineWidth-1.7)<.01&&y<100)window.paintedPulse=true;
        return lineTo.call(this,x,y);
      };
    });
    await call('ui.waveform.select_plot',{index:2});
    await page.waitForFunction(()=>window.paintedPulse,{},{timeout:3000});
    assert.match(await page.locator('#waveform-sampling').innerText(),/16,001.*(?:1,000,000|1\.000e\+6)/);
    const sampledDownload=page.waitForEvent('download');
    await page.locator('[data-action="export-waveform"]').click();
    assert.match((await sampledDownload).suggestedFilename(),/-display\.csv$/);
    assert.deepEqual(errors,[]);
    console.log('Waveform browser checks passed: manual/agent parity, phase/dB/log, plot selection, CSV and invalid views.');
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
