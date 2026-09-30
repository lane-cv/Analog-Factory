'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const context = {window:{}, URL};
const source = path.join(__dirname, '../analog_factory/static/agent-content.js');
if (fs.existsSync(source)) vm.runInNewContext(fs.readFileSync(source, 'utf8'), context);
const content = context.window.AFAgentContent;

test('content renderer exposes the browser integration contract', () => {
  assert.ok(content, 'AFAgentContent module must exist');
  for (const name of ['markdown', 'renderTurn', 'exportSession', 'icon']) assert.equal(typeof content[name], 'function');
});

test('Markdown formats common model output without interpreting HTML', () => {
  const result = content.markdown('# Results\n\n**Gain** and *phase* with `V(out)`\n\n- stable\n- fast\n\n1. Simulate\n2. Inspect\n\n> Check headroom\n\n<img src=x onerror="alert(1)">');
  for (const fragment of ['<h1>Results</h1>', '<strong>Gain</strong>', '<em>phase</em>', '<code>V(out)</code>', '<ul>', '<ol', '<blockquote>', '&lt;img']) assert.ok(result.includes(fragment), fragment);
  assert.doesNotMatch(result, /<img|<script|<[^>]+\sonerror=/);
});

test('links allow only explicit HTTP(S) destinations and never fetch images', () => {
  const result = content.markdown('[safe](https://example.com/a?q=one&b=two) [bad](javascript:alert(1)) [encoded](%6a%61vascript:alert(1)) [entity](javascript&#58;alert(1)) [control](java\u0009script:alert(1)) ![plot](https://example.com/track.png)');
  assert.match(result, /href="https:\/\/example.com\/a\?q=one&amp;b=two"/);
  assert.equal((result.match(/<a /g) || []).length, 2, 'safe image becomes an explicit link, never an image request');
  assert.doesNotMatch(result, /<img|href="(?:javascript|%6a|data:|file:)/i);
  assert.match(result, /rel="noopener noreferrer"/);
});

test('fenced code preserves literal quotes, HTML, and exact copied content', () => {
  const code = 'print("<b>literal</b>")\nconst sample = `**x**`;';
  const result = content.markdown('```python\n' + code + '\n```');
  assert.match(result, /agent-code-language[^>]*>python</);
  assert.match(result, /&lt;b&gt;literal&lt;\/b&gt;/);
  assert.doesNotMatch(result, /<b>|<strong>/);
  assert.equal(decodeURIComponent(result.match(/data-copy-code="([^"]*)"/)[1]), code);
});

test('tables render escaped cells, alignment, and inline formatting', () => {
  const result = content.markdown('| Metric | Result |\n| :--- | ---: |\n| **Gain** | 60 dB |\n| `<x>` | stable |');
  assert.match(result, /<table>/);
  assert.match(result, /<th[^>]*>Metric<\/th>/);
  assert.match(result, /<strong>Gain<\/strong>/);
  assert.match(result, /<code>&lt;x&gt;<\/code>/);
  assert.doesNotMatch(result, /<x>/);
});

test('turn renderer shows plain user text, Markdown reply, and actionable copy/edit buttons', () => {
  const result = content.renderTurn({id:'turn-1', message:'**not bold** <script>', reply:'**Actual result**', status:'succeeded', events:[]});
  assert.match(result, /class="agent-message-user"[^>]*data-user-turn="turn-1"/);
  assert.match(result, /\*\*not bold\*\* &lt;script&gt;/);
  assert.match(result, /<strong>Actual result<\/strong>/);
  for (const action of ['copy-user', 'copy-reply', 'edit-resend']) assert.match(result, new RegExp(`data-agent-action="${action}"`));
  assert.doesNotMatch(result, /<script>/);
});

test('tool arguments remain visible when the call also contains display text', () => {
  const result = content.renderTurn({id:'turn-2', message:'inspect', reply:'Done', status:'succeeded', events:[
    {type:'tool_call', tool:'read_file', tool_call_id:'call-a', arguments:{path:'circuit.sp'}, text:'read_file'},
    {type:'tool_result', tool:'read_file', tool_call_id:'call-a', text:'R1 in out 10k'},
  ]});
  assert.match(result, /circuit\.sp/);
  assert.match(result, /R1 in out 10k/);
  assert.match(result, /<details[^>]*data-disclosure=/);
});

test('repeated tool names with native IDs remain distinct and pair with their own results', () => {
  const result = content.renderTurn({id:'parallel', message:'inspect', status:'succeeded', events:[
    {type:'tool_call', tool:'read_file', tool_call_id:'one', arguments:{path:'a.sp'}},
    {type:'tool_call', tool:'read_file', tool_call_id:'two', arguments:{path:'b.sp'}},
    {type:'tool_result', tool:'read_file', tool_call_id:'two', text:'second result'},
    {type:'tool_result', tool:'read_file', tool_call_id:'one', text:'first result'},
  ]});
  const tools = [...result.matchAll(/<details class="agent-tool"[^>]*data-tool-call-id="([^"]*)"[^>]*>([\s\S]*?)<\/details>/g)];
  assert.equal(tools.length, 2);
  assert.match(tools.find(match => match[1] === 'one')[2], /a\.sp[\s\S]*first result/);
  assert.match(tools.find(match => match[1] === 'two')[2], /b\.sp[\s\S]*second result/);
});

test('legacy tool calls without IDs do not merge separate invocations', () => {
  const result = content.renderTurn({id:'legacy', message:'inspect', status:'succeeded', events:[
    {type:'tool_call', tool:'command', text:'first command'}, {type:'tool_result', tool:'command', text:'first output'},
    {type:'tool_call', tool:'command', text:'second command'}, {type:'tool_result', tool:'command', text:'second output'},
  ]});
  assert.equal((result.match(/class="agent-tool"/g) || []).length, 2);
  const keys = [...result.matchAll(/data-disclosure="([^"]*)"/g)].map(match => match[1]);
  assert.equal(new Set(keys).size, keys.length);
});

test('reply events are not repeated and reasoning is shown only when provided', () => {
  const turn = {id:'reply', message:'hello', reply:'Single reply', status:'succeeded', events:[{type:'message', text:'Single reply'}]};
  assert.equal((content.renderTurn(turn).match(/Single reply/g) || []).length, 1);
  assert.doesNotMatch(content.renderTurn(turn), /agent-thinking/);
  turn.events.unshift({type:'thinking', text:'Provider reasoning summary'});
  assert.match(content.renderTurn(turn), /agent-thinking/);
  assert.match(content.renderTurn(turn), /Provider reasoning summary/);
});

test('tool error results are labeled failed and all unsafe detail text is escaped', () => {
  const result = content.renderTurn({id:'bad" onclick="bad', message:'<svg/onload=bad>', error:'<script>bad</script>', status:'failed', events:[
    {type:'tool_call', tool:'read_file', tool_call_id:'bad"', arguments:{path:'<unsafe>'}},
    {type:'tool_result', tool:'read_file', tool_call_id:'bad"', text:'{"error":"missing file"}'},
  ]});
  assert.match(result, /data-status="failed"/);
  assert.match(result, /&lt;script&gt;bad&lt;\/script&gt;/);
  assert.doesNotMatch(result, /<svg\/|<script|data-turn-id="bad" onclick=/);
});

test('usage and attachment facts render only from session data', () => {
  const result = content.renderTurn({id:'usage', message:'inspect', reply:'Done', status:'succeeded', files:['design.sp'], run_id:'run-123', events:[], usage:{input_tokens:12, output_tokens:3, total_tokens:15}});
  assert.match(result, /15/);
  assert.match(result, /design\.sp/);
  assert.match(result, /run-123/);
  assert.doesNotMatch(content.renderTurn({id:'empty', message:'hello', status:'running', events:[]}), /agent-usage/);
});

test('uploaded text attachments remain visible and their contents survive readable exports', () => {
  const turn = {id:'attached',message:'Review the attached netlist',reply:'Done',status:'succeeded',attachments:[{name:'new-circuit.sp',content:'* imported\nR2 out 0 1k'}],events:[]};
  assert.match(content.renderTurn(turn), /new-circuit\.sp/);
  for (const format of ['md','txt']) {
    const result = content.exportSession({id:'attachment-session',turns:[turn]}, format);
    assert.match(result.content, /new-circuit\.sp/);
    assert.match(result.content, /R2 out 0 1k/);
  }
});

test('JSON export preserves the public session exactly', () => {
  const session = {id:'export',title:'A/B <circuit>',backend:'api',mode:'read-only',turns:[{id:'a',message:'hello',reply:'result',events:[{type:'tool_call', arguments:{path:'a.sp'}}]}]};
  const result = content.exportSession(session, 'json');
  assert.deepEqual(JSON.parse(result.content), session);
  assert.equal(result.type, 'application/json;charset=utf-8');
  assert.match(result.filename, /\.json$/);
  assert.doesNotMatch(result.filename, /[<>/\\]/);
});

test('readable exports preserve messages and tool arguments/results without fabricated content', () => {
  const session = {id:'export',title:'Circuit check',backend:'api',mode:'read-only',turns:[{id:'a',message:'check **gain**',reply:'Gain is 12 dB',status:'succeeded',events:[{type:'tool_call',tool:'read_file',arguments:{path:'a.sp'},text:'read_file'},{type:'tool_result',tool:'read_file',text:'V1 in 0 1'}]}]};
  for (const format of ['md','txt']) {
    const result = content.exportSession(session, format);
    for (const text of ['check **gain**','Gain is 12 dB','a.sp','V1 in 0 1']) assert.ok(result.content.includes(text), text);
    assert.doesNotMatch(result.content, /<details|data-agent-action|Thought for/);
    assert.ok(result.filename.endsWith('.' + format));
  }
});

test('Markdown export retains a large CLI result with many backtick runs', () => {
  const output = '` '.repeat(150000) + '\n```````';
  const session = {id:'large-cli', turns:[{id:'large', message:'Inspect output', status:'succeeded', events:[{type:'tool_result', tool:'command_execution', text:output}]}]};
  const result = content.exportSession(session, 'md');
  assert.ok(result.content.includes('````````text\ntext: ' + output + '\n````````\n'), 'One safe fence encloses the exact 300 KB result');
});

test('Markdown export safely fences a maximum-size text attachment without truncating it', () => {
  const attachment = '` '.repeat(32765) + '\n`````';
  const session = {id:'large-attachment', turns:[{id:'attached', message:'Inspect attachment', attachments:[{name:'ticks.txt',content:attachment}], events:[]}]};
  const result = content.exportSession(session, 'md');
  assert.ok(result.content.includes('Attachment: ticks.txt\n``````text\n' + attachment + '\n``````\n'));
});
