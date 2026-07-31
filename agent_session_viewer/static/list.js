(function() {
  const COLLAPSE_KEY = 'asv-collapsed-projects';
  const PROMPTS_KEY = 'asv-list-prompts';

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

  const loadCollapsed = () => {
    try { return new Set(JSON.parse(localStorage.getItem(COLLAPSE_KEY) || '[]')); }
    catch (e) { return new Set(); }
  };
  const collapsed = loadCollapsed();
  const saveCollapsed = () => {
    try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify(Array.from(collapsed))); } catch (e) { /* ignore */ }
  };

  groups.forEach(group => {
    if (collapsed.has(group.dataset.projectKey)) group.open = false;
    const summary = group.querySelector('.project-summary');
    if (!summary) return;
    // Persist from click, not the `toggle` event: `toggle` also fires (async)
    // for the programmatic open/close done by the filter below.
    summary.addEventListener('click', () => {
      // The default action flips `open` after handlers run.
      const willOpen = !group.open;
      if (willOpen) collapsed.delete(group.dataset.projectKey);
      else collapsed.add(group.dataset.projectKey);
      saveCollapsed();
    });
  });

  // Instant filter over the rendered rows; Enter still submits the server search.
  const input = document.querySelector('.search-box');
  const countLine = document.getElementById('count-line');
  const noMatches = document.getElementById('no-client-matches');
  const plural = (n, word) => n + ' ' + word + (n === 1 ? '' : 's');

  function applyFilter() {
    const term = (input.value || '').trim().toLowerCase();
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
      group.open = term ? true : !collapsed.has(group.dataset.projectKey);
    });
    if (countLine) {
      countLine.textContent = plural(visibleSessions, 'session')
        + ' in ' + plural(visibleProjects, 'project')
        + (term ? ' matching “' + term + '”' : '');
    }
    if (noMatches) noMatches.hidden = visibleSessions !== 0;
  }

  if (input) {
    input.addEventListener('input', applyFilter);
    // Page may arrive with a server-side q: sync count/open state once.
    if ((input.value || '').trim()) applyFilter();
  }
})();
