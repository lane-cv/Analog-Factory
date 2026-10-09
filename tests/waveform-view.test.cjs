const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const modulePath = path.join(__dirname, '../analog_factory/static/waveform-view.js');
assert.ok(fs.existsSync(modulePath), 'The shared waveform display implementation is missing');
const waveforms = require(modulePath);

const complex = {
  title: 'RC amplifier', plot: 'AC Analysis',
  variables: [{name:'frequency',unit:'Hz',type:'frequency'}, {name:'v(out)',unit:'V',type:'voltage'}, {name:'v(in)',unit:'V',type:'voltage'}],
  points: [[1,2,1],[10,Math.SQRT2,1],[100,0,0]],
  complex_points: [[[1,0],[2,0],[1,0]], [[10,0],[1,-1],[0,1]], [[100,0],[0,0],[0,0]]]
};

test('complex gain uses the selected input phasor and preserves raw measurements', () => {
  const before = JSON.stringify(complex);
  const result = waveforms.view(complex, {component:'phase',reference:2});
  assert.equal(result.points[0][1], 0);
  assert.equal(result.points[1][1], -135);
  assert.equal(result.points[2][1], null);
  assert.equal(result.variables[1].unit, '°');
  assert.match(result.variables[1].name, /v\(out\).*v\(in\)/);
  assert.equal(JSON.stringify(complex), before);
});

test('dB uses 20 log10 of amplitude and marks zero gain undefined', () => {
  const result = waveforms.view(complex, {component:'db'});
  assert.ok(Math.abs(result.points[0][1]-6.020599913279624) < 1e-12);
  assert.equal(result.points[2][1], null);
  assert.equal(result.variables[1].unit, 'dB re 1 V');
  assert.equal(waveforms.view(complex,{component:'db',reference:2}).variables[1].unit, 'dB');
});

test('unwrap removes phase jumps without changing independently selected traces', () => {
  const data = structuredClone(complex);
  data.complex_points = [170,-170,-150].map((angle,index) => [[10**index,0], [Math.cos(angle*Math.PI/180),Math.sin(angle*Math.PI/180)], [1,0]]);
  const result = waveforms.view(data,{component:'phase',unwrap:true});
  result.points.forEach((row,index) => assert.ok(Math.abs(row[1]-[170,190,210][index]) < 1e-9));
  assert.deepEqual(result.points.map(row => row[2]), [0,0,0]);
});

test('real and imaginary components use the stored complex values', () => {
  assert.deepEqual(waveforms.view(complex,{component:'real'}).points.map(row => row[1]),[2,1,0]);
  assert.deepEqual(waveforms.view(complex,{component:'imaginary'}).points.map(row => row[1]),[0,-1,0]);
});

test('plot selection switches variables and samples and validates target', () => {
  const transient = {title:'Transient',plot:'Transient Analysis',variables:complex.variables.slice(0,2),points:[[0,-1],[1,1]]};
  const data = {...complex,plots:[complex,transient]};
  assert.deepEqual(waveforms.view(data,{plot:1}).points, transient.points);
  assert.equal(waveforms.plots(data).length,2);
  assert.throws(() => waveforms.view(data,{plot:2}),/plot/i);
  assert.throws(() => waveforms.view(data,{plot:1,component:'phase'}),/complex/i);
  assert.throws(() => waveforms.view(data,{reference:0}),/reference/i);
});

test('log domain maps decades equally, roundtrips cursor and rejects nonpositive scales', () => {
  const result = waveforms.axis([1,10,100,1000,10000],'log');
  assert.equal(result.forward(100), 2);
  assert.equal(result.inverse(2), 100);
  assert.deepEqual(result.bounds,[0,4]);
  assert.throws(() => waveforms.axis([0,1,10],'log'),/positive/i);
  assert.throws(() => waveforms.axis([10,1,100],'linear'),/increasing/i);
});

test('CSV contains selected plot transformed values, quoted names and units, blank undefined values', () => {
  const data = structuredClone(complex); data.variables[1].name = 'v(a,b)';
  const csv = waveforms.csv(waveforms.view(data,{component:'db'}));
  assert.match(csv,/"v\(a,b\) \[dB re 1 V\]"/);
  assert.match(csv,/100,,\r?\n?$/);
  assert.ok(!csv.includes('Infinity'));
});

test('unknown view settings and malformed complex dimensions fail explicitly', () => {
  assert.throws(() => waveforms.view(complex,{component:'unknown'}),/component/i);
  assert.throws(() => waveforms.view(complex,{plot:-1}),/plot/i);
  const data = structuredClone(complex); data.complex_points.pop();
  assert.throws(() => waveforms.view(data,{component:'phase'}),/complex/i);
});
