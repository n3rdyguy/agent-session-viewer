(function() {
  // Token-usage bar widths. The CSP has no style-src 'unsafe-inline', so the
  // template ships percentages as data-pct and they are applied here via CSSOM
  // (which style-src does not restrict).
  document.querySelectorAll('[data-pct]').forEach(function(el) {
    const pct = parseFloat(el.dataset.pct);
    if (Number.isFinite(pct)) {
      el.style.width = Math.max(0, Math.min(100, pct)) + '%';
    }
  });

  // Key names come from prefs.js so the settings page and this file cannot drift.
  const MD_KEY = window.ASV_PREFS.KEYS.markdown;
  const FILE_READS_KEY = window.ASV_PREFS.KEYS.fileReads;
  const PREVIEW_KEY = window.ASV_PREFS.KEYS.preview;
  const tabs = document.querySelectorAll('#view-tabs [data-tab]');
  function activateTab(name) {
    tabs.forEach(b => b.classList.toggle('active', b.dataset.tab === name));
    document.querySelectorAll('.tab-panel').forEach(p => {
      p.classList.toggle('active', p.id === 'tab-' + name);
    });
    // Hidden tab panels (display:none) report zero height on first load, so
    // preview fade (.is-clipped) must be remeasured after a tab is shown.
    schedulePreviewClipUpdate();
  }
  tabs.forEach(btn => {
    btn.addEventListener('click', () => {
      activateTab(btn.dataset.tab);
    });
  });

  // Keep scroll-margin / sticky bubble headers in sync with the real header height
  // (wrap on narrow viewports can grow it beyond the CSS default). Stick flush
  // under the site bar — no extra gap (content would show through otherwise).
  function stickySiteHeaderOffsetPx() {
    const raw = getComputedStyle(document.documentElement)
      .getPropertyValue('--sticky-site-header-offset').trim();
    const px = parseFloat(raw);
    return Number.isFinite(px) ? px : 0;
  }

  function syncStickyHeaderOffset() {
    const header = document.querySelector('body > header');
    if (!header) return;
    const px = Math.ceil(header.getBoundingClientRect().height);
    document.documentElement.style.setProperty('--sticky-site-header-offset', px + 'px');
    updateStuckBubbleHeaders();
  }

  // Toggle .is-stuck so sticky bubble headers can square off under the site bar
  // and seal corner peeks (rounded radius would otherwise show scrolled content).
  function updateStuckBubbleHeaders() {
    const top = stickySiteHeaderOffsetPx();
    document.querySelectorAll('.bubble-header').forEach(function(h) {
      const bubble = h.closest('.bubble');
      if (!bubble) {
        h.classList.remove('is-stuck');
        return;
      }
      const hr = h.getBoundingClientRect();
      const br = bubble.getBoundingClientRect();
      // Pinned when the header is at the sticky offset and the bubble still
      // extends below it (otherwise the header is scrolling away with the card).
      const stuck = hr.top <= top + 1 && br.bottom > hr.bottom + 1;
      h.classList.toggle('is-stuck', stuck);
    });
  }

  syncStickyHeaderOffset();
  window.addEventListener('resize', syncStickyHeaderOffset);
  window.addEventListener('scroll', updateStuckBubbleHeaders, { passive: true });

  // Prompt-history links deep-link into chat bubbles (id="turn-N").
  function jumpToTurnAnchor(anchorId) {
    if (!anchorId || !/^turn-\d+$/.test(anchorId)) return false;
    activateTab('chat');
    const el = document.getElementById(anchorId);
    if (!el) return false;
    syncStickyHeaderOffset();
    // scroll-margin-top on .bubble accounts for the sticky site header.
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    el.classList.add('turn-highlight');
    clearTimeout(jumpToTurnAnchor._t);
    jumpToTurnAnchor._t = setTimeout(() => el.classList.remove('turn-highlight'), 2200);
    return true;
  }
  document.querySelectorAll('a.prompt-history-link[href^="#turn-"]').forEach(link => {
    link.addEventListener('click', (ev) => {
      const href = link.getAttribute('href') || '';
      const id = href.charAt(0) === '#' ? href.slice(1) : '';
      if (jumpToTurnAnchor(id)) {
        // Keep the hash in the URL for shareable deep links without a hard jump.
        if (history.replaceState) {
          history.replaceState(null, '', '#' + id);
          ev.preventDefault();
        }
      }
    });
  });
  if (location.hash && location.hash.indexOf('#turn-') === 0) {
    // Wait a tick so layout/fonts settle before scrolling.
    setTimeout(() => jumpToTurnAnchor(location.hash.slice(1)), 0);
  }

  const toast = document.getElementById('copy-toast');
  function showToast(msg) {
    if (!toast) return;
    toast.textContent = msg;
    toast.classList.add('show');
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => toast.classList.remove('show'), 1800);
  }

  function imageCopySource(figureOrImg) {
    const figure = figureOrImg.closest ? figureOrImg.closest('.chat-image') : null;
    const img = figure
      ? figure.querySelector('img.preview-image')
      : (figureOrImg.matches && figureOrImg.matches('img.preview-image') ? figureOrImg : null);
    if (!img) return { img: null, src: '' };
    const src = img.getAttribute('data-copy-src') || img.currentSrc || img.src || '';
    return { img: img, src: src };
  }

  async function copyTextToClipboard(text) {
    if (!text) throw new Error('empty');
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
  }

  async function copyImagePixels(img, src) {
    // Prefer the live element when already decoded; otherwise fetch the source.
    let blob = null;
    if (img && img.complete && img.naturalWidth > 0 && typeof createImageBitmap === 'function') {
      try {
        const bitmap = await createImageBitmap(img);
        const canvas = document.createElement('canvas');
        canvas.width = bitmap.width;
        canvas.height = bitmap.height;
        canvas.getContext('2d').drawImage(bitmap, 0, 0);
        blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
      } catch (_) { /* fall through to fetch */ }
    }
    if (!blob) {
      const resp = await fetch(src);
      blob = await resp.blob();
    }
    const type = (blob && blob.type) || 'image/png';
    if (!(navigator.clipboard && window.ClipboardItem)) {
      await copyTextToClipboard(src);
      showToast('Image URL copied (bitmap clipboard unavailable)');
      return;
    }
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
  }

  async function copyImageUrl(src) {
    // Prefer absolute URL for /media links so paste targets resolve.
    let out = src || '';
    if (out && out.startsWith('/')) {
      out = window.location.origin + out;
    }
    await copyTextToClipboard(out);
    showToast(out.startsWith('data:') ? 'Data URL copied' : 'Image URL copied');
  }

  function revealLocalImage(figure) {
    if (!figure) return;
    const src = figure.getAttribute('data-media-src') || '';
    if (!src) {
      showToast('No local image URL');
      return;
    }
    const btn = figure.querySelector('.image-reveal-btn');
    const frame = figure.querySelector('.image-reveal-frame');
    const img = figure.querySelector('img.preview-image');
    const missing = figure.querySelector('.image-missing');
    if (!frame || !img) return;

    if (btn) {
      btn.disabled = true;
      const label = btn.querySelector('.image-reveal-label');
      if (label) label.textContent = 'Loading…';
    }

    const showFrame = function() {
      frame.hidden = false;
      if (btn) btn.hidden = true;
      figure.classList.add('is-revealed');
    };

    img.onload = function() {
      if (missing) missing.hidden = true;
      img.style.display = '';
      showFrame();
    };
    img.onerror = function() {
      img.style.display = 'none';
      if (missing) missing.hidden = false;
      showFrame();
      if (btn) {
        btn.hidden = false;
        btn.disabled = false;
        const label = btn.querySelector('.image-reveal-label');
        if (label) label.textContent = 'Retry show image';
      }
      showToast('Could not load local image');
    };

    // Force reload if retrying after a previous error.
    if (img.getAttribute('src') === src) {
      img.removeAttribute('src');
    }
    img.setAttribute('src', src);
    img.setAttribute('data-copy-src', src);
  }

  document.addEventListener('click', (ev) => {
    const revealBtn = ev.target.closest('.image-reveal-btn');
    if (revealBtn) {
      ev.preventDefault();
      ev.stopPropagation();
      revealLocalImage(revealBtn.closest('.chat-image-local'));
      return;
    }

    const btn = ev.target.closest('.image-copy-btn');
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    const mode = btn.getAttribute('data-copy-mode') || 'image';
    const figure = btn.closest('.chat-image');
    // Auto-reveal local images before copying pixels so src is populated.
    if (mode === 'image' && figure && figure.classList.contains('chat-image-local') && !figure.classList.contains('is-revealed')) {
      revealLocalImage(figure);
    }
    const { img, src } = imageCopySource(btn);
    const resolvedSrc = src || (figure && figure.getAttribute('data-media-src')) || '';
    if (!resolvedSrc) {
      showToast('No image source to copy');
      return;
    }
    if (mode === 'url') {
      copyImageUrl(resolvedSrc).catch(() => showToast('Copy URL failed'));
      return;
    }
    // Wait a tick for reveal to assign src when needed.
    const tryCopy = function() {
      const live = imageCopySource(btn);
      const liveSrc = live.src || resolvedSrc;
      return copyImagePixels(live.img, liveSrc);
    };
    Promise.resolve()
      .then(tryCopy)
      .catch(() => new Promise((r) => setTimeout(r, 120)).then(tryCopy))
      .catch(() => {
        copyImageUrl(resolvedSrc)
          .then(() => showToast('Bitmap copy failed - URL copied instead'))
          .catch(() => showToast('Copy failed - try right-click → Copy image'));
      });
  });


  // ── Fold / expand-all / copy (always - do not gate on marked CDN) ──
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
    var foldBody = fold.querySelector('.fold-body');
    if (foldBody && !collapsed) foldBody.classList.remove('is-clipped');
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
    schedulePreviewClipUpdate();
  }

  function toggleFold(fold) {
    if (!fold) return;
    var collapsed = fold.getAttribute('data-collapsed') !== 'false';
    if (collapsed) {
      // Expanding: content grows below - no scroll fix needed.
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

  function setDetailsOpen(detailsList, open) {
    detailsList.forEach(function(d) { d.open = !!open; });
  }

  function fileReadsIn(root) {
    return root ? root.querySelectorAll('details.inline-file-read') : [];
  }

  function bubbleBodyCollapsed(root) {
    return root && root.getAttribute('data-body-collapsed') === 'true';
  }

  function setBubbleBodyCollapsed(root, collapsed) {
    if (!root || !root.hasAttribute('data-body-collapsed')) return;
    root.setAttribute('data-body-collapsed', collapsed ? 'true' : 'false');
    var body = root.querySelector(':scope > .bubble-body');
    if (body && !collapsed) body.classList.remove('is-clipped');
    var btn = root.querySelector(':scope > .bubble-header .fold-header-btn');
    if (btn) {
      btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      var hasFiles = fileReadsIn(root).length > 0;
      if (collapsed) {
        btn.title = 'Expand';
      } else {
        btn.title = hasFiles && fileReadsModeOn() ? 'Collapse' : 'Collapse';
      }
    }
    schedulePreviewClipUpdate();
  }

  function syncFileReadHeaderBtn(root) {
    if (!root) return;
    var btn = root.querySelector(':scope > .bubble-header .fold-header-btn');
    if (!btn) return;
    // Body-level collapse wins over per-file open state
    if (root.hasAttribute('data-body-collapsed')) {
      var bodyClosed = bubbleBodyCollapsed(root);
      btn.setAttribute('aria-expanded', bodyClosed ? 'false' : 'true');
      btn.title = bodyClosed ? 'Expand' : 'Collapse';
      return;
    }
    var files = fileReadsIn(root);
    if (!files.length) return;
    var anyOpen = false;
    files.forEach(function(d) { if (d.open) anyOpen = true; });
    btn.setAttribute('aria-expanded', anyOpen ? 'true' : 'false');
    btn.title = anyOpen ? 'Collapse files' : 'Expand files';
  }

  function fileReadsModeOn() {
    return document.body.classList.contains('file-reads-on');
  }

  document.querySelectorAll('.fold-header-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var root = btn.closest('.bubble, .artifact-doc');
      if (!root) return;

      // tool_result: whole body starts collapsed; chevron toggles the bubble body.
      // File cards inside may already be open (single-file default) - that is fine.
      if (root.hasAttribute('data-body-collapsed')) {
        var expand = bubbleBodyCollapsed(root);
        setBubbleBodyCollapsed(root, !expand);
        return;
      }

      var files = fileReadsIn(root);
      // When File cards are on, chevron opens all file rows (+ prefix folds).
      // When off, behave like a normal fold on the flat tool_result body.
      if (files.length && fileReadsModeOn()) {
        var openFiles = btn.getAttribute('aria-expanded') !== 'true';
        setDetailsOpen(files, openFiles);
        root.querySelectorAll('.file-reads-split .fold').forEach(function(f) {
          setFoldCollapsed(f, !openFiles);
        });
        btn.setAttribute('aria-expanded', openFiles ? 'true' : 'false');
        btn.title = openFiles ? 'Collapse files' : 'Expand files';
        return;
      }
      var fold = root.querySelector(
        fileReadsModeOn() ? '.fold' : '.file-reads-flat .fold, .fold'
      );
      if (!fold && root.querySelector('.file-reads-flat')) {
        fold = root.querySelector('.file-reads-flat .fold');
      }
      toggleFold(fold);
    });
  });

  // Keep tool_result chevron in sync when individual file rows are toggled
  document.querySelectorAll('details.inline-file-read').forEach(function(d) {
    d.addEventListener('toggle', function() {
      syncFileReadHeaderBtn(d.closest('.bubble'));
    });
  });
  document.querySelectorAll('.bubble[data-file-reads="true"]').forEach(syncFileReadHeaderBtn);
  document.querySelectorAll('.bubble[data-body-collapsed]').forEach(function(root) {
    syncFileReadHeaderBtn(root);
  });

  function foldsInActiveTab() {
    var active = document.querySelector('.tab-panel.active') || document;
    return active.querySelectorAll('.fold');
  }

  function fileReadsInActiveTab() {
    var active = document.querySelector('.tab-panel.active') || document;
    return active.querySelectorAll('details.inline-file-read');
  }

  function bodyCollapsedBubblesInActiveTab() {
    var active = document.querySelector('.tab-panel.active') || document;
    return active.querySelectorAll('.bubble[data-body-collapsed]');
  }

  var expandAll = document.getElementById('expand-all');
  var collapseAll = document.getElementById('collapse-all');
  if (expandAll) {
    expandAll.addEventListener('click', function() {
      foldsInActiveTab().forEach(function(f) { setFoldCollapsed(f, false); });
      setDetailsOpen(fileReadsInActiveTab(), true);
      bodyCollapsedBubblesInActiveTab().forEach(function(b) {
        setBubbleBodyCollapsed(b, false);
      });
      document.querySelectorAll('.bubble[data-file-reads="true"]').forEach(syncFileReadHeaderBtn);
    });
  }
  if (collapseAll) {
    collapseAll.addEventListener('click', function() {
      var anchor = firstVisibleBlock();
      preserveAnchorScroll(anchor, function() {
        foldsInActiveTab().forEach(function(f) { setFoldCollapsed(f, true); });
        setDetailsOpen(fileReadsInActiveTab(), false);
        bodyCollapsedBubblesInActiveTab().forEach(function(b) {
          setBubbleBodyCollapsed(b, true);
        });
        document.querySelectorAll('.bubble[data-file-reads="true"]').forEach(syncFileReadHeaderBtn);
      });
    });
  }

  function getBlockRoot(el) {
    return el.closest('.bubble, .artifact-doc');
  }

  function getBubbleSourceText(root, selector) {
    if (!root) return '';
    var blocks = root.querySelectorAll(selector);
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

  function getBubbleRawText(root) {
    return getBubbleSourceText(root, '.md-block textarea.raw-src, .md-block script.raw-src');
  }

  function getBubbleMarkdownText(root) {
    return getBubbleSourceText(root, '.md-block textarea.md-src, .md-block script.md-src');
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
        copyText(formatBubbleMarkdown(root, getBubbleMarkdownText(root)), 'Markdown copied');
      } else {
        copyText(raw, 'Raw text copied');
      }
      closeAllCopyMenus();
      return;
    }
    if (!ev.target.closest('.copy-menu')) closeAllCopyMenus();
  });

  // ── Markdown toggle (optional and fail-closed) ────────────
  var mdToggle = document.getElementById('md-toggle');
  var markdownLibrariesOk = (
    typeof marked !== 'undefined' &&
    typeof marked.parse === 'function' &&
    typeof DOMPurify !== 'undefined' &&
    typeof DOMPurify.sanitize === 'function'
  );

  function isHashOnlyHref(href) {
    if (!href) return true;
    var h = String(href).trim();
    return h.charAt(0) === '#' || h.toLowerCase().indexOf('javascript:') === 0;
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

  function renderMarkdown(src) {
    src = normalizeNewlines(src);
    if (!markdownLibrariesOk) return null;
    try {
      var html = marked.parse(src);
      html = stripHashOnlyLinks(html);
      html = DOMPurify.sanitize(html, {
        ALLOWED_TAGS: [
          'a', 'abbr', 'b', 'blockquote', 'br', 'code', 'del', 'div', 'em',
          'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'i', 'li', 'ol', 'p',
          'pre', 's', 'span', 'strong', 'sub', 'sup', 'table', 'tbody', 'td',
          'th', 'thead', 'tr', 'u', 'ul'
        ],
        ALLOWED_ATTR: ['class', 'href', 'rel', 'title'],
        ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto):|[/?#.]|[^a-z+.-][^:]*$)/i,
        ALLOW_DATA_ATTR: false,
        ALLOW_ARIA_ATTR: false,
        FORBID_TAGS: [
          'form', 'iframe', 'img', 'input', 'math', 'object', 'script', 'style',
          'svg', 'template'
        ]
      });
      html = stripHashOnlyLinks(html);
      return html;
    } catch (err) {
      return null;
    }
  }

  function renderPlainFallback(container, src) {
    var pre = document.createElement('pre');
    pre.className = 'md-preserve';
    pre.textContent = normalizeNewlines(src);
    container.replaceChildren(pre);
  }

  function setMarkdownMode(on) {
    document.body.classList.toggle('md-on', !!on);
    document.querySelectorAll('.md-block').forEach(function(block) {
      var plain = block.querySelector('.md-plain');
      var rich = block.querySelector('.md-rich');
      if (!plain || !rich) return;
      if (on && markdownLibrariesOk) {
        if (!rich.dataset.rendered) {
          var source = readSrc(block);
          var sanitized = renderMarkdown(source);
          if (sanitized === null) {
            renderPlainFallback(rich, source);
          } else {
            // This is the only session-derived HTML sink. `sanitized` can only
            // be returned after DOMPurify succeeds with the allowlist above.
            rich.innerHTML = sanitized;
          }
          rich.dataset.rendered = '1';
        }
        plain.hidden = true;
        rich.hidden = false;
      } else {
        plain.hidden = false;
        rich.hidden = true;
      }
    });
    schedulePreviewClipUpdate();
  }

  if (mdToggle) {
    if (markdownLibrariesOk && typeof marked.use === 'function') {
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
          },
          image: function(token) {
            var href = token && token.href != null ? token.href : (arguments[0] || '');
            var title = token && token.title != null ? token.title : (arguments[1] || '');
            var alt = token && token.text != null ? token.text : (arguments[2] || href || 'image');
            var text = escapeHtml(String(alt || href || 'image'));
            if (isHashOnlyHref(href)) return text;
            var t = title ? ' title="' + escapeHtml(String(title)) + '"' : '';
            // Transcript Markdown may contain image syntax or raw output URLs.
            // Keep it navigable, but never fetch/render it as an image here.
            return '<a class="md-image-link" href="' + escapeHtml(String(href)) + '"' + t +
              ' rel="noopener">' + text + '</a>';
          }
        }
      });
    } else if (markdownLibrariesOk && marked.setOptions) {
      marked.setOptions({ gfm: true, breaks: true });
    }

    var prefer = true;
    try {
      var stored = localStorage.getItem(MD_KEY);
      if (stored === '0') prefer = false;
      if (stored === '1') prefer = true;
    } catch (e) {}

    if (!markdownLibrariesOk) {
      prefer = false;
      mdToggle.disabled = true;
      mdToggle.title = 'Markdown parser or sanitizer failed to load';
    }
    mdToggle.checked = prefer;
    setMarkdownMode(prefer && markdownLibrariesOk);
    mdToggle.addEventListener('change', function() {
      var on = mdToggle.checked && markdownLibrariesOk;
      try { localStorage.setItem(MD_KEY, mdToggle.checked ? '1' : '0'); } catch (e) {}
      setMarkdownMode(on);
    });
  }

  // ── File-cards toggle (split shell file reads in tool_result) ──
  var fileReadsToggle = document.getElementById('file-reads-toggle');
  function setFileReadsMode(on) {
    document.body.classList.toggle('file-reads-on', !!on);
    schedulePreviewClipUpdate();
  }
  if (fileReadsToggle) {
    var fileReadsPrefer = true;
    try {
      var frStored = localStorage.getItem(FILE_READS_KEY);
      if (frStored === '0') fileReadsPrefer = false;
      if (frStored === '1') fileReadsPrefer = true;
    } catch (e) {}
    fileReadsToggle.checked = fileReadsPrefer;
    setFileReadsMode(fileReadsPrefer);
    fileReadsToggle.addEventListener('change', function() {
      try { localStorage.setItem(FILE_READS_KEY, fileReadsToggle.checked ? '1' : '0'); } catch (e) {}
      setFileReadsMode(fileReadsToggle.checked);
    });
  } else {
    // No control on this page - default to split view when file cards exist
    setFileReadsMode(true);
  }

  // ── Preview toggle (collapsed bubble/fold snippet for all roles) ──
  var previewToggle = document.getElementById('preview-toggle');
  function setPreviewMode(on) {
    document.body.classList.toggle('preview-on', !!on);
    schedulePreviewClipUpdate();
  }
  if (previewToggle) {
    var previewPrefer = true;
    try {
      var pvStored = localStorage.getItem(PREVIEW_KEY);
      if (pvStored === '0') previewPrefer = false;
      if (pvStored === '1') previewPrefer = true;
    } catch (e) {}
    previewToggle.checked = previewPrefer;
    setPreviewMode(previewPrefer);
    previewToggle.addEventListener('change', function() {
      try { localStorage.setItem(PREVIEW_KEY, previewToggle.checked ? '1' : '0'); } catch (e) {}
      setPreviewMode(previewToggle.checked);
    });
  } else {
    setPreviewMode(true);
  }

  // Fade the bottom of collapsed previews only when content actually overflows
  // the max-height cap (short messages that fit stay fully opaque).
  var previewClipRaf = 0;
  function updatePreviewClipState() {
    previewClipRaf = 0;
    var previewOn = document.body.classList.contains('preview-on');
    document.querySelectorAll('.bubble-body.is-clipped, .fold-body.is-clipped').forEach(function(el) {
      if (!previewOn) el.classList.remove('is-clipped');
    });
    if (!previewOn) return;

    document.querySelectorAll(
      '.bubble[data-body-collapsed="true"] > .bubble-body'
    ).forEach(function(el) {
      el.classList.toggle('is-clipped', el.scrollHeight > el.clientHeight + 1);
    });
    document.querySelectorAll(
      '.fold[data-collapsed="true"] > .fold-body'
    ).forEach(function(el) {
      el.classList.toggle('is-clipped', el.scrollHeight > el.clientHeight + 1);
    });
  }
  function schedulePreviewClipUpdate() {
    if (previewClipRaf) return;
    previewClipRaf = window.requestAnimationFrame(function() {
      // Second frame: wait for layout after mode toggles / markdown swap.
      previewClipRaf = window.requestAnimationFrame(updatePreviewClipState);
    });
  }
  window.addEventListener('resize', schedulePreviewClipUpdate);
  schedulePreviewClipUpdate();

})();
