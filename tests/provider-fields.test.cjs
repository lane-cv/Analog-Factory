const assert = require('node:assert/strict');
const {test} = require('node:test');
const fields = require('../analog_factory/static/provider-fields.js');

test('public config round trip retains capability controls and excludes credentials', () => {
  const config = {protocol:'ollama',model:'local',retries:2,concurrency:1,stream:true,
    capabilities:{streaming:true,images:true,generation_parameters:['temperature']},
    generation:{temperature:.4},model_capabilities:{local:{images:true}},api_key:'private',clear_api_key:true};
  const safe = fields.publicConfig(config);
  assert.equal(safe.api_key,undefined);assert.equal(safe.clear_api_key,undefined);
  assert.deepEqual(safe.generation,{temperature:.4});assert.equal(safe.retries,2);
  assert.deepEqual(safe.model_capabilities,{local:{images:true}});
});

test('form parameters are explicit and scoped, with unsupported values omitted', () => {
  const data = new FormData();
  for (const [key,value] of Object.entries({retries:'2',concurrency:'1',cap_streaming:'on',stream:'on',
    cap_tools:'on',cap_temperature:'on',generation_temperature:'.5',generation_top_p:'.7',model_capabilities:'{}'})) data.set('project_'+key,value);
  const result=fields.readAdvanced(data,'project_');
  assert.deepEqual(result.generation,{temperature:.5});assert.equal(result.stream,true);
  assert.deepEqual(result.capabilities.generation_parameters,['temperature']);
});

test('invalid structured capability input fails locally without losing the draft', () => {
  const data=new FormData();data.set('model_capabilities','[1,2]');
  assert.throws(()=>fields.readAdvanced(data),/object/);
});
