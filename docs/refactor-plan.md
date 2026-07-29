# Refactor plan (KISS)

Keep it simple. Ship small steps. Prefer boring structure over clever abstractions.

**Goal:** Make the codebase easier to change without breaking Grok/Codex/Claude viewing.

**Non-goals (for now):** plugins, DB, async, framework swaps, over-engineered agent APIs.

---

## Current state (one sentence)

`app.py` is a ~4k-line Flask app: helpers + three agents + HTML/CSS/JS + routes.

---

## Principles

1. **One change per phase** — merge when it still runs and looks right.
2. **No behavior change unless noted** — refactors first, features later.
3. **Delete more than you invent** — shared helpers only when copy-paste hurts twice.
4. **Claude stays WIP** — don’t block structure work on Claude parity.
5. **Local tool** — path safety and loopback binding stay as-is.

---

## Phase 0 — Housekeeping (tiny) ✅

- [x] Wire `main.py` → `app.run` (script: `agent-session-viewer`).
- [x] Real `description` in `pyproject.toml`.
- [x] Debug only when `ASV_DEBUG=1` (default off).

**Done when:** README and entrypoint agree; no surprise “Hello from…” script.

---

## Phase 1 — Templates & static out of `app.py` ✅

Move big strings only. Keep all Python logic in place.

```text
templates/
  base.html
  list.html
  view.html
  partials/bubbles.html   # macros
static/
  app.css
  app.js
```

- [x] Switch `render_template_string` → `render_template`.
- [x] One CSS file, one JS file (fold / copy / markdown / tabs).

**Done when:** UI identical; `app.py` is mostly Python.

---

## Phase 2 — Thin routes ✅

One place to load a session for `/view` and `/export`:

```python
def load_session(agent: str, path: Path) -> dict:
    # turns, title, summary, resources, artifacts,
    # hunks, terminal_logs, recaps, updates
```

- [x] Always build timeline/events with `make_turn` (drop the “fix up missing html” loop).
- [x] Shared `summary_to_markdown(...)` for export headers (Grok/Codex).

**Done when:** routes are short; no agent `if/elif` trees duplicated across view + export.

---

## Phase 3 — Stop double work (Codex) ✅

Today Codex walks the rollout twice (conversation + scan).

- One pass returns conversation **and** summary/tokens/events/patches.
- Or: scan once per request and reuse.

**Done when:** opening a Codex session reads the jsonl once.

---

## Phase 4 — Small shared utilities (only if still messy)

- [x] `iter_jsonl(path)` for all agents.
- [x] `time` = real timestamp only; `id` always in msgid (keep template guards as belt-and-suspenders).
- [x] Same empty/finalize helpers for Grok + Codex token fields.

**Done when:** fewer near-duplicate loops; no new framework.

---

## Phase 5 — Light package split (optional)

Only after phases 1–3 feel good. Keep packages flat:

```text
# still runnable as today, or:
agent_session_viewer/
  app.py          # routes
  util.py
  images.py
  turns.py
  agents/
    grok.py
    codex.py
    claude.py
```

- Do **not** introduce registries, base classes, or plugin loaders unless a third agent forces it.
- Claude implements the same `load_session` shape with empty extras.

**Done when:** files are under ~500–800 lines each; imports stay obvious.

---

## Phase 6 — Minimal tests (fixtures, not mocks)

High value, small surface:

1. `path_allowed` / image path edge cases  
2. `extract_text_and_images` on a few hand-written snippets  
3. One tiny Grok jsonl + one Codex rollout fragment → turn roles/ids  

No full browser suite required.

**Done when:** `uv run pytest` covers the pure parsers you fear breaking.

---

## Phase 7 — Claude parity (feature work, separate track)

Follow README TODO. Implement behind the same `load_session` interface.

Not part of the structure refactor; do after the app is easy to extend.

---

## Explicitly skip

| Idea | Why skip |
|------|----------|
| Agent plugin system | Three agents; `if agent ==` is fine |
| Background workers / cache service | Local UI; optimize when list is slow |
| Rewrite in another stack | Cost >> benefit |
| Perfect TypedDict everywhere | Optional later on list/session dicts only |

---

## Suggested order

```text
0 housekeep → 1 templates/static → 2 load_session → 3 codex single-pass
     → 4 small utils (if needed) → 5 split packages (optional) → 6 tests → 7 Claude
```

Stop after any phase if the app is “good enough.” KISS means **you don’t have to finish the list**.

---

## Checklist (copy into PRs)

- [x] Phase 0 — entrypoint / metadata  
- [x] Phase 1 — templates + static  
- [x] Phase 2 — `load_session` + thin routes  
- [x] Phase 3 — Codex one pass  
- [x] Phase 4 — shared jsonl / time / tokens
- [ ] Phase 5 — package split (optional)  
- [ ] Phase 6 — fixture tests  
- [ ] Phase 7 — Claude WIP items (README)
