"""Static recommended-settings tips per agent (from official docs / user guides)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tip:
    title: str
    body: str
    """Why it matters / what to do."""

    key: str = ""
    """Optional config key this tip maps to (for current-value display)."""

    permissive_values: tuple[str, ...] = ()
    """If current value matches one of these (case-insensitive), show a mild note."""

    note_if_permissive: str = (
        "This is quite open — fine for a trusted personal machine; tighten if you share "
        "the environment or work on untrusted repos."
    )

    note_if_unset: str = ""
    """Shown when the mapped key is missing (product default may still be active)."""

    placement: str = "recommended"
    """Where to render: ``recommended``, ``settings``, or ``both``."""


GROK_TIPS: tuple[Tip, ...] = (
    Tip(
        title="Pin a default model and reasoning effort",
        body=(
            "Set `[models] default` and `default_reasoning_effort` in `~/.grok/config.toml` "
            "so new sessions start consistent. Raise effort for hard refactors; lower it for "
            "quick edits to save latency and tokens."
        ),
        key="models.default",
    ),
    Tip(
        title="Use AGENTS.md for project conventions",
        body=(
            "Put durable coding conventions, build commands, and layout notes in `AGENTS.md` "
            "(or `CLAUDE.md`) at the repo root. Grok loads them automatically from home rules "
            "down through the working directory — keep them short and specific."
        ),
    ),
    Tip(
        title="Skills load from many locations (not only ~/.grok)",
        body=(
            "Grok discovers skills in priority order (higher wins on name clashes): "
            "(1) `./.grok/skills` and `./.grok/commands` (cwd), "
            "(2) project `.claude` / `.cursor` skill dirs when present, "
            "(3) repo-root `.grok/skills`, "
            "(4) user `~/.grok/skills` and `~/.grok/commands`, "
            "(5) Claude compat `~/.claude/skills` and `~/.claude/commands`, "
            "(6) Cursor compat `~/.cursor/skills`. "
            "It also scans `.agents/skills` (and `commands`) at each tier and walks "
            "directories between cwd and the repo root. Deduplicate is by skill name. "
            "Claude/Cursor scans default on; turn them off with "
            "`[compat.claude] skills = false` / `[compat.cursor] skills = false` "
            "(or `GROK_CLAUDE_SKILLS_ENABLED` / `GROK_CURSOR_SKILLS_ENABLED`). "
            "Add extra roots with `[skills] paths`, hide with `ignore`, or soft-disable "
            "names with `disabled`. Discovery ignores `.gitignore` for these roots."
        ),
        placement="both",
    ),
    Tip(
        title="Skills for repeatable workflows",
        body=(
            "Put personal packages under `~/.grok/skills/<name>/SKILL.md` with a clear "
            "`description` (and optional `when-to-use`). Grok activates a skill when the "
            "description matches the task. Shared team skills belong in the repo "
            "(`.grok/skills` or `.agents/skills`); Claude/Cursor skill trees are also "
            "loaded by default — see the multi-location tip."
        ),
    ),
    Tip(
        title="Choose permission mode deliberately",
        body=(
            "`[ui] permission_mode` and `yolo` control how much the agent can do without "
            "prompts. Prefer explicit approvals for untrusted trees; use always-approve only "
            "on machines and repos you fully trust."
        ),
        key="ui.permission_mode",
        permissive_values=("always-approve", "always_approve", "yolo"),
    ),
    Tip(
        title="Hooks for must-run checks",
        body=(
            "Put deterministic automation in `~/.grok/hooks/*.json` (format, tests, "
            "notifications). Use hooks when the model must not skip a step; use AGENTS.md "
            "when guidance is enough. Project hooks need folder trust."
        ),
    ),
    Tip(
        title="MCP servers sparingly",
        body=(
            "Declare MCP under `[mcp_servers.<name>]` only when you need the tools. Disable "
            "unused servers (`enabled = false`) to cut startup noise and attack surface."
        ),
    ),
)

_CLAUDE_COAUTHOR_TIP = Tip(
    title="Turn off Co-Authored-By on git commits",
    body=(
        "By default Claude Code adds itself as a git Co-Author on every commit it creates "
        "(a `Co-Authored-By: Claude ...` trailer in the commit message). That stays on "
        "until you opt out. Set `includeCoAuthoredBy` to `false` in "
        "`~/.claude/settings.json`, or change it via `/config` in a session. "
        "Related: the `attribution` object controls commit/PR attribution text "
        "(`attribution.commit`, `attribution.pr`) when you want custom wording instead "
        "of only toggling co-author trailers."
    ),
    key="includeCoAuthoredBy",
    permissive_values=("true", "1", "yes"),
    note_if_permissive=(
        "Co-Authored-By is currently ON — Claude will keep attaching itself to commits "
        "it creates until you set includeCoAuthoredBy to false."
    ),
    note_if_unset=(
        "This key is unset, so Claude’s default applies: Co-Authored-By is ON. "
        "Add \"includeCoAuthoredBy\": false to ~/.claude/settings.json to disable it."
    ),
    placement="both",
)

CLAUDE_TIPS: tuple[Tip, ...] = (
    _CLAUDE_COAUTHOR_TIP,
    Tip(
        title="Know your permission default",
        body=(
            "`permissions.defaultMode` in `~/.claude/settings.json` sets how often Claude "
            "asks before tools run (`dontAsk`, `acceptEdits`, etc.). Pick intentionally — "
            "it applies to all projects unless overridden."
        ),
        key="permissions.defaultMode",
        permissive_values=("dontask", "bypasspermissions", "dangerously-skip-permissions"),
    ),
    Tip(
        title="CLAUDE.md for memory, hooks for guarantees",
        body=(
            "Put conventions and project facts in `CLAUDE.md`. Use hooks in settings for "
            "actions that must always run (lint on edit, block dangerous bash). Hooks are "
            "deterministic; CLAUDE.md is advisory context."
        ),
    ),
    Tip(
        title="Skills as reusable /commands",
        body=(
            "User skills live in `~/.claude/skills/<name>/SKILL.md`. Write a specific "
            "`description` so Claude knows when to load them. Prefer skills over pasting "
            "long procedures into chat."
        ),
    ),
    Tip(
        title="Share team defaults via project settings",
        body=(
            "Commit `.claude/settings.json` for shared permissions/hooks; keep personal "
            "overrides in `settings.local.json` (gitignored). User `~/.claude/settings.json` "
            "is the personal baseline."
        ),
    ),
    Tip(
        title="Plugins for packaged capability",
        body=(
            "Installed plugins (see `plugins/installed_plugins.json`) add skills and hooks. "
            "Review enabled plugins periodically and remove ones you no longer use."
        ),
    ),
)

CODEX_TIPS: tuple[Tip, ...] = (
    Tip(
        title="Approval policy vs sandbox",
        body=(
            "`approval_policy` and sandbox settings in `~/.codex/config.toml` trade speed for "
            "safety. Prefer prompting on untrusted work; use elevated sandbox only when you "
            "understand the blast radius (especially on Windows)."
        ),
        key="approval_policy",
        permissive_values=("never", "on-failure"),
    ),
    Tip(
        title="AGENTS.md at the repo root",
        body=(
            "Codex loads `AGENTS.md` / `AGENTS.override.md` from the user home and from git "
            "root down to the cwd. Keep agent instructions in AGENTS.md so every session "
            "starts with the same project contract."
        ),
    ),
    Tip(
        title="Disable skills without deleting them",
        body=(
            "Use `[[skills.config]]` entries with `enabled = false` to turn off a skill path "
            "while keeping the files. Restart Codex after changes."
        ),
    ),
    Tip(
        title="Trust before project config",
        body=(
            "Project-scoped `.codex/config.toml` and project docs load only when the project "
            "is trusted. Do not blindly trust unknown repos."
        ),
    ),
    Tip(
        title="Reasoning effort matches the task",
        body=(
            "`model_reasoning_effort` (e.g. low/medium/high) should match the job: low for "
            "routine edits, higher for multi-file design. Pair with an explicit `model`."
        ),
        key="model_reasoning_effort",
    ),
)

CURSOR_TIPS: tuple[Tip, ...] = (
    Tip(
        title="Hooks for deterministic automation",
        body=(
            "`~/.cursor/hooks.json` runs scripts at lifecycle points (before shell, stop, "
            "etc.). Use hooks when something must always run; the model cannot skip them."
        ),
    ),
    Tip(
        title="Rules vs skills vs AGENTS.md",
        body=(
            "Rules (`.cursor/rules`) are auto-attached style/context hints. Skills "
            "(`skills/*/SKILL.md`) are multi-step workflows. `AGENTS.md` is always-on "
            "project guidance. Prefer the lightest layer that works."
        ),
    ),
    Tip(
        title="Prefer project hooks for team automation",
        body=(
            "Repo-local `.cursor/hooks.json` travels with the project; user hooks apply "
            "everywhere. Keep secrets out of hook command lines — they show up in inventories "
            "and process lists."
        ),
    ),
    Tip(
        title="Built-in skills-cursor vs user skills",
        body=(
            "`skills-cursor/` ships with Cursor (create-rule, create-skill, …). Put personal "
            "workflows in `~/.cursor/skills/` so upgrades do not overwrite them."
        ),
    ),
)

TIPS_BY_AGENT: dict[str, tuple[Tip, ...]] = {
    "grok": GROK_TIPS,
    "claude": CLAUDE_TIPS,
    "codex": CODEX_TIPS,
    "cursor": CURSOR_TIPS,
}


def tips_for(agent_id: str, *, placement: str | None = None) -> tuple[Tip, ...]:
    """Return tips for an agent.

    When ``placement`` is set (``recommended`` or ``settings``), only tips that
    include that placement are returned (``both`` matches either).
    """
    tips = TIPS_BY_AGENT.get(agent_id, ())
    if placement is None:
        return tips
    return tuple(
        t
        for t in tips
        if t.placement == placement or t.placement == "both"
    )
