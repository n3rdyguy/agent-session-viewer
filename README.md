# Agent Session Viewer

Local web UI for browsing, searching, and exporting coding-agent conversations.

Supports:

- **Grok Build** (`~/.grok/sessions`)
- **Codex CLI** (`~/.codex/sessions`)
- **Claude Code** (`~/.claude/projects`)

Everything stays on your machine. The app only reads session files; it does not send data anywhere.

> Claude Code parity was implemented by Claude Code, which read its own `~/.claude`
> transcripts to reverse-engineer the format — then found its own edits sitting in the
> live session file and used them as test data for the file-edit parser. The snake ate
> its own tail and reported the calorie count.

---

## Features

### All agents

- Unified session list across Grok, Claude, and Codex
- Filter by agent (All / Grok / Claude / Codex)
- Search title, ID, path, model, and working directory
- Chat-style conversation view with role-colored bubbles
- Collapsible long tool outputs
- **Markdown** toggle (GFM via [marked](https://marked.js.org/), sanitized with DOMPurify)
  - Preference stored in `localStorage`
  - Unfenced code-only messages and tool output are detected and rendered in language-tagged code blocks
  - System instruction boxes and Markdown-labeled/`.md` artifacts are always interpreted as Markdown
  - Agent tags like `<user_info>` / `<system-reminder>` stay visible; headings, lists, and bold still render
  - Image syntax and raw `<img>` tags in transcript output become links/text instead of loading images
  - Only explicit session image attachments render in the separate image gallery
- One-click **Markdown export**
- Raw transcript / summary download
- Agent-aware path safety: only recognized sessions and their associated passive image
  media are served
- Resilient JSONL reading: damaged or non-object records are skipped, later records remain
  visible, and the session view reports bounded parse diagnostics
- Process-local discovery caching: unchanged session cards are reused by list, filter, and
  search requests; Claude card scans use bounded memory and Codex card scans read only a
  small metadata/headline window

### Grok-focused (rich session view)

Grok sessions get a deeper view of the on-disk session folder:

| Area | What you get |
|------|----------------|
| **Summary** | Title, model, agent name, reasoning effort, sandbox, cwd, message counts, branch/commit |
| **Token usage** | Estimated in / out / cached / reasoning from `updates.jsonl` `turn_completed` events; context window from `signals.json` |
| **Todos & settings** | `resources_state.json` — todo checklist, tool params, scheduler / completions |
| **Chat history** | Full `chat_history.jsonl`: user, assistant, **reasoning** (summary + `<encrypted>`), tool calls/results with **call ids** |
| **Terminal** | Matched `terminal/<call-id>.log` previews and enrichment of thin tool results |
| **Hunk records** | File edit events from `hunk_records.jsonl` |
| **Recap requests** | Index of `recap_requests/*.json` (id, time, trigger, model) |
| **Updates stream** | Second tab: aggregated `updates.jsonl` timeline (chunks collapsed, tool ids kept) |
| **Images** | `<image_files>` paths (clickable + preview via `/media`), inline `data:image/…;base64,…` previews with JSON snippet; click data-URL images to **copy to clipboard** |

### Codex-focused (rich rollout view)

Codex `rollout-*.jsonl` sessions under `~/.codex/sessions/` (and `archived_sessions/` if present) get a similar deep view:

| Area | What you get |
|------|----------------|
| **List titles** | Safe `thread_name` from `~/.codex/session_index.jsonl`, plus a safe first-user-message headline when available |
| **Summary** | Session id, model, originator/CLI, cwd, approval/sandbox, reasoning effort, personality, plan type, git branch/commit |
| **Token usage** | From `event_msg` / `token_count` — last cumulative `total_token_usage` (in / out / cached / reasoning) + context window |
| **Settings** | Approval policy, sandbox, effort, personality, provider, repo URL |
| **AGENTS.md** | Injected workspace instructions from `world_state` |
| **Chat history** | User + agent messages, **reasoning** (`<encrypted>` when present), tool calls/results with **call ids**, patches, image generation, task start/complete |
| **Patches** | File edits from `patch_apply_end` (paths + diff snippets) |
| **Events timeline** | Second tab: tasks, patches, image generation (not the full chat) |
| **Images** | User `local_images` / generated images; narrowly named clipboard captures under temp (`codex-clipboard-*`) can be previewed when linked from an authorized Codex session |

### Claude-focused (rich transcript view)

Claude Code `<session-uuid>.jsonl` transcripts under `~/.claude/projects/<encoded-cwd>/`:

| Area | What you get |
|------|----------------|
| **List titles** | Real titles from `ai-title` / `custom-title` / `last-prompt` records, plus a first-user-message headline; the working directory comes from the records, not the lossy encoded folder name |
| **Summary** | Session id, model, cwd, git branch, CLI version, permission mode, reasoning effort, message counts |
| **Token usage** | Summed `message.usage` — input, output, cache **reads** and **writes**, with per-model rows when a session mixes models (e.g. Opus and Sonnet) |
| **Settings** | Model, CLI version, permission mode, mode, effort, entrypoint, slug, cwd, git branch |
| **Todos** | `~/.claude/todos/<session-id>-agent-*.json` checklist |
| **Chat history** | User + assistant messages, **thinking** (`<encrypted>` when only a signature is stored), tool calls/results matched by **`toolu_` call id** |
| **System turns** | Slash commands and hook/informational `system` records, injected attachments (plan mode, permissions, task reminders), and `isMeta` `<system-reminder>` records shown as **system reminders** rather than as user messages |
| **Subagents** | Sidechain transcripts from `<session>/subagents/agent-*.jsonl` merged inline in timestamp order and tagged with the agent type; each is also viewable on its own |
| **File edits** | Hunk records from `Edit` / `Write` / `MultiEdit` / `NotebookEdit` `toolUseResult`, with added/removed counts and line ranges from `structuredPatch` |
| **Artifacts** | Skill and agent listings, plus the session's prompt history from `~/.claude/history.jsonl` |
| **Events timeline** | Second tab: hook summaries, slash commands, turn durations, queue operations, permission-mode changes, file-history snapshots, attachments |

Claude does not record a context-window size in the transcript, so the context bar stays
hidden; token totals are summed per message rather than read from a running total.

---

## Requirements

- Python **3.10+** (the oldest version covered by CI; `.python-version` selects the
  preferred development version)
- [uv](https://github.com/astral-sh/uv)

Markdown rendering uses package-vendored **marked** and **DOMPurify** assets and has no
runtime CDN dependency. Session data itself is never uploaded.

### Runtime dependencies

The Python runtime dependency is Flask. Browser-side Markdown rendering uses the pinned,
package-owned marked and DOMPurify distributions listed with their versions, licenses,
and upgrade procedure in `agent_session_viewer/static/vendor/README.md`. They are served
locally; viewing a session does not require a CDN or other network service.

---

## Installation

```bash
cd agent-session-viewer
uv sync
```

The installed application, including its templates and static assets, lives in the
`agent_session_viewer/` package. The root `app.py` and `main.py` files remain deprecated
source-checkout compatibility entrypoints for the v1 command forms.

### Package layout

```text
agent_session_viewer/
  __main__.py     # python -m agent_session_viewer
  cli.py          # installed console entrypoint
  app.py          # Flask routes and local server
  config.py       # Agent home-directory settings
  discovery.py    # Session discovery and list metadata
  images.py       # Image/content extraction and safe path handling
  session.py      # Shared load/export shape
  turns.py        # Canonical conversation turns
  types.py        # Shared session, turn, card, image, and diagnostic shapes
  util.py         # JSONL, token, time, and path helpers
  templates/      # package-owned Jinja templates
  static/         # package-owned CSS, JavaScript, icons, and vendored libraries
  agents/
    grok.py
    codex.py
    claude.py
```

The agent modules use explicit branches and imports—there is no plugin
registry or framework layer. Codex rollout records and Claude transcripts are
each decoded once per session request and reused for the conversation, summary,
tokens, events, and patches.

---

## Usage

```bash
uv run python app.py
# or
uv run python main.py
# or after uv sync:
uv run agent-session-viewer
# or:
uv run python -m agent_session_viewer
```

Open **http://127.0.0.1:5050**

The server binds to `127.0.0.1` only (local loopback).

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
uv run python app.py

# one-shot:
ASV_DEBUG=1 ASV_TIMING_DEBUG=1 uv run python app.py
```

**Windows PowerShell:**

```powershell
$env:ASV_DEBUG = "1"
$env:ASV_TIMING_DEBUG = "1"
$env:GROK_HOME = "~/.grok"
uv run python app.py

# one-shot:
$env:ASV_DEBUG = "1"; $env:ASV_TIMING_DEBUG = "1"; uv run python app.py
```

**Windows cmd.exe:**

```bat
set ASV_DEBUG=1
set ASV_TIMING_DEBUG=1
set GROK_HOME=~/.grok
uv run python app.py
```

---

## Session locations

| Agent | Default (Linux / macOS) | Default (Windows) | Override env var |
|-------|-------------------------|-------------------|------------------|
| Grok Build | `~/.grok/sessions/` | `%USERPROFILE%\.grok\sessions\` | `GROK_HOME` (product root) |
| Claude Code | `~/.claude/projects/` | `%USERPROFILE%\.claude\projects\` | `CLAUDE_HOME` |
| Codex CLI | `~/.codex/sessions/` (+ `archived_sessions`) | `%USERPROFILE%\.codex\sessions\` | `CODEX_HOME` |

On Windows, Grok session group folders are URL-encoded paths under `sessions\` (e.g. `C%3A%5CUsers%5C…`).

Claude also reads two sibling files under `CLAUDE_HOME` when they exist: `todos/<session-id>-agent-*.json`
for the todo checklist and `history.jsonl` for that session's prompt history. Neither is
served by a route; both are read locally and degrade to empty when missing or damaged.

---

## Routes

| Path | Purpose |
|------|---------|
| `/` | Session list, search, agent filters |
| `/view?agent=&path=` | Conversation plus available summary, artifacts, patches, and events |
| `/export?agent=&path=` | Download conversation as Markdown |
| `/raw?agent=&path=` | Download the raw file derived from an authorized session |
| `/media?agent=&session=&path=` | Serve passive image media associated with an authorized session |

Application links are generated by Flask, including query encoding. Search text therefore
round-trips through every agent filter unchanged, including Unicode and reserved URL
characters.

All filesystem routes validate the agent and the expected on-disk session layout. A path
is not authorized merely because it is somewhere below an agent home. Resolved paths that
escape through traversal or symlinks, cross-agent paths, home-level configuration files,
and active SVG media are denied. Grok raw downloads prefer `chat_history.jsonl` and fall
back to `summary.json`; Claude and Codex raw downloads are their authorized JSONL file.
Claude authorizes exactly two shapes: `<project>/<session>.jsonl` and a subagent transcript
at `<project>/<session>/subagents/agent-*.jsonl`. Anything else at that depth — a different
directory name, a different filename prefix, a non-`.jsonl` suffix, or a deeper path — is
denied.

### Security and supported threat model

The supported threat model is local, loopback-only use. The app has no authentication;
binding it to a non-loopback interface is unsupported unless an authentication boundary is
added. Session contents are untrusted, and remote media is not loaded automatically.
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
  temporary Grok, Claude, and Codex homes
- Agent-home path safety, traversal, prefix collisions, and symlink escapes
- Image path detection and explicit mixed-content image extraction
- Ensuring Markdown/HTML output hrefs are not treated as image attachments
- Language-aware fencing of unfenced code in rendered, copied, and exported Markdown
- Turn roles and message IDs from tiny Grok, Claude, and Codex JSONL fixtures
- Claude token accounting (cache reads/writes, per-model rows), file-edit hunks from
  `structuredPatch`, chat counts that exclude tool-transport records, inline subagent
  tagging, list-title precedence, and the subagent authorization shape
- Search/filter URL round-tripping for reserved characters and Unicode
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
```

Reasoning steps show the model **summary** text; full chain-of-thought is stored encrypted and displayed as `<encrypted>` (API `reasoningTokens` still count real reasoning usage).

---

## Notes

- Read-only with respect to agent data: no writes to `~/.grok`, `~/.claude`, or `~/.codex`.
- Claude subagent transcripts are merged into their parent session's chat inline and tagged; opening one directly is also supported.
- Token totals are **estimates** reconstructed from `turn_completed.usage` (not a separate billing ledger). Reasoning is usually included in output tokens; the UI shows both.
- Large tool outputs are collapsed until expanded.
- Image and raw file access is restricted to paths under configured agent homes.

---

## License

MIT
