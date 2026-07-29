```markdown
# Agent Session Viewer

A clean local web UI for browsing and exporting coding agent conversations.

Supports:
- **Grok Build**
- **Claude Code**
- **Codex CLI**

---

## Features

- Unified list of all sessions across the three agents
- Filter by agent (All / Grok / Claude / Codex)
- Full-text search (title, ID, path, model, cwd)
- Clean chat-bubble conversation view
- Collapsible long tool outputs
- One-click **Markdown export**
- Raw file download
- Completely offline & local — never leaves your machine

---

## Requirements

- Python 3.10+
- [uv](https://github.com/astral-sh/uv)

---

## Installation

```bash
# Create project
mkdir agent-session-viewer && cd agent-session-viewer

# Initialize
uv init

# Add dependency
uv add flask

# Place the `app.py` file in this directory
```

---

## Usage

```bash
uv run python app.py
```

Then open:

**http://127.0.0.1:5050**

---

## Session Locations

| Agent        | Default Path                          | Override Env Var     |
|--------------|---------------------------------------|----------------------|
| Grok Build   | `~/.grok/sessions/`                   | `GROK_HOME`          |
| Claude Code  | `~/.claude/projects/`                 | `CLAUDE_CONFIG_DIR`  |
| Codex CLI    | `~/.codex/sessions/`                  | `CODEX_HOME`         |

---

## Screenshots

> List view with search + filters  
> Conversation view with bubbles + Markdown export button

---

## Notes

- The app only reads files under the known agent directories (path safety checks are in place).
- Large tool outputs are collapsed by default for readability.
- Markdown export produces a clean, readable `.md` file of the entire conversation.

---

## License

MIT
```