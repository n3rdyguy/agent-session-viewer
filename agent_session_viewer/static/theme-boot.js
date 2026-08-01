/*
 * Applies the stored theme to <html> before first paint.
 *
 * Loaded blocking from <head> because the CSP is script-src 'self' and inline
 * scripts are not allowed, so the usual inline snippet is not an option. Keep
 * this file tiny - it runs ahead of the stylesheet's effect on every page.
 *
 * "auto" is resolved here rather than in CSS so app.css only needs a single
 * [data-theme="light"] block instead of duplicating it inside a
 * prefers-color-scheme media query.
 *
 * With JavaScript disabled no attribute is set and the dark :root defaults
 * apply, which matches the app's behaviour before settings existed.
 */
(function () {
  var KEY = 'asv-theme';
  var LIGHT = '(prefers-color-scheme: light)';

  function stored() {
    try {
      var value = localStorage.getItem(KEY);
      return value === 'light' || value === 'dark' || value === 'auto' ? value : 'dark';
    } catch (e) {
      return 'dark';
    }
  }

  function resolve(choice) {
    if (choice !== 'auto') return choice;
    try {
      return window.matchMedia(LIGHT).matches ? 'light' : 'dark';
    } catch (e) {
      return 'dark';
    }
  }

  function apply(choice) {
    document.documentElement.setAttribute('data-theme', resolve(choice));
  }

  apply(stored());

  // Follow the OS while the choice is "auto".
  try {
    var query = window.matchMedia(LIGHT);
    var onChange = function () {
      if (stored() === 'auto') apply('auto');
    };
    if (query.addEventListener) query.addEventListener('change', onChange);
    else if (query.addListener) query.addListener(onChange);
  } catch (e) { /* ignore */ }

  // Let the settings page re-apply without a reload, and keep other open tabs
  // in step when the preference changes.
  window.asvApplyTheme = apply;
  window.addEventListener('storage', function (event) {
    if (event.key === KEY) apply(stored());
  });
})();
