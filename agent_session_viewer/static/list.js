(function() {
  const EXPAND_KEY = 'asv-list-expanded';
  const OVERRIDES_KEY = 'asv-project-overrides';
  const PROMPTS_KEY = 'asv-list-prompts';
  // Superseded by EXPAND_KEY + OVERRIDES_KEY.
  try { localStorage.removeItem('asv-collapsed-projects'); } catch (e) { /* ignore */ }

  // Prompts toggle: reveal the first-prompt line under each session row.
  const promptsToggle = document.getElementById('prompts-toggle');
  if (promptsToggle) {
    let promptsOn = false;
    try { promptsOn = localStorage.getItem(PROMPTS_KEY) === '1'; } catch (e) { /* ignore */ }
    promptsToggle.checked = promptsOn;
    document.body.classList.toggle('prompts-on', promptsOn);
    promptsToggle.addEventListener('change', () => {
      document.body.classList.toggle('prompts-on', promptsToggle.checked);
      try { localStorage.setItem(PROMPTS_KEY, promptsToggle.checked ? '1' : '0'); } catch (e) { /* ignore */ }
    });
  }

  const groups = Array.from(document.querySelectorAll('details.project-group'));
  if (!groups.length) return;

  // Expand toggle sets the baseline for every group (off = collapsed); clicking
  // a single group records an override relative to that baseline. Flipping the
  // toggle clears the overrides so all groups follow it again.
  let expandAll = false;
  try { expandAll = localStorage.getItem(EXPAND_KEY) === '1'; } catch (e) { /* ignore */ }
  const loadOverrides = () => {
    try { return new Set(JSON.parse(localStorage.getItem(OVERRIDES_KEY) || '[]')); }
    catch (e) { return new Set(); }
  };
  const overrides = loadOverrides();
  const saveOverrides = () => {
    try { localStorage.setItem(OVERRIDES_KEY, JSON.stringify(Array.from(overrides))); } catch (e) { /* ignore */ }
  };
  const desiredOpen = (key) => expandAll !== overrides.has(key);

  // Pinned projects float above the rest, keeping the active sort within
  // each partition.
  const PINS_KEY = 'asv-pinned-projects';
  const loadPins = () => {
    try { return new Set(JSON.parse(localStorage.getItem(PINS_KEY) || '[]')); }
    catch (e) { return new Set(); }
  };
  const pins = loadPins();
  const savePins = () => {
    try { localStorage.setItem(PINS_KEY, JSON.stringify(Array.from(pins))); } catch (e) { /* ignore */ }
  };

  const expandToggle = document.getElementById('expand-toggle');
  if (expandToggle) expandToggle.checked = expandAll;

  groups.forEach(group => {
    group.open = desiredOpen(group.dataset.projectKey);
    const summary = group.querySelector('.project-summary');
    if (!summary) return;
    // Persist from click, not the `toggle` event: `toggle` also fires (async)
    // for the programmatic open/close done by the filter below.
    summary.addEventListener('click', () => {
      // The default action flips `open` after handlers run.
      const willOpen = !group.open;
      if (willOpen === expandAll) overrides.delete(group.dataset.projectKey);
      else overrides.add(group.dataset.projectKey);
      saveOverrides();
    });
    const pinBtn = group.querySelector('.pin-btn');
    if (pinBtn) {
      const syncPin = () => {
        const on = pins.has(group.dataset.projectKey);
        group.classList.toggle('pinned', on);
        pinBtn.classList.toggle('on', on);
        pinBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
        pinBtn.title = on ? 'Unpin project' : 'Pin project';
      };
      syncPin();
      pinBtn.addEventListener('click', (ev) => {
        ev.preventDefault();   // don't toggle the <details>
        ev.stopPropagation();  // don't record an open/close override
        const key = group.dataset.projectKey;
        if (pins.has(key)) pins.delete(key);
        else pins.add(key);
        savePins();
        syncPin();
        applySort();
      });
    }
  });

  // Instant filter over the rendered rows; Enter still submits the server search.
  const input = document.querySelector('.search-box');
  const countLine = document.getElementById('count-line');
  const noMatches = document.getElementById('no-client-matches');
  const plural = (n, word) => n + ' ' + word + (n === 1 ? '' : 's');

  function applyFilter() {
    const term = ((input && input.value) || '').trim().toLowerCase();
    let visibleSessions = 0;
    let visibleProjects = 0;
    groups.forEach(group => {
      let hits = 0;
      group.querySelectorAll('.session-row').forEach(row => {
        const hit = !term || (row.dataset.search || '').includes(term);
        row.classList.toggle('is-filtered', !hit);
        if (hit) hits += 1;
      });
      group.classList.toggle('is-filtered', hits === 0);
      if (hits) { visibleProjects += 1; visibleSessions += hits; }
      // Reveal matches while filtering; restore the persisted state on clear.
      group.open = term ? true : desiredOpen(group.dataset.projectKey);
    });
    if (countLine) {
      countLine.textContent = plural(visibleSessions, 'session')
        + ' in ' + plural(visibleProjects, 'project')
        + (term ? ' matching “' + term + '”' : '');
    }
    if (noMatches) noMatches.hidden = visibleSessions !== 0;
  }

  // Sort by updated/created, newest or oldest first. Rows carry both epochs as
  // data attributes; groups order by their first row under the active direction.
  const SORT_FIELD_KEY = 'asv-list-sort-field';
  const SORT_DIR_KEY = 'asv-list-sort-dir';
  const sortField = document.getElementById('sort-field');
  const sortDirBtn = document.getElementById('sort-dir');
  const SORT_FIELDS = ['updated', 'created', 'name'];
  let field = 'updated';
  let dir = 'desc';
  try {
    const storedField = localStorage.getItem(SORT_FIELD_KEY);
    if (SORT_FIELDS.includes(storedField)) field = storedField;
    if (localStorage.getItem(SORT_DIR_KEY) === 'asc') dir = 'asc';
  } catch (e) { /* ignore */ }

  function applySort() {
    const sign = dir === 'desc' ? -1 : 1;
    const byName = field === 'name';
    const rowKey = row => (byName
      ? (row.dataset.title || '')
      : parseFloat(row.dataset[field]) || 0);
    const cmp = (a, b) => (byName ? String(a).localeCompare(String(b)) : a - b);
    groups.forEach(group => {
      const list = group.querySelector('.session-list');
      if (!list) return;
      Array.from(list.querySelectorAll('.session-row'))
        .sort((a, b) => sign * cmp(rowKey(a), rowKey(b)))
        .forEach(row => list.appendChild(row));
    });
    const groupKey = group => {
      if (byName) return group.dataset.name || '';
      const keys = Array.from(group.querySelectorAll('.session-row')).map(rowKey);
      if (!keys.length) return 0;
      return dir === 'desc' ? Math.max(...keys) : Math.min(...keys);
    };
    const parent = groups[0].parentNode;
    const pinRank = group => (pins.has(group.dataset.projectKey) ? 0 : 1);
    groups.slice()
      .sort((a, b) => (pinRank(a) - pinRank(b)) || sign * cmp(groupKey(a), groupKey(b)))
      .forEach(group => parent.insertBefore(group, noMatches));
  }

  const updateDirButton = () => {
    sortDirBtn.textContent = dir === 'desc' ? '↓' : '↑';
    sortDirBtn.title = field === 'name'
      ? (dir === 'desc' ? 'Z to A' : 'A to Z')
      : (dir === 'desc' ? 'Newest first' : 'Oldest first');
  };

  if (sortField && sortDirBtn) {
    sortField.value = field;
    updateDirButton();
    sortField.addEventListener('change', () => {
      field = SORT_FIELDS.includes(sortField.value) ? sortField.value : 'updated';
      try { localStorage.setItem(SORT_FIELD_KEY, field); } catch (e) { /* ignore */ }
      updateDirButton();
      applySort();
    });
    sortDirBtn.addEventListener('click', () => {
      dir = dir === 'desc' ? 'asc' : 'desc';
      try { localStorage.setItem(SORT_DIR_KEY, dir); } catch (e) { /* ignore */ }
      updateDirButton();
      applySort();
    });
    // Server renders updated-desc with no pins; re-apply any persisted deviation.
    if (field !== 'updated' || dir !== 'desc' || pins.size) applySort();
  }

  if (expandToggle) {
    expandToggle.addEventListener('change', () => {
      expandAll = expandToggle.checked;
      overrides.clear();
      saveOverrides();
      try { localStorage.setItem(EXPAND_KEY, expandAll ? '1' : '0'); } catch (e) { /* ignore */ }
      // Re-applies open states; groups with an active filter term stay open.
      applyFilter();
    });
  }

  if (input) {
    input.addEventListener('input', applyFilter);
    // Page may arrive with a server-side q: sync count/open state once.
    if ((input.value || '').trim()) applyFilter();
  }
})();
