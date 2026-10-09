// Run only against a disposable workspace. Exercises real registry persistence.
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const base = process.env.AF_URL || 'http://127.0.0.1:8765';

(async () => {
  const browser = await chromium.launch({channel:'chromium',headless:true, args:['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport:{width:1500,height:1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base);
    await page.locator('body:not([inert])').waitFor();
    await page.waitForFunction(() => window.AFOperations?.names().includes('ui.lifecycle.open'));
    const invoke = (operation_id, parameters = {}, options = {}) => page.evaluate(
      ({operation_id,parameters,options}) => AFOperations.invoke(operation_id, parameters, options),
      {operation_id,parameters,options});
    const mutate = async (operation, parameters) => {
      const read = await invoke(parameters.project_id ? 'project.detail' : 'project.list',
        parameters.project_id ? {project_id:parameters.project_id} : {});
      return invoke(operation, parameters, {expected_version:read.version});
    };
    const dispatch = (name, parameters = {}) => page.evaluate(({name,parameters}) =>
      AFOperations.dispatch(name, parameters), {name,parameters});
    const created = (await invoke('project.create', {name:'Lifecycle browser fixture'})).data;
    await page.reload();
    await page.locator('body:not([inert])').waitFor();
    await dispatch('ui.workspace.select_project', {project_id:created.id});
    await mutate('file.write', {project_id:created.id,path:'notes.txt',content:'original content'});
    await mutate('file.write', {project_id:created.id,path:'notes.txt',content:'revised content'});
    await page.waitForFunction(() => AFOperations.snapshot().workspace.files.includes('notes.txt'));
    await dispatch('ui.editor.open', {path:'notes.txt'});
    const dialog = page.locator('#project-lifecycle-dialog');
    const submit = async () => {
      await dialog.locator('button[type=submit]').click();
      await page.waitForFunction(() => !document.querySelector('#project-lifecycle-dialog').open);
      await page.waitForFunction(() => !AFOperations.snapshot().lifecycle.busy);
    };

    // Native mouse/keyboard interaction reaches the exact registered business handler.
    await page.locator('#project-lifecycle-controls [data-lifecycle-open=manage]').click();
    await dialog.locator('[data-lifecycle-open=rename]').click();
    await dialog.locator('[name=name]').fill('Lifecycle renamed in browser');
    await submit();
    assert.equal((await invoke('project.detail', {project_id:created.id})).data.project.name, 'Lifecycle renamed in browser');

    await dispatch('ui.lifecycle.open', {action:'move'});
    await dialog.locator('[name=destination]').fill('docs/moved.txt');
    await submit();
    assert.equal((await invoke('file.read', {project_id:created.id,path:'docs/moved.txt'})).data.content, 'revised content');
    const history = (await invoke('file.history', {project_id:created.id,path:'docs/moved.txt'})).data;
    assert.equal(history.length, 1);
    assert.equal(history[0].path, 'docs/moved.txt');
    assert.equal(await page.evaluate(() => AFOperations.snapshot().editor.path), 'docs/moved.txt');

    // Pending dialog revision cannot overwrite a concurrent project rename.
    await dispatch('ui.lifecycle.open', {action:'rename'});
    await dispatch('ui.lifecycle.set_draft', {name:'Stale browser rename'});
    await mutate('project.rename', {project_id:created.id,name:'Concurrent registry rename'});
    const stale = await page.evaluate(async () => {
      try { await AFOperations.dispatch('ui.lifecycle.submit'); return null; }
      catch (error) { return error.code; }
    });
    assert.equal(stale, 'VERSION_CONFLICT');
    assert.equal((await invoke('project.detail', {project_id:created.id})).data.project.name, 'Concurrent registry rename');
    await dispatch('ui.lifecycle.close');

    await dispatch('ui.lifecycle.open', {action:'file_trash'});
    await submit();
    const trash = (await invoke('file.trash.list', {project_id:created.id})).data;
    assert.equal(trash.length, 1);
    assert.equal(await page.evaluate(() => AFOperations.snapshot().editor.path), null);
    await dispatch('ui.lifecycle.open', {action:'manage'});
    await dialog.locator(`[data-lifecycle-restore-file="${trash[0].id}"]`).click();
    await dialog.locator('[name=destination]').fill('restored.txt');
    await submit();
    assert.equal((await invoke('file.read', {project_id:created.id,path:'restored.txt'})).data.content, 'revised content');
    assert.equal((await invoke('file.history', {project_id:created.id,path:'restored.txt'})).data[0].id, history[0].id);

    // A real file picker restores a checksummed backup with all source history.
    const backup = (await invoke('project.backup', {project_id:created.id})).data;
    await dispatch('ui.lifecycle.open', {action:'import'});
    await dialog.locator('[name=name]').fill('Restored from browser ZIP');
    await dialog.locator('[name=archive]').setInputFiles({name:'fixture.zip',mimeType:'application/zip',
      buffer:Buffer.from(backup.content_base64,'base64')});
    await page.waitForFunction(() => AFOperations.snapshot().lifecycle.archive_loaded);
    await submit();
    const importedId = await page.evaluate(() => AFOperations.snapshot().project_id);
    assert.notEqual(importedId, created.id);
    assert.equal((await invoke('file.read', {project_id:importedId,path:'restored.txt'})).data.content, 'revised content');
    assert.equal((await invoke('file.history', {project_id:importedId,path:'restored.txt'})).data[0].id, history[0].id);

    await dispatch('ui.lifecycle.open', {action:'trash'});
    await submit();
    assert.ok(!(await invoke('project.list')).data.some(project => project.id === importedId));
    await dispatch('ui.lifecycle.open', {action:'manage'});
    await dialog.locator(`[data-lifecycle-restore-project="${importedId}"]`).click();
    await submit();
    assert.equal(await page.evaluate(() => AFOperations.snapshot().project_id), importedId);

    // Server-issued semantic UI commands receive acknowledgements with real state.
    await page.waitForFunction(() => AFOperations.status().client_id);
    const remote = async (operation_id, parameters = {}) => {
      let ready;
      for (let attempt=0;attempt<100;attempt++) {
        const current = await invoke('ui.sessions');
        const local = await page.evaluate(() => ({id:AFOperations.status().client_id,state:AFOperations.snapshot()}));
        const session = current.data.sessions.find(item => item.client_id === local.id);
        if (session && JSON.stringify(session.state)===JSON.stringify(local.state)) {
          ready = {session,version:current.version}; break;
        }
        await new Promise(resolve => setTimeout(resolve,50));
      }
      assert.ok(ready, 'Frontend published current lifecycle state');
      const {session,version} = ready;
      return invoke(operation_id, {client_id:session.client_id,ui_version:session.version,...parameters},
        operation_id === 'ui.lifecycle.submit' ? {expected_version:version} : {});
    };
    await remote('ui.lifecycle.open', {action:'rename'});
    await remote('ui.lifecycle.set_draft', {name:'Acknowledged lifecycle command'});
    assert.equal(await dialog.locator('[name=name]').inputValue(), 'Acknowledged lifecycle command');
    const acknowledged = await remote('ui.lifecycle.submit');
    assert.equal(acknowledged.data.data.name, 'Acknowledged lifecycle command');
    assert.equal((await invoke('project.detail', {project_id:importedId})).data.project.name, 'Acknowledged lifecycle command');
    assert.deepEqual(errors, []);
    console.log('Lifecycle browser: rename/move/history/trash/restore/import, stale revision and acknowledged UI parity passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
