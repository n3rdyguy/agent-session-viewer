# Agent Session Viewer

Local web UI for browsing, searching, and exporting coding-agent conversations.

Supports:

- **Grok Build** (`~/.grok/sessions`)
- **Codex CLI** (`~/.codex/sessions`)
- **Claude Code** (`~/.claude/projects`)
- **Cursor** (`~/.cursor/projects/…/agent-transcripts/`)

Everything stays on your machine. The app only reads session files; it does not send data anywhere.

> Claude Code parity was implemented by Claude Code, which read its own `~/.claude`
> transcripts to reverse-engineer the format - then found its own edits sitting in the
> live session file and used them as test data for the file-edit parser. The snake ate
> its own tail and reported the calorie count.

---

## Quick start

```bash
# 1. Install uv (macOS / Linux; see Installation for Windows and alternatives)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. From the project directory
uv sync
uv run agent-session-viewer
```

Open **http://127.0.0.1:5050**. Nothing to configure: the viewer reads Grok, Claude, Codex,
and Cursor sessions from their default home directories, and only reads them.

The front page groups sessions by project. Click a project header to expand it, then a
session to open it - or use **Expand** to open every group at once. Type in the search box
to filter instantly, press Enter for a server-side search, and use **Sort** to order by
updated, created, or name. Pin the projects you live in and they stay on top.

---

## Features

### Session list (front page)

- Sessions grouped into collapsible **per-project** sections derived from each session's
  working directory; sessions without one land in a single "(no project)" group
- **Expand** toggle: groups start collapsed; opening or closing an individual group is
  remembered as an override, and flipping the toggle clears the overrides
- **Pin** any project to float it above the rest (pinned groups keep the active sort)
- Two-letter **agent badges** on each project header (`CL` / `CO` / `GR` / `CU`) for the agents
  that have sessions in that project, in the same colors as the session badges
- **Sort** by updated, created, or name, ascending or descending
- Instant client-side filtering as you type, over the same fields as the server search;
  Enter still submits the form for a server-side search that survives a reload
- **Prompts** toggle: show the first user prompt under each session row
- Relative times ("2h ago") with the full timestamp in the tooltip, plus message count and
  model per row
- All list preferences (expand, per-group overrides, pins, sort, prompts) are stored in
  `localStorage`; with JavaScript disabled the list still renders, searches, and navigates

### All agents

- Unified session list across Grok, Claude, Codex, and Cursor
- Filter by agent (All / Grok / Claude / Codex / Cursor)
- Search title, ID, path, model, working directory, and first-prompt headline
- Chat-style conversation view with role-colored bubbles
- **Preview** toggle: chat and events start collapsed; long content shows a short faded
  snippet (fade only when the body overflows). Preference stored in `localStorage`
- **File cards** toggle: shell tool results that pulled files split into per-file cards
  (preference in `localStorage`)
- Header chevron plus **Expand all** / **Collapse all** for the active tab
- **Subagents** tab when the session spawned children (Claude sidechain JSONL under
  `subagents/agent-*.jsonl`; Grok `subagents/<id>/meta.json` plus sibling child session)
- **Todos and prompt history** card when either is available; each prompt row is one line
  (ellipsis when long) and jumps to that turn in chat when a match exists
- One-click **Markdown export**
- Raw transcript / summary download
- **Markdown** toggle (GFM via [marked](https://marked.js.org/), sanitized with DOMPurify)
  - Preference stored in `localStorage`
  - Unfenced code-only messages and tool output are detected and rendered in language-tagged code blocks
  - System instruction boxes and Markdown-labeled/`.md` artifacts are always interpreted as Markdown
  - Agent tags like `<user_info>` / `<system-reminder>` stay visible; headings, lists, and bold still render
  - Image syntax and raw `<img>` tags in transcript output become links/text instead of loading images
  - Only explicit session image attachments render in the separate image gallery
- Agent-aware path safety: only recognized sessions and their associated passive image
  media are served
- Resilient JSONL reading: damaged or non-object records are skipped, later records remain
  visible, and the session view reports bounded parse diagnostics
- Process-local discovery caching: unchanged session cards are reused by list, filter, and
  search requests; Claude card scans use bounded memory and Codex card scans decode only a
  small metadata/headline window. Message counts on the list are the cheap full-file record
  count (exact chat counts need a full decode and are deferred to the session view)

### Grok-focused (rich session view)

Grok sessions get a deeper view of the on-disk session folder:

| Area | What you get |
|------|----------------|
| **Summary** | Title, model, agent name, reasoning effort, sandbox, cwd, message counts, branch/commit |
| **Token usage** | Estimated in / out / cached / reasoning from `updates.jsonl` `turn_completed` events; context window from `signals.json` |
| **Todos & prompt history** | Checklist from `resources_state.json` (or replayed `todo_write` calls); project-level `prompt_history.jsonl` filtered by session id, with jump-to-turn links |
| **Settings** | Tool params, scheduler / completions, other resource state |
| **System instructions** | Injected system / developer / project-instruction blobs lifted out of the chat stream into a collapsed panel |
| **Chat history** | Full `chat_history.jsonl`: user, assistant, **reasoning** (summary + `<encrypted>`), tool calls/results with **call ids** |
| **Terminal** | Matched `terminal/<call-id>.log` previews and enrichment of thin tool results |
| **Hunk records** | File edit events from `hunk_records.jsonl` |
| **Recap requests** | Index of `recap_requests/*.json` (id, time, trigger, model) |
| **Updates stream** | Second tab: aggregated `updates.jsonl` timeline (chunks collapsed, tool ids kept) |
| **Subagents** | Third tab when present: child runs from `subagents/<id>/meta.json`, with spawn metadata and full sibling child-session chat when `child_session_id` exists under the same project folder; **Open full session** links to the child directory |
| **Images** | `<image_files>` paths (clickable + preview via `/media`), inline `data:image/…;base64,…` previews with JSON snippet; click data-URL images to **copy to clipboard** |

### Codex-focused (rich rollout view)

Codex `rollout-*.jsonl` sessions under `~/.codex/sessions/` (and `archived_sessions/` if present) get a similar deep view:

| Area | What you get |
|------|----------------|
| **List titles** | Safe `thread_name` from `~/.codex/session_index.jsonl`, plus a safe first-user-message headline when available |
| **Summary** | Session id, model, originator/CLI, cwd, approval/sandbox, reasoning effort, personality, plan type, git branch/commit |
| **Token usage** | From `event_msg` / `token_count` - last cumulative `total_token_usage` (in / out / cached / reasoning) + context window |
| **Todos & prompt history** | Checklist from `update_plan` tool calls (JSON `function_call`, `custom_tool_call` / `exec`); prompts from `~/.codex/history.jsonl`, with jump-to-turn links |
| **Settings** | Approval policy, sandbox, effort, personality, provider, repo URL |
| **System instructions** | Developer / base / project-instruction injections and `AGENTS.md` from `world_state`, lifted into a collapsed panel |
| **Chat history** | User + agent messages, **reasoning** (`<encrypted>` when present), tool calls/results with **call ids** only (no task lifecycle noise) |
| **Patches** | File edits from `patch_apply_end` (paths + diff snippets) under Session artifacts |
| **Events timeline** | Second tab: `task_started` / `task_complete`, patches, image generation (not mixed into Chat history) |
| **Images** | User `local_images` / generated images; narrowly named clipboard captures under temp (`codex-clipboard-*`) can be previewed when linked from an authorized Codex session |

### Claude-focused (rich transcript view)

Claude Code `<session-uuid>.jsonl` transcripts under `~/.claude/projects/<encoded-cwd>/`:

| Area | What you get |
|------|----------------|
| **List titles** | Real titles from `ai-title` / `custom-title` / `last-prompt` records, plus a first-user-message headline; the working directory comes from the records, not the lossy encoded folder name |
| **Summary** | Session id, model, cwd, git branch, CLI version, permission mode, reasoning effort, message counts |
| **Token usage** | Summed `message.usage` - input, output, cache **reads** and **writes**, with per-model rows when a session mixes models (e.g. Opus and Sonnet) |
| **Settings** | Model, CLI version, permission mode, mode, effort, entrypoint, slug, cwd, git branch |
| **Todos & prompt history** | Task checklist from `task_reminder` attachments (fallback: `~/.claude/todos/<session-id>-agent-*.json`); prompts from `~/.claude/history.jsonl`, with jump-to-turn links |
| **Chat history** | User + assistant messages, **thinking** (`<encrypted>` when only a signature is stored), tool calls/results matched by **`toolu_` call id** |
| **System turns** | Slash commands and hook/informational `system` records, injected attachments (plan mode, permissions, task reminders), and `isMeta` `<system-reminder>` records shown as **system reminders** rather than as user messages (inline; Claude has no separate System instructions panel) |
| **Subagents** | Sidechain transcripts from `<session>/subagents/agent-*.jsonl` merged inline in chat (timestamp order, tagged by agent type) **and** listed on the **Subagents** tab with each child's full transcript in isolation; opening a subagent path directly is still supported |
| **File edits** | Hunk records from `Edit` / `Write` / `MultiEdit` / `NotebookEdit` `toolUseResult`, with added/removed counts and line ranges from `structuredPatch` |
| **Memory** | Project `<cwd>/CLAUDE.md` and user `~/.claude/CLAUDE.md` as artifact documents |
| **Artifacts** | Skill and agent listings |
| **Events timeline** | Second tab: hook summaries, slash commands, turn durations, queue operations, permission-mode changes, file-history snapshots, attachments |

Claude does not record a context-window size in the transcript, so the context bar stays
hidden; token totals are summed per message rather than read from a running total.

Claude also has no equivalent of Codex's `session_meta.base_instructions`: injected
instructions are never written to the transcript. The memory documents are therefore read
from disk and carry the caveat **"on disk now, may differ from this session"** in their
header - they show those files as they stand now, which may differ from what the session
was actually given. The caveat sits in the subtitle rather than the body,
so both copy actions still yield the file verbatim. Only a real file still named
`CLAUDE.md` is read - a symlink pointing elsewhere resolves to a different name and is
refused - and oversized files are truncated for display.

---

## Requirements

| Need | Notes |
|------|--------|
| **[Python 3.10+](https://www.python.org/downloads/)** | Oldest version covered by CI. `.python-version` pins the preferred development version (currently 3.14); `uv` can install it for you. |
| **[uv](https://docs.astral.sh/uv/)** | Package manager + project runner (creates `.venv`, installs deps from `uv.lock`). |
| **A browser** | View the UI at `http://127.0.0.1:5050`. |
| **Node.js** | **Not required** for running or developing the viewer. Markdown uses package-vendored **marked** and **DOMPurify** under `agent_session_viewer/static/vendor/` (no CDN, no `npm install`). |

Runtime Python dependency: **Flask** only. Session data stays on your machine and is never uploaded.

### Runtime dependencies

Browser-side Markdown uses the pinned, package-owned marked and DOMPurify distributions
listed with versions, licenses, and upgrade notes in
`agent_session_viewer/static/vendor/README.md`. They are served locally; viewing a session
does not need Node, a CDN, or other network services.

---

## Installation

### 1. Install uv

[uv](https://docs.astral.sh/uv/) is a fast Python package manager from Astral. It replaces
the usual `pip` + manual venv workflow for this project: `uv sync` creates `.venv` and
installs locked dependencies; `uv run …` runs commands in that environment without you
activating it.

**macOS / Linux** (curl installer):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows** (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Alternative installs** (any platform):

```bash
# If you already have pip on a system Python:
pip install uv

# Or via pipx (isolated tool install):
pipx install uv

# Homebrew (macOS / Linux):
brew install uv

# WinGet (Windows):
winget install --id=astral-sh.uv -e
```

Confirm:

```bash
uv --version
```

If the shell cannot find `uv` after install, open a new terminal (or ensure the installer’s
bin directory is on your `PATH`). Official install docs:
[docs.astral.sh/uv/getting-started/installation](https://docs.astral.sh/uv/getting-started/installation/).

### 2. Clone and sync the project

```bash
cd agent-session-viewer
uv sync
```

What that does:

- Creates a project virtual environment at **`.venv`** (you can leave it alone)
- Installs the app and dependencies from **`uv.lock`** (reproducible)
- Can install a matching Python if needed (see `uv python install` / project pin)

You do **not** need a separate `python -m venv` or `pip install -r …` step. Prefer
`uv run …` over activating `.venv` by hand so every command uses the same locked env.

Dev tools (pytest, ruff, pyright, playwright) are in the `dev` dependency group and come
along with a normal `uv sync`. For a frozen CI-style install: `uv sync --frozen`.

The installed application, including templates and static assets, lives entirely in the
`agent_session_viewer/` package.

### Package layout

```text
agent_session_viewer/
  __main__.py       # python -m agent_session_viewer
  cli.py            # installed console entrypoint
  app.py            # Flask routes and local server
  authorization.py  # Agent-aware path authorization
  config.py         # Agent home-directory settings
  discovery.py      # Session discovery and list metadata
  file_reads.py     # Shell file-read splitting into per-file cards
  images.py         # Image/content extraction and safe path handling
  markdown_output.py
  grouping.py       # Per-project grouping for the session list
  session.py        # Shared load/export shape
  turns.py          # Canonical conversation turns
  types.py          # Shared session, turn, card, image, and diagnostic shapes
  util.py           # JSONL, token, time, and path helpers
  registry.py       # One AgentSpec per agent: homes, path rules, media roots, labels
  home_inventory/   # /agents page: settings, skills, hooks, catalogs
  templates/        # package-owned Jinja templates
  static/           # package-owned CSS, JavaScript, icons, and vendored libraries
    theme-boot.js   # Applies the stored theme in <head>, before first paint
    prefs.js        # Shared localStorage keys; default-agent redirect
    settings.js     # Settings page controls
    agents.js       # Agents inventory sticky TOC / expand skills
  agents/
    grok.py
    codex.py
    claude.py
    cursor.py
    loaders.py      # agent id -> load_session, the only agent interface
```

Agent-specific behaviour lives in one descriptor per agent in `registry.py`, which the
route, discovery, authorization, export, and template layers all read from instead of
branching on the agent name. Each parser module exposes the same
`load_session(path) -> SessionData` entrypoint; there is no base class, protocol, or
plugin loader behind it. Adding an agent means: one `AgentSpec`, one parser module, an
entry in `agents/loaders.py` and `discovery._DISCOVERERS`, the `Agent` literal in
`authorization.py`, and one `.badge` rule in `app.css` (the CSP rules out registry-driven
colours). `tests/test_registry.py` fails if any of those fall out of step.

Codex rollout records and Claude transcripts are each decoded once per session request and
reused for the conversation, summary, tokens, events, and patches.

---

## Usage

After `uv sync`:

```bash
# Preferred (installed console entrypoint):
uv run agent-session-viewer

# Module form:
uv run python -m agent_session_viewer
```

Open **http://127.0.0.1:5050** in any modern browser. No build step and no Node.

The server binds to `127.0.0.1` only (local loopback).

### Windows 10 + WSL

Some Windows 10 / WSL installations do not forward WSL loopback ports to Windows. In
that case, bind the server to all WSL interfaces:

```bash
uv run agent-session-viewer --host 0.0.0.0
```

Flask will print a WSL address such as `http://172.27.212.98:5050`; open that address
from your Windows browser. The WSL IP address can change when WSL restarts, so use the
address printed each time you start the server. Binding to `0.0.0.0` makes the server
reachable beyond WSL loopback; do not use it on an untrusted network.

### Environment variables

See [`.env.example`](.env.example). Copy it to `.env` in the directory where you start the
app (the file is gitignored). The app loads it automatically. Variables explicitly set by
your shell or IDE take precedence over `.env`; `~` in path overrides is expanded on all
platforms.

| Variable | Purpose | Default |
|----------|---------|---------|
| `ASV_DEBUG` | Flask debug / reloader (`1` / `true` / `yes` / `on`) | off |
| `ASV_TIMING_DEBUG` | Log discovery and session-load timings | off |
| `GROK_HOME` | Grok product root (sessions under `…/sessions`) | `~/.grok` |
| `CLAUDE_HOME` | Claude Code root (projects under `…/projects`) | `~/.claude` |
| `CODEX_HOME` | Codex root (sessions under `…/sessions`) | `~/.codex` |
| `CURSOR_HOME` | Cursor root (`projects/…/agent-transcripts` sessions + inventory) | `~/.cursor` |

`ASV_TIMING_DEBUG` is independent of `ASV_DEBUG`. After enabling it, reload the session
list or open a session. Timing entries are written to the terminal running the server:

```text
INFO agent_session_viewer.discovery: session discovery completed in 12.4 ms
INFO agent_session_viewer.session: session loading (codex) completed in 48.7 ms
```

**Linux / macOS** (bash/zsh):

```bash
export ASV_DEBUG=1
export ASV_TIMING_DEBUG=1
export GROK_HOME=~/.grok
uv run agent-session-viewer

# one-shot:
ASV_DEBUG=1 ASV_TIMING_DEBUG=1 uv run agent-session-viewer
```

**Windows PowerShell:**

```powershell
$env:ASV_DEBUG = "1"
$env:ASV_TIMING_DEBUG = "1"
$env:GROK_HOME = "~/.grok"
uv run agent-session-viewer

# one-shot:
$env:ASV_DEBUG = "1"; $env:ASV_TIMING_DEBUG = "1"; uv run agent-session-viewer
```

**Windows cmd.exe:**

```bat
set ASV_DEBUG=1
set ASV_TIMING_DEBUG=1
set GROK_HOME=~/.grok
uv run agent-session-viewer
```

---

## Session locations

| Agent | Default (Linux / macOS) | Default (Windows) | Override env var |
|-------|-------------------------|-------------------|------------------|
| Grok Build | `~/.grok/sessions/` | `%USERPROFILE%\.grok\sessions\` | `GROK_HOME` (product root) |
| Claude Code | `~/.claude/projects/` | `%USERPROFILE%\.claude\projects\` | `CLAUDE_HOME` |
| Codex CLI | `~/.codex/sessions/` (+ `archived_sessions`) | `%USERPROFILE%\.codex\sessions\` | `CODEX_HOME` |
| Cursor | `~/.cursor/projects/…/agent-transcripts/` | `%USERPROFILE%\.cursor\projects\…` | `CURSOR_HOME` |

On Windows, Grok session group folders are URL-encoded paths under `sessions\` (e.g. `C%3A%5CUsers%5C…`).

Claude also reads two sibling files under `CLAUDE_HOME` when they exist: `todos/<session-id>-agent-*.json`,
a fallback for Claude Code versions that wrote the checklist to disk rather than into the
transcript, and `history.jsonl` for that session's prompt history. It reads
`CLAUDE.md` from `CLAUDE_HOME` and from the session's recorded working directory - the one
place the viewer reads outside an agent home, guarded by a fixed filename, a post-resolution
name check, and a size cap. None of these are served by a route; all are read locally and
degrade to empty when missing or damaged.

---

## Routes

| Path | Purpose |
|------|---------|
| `/` | Session list grouped by project, search, agent filters |
| `/view?agent=&path=` | Session view: summary, todos/prompt history, artifacts, system instructions (Grok/Codex), chat, and events |
| `/export?agent=&path=` | Download conversation as Markdown |
| `/raw?agent=&path=` | Download the raw file derived from an authorized session |
| `/media?agent=&session=&path=` | Serve passive image media associated with an authorized session |
| `/agents` | Read-only inventory of each agent home (settings, skills, hooks, plugins, MCP, instruction files) |
| `/settings` | Browser preferences, and a read-only summary of this install |

Application links are generated by Flask, including query encoding. Search text therefore
round-trips through every agent filter unchanged, including Unicode and reserved URL
characters.

### Agents (home inventory)

The **Agents** control to the left of Settings opens `/agents`. It is a read-only dump of
each coding-agent **global home** directory:

| Agent | Default home | What is listed |
|-------|--------------|----------------|
| Grok | `~/.grok` | `config.toml`, skills (user + bundled + Claude/Cursor compat), hooks, MCP, plugins, `AGENTS.md` / variants, rules, home map |
| Claude | `~/.claude` | `settings.json`, skills, hooks, plugins metadata, `CLAUDE.md` / variants, rules/commands/agents, statusline |
| Codex | `~/.codex` | `config.toml`, skills, plugins, MCP, rules, `AGENTS.md` / variants |
| Cursor | `~/.cursor` | `hooks.json`, user skills + `skills-cursor`, agents/rules, MCP if present; sessions via agent-transcripts |

The page also shows **recommended settings** tips (from official docs / common practice)
and the current value of a few mapped keys when present — including Claude’s
`includeCoAuthoredBy` default (Co-Authored-By trailers on commits until turned off) and
Grok’s multi-location skill discovery (`.grok` / `.claude` / `.cursor` / `.agents` trees).
Secrets in config are redacted; session transcripts, auth files, and plugin source caches
are never opened. Nothing is written. Override homes with `GROK_HOME`, `CLAUDE_HOME`,
`CODEX_HOME`, or `CURSOR_HOME`.

### Settings

The gear icon on the right of the header opens `/settings`, which collects every display
preference in one place:

| Group | Settings |
|-------|----------|
| Appearance | Theme: Dark, Light, or Auto (follows the operating system) |
| Session list | Default agent filter, default sort field and direction, relative or absolute row timestamps, expand project groups, show first prompt |
| Session view | Render Markdown, file cards, preview collapsed bubbles |
| Stored data | Clear pinned projects, clear per-project expand overrides, reset all preferences |
| This install | Version, loaded `.env`, debug flags, and each agent's env var, session roots, and session count |

Preferences are stored in `localStorage` under `asv-*` keys and applied client-side, so
they are per-browser and never leave the machine. The page is read-only with respect to the
server: there is no write route, and agent homes are still configured only by environment
variable or `.env` (see [Configuration](#configuration)) - `/settings` just shows what is
in effect.

Session roots are listed per agent. A root the agent always has is flagged **missing** when
it is absent, which usually means that agent is not installed or its home points elsewhere.
A root the agent creates lazily is labelled *not created yet* instead: Codex only creates
`archived_sessions` the first time a session is archived, so its absence is normal and not
worth a warning. Both kinds are searched identically - the distinction is presentation
only, and lives in `AgentSpec.session_subdirs` vs `AgentSpec.optional_subdirs`.

The theme is applied by `static/theme-boot.js`, loaded blocking in `<head>` so the stored
theme lands before first paint. "Auto" is resolved to a concrete theme there rather than in
CSS, which keeps `app.css` to a single `[data-theme="light"]` override block. With
JavaScript disabled no attribute is set and the dark default applies. Agent badge colours
and every other colour are CSS custom properties; adding a colour means adding it to both
`:root` and the light block.

All filesystem routes validate the agent and the expected on-disk session layout. A path
is not authorized merely because it is somewhere below an agent home. Resolved paths that
escape through traversal or symlinks, cross-agent paths, home-level configuration files,
and active SVG media are denied. Grok raw downloads prefer `chat_history.jsonl` and fall
back to `summary.json`; Claude and Codex raw downloads are their authorized JSONL file.
Claude authorizes exactly two shapes: `<project>/<session>.jsonl` and a subagent transcript
at `<project>/<session>/subagents/agent-*.jsonl`. Anything else at that depth - a different
directory name, a different filename prefix, a non-`.jsonl` suffix, or a deeper path - is
denied.

### Security and supported threat model

The supported threat model is local, loopback-only use. The app has no authentication;
binding it to a non-loopback interface is unsupported unless an authentication boundary is
added. Session contents are untrusted, and remote media is not loaded automatically.

One deliberate exception to "read only under an agent home": Claude memory documents are
read from the working directory recorded in the session. That path is untrusted session
data, so the read is constrained to a fixed `CLAUDE.md` filename, the resolved target must
still be a regular file with that name (which refuses symlinks aimed at other files), the
content is size-capped, and it is never exposed through `/media` or `/raw`.
Markdown is sanitized in the browser and falls back to escaped plain text if either the
parser or sanitizer is unavailable. Filesystem routes resolve and authorize paths against
the selected agent and session before reading them.

---

## Tests

Install the locked development environment and run all local quality gates with:

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
uv build
uv run python scripts/smoke_wheel.py
```

The fixture-based tests cover:

- Flask list, view, export, raw-download, and media route behavior with isolated
  temporary Grok, Claude, Codex, and Cursor homes
- Agent-home path safety, traversal, prefix collisions, and symlink escapes
- Image path detection and explicit mixed-content image extraction
- Ensuring Markdown/HTML output hrefs are not treated as image attachments
- Language-aware fencing of unfenced code in rendered, copied, and exported Markdown
- Turn roles and message IDs from tiny Grok, Claude, and Codex JSONL fixtures
- Claude token accounting (cache reads/writes, per-model rows), file-edit hunks from
  `structuredPatch`, chat counts that exclude tool-transport records, inline subagent
  tagging, dedicated Subagents-tab payloads, list-title precedence, and the subagent
  authorization shape
- Grok subagent index (`subagents/*/meta.json`) and sibling child-session loading
- Cursor agent-transcript discovery/parsing under `projects/…/agent-transcripts/`
- Agents inventory page (`/agents`): redaction, skill discovery, settings catalog
- Search/filter URL round-tripping for reserved characters and Unicode
- Per-project grouping (path spellings that merge, the "(no project)" bucket, recency
  ordering) and the rendered list controls: collapsed-by-default groups, sort options,
  pins, and per-agent badges
- Python 3.10 (the supported lower bound) and the preferred development version on both
  Windows and Linux in CI

The suite intentionally uses hand-written fixtures rather than mocks and
does not require a browser or real agent session data.

---

## Grok session files (reference)

A typical Grok session directory includes:

```
<summary.json>           # title, model, counts, cwd
chat_history.jsonl       # canonical transcript
updates.jsonl            # live stream / usage per turn
resources_state.json     # todos, tool settings
signals.json             # context window, counters
hunk_records.jsonl       # file edit hunks
terminal/<call-id>.log   # shell / task output
recap_requests/*.json    # compaction / recap payloads
assets/                  # user-attached images

# Sibling of session folders (per project cwd):
../prompt_history.jsonl  # user prompts for all sessions in this project

# Child agents (when present):
subagents/<child-id>/meta.json   # spawn metadata (type, status, prompt, model)
# Full child transcript is usually a sibling session directory named
# <child_session_id>/ under the same encoded-cwd folder (see Subagents tab).
```

Reasoning steps show the model **summary** text; full chain-of-thought is stored encrypted and displayed as `<encrypted>` (API `reasoningTokens` still count real reasoning usage).

---

## Notes

- Read-only with respect to agent data: no writes to `~/.grok`, `~/.claude`, `~/.codex`, or `~/.cursor`.
- **Subagents tab:** Claude sidechains (`…/subagents/agent-*.jsonl`) are still merged into the parent chat and listed per-child on the Subagents tab; Grok child runs appear from `subagents/<id>/meta.json` with an optional full sibling session. Opening Claude subagent paths (and Grok child session directories) directly is supported when authorized.
- Token totals are **estimates** reconstructed from usage records (not a separate billing ledger). Reasoning is usually included in output tokens; the UI shows both.
- Chat and events bubbles start collapsed; use Preview, the header chevron, or Expand all.
- Image and raw file access is restricted to paths under configured agent homes (plus the
  guarded Claude `CLAUDE.md` memory read described above).

---

## License

MIT
