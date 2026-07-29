(function() {
  const MD_KEY = 'asv-markdown';
  const tabs = document.querySelectorAll('#view-tabs [data-tab]');
  tabs.forEach(btn => {
    btn.addEventListener('click', () => {
      tabs.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      const panel = document.getElementById('tab-' + btn.dataset.tab);
      if (panel) panel.classList.add('active');
    });
  });

  const toast = document.getElementById('copy-toast');
  function showToast(msg) {
    if (!toast) return;
    toast.textContent = msg;
    toast.classList.add('show');
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => toast.classList.remove('show'), 1800);
  }

  async function copyImageToClipboard(img) {
    try {
      const resp = await fetch(img.src);
      const blob = await resp.blob();
      const type = blob.type || 'image/png';
      if (navigator.clipboard && window.ClipboardItem) {
        // Chrome often wants image/png specifically
        let itemBlob = blob;
        if (type !== 'image/png' && typeof createImageBitmap === 'function') {
          try {
            const bitmap = await createImageBitmap(blob);
            const canvas = document.createElement('canvas');
            canvas.width = bitmap.width;
            canvas.height = bitmap.height;
            canvas.getContext('2d').drawImage(bitmap, 0, 0);
            itemBlob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
          } catch (_) { /* use original */ }
        }
        await navigator.clipboard.write([
          new ClipboardItem({ [itemBlob.type || 'image/png']: itemBlob })
        ]);
        showToast('Image copied to clipboard');
        return;
      }
      // Fallback: copy data URL as text
      await navigator.clipboard.writeText(img.src);
      showToast('Image data URL copied as text');
    } catch (err) {
      try {
        await navigator.clipboard.writeText(img.src);
        showToast('Image data URL copied as text');
      } catch (e2) {
        showToast('Copy failed — try right-click → Copy image');
      }
    }
  }

  document.addEventListener('click', (ev) => {
    const img = ev.target.closest('img.copyable-image');
    if (!img) return;
    ev.preventDefault();
    copyImageToClipboard(img);
  });


  // ── Fold / expand-all / copy (always — do not gate on marked CDN) ──
  function cardRootForFold(fold) {
    return fold ? fold.closest('.bubble, .artifact-doc') : null;
  }

  function headerBtnForFold(fold) {
    var root = cardRootForFold(fold);
    return root ? root.querySelector('.fold-header-btn') : null;
  }

  function setFoldCollapsed(fold, collapsed) {
    if (!fold) return;
    fold.setAttribute('data-collapsed', collapsed ? 'true' : 'false');
    var expanded = collapsed ? 'false' : 'true';
    var btn = fold.querySelector('.fold-toggle');
    if (btn) {
      btn.setAttribute('aria-expanded', expanded);
      var more = btn.querySelector('.fold-label-more');
      var less = btn.querySelector('.fold-label-less');
      if (more) more.hidden = !collapsed;
      if (less) less.hidden = collapsed;
    }
    var headerBtn = headerBtnForFold(fold);
    if (headerBtn) {
      headerBtn.setAttribute('aria-expanded', expanded);
      headerBtn.title = collapsed ? 'Expand' : 'Collapse';
    }
  }

  function toggleFold(fold) {
    if (!fold) return;
    var collapsed = fold.getAttribute('data-collapsed') !== 'false';
    if (collapsed) {
      // Expanding: content grows below — no scroll fix needed.
      setFoldCollapsed(fold, false);
    } else {
      // Collapsing: keep this card from yanking the page.
      var anchor = cardRootForFold(fold) || fold;
      preserveAnchorScroll(anchor, function() {
        setFoldCollapsed(fold, true);
      });
    }
  }

  // Keep the page from jumping when tall folds shrink. Pin a stable anchor's
  // viewport Y across the mutation; if we were scrolled deep into content that
  // collapsed away, bring that block back under the sticky header.
  function scrollPad() {
    return 72; // site header + a little breathing room
  }

  function preserveAnchorScroll(anchor, action) {
    if (!anchor) {
      action();
      return;
    }
    var before = anchor.getBoundingClientRect().top;
    action();
    var after = anchor.getBoundingClientRect().top;
    var delta = after - before;
    if (Math.abs(delta) > 0.5) window.scrollBy(0, delta);
    // If the anchor fully left the viewport (common when collapsing a block we
    // were scrolled into the middle of), pin its top under the header.
    var pad = scrollPad();
    var r = anchor.getBoundingClientRect();
    if (r.bottom < pad || r.top > window.innerHeight - 40) {
      window.scrollBy(0, r.top - pad);
    }
  }

  function firstVisibleBlock() {
    var root = document.querySelector('.tab-panel.active') || document;
    var nodes = root.querySelectorAll('.bubble, .artifact-doc');
    var pad = scrollPad();
    for (var i = 0; i < nodes.length; i++) {
      var r = nodes[i].getBoundingClientRect();
      if (r.bottom > pad && r.top < window.innerHeight) return nodes[i];
    }
    return null;
  }

  document.querySelectorAll('.fold-toggle').forEach(function(btn) {
    btn.addEventListener('click', function() {
      toggleFold(btn.closest('.fold'));
    });
  });

  document.querySelectorAll('.fold-header-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var root = btn.closest('.bubble, .artifact-doc');
      var fold = root ? root.querySelector('.fold') : null;
      toggleFold(fold);
    });
  });

  function foldsInActiveTab() {
    var active = document.querySelector('.tab-panel.active') || document;
    return active.querySelectorAll('.fold');
  }

  var expandAll = document.getElementById('expand-all');
  var collapseAll = document.getElementById('collapse-all');
  if (expandAll) {
    expandAll.addEventListener('click', function() {
      foldsInActiveTab().forEach(function(f) { setFoldCollapsed(f, false); });
    });
  }
  if (collapseAll) {
    collapseAll.addEventListener('click', function() {
      var anchor = firstVisibleBlock();
      preserveAnchorScroll(anchor, function() {
        foldsInActiveTab().forEach(function(f) { setFoldCollapsed(f, true); });
      });
    });
  }

  function getBlockRoot(el) {
    return el.closest('.bubble, .artifact-doc');
  }

  function getBubbleRawText(root) {
    if (!root) return '';
    var blocks = root.querySelectorAll('.md-block textarea.md-src, .md-block script.md-src');
    if (!blocks.length) return '';
    var best = '';
    blocks.forEach(function(el) {
      var t = '';
      if (el.tagName === 'TEXTAREA') {
        t = el.value || '';
      } else {
        try { t = JSON.parse(el.textContent || '""'); } catch (e) { t = el.textContent || ''; }
      }
      if (String(t).length >= best.length) best = String(t);
    });
    if (root.classList.contains('reasoning') && root.querySelector('.encrypted-tag')) {
      if (best.indexOf('<encrypted>') === -1) {
        best = best.replace(/\s*$/, '') + '\n<encrypted>';
      }
    }
    return best;
  }

  function formatBubbleMarkdown(root, raw) {
    if (!root) return raw || '';
    var role = (root.dataset.role || 'message');
    // Chat roles uppercase; document titles keep their casing
    if (!root.classList.contains('artifact-doc')) {
      role = String(role).toUpperCase();
    }
    var bits = [role];
    if (root.dataset.time) bits.push(root.dataset.time);
    if (root.dataset.id) bits.push('`' + root.dataset.id + '`');
    if (root.dataset.model) bits.push(root.dataset.model);
    if (root.dataset.meta) bits.push(root.dataset.meta);
    return '### ' + bits.join(' · ') + '\n\n' + (raw || '') + '\n';
  }

  function copyText(text, label) {
    function ok() { showToast(label || 'Copied to clipboard'); }
    function fail() { showToast('Copy failed'); }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(ok).catch(function() {
        try {
          var ta = document.createElement('textarea');
          ta.value = text;
          ta.style.position = 'fixed';
          ta.style.left = '-9999px';
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
          ok();
        } catch (e2) { fail(); }
      });
    } else {
      try {
        var ta2 = document.createElement('textarea');
        ta2.value = text;
        ta2.style.position = 'fixed';
        ta2.style.left = '-9999px';
        document.body.appendChild(ta2);
        ta2.select();
        document.execCommand('copy');
        document.body.removeChild(ta2);
        ok();
      } catch (e3) { fail(); }
    }
  }

  function closeAllCopyMenus(except) {
    document.querySelectorAll('.copy-menu.open').forEach(function(m) {
      if (except && m === except) return;
      m.classList.remove('open');
      var b = m.querySelector('.copy-btn');
      if (b) b.setAttribute('aria-expanded', 'false');
    });
  }

  document.addEventListener('click', function(ev) {
    var btn = ev.target.closest('.copy-btn');
    if (btn) {
      ev.preventDefault();
      ev.stopPropagation();
      var menu = btn.closest('.copy-menu');
      var open = menu.classList.contains('open');
      closeAllCopyMenus();
      if (!open) {
        menu.classList.add('open');
        btn.setAttribute('aria-expanded', 'true');
      }
      return;
    }
    var item = ev.target.closest('.copy-menu-panel [data-copy]');
    if (item) {
      ev.preventDefault();
      ev.stopPropagation();
      var root = getBlockRoot(item);
      var mode = item.getAttribute('data-copy');
      var raw = getBubbleRawText(root);
      if (mode === 'markdown') {
        copyText(formatBubbleMarkdown(root, raw), 'Markdown copied');
      } else {
        copyText(raw, 'Raw text copied');
      }
      closeAllCopyMenus();
      return;
    }
    if (!ev.target.closest('.copy-menu')) closeAllCopyMenus();
  });

  // ── Markdown toggle (optional — CDN may be offline) ──────
  var mdToggle = document.getElementById('md-toggle');
  var markedOk = (typeof marked !== 'undefined');

  function isHashOnlyHref(href) {
    if (!href) return true;
    var h = String(href).trim();
    return h.charAt(0) === '#' || h.indexOf('javascript:') === 0;
  }

  function stripHashOnlyLinks(html) {
    return String(html)
      .replace(/<a\b([^>]*?)href\s*=\s*(["'])#(?:(?!\2).)*\2([^>]*)>([\s\S]*?)<\/a>/gi, '$4')
      .replace(/<a\b([^>]*?)href\s*=\s*#([^\s>]*)([^>]*)>([\s\S]*?)<\/a>/gi, '$4');
  }

  function readSrc(block) {
    var el = block.querySelector('textarea.md-src, script.md-src');
    if (!el) return '';
    if (el.tagName === 'TEXTAREA') return el.value || '';
    try { return JSON.parse(el.textContent || '""'); }
    catch (e) { return el.textContent || ''; }
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function normalizeNewlines(src) {
    return String(src || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  }

  var MD_HTML_TAGS = {
    a:1, abbr:1, b:1, blockquote:1, br:1, code:1, del:1, div:1, em:1,
    h1:1, h2:1, h3:1, h4:1, h5:1, h6:1, hr:1, i:1, img:1, input:1, li:1,
    ol:1, p:1, pre:1, s:1, span:1, strong:1, sub:1, sup:1, table:1,
    tbody:1, td:1, th:1, thead:1, tr:1, u:1, ul:1
  };

  function escapeAgentTags(src) {
    var re = new RegExp('</?([A-Za-z][\\w:-]*)\\b[^>]*>', 'g');
    return src.replace(re, function(match, name) {
      if (MD_HTML_TAGS[String(name).toLowerCase()]) return match;
      return escapeHtml(match);
    });
  }

  function renderMarkdown(src) {
    src = normalizeNewlines(src);
    var html = '';
    try {
      var prepared = escapeAgentTags(src);
      html = marked.parse(prepared);
      html = stripHashOnlyLinks(html);
    } catch (err) {
      html = '<pre class="md-preserve">' + escapeHtml(src) + '</pre>';
    }
    if (typeof DOMPurify !== 'undefined') {
      html = DOMPurify.sanitize(html, {
        USE_PROFILES: { html: true },
        ADD_ATTR: ['target', 'rel', 'class'],
        ADD_TAGS: ['pre']
      });
      html = stripHashOnlyLinks(html);
    }
    return html;
  }

  function setMarkdownMode(on) {
    document.body.classList.toggle('md-on', !!on);
    document.querySelectorAll('.md-block').forEach(function(block) {
      var plain = block.querySelector('.md-plain');
      var rich = block.querySelector('.md-rich');
      if (!plain || !rich) return;
      if (on && markedOk) {
        if (!rich.dataset.rendered) {
          rich.innerHTML = renderMarkdown(readSrc(block));
          rich.dataset.rendered = '1';
        }
        plain.hidden = true;
        rich.hidden = false;
      } else {
        plain.hidden = false;
        rich.hidden = true;
      }
    });
  }

  if (mdToggle) {
    if (markedOk && typeof marked.use === 'function') {
      marked.use({
        gfm: true,
        breaks: true,
        renderer: {
          link: function(token) {
            var href = token && token.href != null ? token.href : (arguments[0] || '');
            var title = token && token.title != null ? token.title : (arguments[1] || '');
            var text;
            if (token && token.tokens && this.parser) {
              text = this.parser.parseInline(token.tokens);
            } else {
              text = arguments[2] != null ? arguments[2] : String(href || '');
            }
            if (isHashOnlyHref(href)) return text;
            var t = title ? ' title="' + escapeHtml(String(title)) + '"' : '';
            return '<a href="' + escapeHtml(String(href)) + '"' + t + ' rel="noopener">' + text + '</a>';
          }
        }
      });
    } else if (markedOk && marked.setOptions) {
      marked.setOptions({ gfm: true, breaks: true });
    }

    var prefer = true;
    try {
      var stored = localStorage.getItem(MD_KEY);
      if (stored === '0') prefer = false;
      if (stored === '1') prefer = true;
    } catch (e) {}

    if (!markedOk) {
      prefer = false;
      mdToggle.disabled = true;
      mdToggle.title = 'Markdown library failed to load (CDN offline?)';
    }
    mdToggle.checked = prefer;
    setMarkdownMode(prefer && markedOk);
    mdToggle.addEventListener('change', function() {
      var on = mdToggle.checked && markedOk;
      try { localStorage.setItem(MD_KEY, mdToggle.checked ? '1' : '0'); } catch (e) {}
      setMarkdownMode(on);
    });
  }

})();
