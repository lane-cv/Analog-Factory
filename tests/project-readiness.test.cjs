const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const modulePath = require('node:path').resolve(__dirname, '../analog_factory/static/project-readiness.js');

function assess(template, status) {
  assert.ok(fs.existsSync(modulePath), 'Project readiness module must exist');
  return require(modulePath).assess(template, status);
}

function environment() {
  return {project_id: null, toolchain: {
    tools: Object.fromEntries(['ngspice', 'xschem', 'magic', 'netgen'].map(name => [name, {available: true}])),
    pdk: {available: true, variant: 'sky130A', ota5: {available: true, diagnostic: 'OTA assets checked', paths: {
      models: '/pdk/sky130A/libs.tech/combined/sky130.lib.spice',
      xschem: '/pdk/sky130A/libs.tech/xschem/xschemrc',
      magic: '/pdk/sky130A/libs.tech/magic/sky130A.magicrc',
      netgen: '/pdk/sky130A/libs.tech/netgen/sky130A_setup.tcl'
    }}}, desktop: {available: true}
  }};
}

test('educational projects can be created without simulator or optional GUI tools', () => {
  const status = environment();
  for (const tool of Object.values(status.toolchain.tools)) tool.available = false;
  status.toolchain.desktop.available = false;
  status.toolchain.pdk.available = false;
  const result = assess('amplifier', status);
  assert.equal(result.canCreate, true);
  assert.equal(result.canSimulate, false);
  assert.equal(result.checks.find(check => check.id === 'ngspice').status, 'missing');
  assert.equal(result.checks.find(check => check.id === 'desktop').required, false);
});

test('educational simulation needs only a confirmed simulator', () => {
  const status = environment();
  delete status.toolchain.pdk;
  delete status.toolchain.desktop;
  delete status.toolchain.tools.xschem;
  const result = assess('amplifier', status);
  assert.equal(result.canCreate, true);
  assert.equal(result.canSimulate, true);
  assert.equal(result.checks.find(check => check.id === 'xschem').status, 'unknown');
});

test('OTA creation uses explicit combined-model asset qualification rather than generic PDK availability', () => {
  const status = environment();
  status.toolchain.pdk.ota5 = {available: false, diagnostic: 'Missing libs.tech/combined/sky130.lib.spice'};
  const result = assess('ota5', status);
  assert.equal(result.canCreate, false);
  assert.equal(result.canSimulate, false);
  const check = result.checks.find(check => check.id === 'ota5_assets');
  assert.equal(check.status, 'missing');
  assert.equal(check.required, true);
  assert.match(check.reason, /combined/);
});

test('OTA creation requires no installed native GUI executable when its assets are prepared', () => {
  const status = environment();
  status.toolchain.tools.xschem.available = false;
  status.toolchain.tools.magic.available = false;
  status.toolchain.tools.netgen.available = false;
  status.toolchain.desktop.available = false;
  const result = assess('ota5', status);
  assert.equal(result.canCreate, true);
  assert.equal(result.canSimulate, true);
});

test('OTA creation stays available when generic PDK qualification blocks the simulation service', () => {
  const status = environment();
  status.toolchain.pdk.available = false;
  const result = assess('ota5', status);
  assert.equal(result.canCreate, true);
  assert.equal(result.canSimulate, false);
  assert.equal(result.checks.find(check => check.id === 'pdk').status, 'missing');
});

test('missing OTA qualification or missing required asset paths remains unknown', () => {
  for (const ota5 of [undefined, {available: true}, {available: true, paths: {models: '/pdk/model'}}]) {
    const status = environment();
    status.toolchain.pdk.ota5 = ota5;
    const result = assess('ota5', status);
    assert.equal(result.canCreate, false);
    assert.equal(result.checks.find(check => check.id === 'ota5_assets').status, 'unknown');
  }
});

test('generic SKY130 remains creatable but its shipped placeholder requires project editing before simulation', () => {
  for (const available of [true, false]) {
    const status = environment();
    status.toolchain.pdk.available = available;
    const result = assess('sky130', status);
    assert.equal(result.canCreate, true);
    assert.equal(result.canSimulate, false);
    assert.equal(result.checks.find(check => check.id === 'model_library').status, 'action_required');
  }
});

test('loading errors and missing status never claim a tool is ready', () => {
  for (const status of [null, undefined, {}, {error: 'Network failure'}, {toolchain: {}}]) {
    const result = assess('amplifier', status);
    assert.equal(result.canCreate, true);
    assert.equal(result.canSimulate, false);
    assert.equal(result.known, false);
    assert.equal(result.checks.find(check => check.id === 'ngspice').status, 'unknown');
    assert.equal(assess('ota5', status).canCreate, false);
  }
});

test('selected-project overrides cannot qualify a new project', () => {
  const status = environment();
  status.project_id = 'a'.repeat(32);
  const result = assess('ota5', status);
  assert.equal(result.canCreate, false);
  assert.equal(result.canSimulate, false);
  assert.equal(result.known, false);
  assert.ok(result.checks.every(check => check.status === 'unknown'));
});

test('truthy malformed availability values do not qualify tools', () => {
  const status = environment();
  status.toolchain.tools.ngspice.available = 'true';
  assert.equal(assess('amplifier', status).canSimulate, false);
  assert.equal(assess('amplifier', status).checks.find(check => check.id === 'ngspice').status, 'unknown');
  assert.equal(assess('unrecognized', environment()).canCreate, false);
});

test('browser build exposes the same readiness behavior without mutating status', () => {
  assert.ok(fs.existsSync(modulePath), 'Project readiness module must exist');
  const context = {window: {}};
  vm.runInNewContext(fs.readFileSync(modulePath, 'utf8'), context);
  const status = environment();
  const before = JSON.stringify(status);
  assert.equal(context.window.AFProjectReadiness.assess('ota5', status).canCreate, true);
  assert.equal(JSON.stringify(status), before);
});
