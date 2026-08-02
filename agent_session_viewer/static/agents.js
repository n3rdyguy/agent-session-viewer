/**
 * Agents inventory page: sticky TOC sync (agent + topic), expand/collapse skills.
 * No server calls; CSP is script-src 'self'.
 */
(function () {
  const sticky = document.getElementById("agents-sticky");
  if (!sticky) return;

  const siteHeader = document.querySelector("header");
  const agentLinks = Array.from(sticky.querySelectorAll(".agents-toc-agent"));
  const sectionSets = Array.from(sticky.querySelectorAll(".agents-toc-sections"));
  const agentSections = Array.from(document.querySelectorAll(".agent-section[data-agent]"));
  // Topic panels (Tips, Settings, …) — one scroll target each.
  const topicPanels = Array.from(document.querySelectorAll(".agent-section .panel[id]"));
  const fullTocLinks = Array.from(
    document.querySelectorAll(".agents-toc-full a[href^='#']"),
  );

  let activeAgentId = "";
  let activeTopicId = "";

  function measureOffsets() {
    const siteH = siteHeader ? Math.ceil(siteHeader.getBoundingClientRect().height) : 0;
    document.documentElement.style.setProperty("--sticky-site-header-offset", siteH + "px");
    const tocH = Math.ceil(sticky.getBoundingClientRect().height);
    document.documentElement.style.setProperty("--sticky-agents-toc-offset", tocH + "px");
    const total = siteH + tocH + 12;
    document.documentElement.style.setProperty("--agents-scroll-margin", total + "px");
  }

  measureOffsets();
  window.addEventListener("resize", measureOffsets);

  function setActiveAgent(agentId) {
    if (!agentId || agentId === activeAgentId) return;
    activeAgentId = agentId;
    agentLinks.forEach((a) => {
      a.classList.toggle("is-active", a.dataset.agent === agentId);
    });
    sectionSets.forEach((nav) => {
      const on = nav.dataset.agent === agentId;
      nav.classList.toggle("is-active", on);
      if (on) nav.removeAttribute("hidden");
      else nav.setAttribute("hidden", "");
    });
    // Sticky height can change when switching section navs with different link counts.
    measureOffsets();
  }

  function setActiveTopic(topicId) {
    if (!topicId) return;
    if (topicId === activeTopicId) return;
    activeTopicId = topicId;
    const hash = "#" + topicId;

    sticky.querySelectorAll(".agents-toc-sections a").forEach((a) => {
      a.classList.toggle("is-active", a.getAttribute("href") === hash);
    });
    fullTocLinks.forEach((a) => {
      a.classList.toggle("is-active", a.getAttribute("href") === hash);
    });
  }

  function scrollLine() {
    const siteH = siteHeader ? siteHeader.getBoundingClientRect().height : 0;
    const tocH = sticky.getBoundingClientRect().height;
    // A bit below the sticky stack so the heading under the bar counts as "current".
    return siteH + tocH + 20;
  }

  /**
   * Pick the last element whose top has crossed the sticky line.
   * Works for agent sections and nested topic panels.
   */
  function pickCurrent(elements, line, getId) {
    let current = null;
    for (const el of elements) {
      if (el.getBoundingClientRect().top <= line) current = el;
    }
    return current ? getId(current) : null;
  }

  function syncFromScroll() {
    const line = scrollLine();

    const agentId =
      pickCurrent(agentSections, line, (el) => el.dataset.agent) ||
      (agentSections[0] && agentSections[0].dataset.agent);
    if (agentId) setActiveAgent(agentId);

    // Prefer a topic panel under the line; fall back to first panel of the active agent.
    let topicId = pickCurrent(topicPanels, line, (el) => el.id);
    if (!topicId && agentId) {
      const first = document.querySelector(
        '.agent-section[data-agent="' + agentId + '"] .panel[id]',
      );
      if (first) topicId = first.id;
    }
    if (topicId) setActiveTopic(topicId);
  }

  let scrollTick = false;
  window.addEventListener(
    "scroll",
    () => {
      if (scrollTick) return;
      scrollTick = true;
      requestAnimationFrame(() => {
        scrollTick = false;
        syncFromScroll();
      });
    },
    { passive: true },
  );

  agentLinks.forEach((a) => {
    a.addEventListener("click", () => {
      setActiveAgent(a.dataset.agent);
      // Jumping to agent top → first topic of that agent.
      const first = document.querySelector(
        '.agent-section[data-agent="' + a.dataset.agent + '"] .panel[id]',
      );
      if (first) setActiveTopic(first.id);
      requestAnimationFrame(measureOffsets);
    });
  });

  sticky.querySelectorAll(".agents-toc-sections a").forEach((a) => {
    a.addEventListener("click", () => {
      const href = a.getAttribute("href") || "";
      if (href.startsWith("#")) {
        const id = href.slice(1);
        const panel = document.getElementById(id);
        const section = panel && panel.closest(".agent-section");
        if (section && section.dataset.agent) setActiveAgent(section.dataset.agent);
        setActiveTopic(id);
      }
    });
  });

  // Expand / collapse all skills in one agent block.
  document.querySelectorAll(".skills-expand-all").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-skills-for");
      const root = document.querySelector('[data-skills-root="' + id + '"]');
      if (!root) return;
      root.querySelectorAll("details.skill-details").forEach((d) => {
        d.open = true;
      });
    });
  });
  document.querySelectorAll(".skills-collapse-all").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-skills-for");
      const root = document.querySelector('[data-skills-root="' + id + '"]');
      if (!root) return;
      root.querySelectorAll("details.skill-details").forEach((d) => {
        d.open = false;
      });
    });
  });

  // Honour hash on load (e.g. deep link to skills).
  if (location.hash) {
    const el = document.querySelector(location.hash);
    if (el) {
      const section = el.closest(".agent-section") || (el.classList.contains("agent-section") ? el : null);
      if (section && section.dataset.agent) setActiveAgent(section.dataset.agent);
      if (el.classList.contains("panel") && el.id) setActiveTopic(el.id);
      else if (el.id && el.id.indexOf("-") !== -1) {
        // agent-grok or agent-grok-settings
        const panel = el.classList.contains("panel") ? el : el.querySelector(".panel[id]");
        if (panel) setActiveTopic(panel.id);
      }
    }
    // After browser scroll-to-hash, re-sync once layout settles.
    requestAnimationFrame(() => {
      measureOffsets();
      syncFromScroll();
    });
  } else {
    syncFromScroll();
  }

  window.addEventListener("load", () => {
    measureOffsets();
    syncFromScroll();
  });
})();
