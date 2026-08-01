/*
 * Settings page behaviour.
 *
 * Every control writes straight to localStorage through prefs.js and takes
 * effect on the next page you open. The theme is the one exception: it applies
 * immediately so the choice is visible while you make it.
 */
(function () {
  var PREFS = window.ASV_PREFS;
  var saved = document.getElementById('prefs-saved');
  var savedTimer = null;

  function note(message) {
    if (!saved) return;
    saved.textContent = message;
    saved.classList.add('is-visible');
    clearTimeout(savedTimer);
    savedTimer = setTimeout(function () {
      saved.classList.remove('is-visible');
      saved.textContent = '';
    }, 2400);
  }

  // A <select> whose value is stored verbatim.
  function bindSelect(id, prefName, onChange) {
    var el = document.getElementById(id);
    if (!el) return;
    el.value = PREFS.get(prefName);
    // A stored value that is no longer offered (hand-edited storage, or an agent
    // that is gone) would leave the select blank; fall back to the default.
    if (el.selectedIndex < 0) {
      el.value = PREFS.DEFAULTS[prefName];
      if (el.selectedIndex < 0) el.selectedIndex = 0;
    }
    el.addEventListener('change', function () {
      PREFS.set(prefName, el.value);
      if (onChange) onChange(el.value);
      note('Saved');
    });
  }

  // A checkbox stored as '1' / '0'.
  function bindToggle(id, prefName) {
    var el = document.getElementById(id);
    if (!el) return;
    el.checked = PREFS.isOn(prefName);
    el.addEventListener('change', function () {
      PREFS.set(prefName, el.checked ? '1' : '0');
      note('Saved');
    });
  }

  bindSelect('pref-theme', 'theme', function (value) {
    if (window.asvApplyTheme) window.asvApplyTheme(value);
  });
  bindSelect('pref-agent', 'defaultAgent');
  bindSelect('pref-sort-field', 'sortField');
  bindSelect('pref-sort-dir', 'sortDir');
  bindSelect('pref-time-format', 'timeFormat');

  bindToggle('pref-expand', 'expand');
  bindToggle('pref-prompts', 'prompts');
  bindToggle('pref-markdown', 'markdown');
  bindToggle('pref-file-reads', 'fileReads');
  bindToggle('pref-preview', 'preview');

  function refreshCounts() {
    var pins = document.getElementById('prefs-pin-count');
    var overrides = document.getElementById('prefs-override-count');
    if (pins) pins.textContent = PREFS.getList('pins').length;
    if (overrides) overrides.textContent = PREFS.getList('overrides').length;
  }
  refreshCounts();

  function bindButton(id, handler, message) {
    var el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('click', function () {
      handler();
      refreshCounts();
      note(message);
    });
  }

  bindButton('prefs-clear-pins', function () { PREFS.remove('pins'); }, 'Pins cleared');
  bindButton('prefs-clear-overrides', function () { PREFS.remove('overrides'); },
    'Overrides cleared');

  bindButton('prefs-reset', function () {
    Object.keys(PREFS.KEYS).forEach(function (name) { PREFS.remove(name); });
    // Re-read every control from the now-empty store, and drop back to dark.
    ['pref-theme', 'pref-agent', 'pref-sort-field', 'pref-sort-dir', 'pref-time-format']
      .forEach(function (id) {
        var el = document.getElementById(id);
        if (!el) return;
        var pref = {
          'pref-theme': 'theme',
          'pref-agent': 'defaultAgent',
          'pref-sort-field': 'sortField',
          'pref-sort-dir': 'sortDir',
          'pref-time-format': 'timeFormat'
        }[id];
        el.value = PREFS.DEFAULTS[pref];
        if (el.selectedIndex < 0) el.selectedIndex = 0;
      });
    [['pref-expand', 'expand'], ['pref-prompts', 'prompts'], ['pref-markdown', 'markdown'],
      ['pref-file-reads', 'fileReads'], ['pref-preview', 'preview']]
      .forEach(function (pair) {
        var el = document.getElementById(pair[0]);
        if (el) el.checked = PREFS.DEFAULTS[pair[1]] === '1';
      });
    if (window.asvApplyTheme) window.asvApplyTheme(PREFS.DEFAULTS.theme);
  }, 'All preferences reset');
})();
