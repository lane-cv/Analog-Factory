'use strict';

// Workspace geometry stays in the browser; project and server settings are untouched.
(() => {
  function initializeLayout() {
    const root = document.documentElement, body = document.body;
    const find = selector => document.querySelector(selector);
    const t = (key, values) => window.AFI18n.t(key, values);
    const defaults = {tree:240, ai:380, log:180, composer:132, hideTree:false, hideAI:false, collapsed:false};
    const storageKey = 'af-workspace-layout';
    let saved;
    try { saved = JSON.parse(localStorage.getItem(storageKey)); } catch (_) { /* Optional local cache. */ }
    let layout = {...defaults};
    for (const name of ['tree','ai','log','composer']) if (Number.isFinite(saved?.[name])) layout[name] = saved[name];
    for (const name of ['hideTree','hideAI','collapsed']) if (typeof saved?.[name] === 'boolean') layout[name] = saved[name];
    const panels = {tree:find('#project-panel'), ai:find('#assistant-panel'), log:find('.log-panel')};
    const handles = {tree:find('#tree-resizer'), ai:find('#ai-resizer'), log:find('#log-resizer')};
    const triggers = {tree:find('#toggle-tree'), ai:find('#toggle-ai'), log:find('#toggle-logs')};
    const backdrop = find('#panel-backdrop');
    if (Object.values(panels).some(value => !value) || Object.values(handles).some(value => !value) || !backdrop) return;
    const expanded = () => body.classList.contains('agent-expanded');
    const compact = name => !(name === 'ai' && expanded()) && innerWidth <= (name === 'tree' ? 760 : 1100);
    const visible = name => compact(name) ? body.classList.contains(`show-${name}`) : !body.classList.contains(`hide-${name}`);
    const save = () => { try { localStorage.setItem(storageKey, JSON.stringify(layout)); } catch (_) { /* Layout still works without storage. */ } };
    const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
    let resizeFrame = null, drag = null, modal = null, returnFocus = null;
    const inertBefore = new Map();
    function notifyResize() {
      if (resizeFrame !== null) return;
      resizeFrame = requestAnimationFrame(() => { resizeFrame = null; window.dispatchEvent(new Event('resize')); });
    }
    function limits(name) {
      if (name === 'log') {
        const workspaceHeight = find('.workspace').clientHeight;
        const available = workspaceHeight - find('.tabs').offsetHeight - find('.statusbar').offsetHeight;
        // Preserve a useful design viewport before allocating space to the log.
        // Only the displayed size is clamped, so a taller viewport restores the preference.
        const minimum = Math.min(90, Math.max(64, available - 140));
        return [minimum, Math.max(minimum, Math.min(480, available - 140, Math.round(workspaceHeight * .58), innerWidth <= 760 ? Math.floor(innerHeight * .45) : Infinity))];
      }
      const other = name === 'tree' ? 'ai' : 'tree';
      const reserved = !compact(other) && visible(other) ? layout[other] + 6 : 0;
      const minimum = name === 'tree' ? 180 : 260;
      return [minimum, Math.max(minimum, Math.min(name === 'tree' ? 400 : 900, innerWidth - reserved - 366))];
    }
    function focusable(panel) {
      return [...panel.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),summary,[tabindex="0"]')]
        .filter(element => element.getClientRects().length && !element.closest('[inert]'));
    }
    function syncModal() {
      const next = ['tree','ai'].find(name => compact(name) && visible(name)) || null;
      if (next === modal) return;
      const previous = modal;
      for (const [element, value] of inertBefore) element.inert = value;
      inertBefore.clear();
      if (previous) {
        panels[previous].removeAttribute('aria-modal');
        panels[previous].removeAttribute('role');
      }
      modal = next;
      backdrop.hidden = !next;
      if (next) {
        if (!previous || document.activeElement === triggers[next]) returnFocus = document.activeElement;
        panels[next].setAttribute('role', 'dialog');
        panels[next].setAttribute('aria-modal', 'true');
        const topbarBackground = [...find('.topbar').children].filter(element => element !== triggers.tree && element !== triggers.ai);
        for (const element of [...topbarBackground,find('.central-workspace'),panels[next === 'tree' ? 'ai' : 'tree'],handles.tree,handles.ai]) {
          inertBefore.set(element, element.inert); element.inert = true;
        }
        (focusable(panels[next])[0] || panels[next]).focus({preventScroll:true});
      } else if (previous) {
        const active = document.activeElement;
        if (active === body || active === backdrop || panels[previous].contains(active)) {
          const target = returnFocus?.isConnected && returnFocus.getClientRects().length ? returnFocus : triggers[previous];
          target.focus({preventScroll:true});
        }
        returnFocus = null;
      }
    }
    function sync() {
      for (const name of ['tree','ai','log']) {
        const [minimum, maximum] = limits(name), size = Math.round(clamp(layout[name], minimum, maximum));
        root.style.setProperty(name === 'log' ? '--log-height' : `--${name}-width`, `${size}px`);
        if (name === 'log') root.style.setProperty('--log-min-height', `${minimum}px`);
        handles[name].setAttribute('aria-valuemin', String(minimum));
        handles[name].setAttribute('aria-valuemax', String(maximum));
        handles[name].setAttribute('aria-valuenow', String(size));
        handles[name].setAttribute('aria-valuetext', t('{size} pixels', {size}));
        handles[name].hidden = name === 'log' ? panels.log.classList.contains('collapsed') : compact(name) || !visible(name) || name === 'ai' && expanded();
        triggers[name].setAttribute('aria-expanded', String(name === 'log' ? !panels.log.classList.contains('collapsed') : visible(name)));
      }
      const expandButton = find('#agent-expand');
      expandButton?.setAttribute('aria-expanded', String(expanded()));
      const expandLabel = expanded() ? 'Collapse to sidebar' : 'Expand agent workspace';
      if (expandButton) { expandButton.title = t(expandLabel); expandButton.setAttribute('aria-label', t(expandLabel)); expandButton.dataset.i18nTitle = expandLabel; expandButton.dataset.i18nAriaLabel = expandLabel; }
      syncModal();
      syncComposer();
    }
    function closeOverlay() {
      if (!modal) return;
      body.classList.remove(`show-${modal}`);
      sync(); notifyResize();
    }
    function toggle(name) {
      if (name === 'ai' && expanded()) setExpanded(false, false);
      if (name === 'log') {
        layout.collapsed = panels.log.classList.toggle('collapsed');
      } else if (compact(name)) {
        const opening = !visible(name);
        body.classList.remove('show-tree','show-ai');
        if (opening) body.classList.add(`show-${name}`);
      } else {
        layout[name === 'tree' ? 'hideTree' : 'hideAI'] = body.classList.toggle(`hide-${name}`);
      }
      save(); sync(); notifyResize();
    }
    function setExpanded(value, focus = true) {
      body.classList.toggle('agent-expanded', value);
      body.classList.remove('hide-ai','show-ai','show-tree');
      layout.hideAI = false;
      if (!value && innerWidth <= 1100) body.classList.add('show-ai');
      sync(); save(); notifyResize();
      if (focus) find('#chat-input')?.focus({preventScroll:true});
    }
    find('#agent-expand')?.addEventListener('click', () => setExpanded(!expanded()));
    find('#agent-close')?.addEventListener('click', () => toggle('ai'));

    const composerHandle = find('#agent-composer-resizer');
    let composerDrag = null;
    function composerLimits() { return [innerHeight <= 540 ? 92 : 112, Math.max(112, Math.min(420, innerHeight - 270))]; }
    function syncComposer() {
      if (!composerHandle) return;
      const [min, max] = composerLimits(), value = Math.round(clamp(layout.composer, min, max));
      root.style.setProperty('--agent-composer-height', `${value}px`);
      for (const [key, size] of Object.entries({min,max,now:value})) composerHandle.setAttribute(`aria-value${key}`, String(size));
      composerHandle.setAttribute('aria-valuetext', t('{size} pixels', {size:value}));
    }
    function finishComposer(cancel = false) {
      if (!composerDrag) return;
      const previous = composerDrag; composerDrag = null;
      if (cancel) layout.composer = previous.original;
      if (composerHandle.hasPointerCapture(previous.id)) composerHandle.releasePointerCapture(previous.id);
      body.classList.remove('resizing-panels','resizing-horizontal'); syncComposer(); save();
    }
    composerHandle?.addEventListener('keydown', event => {
      if (!['ArrowUp','ArrowDown','Home','End'].includes(event.key)) return;
      event.preventDefault(); const [min,max] = composerLimits();
      layout.composer = event.key === 'Home' ? min : event.key === 'End' ? max : clamp(Number(composerHandle.getAttribute('aria-valuenow')) + (event.key === 'ArrowUp' ? 1 : -1) * (event.shiftKey ? 40 : 12), min, max);
      syncComposer(); save();
    });
    composerHandle?.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      event.preventDefault(); composerHandle.focus(); composerDrag = {id:event.pointerId,y:event.clientY,initial:Number(composerHandle.getAttribute('aria-valuenow')),original:layout.composer};
      composerHandle.setPointerCapture(event.pointerId); body.classList.add('resizing-panels','resizing-horizontal');
    });
    composerHandle?.addEventListener('pointermove', event => {
      if (composerDrag?.id !== event.pointerId) return;
      const [min,max] = composerLimits(); layout.composer = clamp(composerDrag.initial + composerDrag.y - event.clientY,min,max); syncComposer();
    });
    composerHandle?.addEventListener('pointerup', () => finishComposer());
    composerHandle?.addEventListener('pointercancel', () => finishComposer(true));
    composerHandle?.addEventListener('lostpointercapture', () => finishComposer(true));
    composerHandle?.addEventListener('dblclick', () => { layout.composer = defaults.composer; syncComposer(); save(); });
    function resize(name, value) {
      const [minimum, maximum] = limits(name);
      layout[name] = clamp(value, minimum, maximum);
      sync(); notifyResize();
    }
    function finishDrag(cancel = false) {
      if (!drag) return;
      const previous = drag; drag = null;
      if (cancel) layout[previous.name] = previous.original;
      body.classList.remove('resizing-panels','resizing-horizontal');
      if (previous.handle.hasPointerCapture(previous.pointerId)) previous.handle.releasePointerCapture(previous.pointerId);
      sync(); save(); notifyResize();
    }
    for (const name of ['tree','ai','log']) {
      const handle = handles[name];
      handle.setAttribute('role','separator'); handle.tabIndex = 0;
      handle.setAttribute('aria-orientation', name === 'log' ? 'horizontal' : 'vertical');
      handle.setAttribute('aria-controls', name === 'log' ? 'execution-log' : panels[name].id);
      triggers[name].setAttribute('aria-controls', name === 'log' ? 'execution-log' : panels[name].id);
      triggers[name].addEventListener('click', () => toggle(name));
      handle.addEventListener('keydown', event => {
        const grow = name === 'tree' ? 'ArrowRight' : name === 'ai' ? 'ArrowLeft' : 'ArrowUp';
        const shrink = name === 'tree' ? 'ArrowLeft' : name === 'ai' ? 'ArrowRight' : 'ArrowDown';
        if (![grow,shrink,'Home','End'].includes(event.key)) return;
        event.preventDefault();
        const [minimum, maximum] = limits(name), current = Number(handle.getAttribute('aria-valuenow'));
        resize(name, event.key === 'Home' ? minimum : event.key === 'End' ? maximum : current + (event.key === grow ? 1 : -1) * (event.shiftKey ? 40 : 12));
        save();
      });
      handle.addEventListener('pointerdown', event => {
        if (event.button !== 0 || handle.hidden) return;
        event.preventDefault(); handle.focus({preventScroll:true});
        drag = {name,handle,pointerId:event.pointerId,start:name === 'log' ? event.clientY : event.clientX,initial:Number(handle.getAttribute('aria-valuenow')),original:layout[name]};
        handle.setPointerCapture(event.pointerId);
        body.classList.add('resizing-panels');
        body.classList.toggle('resizing-horizontal', name === 'log');
      });
      handle.addEventListener('pointermove', event => {
        if (!drag || drag.handle !== handle || event.pointerId !== drag.pointerId) return;
        const delta = (name === 'log' ? event.clientY : event.clientX) - drag.start;
        resize(name, drag.initial + delta * (name === 'tree' ? 1 : -1));
      });
      handle.addEventListener('pointerup', () => finishDrag());
      handle.addEventListener('pointercancel', () => finishDrag(true));
      handle.addEventListener('lostpointercapture', () => finishDrag(true));
      handle.addEventListener('dblclick', () => { resize(name, defaults[name]); save(); });
    }
    backdrop.addEventListener('click', closeOverlay);
    document.addEventListener('click', event => {
      if (expanded() && event.target.closest('[data-tab],[data-file],.brand')) { setExpanded(false, false); closeOverlay(); }
      if (modal && event.target.closest('[data-tab]')) closeOverlay();
    });
    find('.skip-link')?.addEventListener('click', event => {
      event.preventDefault();
      const panel = expanded() ? panels.ai : find('#main-content').hidden ? find('#desktop-panel') : find('#main-content');
      panel.tabIndex = -1;
      panel.focus({preventScroll:true});
    });
    document.addEventListener('keydown', event => {
      if (event.defaultPrevented || document.querySelector('dialog[open]')) return;
      if (event.key === 'Escape' && document.querySelector('.agent-popover:not([hidden])')) return;
      if (event.key === 'Escape' && composerDrag) { event.preventDefault(); finishComposer(true); }
      else if (event.key === 'Escape' && (drag || modal)) {
        event.preventDefault();
        if (drag) finishDrag(true); else closeOverlay();
      } else if (event.key === 'Tab' && modal) {
        const elements = focusable(panels[modal]), first = elements[0], last = elements.at(-1);
        if (!first) { event.preventDefault(); return; }
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });
    window.addEventListener('blur', () => { finishDrag(true); finishComposer(true); });
    find('#reset-layout')?.addEventListener('click', () => {
      finishDrag(true); layout = {...defaults};
      body.classList.remove('hide-tree','hide-ai','show-tree','show-ai','agent-expanded');
      panels.log.classList.remove('collapsed');
      save(); sync(); notifyResize();
    });
    const tabs = find('.tabs');
    function syncTabs() {
      const buttons = [...tabs.querySelectorAll('[data-tab]')];
      for (const button of buttons) {
        const active = button.classList.contains('active');
        button.id = `workspace-tab-${button.dataset.tab}`;
        button.setAttribute('role','tab'); button.setAttribute('aria-selected', String(active));
        button.setAttribute('aria-controls', button.dataset.tab === 'desktop' ? 'desktop-panel' : 'main-content');
        button.tabIndex = active ? 0 : -1;
        if (active) {
          const panel = find(button.dataset.tab === 'desktop' ? '#desktop-panel' : '#main-content');
          panel.setAttribute('role','tabpanel'); panel.setAttribute('aria-labelledby', button.id);
        }
      }
    }
    tabs.setAttribute('role','tablist');
    tabs.addEventListener('keydown', event => {
      const buttons = [...tabs.querySelectorAll('[data-tab]')], index = buttons.indexOf(event.target);
      if (index < 0 || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length-1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
      buttons[next].focus(); buttons[next].click(); buttons[next].scrollIntoView({block:'nearest',inline:'nearest'});
    });
    new MutationObserver(syncTabs).observe(tabs, {subtree:true,attributes:true,attributeFilter:['class']});
    new MutationObserver(() => { sync(); notifyResize(); }).observe(body, {attributes:true,attributeFilter:['class']});
    new MutationObserver(sync).observe(panels.log, {attributes:true,attributeFilter:['class']});
    new MutationObserver(sync).observe(root, {attributes:true,attributeFilter:['lang']});
    let lastWidth = innerWidth;
    window.addEventListener('resize', () => {
      if (lastWidth !== innerWidth) {
        finishDrag(true);
        for (const name of ['tree','ai']) {
          const breakpoint = name === 'tree' ? 760 : 1100;
          if (lastWidth <= breakpoint && innerWidth > breakpoint) body.classList.remove(`show-${name}`);
        }
        lastWidth = innerWidth;
      }
      sync();
    });
    body.classList.toggle('hide-tree', layout.hideTree);
    body.classList.toggle('hide-ai', layout.hideAI);
    panels.log.classList.toggle('collapsed', layout.collapsed);
    panels.tree.tabIndex = -1; panels.ai.tabIndex = -1;
    syncTabs(); sync();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initializeLayout, {once:true});
  else initializeLayout();
})();
