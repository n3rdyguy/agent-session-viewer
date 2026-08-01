/*
 * Shared browser-preference storage.
 *
 * Single source of truth for the localStorage keys used by list.js, app.js and
 * settings.js. Loaded blocking from <head> so the default-agent redirect below
 * happens before the wrong session list is painted.
 *
 * Every value is a plain string; nothing here is sent to the server.
 */
(function () {
  var KEYS = {
    theme: 'asv-theme',
    defaultAgent: 'asv-default-agent',
    timeFormat: 'asv-time-format',
    markdown: 'asv-markdown',
    fileReads: 'asv-file-reads',
    preview: 'asv-preview',
    expand: 'asv-list-expanded',
    prompts: 'asv-list-prompts',
    sortField: 'asv-list-sort-field',
    sortDir: 'asv-list-sort-dir',
    pins: 'asv-pinned-projects',
    overrides: 'asv-project-overrides'
  };

  // Defaults match the pre-settings behaviour: dark, all agents, newest first,
  // relative times, Markdown off, file cards and previews on.
  var DEFAULTS = {
    theme: 'dark',
    defaultAgent: '',
    timeFormat: 'relative',
    markdown: '0',
    fileReads: '1',
    preview: '1',
    expand: '0',
    prompts: '0',
    sortField: 'updated',
    sortDir: 'desc'
  };

  function get(name) {
    try {
      var value = localStorage.getItem(KEYS[name]);
      return value === null ? DEFAULTS[name] : value;
    } catch (e) {
      return DEFAULTS[name];
    }
  }

  function set(name, value) {
    try { localStorage.setItem(KEYS[name], value); } catch (e) { /* ignore */ }
  }

  function remove(name) {
    try { localStorage.removeItem(KEYS[name]); } catch (e) { /* ignore */ }
  }

  function getList(name) {
    try { return JSON.parse(localStorage.getItem(KEYS[name]) || '[]'); }
    catch (e) { return []; }
  }

  window.ASV_PREFS = {
    KEYS: KEYS,
    DEFAULTS: DEFAULTS,
    get: get,
    set: set,
    remove: remove,
    getList: getList,
    isOn: function (name) { return get(name) === '1'; }
  };

  // Open the session list on the preferred agent. Only fires on a bare "/" so a
  // shared or bookmarked URL, a search, or an explicit filter always wins.
  // location.replace keeps it out of the back-button history.
  try {
    var agent = get('defaultAgent');
    if (agent && !window.location.search && /\/$/.test(window.location.pathname)) {
      window.location.replace(window.location.pathname + '?agent=' + encodeURIComponent(agent));
    }
  } catch (e) { /* ignore */ }
})();
