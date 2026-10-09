const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const sandbox = {window:{addEventListener(){}},URL,URLSearchParams,Headers};
vm.runInNewContext(fs.readFileSync('analog_factory/static/operations.js','utf8'),sandbox);
const original={resources:{packages:{torch:'2.9.1',tokenizers:'0.22.2'},
 checkpoint:{hashes:{'adapter_model.safetensors':'abc','tokenizer.json':'def'}}},
 API_KEY:'synthetic-not-a-real-secret',nested:[{name:'visible',authorization:'synthetic'}],
 max_tokens:96,public_count:3};
sandbox.window.AFOperations.register('training',{},()=>original);
const snapshot=JSON.parse(JSON.stringify(sandbox.window.AFOperations.snapshot()));
const canonical={project_id:null,training:{resources:{packages:{torch:'2.9.1'},
 checkpoint:{hashes:{'adapter_model.safetensors':'abc'}}},nested:[{name:'visible'}],public_count:3}};
assert.deepEqual(snapshot,canonical,'Published/comparison state must match server redaction even for public tokenizer metadata');
assert.equal(original.resources.packages.tokenizers,'0.22.2','Snapshot redaction must not mutate the live controller');
assert.equal(JSON.stringify(snapshot).includes('synthetic'),false);
console.log('Canonical frontend snapshot matches recursive server redaction without mutating controllers.');
