# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Replaced the per-agent `if/elif` dispatch chains with an agent registry. Each supported
  agent is now one `AgentSpec` in `agent_session_viewer/registry.py` describing its home
  directory, session roots, path-shape authorization, media boundaries, `/raw` candidates,
  Markdown export header, and the per-agent copy in the session view. The route,
  discovery, authorization, export, and template layers read that descriptor instead of
  branching on the agent name.
- Every agent parser now exposes the same `load_session(path) -> SessionData` entrypoint,
  wired up in `agent_session_viewer/agents/loaders.py`. Grok's session assembly moved out
  of `session.py` into `agents/grok.py` alongside Codex's. No base class or protocol was
  introduced - the shared shape is the existing `SessionData` TypedDict.
- Agent home directories resolve through `config` when they are read rather than being
  bound at import, so a single patch point redirects every module.

Apart from the Claude tab labelling fixed below, there is no user-visible change: routes,
URLs, export format, and rendered output are unchanged. Verified by capturing `/view`,
`/export`, `/raw`, and `/` for real sessions of all three agents before and after the
refactor.

### Fixed

- Claude's second tab is now labelled "Events timeline" and describes what it actually
  contains (permission-mode changes, queue operations, hook summaries, turn durations,
  file-history snapshots, attachments, and subagent runs). It previously reused Grok's
  copy and claimed to be aggregated from `updates.jsonl`, a file Claude does not write.
  The README already documented it as "Events timeline"; the UI now matches.

## [0.2.0] - 2026-08-01

Security and resilience release. No session data format changed; all routes and CLI
flags are backward compatible with 0.1.0.

### Security

- Upgraded the vendored DOMPurify from 3.2.6 to 3.4.12. 3.2.6 was affected by
  CVE-2026-0540 (a `SAFE_FOR_XML` regex bypass needing no special configuration, fixed in
  3.3.2) and CVE-2026-41238 (a prototype-pollution route to a custom-element bypass, fixed
  in 3.4.0). The sanitizer allowlist in `app.js` already excluded the tags both rely on, so
  neither was known to be exploitable here. DOMPurify now ships both of its dual-license
  texts, `LICENSE` (Apache-2.0) and `LICENSE-MPL` (MPL-2.0). The vendored marked 15.0.12 is
  unaffected by any published advisory and is unchanged.
- Removed `'unsafe-inline'` from the `style-src` Content-Security-Policy directive. All
  inline `style` attributes were replaced with stylesheet classes; the token-usage and
  context-window bar widths now ship as `data-pct` attributes and are applied from
  `app.js`. The bars render empty when JavaScript is disabled.
- Session Markdown is rendered client-side through vendored `marked` and `DOMPurify`
  with a restrictive sanitizer allowlist. Stored session content cannot execute script,
  including when either vendored library fails to load — rendering falls back to plain
  text rather than raw HTML.
- Added filesystem authorization for the `/view`, `/export`, `/raw`, and `/media` routes.
  Requested paths must resolve under the requesting agent's own home directory and match
  that agent's session shape; media is further restricted by an image-extension allowlist
  and containment under session-derived roots.
- Added `Content-Security-Policy`, `X-Content-Type-Options`, and `Referrer-Policy`
  headers to every response.
- Data-URL images must present matching magic bytes before they are rendered.
- Vendored `marked` and `DOMPurify` locally. The viewer makes no network requests and
  works fully offline.

### Accessibility

- The session count line is now a `role="status"` live region, so client-side search
  filtering announces the new result count instead of removing rows silently.

### Added

- Claude Code sessions reach feature parity with Grok and Codex: transcript rendering,
  system turns, subagent transcripts merged inline, todos, and `CLAUDE.md` memory
  documents.
- Per-project front page with pinned projects, an Expand toggle, per-agent badges, and
  sorting by updated, created, or name.
- Todos and prompt-history panels with deep links into the matching chat turn, for all
  three agents.
- Per-file cards for shell file reads, including Codex `exec` wrappers.
- Bounded discovery caching keyed on file size and mtime, so repeated session listing
  avoids re-reading unchanged files.
- Configuration overrides loaded from a `.env` file in the working directory.
- Quality gates in CI: Ruff lint and format, Pyright, pytest, a distribution build, a
  wheel-content smoke test, and Chromium browser-security regression tests.

### Changed

- Runtime assets (templates, CSS, JavaScript, icons) are owned and served by the
  installed package rather than the source checkout. The root `app.py` and `main.py`
  remain as deprecated shims.
- Codex rollouts load in a single pass instead of repeatedly re-reading the file.
- Detected code in session output is fenced before rendering, so embedded Markdown in
  tool dumps no longer breaks the page.

### Deprecated

- The root `app.py` and `main.py` shims are scheduled for removal in **0.3.0**. They exist
  only so older source checkouts keep working, and are already absent from the wheel. Use
  `agent-session-viewer` or `python -m agent_session_viewer` instead.

### Fixed

- Discovery no longer scans the whole metadata cache on every insert. Evicting the previous
  entry for a changed file now goes through an index, removing a quadratic term worth about
  0.84 s of CPU when first listing 5000 sessions.
- A single malformed JSONL record no longer crashes or truncates a session. Bad records
  are skipped and reported in a diagnostics panel on the session page.
- HTML entities in session content are decoded consistently across views.
- Image previews, media links, and both copy modes work for all three agents.
- Codex session cards show message counts instead of a placeholder.

## [0.1.0] - 2026-07-29

Initial development version. Never tagged or published to an index; recorded here for
continuity. The date is that of the first commit.

### Added

- Initial local viewer for Grok Build, Claude Code, and Codex CLI sessions: session list,
  session view, Markdown export, raw download, and media preview, served from a
  loopback-only Flask app.
