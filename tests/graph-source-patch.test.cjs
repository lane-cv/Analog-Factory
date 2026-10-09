// The review controller must carry exact authority and never activate history.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const calls = [];
const window = {AFI18n: {t: value => value, html: (pieces, ...values) =>
  typeof pieces === 'string' ? pieces : pieces.reduce((text, piece, i) => text + piece + (values[i] ?? ''), '')},
  AFOperations: {invoke: async (name, params) => {calls.push({name, params}); return {data: {}};}}};
vm.runInNewContext(fs.readFileSync('analog_factory/static/graph-controls.js', 'utf8'), {window, document: {}});
const review = {node_id:'patch',kind:'source_patch',review_token:'a'.repeat(64),prompt:'Review resistor',
  value:{path:'testbenches/pre.cir',diff:'-RD=3300\n+RD=2200\n',current_sha256:'before',candidate_sha256:'after'}};
const live = window.AFGraphControls.jobHTML({id:'b'.repeat(32),schema_version:2,status:'running',pending_review:review});
assert.ok(live.includes(`data-graph-review-token="${review.review_token}"`));
assert.ok(live.includes('-RD=3300'));
assert.ok(!window.AFGraphControls.jobHTML({id:'b'.repeat(32),schema_version:2,status:'interrupted',pending_review:review})
  .includes('data-graph-action="review"'));
assert.ok(window.AFGraphControls.catalog.some(row => row[0] === 'source_patch'));
let listener;
const section = {dataset:{graphJob:'b'.repeat(32)},querySelector:() => null};
const button = {disabled:false,dataset:{graphAction:'review',graphNode:'patch',graphApproved:'true',graphReviewToken:review.review_token},
  closest:() => section};
const root = {dataset:{},contains:() => true,addEventListener:(type, callback) => {listener=callback;}};
window.AFGraphControls.bind(root,{projectId:'c'.repeat(32)});
(async () => {
  await listener({target:{closest:() => button},preventDefault(){}});
  assert.equal(calls[0].name,'job.control');
  assert.equal(calls[0].params.review_token,review.review_token);
  assert.equal(calls[0].params.approved,true);
  console.log('Source patch controller: token forwarding, complete diff and inactive historical review passed.');
})().catch(error => {console.error(error);process.exitCode=1;});
