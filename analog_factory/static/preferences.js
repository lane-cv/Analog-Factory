'use strict';

// Runs before styles load so a remembered theme is used on the first paint.
window.AFPreferences = (() => {
  const key = 'af-ui-preferences';
  const defaults = {language:'en', theme:'dark'};
  function normalize(value) {
    return {language:value?.language === 'zh-CN' ? 'zh-CN' : 'en', theme:value?.theme === 'light' ? 'light' : 'dark'};
  }
  let current = defaults;
  function apply(value, cache = true) {
    current = normalize(value);
    document.documentElement.lang = current.language;
    document.documentElement.dataset.theme = current.theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = current.theme === 'light' ? '#f4f6f5' : '#101315';
    if (cache) { try { localStorage.setItem(key, JSON.stringify(current)); } catch (_) { /* Storage can be unavailable in privacy modes. */ } }
    return {...current};
  }
  try { current = normalize(JSON.parse(localStorage.getItem(key))); } catch (_) { current = defaults; }
  apply(current, false);
  return {apply, normalize, current:() => ({...current})};
})();
