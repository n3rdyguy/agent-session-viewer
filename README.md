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
  - Agent tags like `<user_info>` / `<system-reminder>` stay visible; headings, lists, and bold still render
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
| **List titles** | From `~/.codex/session_index.jsonl` (`thread_name`) when available |
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

Or from scratch:

```bash
uv init
uv add flask
# keep app.py in the project root
```

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

Flask debug mode is **off** by default. Enable with `ASV_DEBUG=1` (or `true` / `yes` / `on`).

---

## Session locations

| Agent | Default path | Override env var |
|-------|----------------|------------------|
| Grok Build | `~/.grok/sessions/` | `GROK_HOME` |
| Claude Code | `~/.claude/projects/` | `CLAUDE_CONFIG_DIR` |
| Codex CLI | `~/.codex/sessions/` (+ `archived_sessions`) | `CODEX_HOME` |

On Windows, Grok session group folders are URL-encoded paths under `sessions\` (e.g. `C%3A%5CUsers%5C…`).

---

## Routes

| Path | Purpose |
|------|---------|
| `/` | Session list, search, agent filters |
| `/view?agent=&path=` | Conversation + (for Grok) summary / artifacts / updates |
| `/export?agent=&path=` | Download conversation as Markdown |
| `/raw?path=` | Download raw `chat_history.jsonl` or `summary.json` |
| `/media?path=` | Serve a local image under an allowed agent home |

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
