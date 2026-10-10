'use strict';

// Independent, deliberately small Markdown renderer. Model output is always text;
// only the elements built here become HTML, and links require an explicit web URL.
window.AFAgentContent = (() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const asText = value => typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value, null, 2);
  const array = value => Array.isArray(value) ? value : [];
  const running = value => ['running','waiting_approval','queued','starting','in_progress'].includes(value);
  const encoded = value => encodeURIComponent(String(value).toWellFormed ? String(value).toWellFormed() : String(value).replace(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/g, '\uFFFD'));
  const symbols = {
    copy:'<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h4"/>',
    edit:'<path d="m16 3 5 5M4 15 16 3a3.5 3.5 0 0 1 5 5L9 20l-6 1z"/>',
    chevron:'<path d="m9 5 7 7-7 7"/>',
    down:'<path d="m6 9 6 6 6-6"/>',
    tool:'<path d="M14 6a5 5 0 0 0-6 6L2 18l4 4 6-6a5 5 0 0 0 6-6l-4 4-4-4z"/>',
    file:'<path d="M14 2H5a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9zM14 2v7h7M7 14h10M7 18h7"/>',
    run:'<path d="m8 4 12 8-12 8z"/>',
    check:'<path d="m5 12 4 4L19 6"/>',
    plus:'<path d="M12 5v14M5 12h14"/>',
    close:'<path d="m6 6 12 12M6 18 18 6"/>',
    history:'<path d="M3 11a9 9 0 1 1 2 7M3 4v7h7M12 7v5l3 2"/>',
    download:'<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
    search:'<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
    settings:'<path d="m12 2 2 3 4-1 1 4 3 2-2 3 1 4-4 1-2 3-3-2-4 1-1-4-3-2 2-3-1-4 4-1z"/><circle cx="12" cy="12" r="3"/>',
  };
  function icon(name) {
    return `<svg class="agent-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${symbols[name] || symbols.tool}</svg>`;
  }
  function safeLink(value) {
    if (!/^https?:\/\/[^\s<>"\\]+$/i.test(value) || /[\u0000-\u001f\u007f]/.test(value)) return null;
    try { const url = new URL(value); return ['http:','https:'].includes(url.protocol) && url.hostname ? value : null; }
    catch (_) { return null; }
  }
  function linkAt(text) {
    const match = text.match(/^(!?)\[([^\]\n]*)\]\(/);
    if (!match) return null;
    let end = match[0].length, depth = 1;
    for (; end < text.length && depth; end++) {
      if (text[end] === '\n') return null;
      if (text[end] === '(') depth++;
      else if (text[end] === ')') depth--;
    }
    if (depth) return null;
    return {length:end, image:Boolean(match[1]), label:match[2], url:text.slice(match[0].length, end - 1)};
  }
  function inline(text, depth = 0) {
    if (depth > 6) return escape(text);
    let result = '', cursor = 0;
    while (cursor < text.length) {
      const rest = text.slice(cursor);
      if (rest[0] === '\\' && /^[\\`*_[\]{}()#+.!|>~-]$/.test(rest[1] || '')) { result += escape(rest[1]); cursor += 2; continue; }
      const ticks = rest.match(/^`+/);
      if (ticks) {
        const end = rest.indexOf(ticks[0], ticks[0].length);
        if (end >= ticks[0].length) { result += `<code>${escape(rest.slice(ticks[0].length, end))}</code>`; cursor += end + ticks[0].length; continue; }
      }
      const link = linkAt(rest);
      if (link) {
        const href = safeLink(link.url), label = inline(link.label, depth + 1);
        result += href ? `<a href="${escape(href)}" target="_blank" rel="noopener noreferrer">${label || escape(href)}</a>` : label;
        cursor += link.length; continue;
      }
      let matched = false;
      for (const [mark, tag] of [['**','strong'],['__','strong'],['*','em'],['_','em']]) {
        if (!rest.startsWith(mark) || /\s/.test(rest[mark.length] || ' ') || (mark[0] === '_' && cursor > 0 && /[\w]/.test(text[cursor - 1]))) continue;
        const end = rest.indexOf(mark, mark.length);
        if (end > mark.length && !/\s/.test(rest[end - 1])) {
          result += `<${tag}>${inline(rest.slice(mark.length, end), depth + 1)}</${tag}>`;
          cursor += end + mark.length; matched = true; break;
        }
      }
      if (!matched) { result += escape(text[cursor]); cursor++; }
    }
    return result;
  }
  function tableCells(line) {
    let text = line.trim().replace(/^\|/, '').replace(/\|\s*$/, ''), cells = [], value = '', code = false;
    for (let i = 0; i < text.length; i++) {
      if (text[i] === '\\' && text[i + 1] === '|') { value += '\\|'; i++; }
      else if (text[i] === '`') { code = !code; value += text[i]; }
      else if (text[i] === '|' && !code) { cells.push(value.trim()); value = ''; }
      else value += text[i];
    }
    cells.push(value.trim());
    return cells;
  }
  const listMatch = line => line.match(/^\s{0,3}([-+*]|\d+[.)])\s+(.+)$/);
  const blockStart = line => /^\s*$|^\s{0,3}(?:#{1,6}\s|`{3,}|~{3,}|>|[-+*]\s|\d+[.)]\s)/.test(line);
  function codeBlock(code, language, t = text => text) {
    return `<div class="agent-code-block"><div class="agent-code-header"><span class="agent-code-language">${escape(language || t('Code'))}</span><button type="button" data-copy-code="${escape(encoded(code))}" title="${escape(t('Copy code'))}" aria-label="${escape(t('Copy code'))}">${icon('copy')}</button></div><pre><code>${escape(code)}</code></pre></div>`;
  }
  function markdown(value, depth = 0, t = text => text) {
    const text = asText(value).replace(/\r\n?/g, '\n');
    if (depth > 6) return `<p>${escape(text)}</p>`;
    const lines = text.split('\n'), output = [];
    for (let i = 0; i < lines.length;) {
      if (!lines[i].trim()) { i++; continue; }
      const fence = lines[i].match(/^\s{0,3}(`{3,}|~{3,})(.*)$/);
      if (fence) {
        const language = fence[2].trim().match(/^[a-zA-Z0-9_+.-]+/)?.[0] || '';
        const body = [], close = new RegExp(`^\\s{0,3}${fence[1][0]}{${fence[1].length},}\\s*$`);
        i++;
        while (i < lines.length && !close.test(lines[i])) body.push(lines[i++]);
        if (i < lines.length) i++;
        output.push(codeBlock(body.join('\n'), language, t)); continue;
      }
      const header = lines[i].match(/^\s{0,3}(#{1,6})\s+(.+?)(?:\s+#+)?\s*$/);
      if (header) { output.push(`<h${header[1].length}>${inline(header[2])}</h${header[1].length}>`); i++; continue; }
      if (/^\s{0,3}(?:---+|\*\*\*+|___+)\s*$/.test(lines[i])) { output.push('<hr>'); i++; continue; }
      if (/^\s{0,3}>/.test(lines[i])) {
        const quote = [];
        while (i < lines.length && /^\s{0,3}>/.test(lines[i])) quote.push(lines[i++].replace(/^\s{0,3}>\s?/, ''));
        output.push(`<blockquote>${markdown(quote.join('\n'), depth + 1, t)}</blockquote>`); continue;
      }
      if (i + 1 < lines.length && lines[i].includes('|')) {
        const headings = tableCells(lines[i]), separators = tableCells(lines[i + 1]);
        if (headings.length === separators.length && separators.every(cell => /^:?-{3,}:?$/.test(cell))) {
          const alignment = separators.map(cell => cell.endsWith(':') ? cell.startsWith(':') ? 'center' : 'right' : 'left');
          const cellHtml = (cell, index, tag) => `<${tag} class="agent-align-${alignment[index]}">${inline(cell || '')}</${tag}>`;
          const rows = []; i += 2;
          while (i < lines.length && lines[i].trim() && lines[i].includes('|')) {
            const cells = tableCells(lines[i++]);
            rows.push(`<tr>${headings.map((_, index) => cellHtml(cells[index], index, 'td')).join('')}</tr>`);
          }
          output.push(`<div class="agent-table-wrap"><table><thead><tr>${headings.map((cell, index) => cellHtml(cell, index, 'th')).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`); continue;
        }
      }
      const list = listMatch(lines[i]);
      if (list) {
        const ordered = /^\d/.test(list[1]), tag = ordered ? 'ol' : 'ul', items = [];
        while (i < lines.length) {
          const item = listMatch(lines[i]);
          if (!item || /^\d/.test(item[1]) !== ordered) break;
          let body = item[2]; i++;
          while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !listMatch(lines[i])) body += '\n' + lines[i++].trim();
          items.push(`<li>${inline(body).replace(/\n/g, '<br>')}</li>`);
        }
        output.push(`<${tag}${ordered ? ` start="${parseInt(list[1], 10)}"` : ''}>${items.join('')}</${tag}>`); continue;
      }
      const paragraph = [lines[i++]];
      while (i < lines.length && !blockStart(lines[i]) && !(i + 1 < lines.length && lines[i].includes('|') && tableCells(lines[i + 1]).every(cell => /^:?-{3,}:?$/.test(cell)))) paragraph.push(lines[i++]);
      output.push(`<p>${inline(paragraph.join('\n')).replace(/\n/g, '<br>')}</p>`);
    }
    return output.join('');
  }
  function toolGroups(events) {
    const groups = [], byId = new Map();
    events.forEach((event, index) => {
      if (!event || !(['tool_call','tool_result'].includes(event.type) || event.tool)) return;
      const nativeId = event.tool_call_id ?? event.tool_use_id ?? event.item_id;
      let group = nativeId != null ? byId.get(String(nativeId)) : event.type !== 'tool_call' ? groups.find(item => !item.nativeId && !item.done && item.tool === event.tool) : null;
      if (!group) {
        group = {key:nativeId != null ? `native-${nativeId}` : `event-${index}`, nativeId, tool:event.tool || 'Tool', first:index, events:[], done:false};
        groups.push(group);
        if (nativeId != null) byId.set(String(nativeId), group);
      }
      group.events.push(event);
      if (event.type === 'tool_result') group.done = true;
    });
    return groups;
  }
  function resultFailed(event) {
    if (event.error || ['failed','error','denied'].includes(event.status)) return true;
    if (event.type !== 'tool_result') return false;
    try { return Boolean(JSON.parse(event.text || 'null')?.error); } catch (_) { return false; }
  }
  function toolMarkup(group, turn, t) {
    const last = group.events[group.events.length - 1];
    const status = group.events.some(resultFailed) ? 'failed' : group.done ? last.status || 'completed' : last.status || (running(turn.status) ? 'running' : 'interrupted');
    const fields = group.events.map(event => {
      const result = [];
      if (event.arguments != null) result.push(`<div class="agent-tool-field"><span>${escape(t('Arguments'))}</span><pre>${escape(asText(event.arguments))}</pre></div>`);
      const detail = event.result ?? event.error ?? event.text;
      if (detail != null && !(event.arguments != null && detail === event.tool)) result.push(`<div class="agent-tool-field"><span>${escape(t(event.type === 'tool_result' ? 'Result' : event.arguments != null ? 'Details' : 'Arguments'))}</span><pre>${escape(asText(detail))}</pre></div>`);
      return result.join('');
    }).join('');
    return `<details class="agent-tool" data-disclosure="${escape(`${turn.id}-tool-${group.key}`)}" data-tool-call-id="${escape(group.nativeId ?? group.key)}" data-status="${escape(status)}"><summary>${icon('chevron')}${icon('tool')}<span class="agent-tool-name">${escape(group.tool)}</span><span class="agent-tool-status">${escape(t(status))}</span></summary><div class="agent-tool-body">${fields}</div></details>`;
  }
  function renderTurn(turn, helpers = {}) {
    const t = helpers.t || (text => text), number = helpers.number || (value => String(value));
    const id = escape(turn.id), events = array(turn.events).filter(event => event && typeof event === 'object');
    const action = (name, label, symbol) => `<button type="button" data-agent-action="${name}" data-turn-id="${id}" title="${escape(t(label))}" aria-label="${escape(t(label))}">${icon(symbol)}</button>`;
    const attachments = array(turn.files).map(file => `<span class="agent-attachment">${icon('file')}${escape(typeof file === 'string' ? file : file.path || file.name || '')}</span>`);
    for (const file of array(turn.attachments)) attachments.push(`<span class="agent-attachment">${icon('file')}${escape(file.name)}</span>`);
    if (turn.run_id) attachments.push(`<span class="agent-attachment">${icon('run')}${escape(t('Run'))} ${escape(turn.run_id)}</span>`);
    const user = `<article class="agent-message-user" data-user-turn="${id}"><div class="agent-user-bubble">${attachments.length ? `<div class="agent-attachments">${attachments.join('')}</div>` : ''}<div class="agent-plain-text">${escape(turn.message)}</div></div><div class="agent-message-actions">${action('copy-user','Copy message','copy')}${action('edit-resend','Edit and resend','edit')}</div></article>`;
    const groups = toolGroups(events), content = [], emitted = new Set();
    events.forEach((event, index) => {
      if (['usage','session'].includes(event.type)) return;
      if (event.type === 'approval_review') {
        const labels = {approve:'AI approved this action',reject:'AI rejected this action',manual:'Human decision required',cancelled:'AI review stopped'};
        content.push(`<div class="agent-event-status agent-approval-review" data-status="${escape(event.decision || 'manual')}"><strong>${escape(t(labels[event.decision] || 'Human decision required'))}</strong>${event.model ? `<span>${escape(asText(event.model))}</span>` : ''}<div class="agent-plain-text">${escape(asText(event.reason ?? event.text))}</div></div>`);
        return;
      }
      if (['tool_call','tool_result'].includes(event.type) || event.tool) {
        const group = groups.find(item => item.first === index);
        if (group && !emitted.has(group)) { content.push(toolMarkup(group, turn, t)); emitted.add(group); }
        return;
      }
      const text = asText(event.text ?? event.content ?? event.error);
      if (!text || (event.type === 'message' && text === turn.reply)) return;
      if (['thinking','reasoning'].includes(event.type)) content.push(`<details class="agent-thinking" data-disclosure="${id}-thinking-${index}"><summary>${icon('chevron')}${escape(t('Reasoning'))}</summary><div class="agent-plain-text">${escape(text)}</div></details>`);
      else if (event.type === 'message') content.push(`<div class="agent-markdown">${markdown(text, 0, t)}</div>`);
      else content.push(`<div class="agent-event-status" data-status="${escape(event.status || event.type || 'status')}">${escape(text)}</div>`);
    });
    // Consecutive tool cards share a quiet disclosure, without hiding messages
    // emitted between tool batches or merging calls with identical tool names.
    const grouped = [];
    for (let i = 0; i < content.length;) {
      if (!content[i].startsWith('<details class="agent-tool"')) { grouped.push(content[i++]); continue; }
      const first = i, cards = [];
      while (i < content.length && content[i].startsWith('<details class="agent-tool"')) cards.push(content[i++]);
      grouped.push(`<details class="agent-tool-group" data-disclosure="${id}-tools-${first}"${running(turn.status) ? ' open' : ''}><summary>${icon('chevron')}<span>${escape(t('Used {count} tools', {count:cards.length}).replace('{count}', String(cards.length)))}</span></summary><div class="agent-tool-list">${cards.join('')}</div></details>`);
    }
    if (turn.reply) grouped.push(`<div class="agent-markdown">${markdown(turn.reply, 0, t)}</div>`);
    if (turn.error) grouped.push(`<div class="agent-error" role="alert">${escape(asText(turn.error))}</div>`);
    if (running(turn.status)) grouped.push(`<div class="agent-turn-status" role="status"><span class="agent-status-dot"></span>${escape(t(turn.status === 'waiting_approval' ? 'Waiting for approval' : 'Working…'))}</div>`);
    else if (['failed','cancelled','interrupted'].includes(turn.status)) grouped.push(`<div class="agent-turn-status" data-status="${escape(turn.status)}">${escape(t(turn.status))}</div>`);
    const usage = turn.usage;
    if (usage && typeof usage === 'object') {
      const tokens = Object.entries(usage).filter(([key,value]) => /token|eval_count/.test(key) && typeof value === 'number' && Number.isFinite(value));
      if (tokens.length) grouped.push(`<div class="agent-usage">${tokens.map(([key,value]) => `<span>${escape(t(key.replace(/_/g, ' ')))} ${escape(number(value))}</span>`).join('')}</div>`);
    }
    let date = '';
    if (turn.finished_at && helpers.formatDate) date = `<time datetime="${escape(turn.finished_at)}">${escape(helpers.formatDate(turn.finished_at))}</time>`;
    if (turn.reply || date) grouped.push(`<div class="agent-message-actions">${turn.reply ? action('copy-reply','Copy reply','copy') : ''}${date}</div>`);
    return user + `<article class="agent-message-assistant" data-turn-id="${id}" data-status="${escape(turn.status || 'running')}">${grouped.join('')}</article>`;
  }
  function exportFence(text) {
    let length = 3;
    for (const match of text.matchAll(/`+/g)) length = Math.max(length, match[0].length + 1);
    return '`'.repeat(length);
  }
  function exportSession(session, format) {
    const extension = format === 'json' ? 'json' : ['md','markdown'].includes(format) ? 'md' : 'txt';
    const base = String(session.title || session.id || 'conversation').replace(/[\u0000-\u001f<>:"/\\|?*]/g, '-').replace(/^\.+|[. ]+$/g, '').slice(0, 90) || 'conversation';
    const filename = `${base}.${extension}`;
    if (extension === 'json') return {content:JSON.stringify(session, null, 2) + '\n',filename,type:'application/json;charset=utf-8'};
    const md = extension === 'md', lines = [(md ? '# ' : '') + (session.title || 'Conversation')];
    for (const key of ['id','backend','model','mode','status','created_at','updated_at']) if (session[key] != null) lines.push(`${key}: ${asText(session[key])}`);
    for (const turn of array(session.turns)) {
      lines.push('', md ? '## User' : 'USER', asText(turn.message));
      if (array(turn.files).length) lines.push('Files: ' + turn.files.map(asText).join(', '));
      for (const attachment of array(turn.attachments)) {
        const text = asText(attachment.content);
        lines.push('', 'Attachment: ' + asText(attachment.name));
        if (md) {
          const fence = exportFence(text);
          lines.push(fence + 'text', text, fence);
        } else lines.push(text);
      }
      if (turn.run_id) lines.push('Run: ' + turn.run_id);
      lines.push('', md ? '## Assistant' : 'ASSISTANT');
      for (const event of array(turn.events)) {
        if (!event || event.type === 'session' || (event.type === 'message' && event.text === turn.reply)) continue;
        const details = Object.entries(event).filter(([key]) => key !== 'type' && key !== 'tool').map(([key,value]) => `${key}: ${asText(value)}`).join('\n');
        lines.push('', [event.type, event.tool].filter(Boolean).join(' · '));
        if (md) {
          const fence = exportFence(details);
          lines.push(fence + 'text', details, fence);
        } else lines.push(details);
      }
      if (turn.reply) lines.push('', asText(turn.reply));
      if (turn.error) lines.push('', 'Error: ' + asText(turn.error));
      if (turn.status) lines.push('', 'Status: ' + turn.status);
      if (turn.usage) lines.push('Usage: ' + asText(turn.usage));
    }
    return {content:lines.join('\n') + '\n',filename,type:md ? 'text/markdown;charset=utf-8' : 'text/plain;charset=utf-8'};
  }
  return {renderTurn, markdown, exportSession, icon};
})();
