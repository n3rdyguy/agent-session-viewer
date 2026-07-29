# Agent Session Viewer

Local web UI for browsing, searching, and exporting coding-agent conversations.

Supports:

- **Grok Build** (`~/.grok/sessions`)
- **Codex CLI** (`~/.codex/sessions`)
- **Claude Code** (`~/.claude/projects`) — *work in progress*

Everything stays on your machine. The app only reads session files; it does not send data anywhere.

---

## Claude support (WIP)

Claude Code discovery and basic viewing exist, but the experience is **not** at Grok/Codex parity yet. Treat Claude as incomplete until the items below land.

### TODO

- [ ] Richer session summary (model, cwd, branch, message counts, etc.)
- [ ] Token / usage summary when available on disk
- [ ] Tool call / result ids and terminal enrichment comparable to Grok
- [ ] Artifacts / secondary tabs (settings, patches, timeline) where Claude stores them
- [ ] Image / media path handling parity
- [ ] Validate list titles, search fields, and Markdown export against real `~/.claude/projects` layouts

---

## Features

### All agents

- Unified session list across Grok, Claude, and Codex
- Filter by agent (All / Grok / Claude / Codex) — **Claude filter is WIP**
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
- Path safety: only files under known agent home directories are served

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
| **Images** | User `local_images` / generated images; clipboard captures under temp (`codex-clipboard-*`) allowed for `/media` |

---

## Requirements

- Python **3.14+** (see `.python-version` / `pyproject.toml`)
- [uv](https://github.com/astral-sh/uv)

Markdown rendering loads **marked** and **DOMPurify** from a CDN when you open a session (needs network once for those scripts). Session data itself is never uploaded.

---

## Installation

```bash
cd agent-session-viewer
uv sync
```

The implementation lives in the flat `agent_session_viewer/` package. The
root `app.py` remains as a compatibility entrypoint.

### Package layout

```text
agent_session_viewer/
  app.py          # Flask routes and local server
  config.py       # Agent home-directory settings
  discovery.py    # Session discovery and list metadata
  images.py       # Image/content extraction and safe path handling
  session.py      # Shared load/export shape
  turns.py        # Canonical conversation turns
  util.py         # JSONL, token, time, and path helpers
  agents/
    grok.py
    codex.py
    claude.py
```

The agent modules use explicit branches and imports—there is no plugin
registry or framework layer. Codex rollout records are decoded once per
session request and reused for the conversation, summary, tokens, events,
and patches.

---

## Usage

```bash
uv run python app.py
# or
uv run python main.py
# or after uv sync:
uv run agent-session-viewer
```

Open **http://127.0.0.1:5050**

The server binds to `127.0.0.1` only (local loopback).

### Environment variables

See [`.env.example`](.env.example). Copy to `.env` if useful (gitignored); the app does **not** load `.env` automatically — export vars in your shell or IDE. `~` in path overrides is expanded (home on all platforms).

| Variable | Purpose | Default |
|----------|---------|---------|
| `ASV_DEBUG` | Flask debug / reloader (`1` / `true` / `yes` / `on`) | off |
| `GROK_HOME` | Grok product root (sessions under `…/sessions`) | `~/.grok` |
| `CLAUDE_HOME` | Claude Code root (projects under `…/projects`) | `~/.claude` |
| `CODEX_HOME` | Codex root (sessions under `…/sessions`) | `~/.codex` |

**Linux / macOS** (bash/zsh):

```bash
export ASV_DEBUG=1
export GROK_HOME=~/.grok
uv run python app.py

# one-shot:
ASV_DEBUG=1 uv run python app.py
```

**Windows PowerShell:**

```powershell
$env:ASV_DEBUG = "1"
$env:GROK_HOME = "~/.grok"
uv run python app.py

# one-shot:
$env:ASV_DEBUG = "1"; uv run python app.py
```

**Windows cmd.exe:**

```bat
set ASV_DEBUG=1
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

---

## Routes

| Path | Purpose |
|------|---------|
| `/` | Session list, search, agent filters |
| `/view?agent=&path=` | Conversation plus available summary, artifacts, patches, and events |
| `/export?agent=&path=` | Download conversation as Markdown |
| `/raw?path=` | Download raw `chat_history.jsonl` or `summary.json` |
| `/media?path=` | Serve a local image under an allowed agent home |

---

## Tests

Run the minimal parser suite with:

```bash
uv run pytest
```

The fixture-based tests cover:

- Agent-home path safety, traversal, prefix collisions, and symlink escapes
- Image path detection and explicit mixed-content image extraction
- Ensuring Markdown/HTML output hrefs are not treated as image attachments
- Language-aware fencing of unfenced code in rendered, copied, and exported Markdown
- Turn roles and message IDs from tiny Grok and Codex JSONL fixtures

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
- **Claude Code support is still work in progress** (see [Claude support (WIP)](#claude-support-wip)); Grok and Codex have the richer session views today.
- Token totals are **estimates** reconstructed from `turn_completed.usage` (not a separate billing ledger). Reasoning is usually included in output tokens; the UI shows both.
- Large tool outputs are collapsed until expanded.
- Image and raw file access is restricted to paths under configured agent homes.

---

## License

MIT
