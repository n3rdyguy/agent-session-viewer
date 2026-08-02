"""Documented settings catalogs for each agent (from official docs / user guides).

Each entry is a known configuration key the product documents. The inventory page
merges these with the user's current flattened config so you can see what exists
in the docs versus what is set on this machine.

Sources (curated, not exhaustive for every experimental key):
- Grok: ~/.grok/docs/user-guide/05-configuration.md
- Claude: https://code.claude.com/docs/en/settings
- Codex: https://learn.chatgpt.com/docs/config-file/config-reference
- Cursor: https://cursor.com/docs/cli/reference/configuration (+ hooks/mcp inventory)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogEntry:
    key: str
    description: str
    default: str = ""
    """Documented default when known; empty if the docs do not state one clearly."""


@dataclass(frozen=True)
class CatalogRow:
    key: str
    description: str
    default: str
    current: str
    """Current value from scanned config, or empty when not set."""

    status: str
    """``set`` if present in config, ``unset`` if only catalog/default applies."""


def _e(key: str, description: str, default: str = "") -> CatalogEntry:
    return CatalogEntry(key=key, description=description, default=default)


# ── Grok (config.toml) ──────────────────────────────────────────────

GROK: tuple[CatalogEntry, ...] = (
    _e("cli.auto_update", "Check for updates on launch", "true"),
    _e("cli.installer", "How the CLI was installed (informational)"),
    _e("models.default", "Model for new sessions", "grok-4.5"),
    _e("models.web_search", "Model used by the web_search tool", "grok-4.5"),
    _e("models.default_reasoning_effort", "Default reasoning effort for new sessions"),
    _e("temperature", "Default sampling temperature for models", "0.7"),
    _e("top_p", "Default nucleus sampling", "0.95"),
    _e("max_completion_tokens", "Default max tokens per response", "8192"),
    _e("max_retries", "API retry count", "8"),
    _e("inference_idle_timeout_secs", "Idle timeout for inference streams", "600"),
    _e("stream_tool_calls", "Stream tool call arguments", "true"),
    _e("ui.simple_mode", "Readline-style prompt editing (false = vim in prompt)", "true"),
    _e("ui.vim_mode", "Vim-style scrollback navigation", "false"),
    _e("ui.max_thoughts_width", "Max column width for reasoning display", "120"),
    _e(
        "ui.default_selected_permission",
        "Preselected row on first approval prompt",
        "always_allow_all_sessions",
    ),
    _e("ui.remember_tool_approvals", "Show per-command Always allow options", "false"),
    _e("ui.show_thinking_blocks", "Show agent thinking blocks in the TUI", "true"),
    _e("ui.group_tool_verbs", "Fold runs of read/search/list tool calls", "true"),
    _e("ui.collapsed_edit_blocks", "Show edits as +N/-M summaries", "false"),
    _e("ui.page_flip_on_send", "Snap prompt to top of viewport on send", "true"),
    _e("ui.screen_mode", "Default render mode: fullscreen | minimal"),
    _e("ui.permission_mode", "Permission / approval mode (e.g. always-approve)"),
    _e("ui.yolo", "Skip approvals when true (dangerous)", "false"),
    _e("ui.compact_mode", "Compact TUI layout", "false"),
    _e("ui.show_timeline", "Show timeline in TUI", "true"),
    _e("ui.hunk_tracker_mode", "Hunk tracker display mode"),
    _e("ui.theme", "TUI theme name"),
    _e("ui.scroll_speed", "Wheel/trackpad scroll speed 1–100", "50"),
    _e("ui.scroll_mode", "auto | wheel | trackpad", "auto"),
    _e("ui.scroll_lines", "Lines per scroll tick (unset = terminal default)"),
    _e("ui.invert_scroll", "Natural scrolling", "false"),
    _e("ui.notifications.method", "Notification protocol", "auto"),
    _e("ui.notifications.condition", "When to notify", "unfocused"),
    _e("ui.notifications.idle_threshold_secs", "Seconds unfocused before notify", "3"),
    _e("ui.notifications.sleep_prevention", "Prevent display sleep during turns", "true"),
    _e("ui.notifications.progress_bar", "Tab progress bar (OSC 9;4)", "true"),
    _e("features.telemetry", "Anonymous usage telemetry", "false"),
    _e("features.feedback", "Feedback system", "true"),
    _e("features.lsp_tools", "Expose the lsp tool", "false"),
    _e("features.codebase_indexing", "Code graph indexing", "true"),
    _e("features.two_pass_compaction", "Prefire two-pass compaction", "false"),
    _e("features.remote_fetch", "Allow online model-catalog fetches", "true"),
    _e("session.auto_compact_threshold_percent", "Auto-compact at % of context", "85"),
    _e("session.load_envrc", "Load .envrc environment variables", "true"),
    _e("tools.respect_gitignore", "Tools skip gitignored files", "false"),
    _e("toolset.bash.timeout_secs", "Foreground bash timeout seconds", "120"),
    _e("toolset.bash.output_byte_limit", "Max captured bash output bytes", "20000"),
    _e("toolset.ask_user_question.timeout_enabled", "Timeout ask-user questions", "true"),
    _e("toolset.ask_user_question.timeout_secs", "Ask-user timeout seconds", "1800"),
    _e("toolset.web_fetch.allow_local", "Allow web_fetch to loopback only", "false"),
    _e("memory.enabled", "Persist knowledge across sessions", "false"),
    _e("memory.session.save_on_end", "Write memory summary on session end", "true"),
    _e("subagents.enabled", "Enable subagents", "true"),
    _e("workflows.enabled", "Background workflows / workflow tool", "true"),
    _e(
        "skills.paths",
        "Extra skill directories or SKILL.md files to scan (in addition to built-in locations)",
    ),
    _e("skills.ignore", "Skill paths to hide entirely from discovery"),
    _e("skills.disabled", "Skill names kept listed but inactive"),
    _e(
        "compat.cursor.skills",
        "Scan ~/.cursor/skills and project .cursor/skills (Grok skill discovery)",
        "true",
    ),
    _e("compat.cursor.rules", "Scan Cursor rules dirs", "true"),
    _e("compat.cursor.hooks", "Scan Cursor hooks", "true"),
    _e("compat.cursor.mcps", "Scan Cursor MCP config", "true"),
    _e(
        "compat.claude.skills",
        "Scan ~/.claude/skills (+ commands) and project .claude skill dirs",
        "true",
    ),
    _e("compat.claude.rules", "Scan Claude rules dirs", "true"),
    _e("compat.claude.hooks", "Scan Claude settings hooks", "true"),
    _e("compat.claude.mcps", "Scan Claude MCP config", "true"),
    _e("plugins.paths", "Extra plugin directories"),
    _e("plugins.disabled", "Disabled plugin ids"),
    _e("marketplace.official_marketplace_auto_installed", "Official marketplace auto-install"),
    _e("hints.project_picker_disabled", "Skip project-directory picker", "false"),
    _e("hints.new_session_worktree_mode", "/new worktree prompt: ask|always|never", "never"),
    _e("hints.fork_worktree_mode", "/fork worktree prompt: ask|always|never", "ask"),
)

# ── Claude Code (settings.json) ─────────────────────────────────────

CLAUDE: tuple[CatalogEntry, ...] = (
    _e("model", "Default model for the main session"),
    _e("effortLevel", "Reasoning / effort level preference"),
    _e(
        "permissions.defaultMode",
        "Default permission mode: default, acceptEdits, plan, auto, dontAsk, bypassPermissions",
        "default",
    ),
    _e("permissions.allow", "Permission allow rules (array)"),
    _e("permissions.deny", "Permission deny rules (array)"),
    _e("permissions.ask", "Permission ask rules (array)"),
    _e("hooks", "Lifecycle hooks configuration object"),
    _e("alwaysThinkingEnabled", "Enable extended thinking by default"),
    _e("autoCompactEnabled", "Auto-compact near context limit", "true"),
    _e("autoMemoryEnabled", "Read/write auto memory directory", "true"),
    _e("autoMemoryDirectory", "Custom auto memory directory path"),
    _e("autoUpdatesChannel", "Release channel: latest | stable", "latest"),
    _e("autoScrollEnabled", "Auto-scroll to new output in fullscreen", "true"),
    _e("theme", "UI theme"),
    _e("verbose", "Verbose logging / output"),
    _e("tui", "Terminal UI preferences object"),
    _e("statusLine", "Custom status line configuration"),
    _e("enableArtifact", "Enable artifact feature"),
    _e("disableArtifact", "Disable artifact feature"),
    _e(
        "attribution",
        "Git commit / PR attribution strings (attribution.commit, attribution.pr)",
    ),
    _e(
        "includeCoAuthoredBy",
        "Add Claude as Co-Authored-By on git commits it creates (default on until set false)",
        "true",
    ),
    _e("skipDangerousModePermissionPrompt", "Skip bypass-permissions confirmation"),
    _e("agentPushNotifEnabled", "Push notifications when Claude decides", "false"),
    _e("inputNeededNotifEnabled", "Notify when input is needed"),
    _e("remoteControlAtStartup", "Connect remote control at startup"),
    _e("disableRemoteControl", "Disable remote control"),
    _e("enabledPlugins", "Map of enabled plugins"),
    _e("env", "Environment variables injected into sessions"),
    _e("companyAnnouncements", "Startup announcement messages"),
    _e("advisorModel", "Model for the advisor tool (opus/sonnet)"),
    _e("agent", "Run main thread as a named subagent"),
    _e("fallbackModel", "Fallback model if primary fails"),
    _e("availableModels", "Restrict selectable models"),
    _e("outputStyle", "Output style / system prompt flavor"),
    _e("language", "UI / response language preference"),
    _e("editorMode", "Editor integration mode"),
    _e("diffTool", "External diff tool"),
    _e("plansDirectory", "Directory for plan files"),
    _e("respectGitignore", "Respect .gitignore in tools"),
    _e("fileCheckpointingEnabled", "File checkpointing"),
    _e("showThinkingSummaries", "Show thinking summaries"),
    _e("showTurnDuration", "Show turn duration in UI"),
    _e("spinnerTipsEnabled", "Spinner tips", "true"),
    _e("prefersReducedMotion", "Reduce UI motion"),
    _e("terminalProgressBarEnabled", "Terminal progress bar"),
    _e("voiceEnabled", "Voice features"),
    _e("fastMode", "Fast mode preference"),
    _e("worktree", "Git worktree behavior object"),
    _e("disableAllHooks", "Disable all hooks"),
    _e("disableBypassPermissionsMode", "Block bypass-permissions mode"),
    _e("disableAutoMode", "Disable auto permission mode"),
    _e("disableBundledSkills", "Disable bundled skills"),
    _e("disableSkillShellExecution", "Block skill shell scripts"),
    _e("disableWorkflows", "Disable workflows"),
    _e("teammateMode", "Agent team teammate behavior"),
    _e("teammateDefaultModel", "Default model for teammates"),
    _e("askUserQuestionTimeout", "Auto-continue unanswered questions", "never"),
    _e("cleanupPeriodDays", "Session cleanup retention days"),
    _e("preferredNotifChannel", "Preferred notification channel"),
    _e("allowedMcpServers", "Managed allowlist of MCP servers"),
    _e("deniedMcpServers", "Denylist of MCP servers"),
    _e("enableAllProjectMcpServers", "Enable all project MCP servers"),
    _e("strictKnownMarketplaces", "Restrict plugin marketplaces"),
)

# ── Codex (config.toml) ─────────────────────────────────────────────

CODEX: tuple[CatalogEntry, ...] = (
    _e("model", "Default model id for sessions"),
    _e("model_reasoning_effort", "Reasoning effort: low | medium | high | …"),
    _e(
        "approval_policy",
        "When to pause for approval: untrusted | on-request | never | granular",
    ),
    _e("approvals_reviewer", "Who reviews approvals: user | auto_review", "user"),
    _e("network_access", "Allow network from the agent environment"),
    _e("sandbox_mode", "Sandbox mode (prefer permissions profiles when set)"),
    _e("default_permissions", "Default permissions profile name"),
    _e("allow_login_shell", "Allow login-shell semantics for shell tools", "true"),
    _e("notify", "Notification command argv array"),
    _e("instructions", "Extra developer instructions injected into sessions"),
    _e("developer_instructions", "Additional developer instructions"),
    _e("hide_agent_reasoning", "Hide reasoning from the UI"),
    _e("check_for_update_on_startup", "Check for Codex updates on startup", "true"),
    _e("cli_auth_credentials_store", "Where CLI stores credentials: file|keyring|auto"),
    _e("file_opener", "Preferred file opener"),
    _e("disable_paste_burst", "Disable burst-paste detection in TUI", "false"),
    _e("project_doc_max_bytes", "Max bytes of project docs (AGENTS.md) to load"),
    _e("project_doc_fallback_filenames", "Fallback project doc filenames"),
    _e("history.persistence", "History persistence mode"),
    _e("history.max_bytes", "Max history storage bytes"),
    _e("agents.enabled", "Enable multi-agent tools", "true"),
    _e("agents.default_subagent_model", "Default model for spawned agents"),
    _e("agents.default_subagent_reasoning_effort", "Default effort for spawned agents"),
    _e("agents.max_concurrent_threads_per_session", "Max concurrent subagent threads"),
    _e("features.js_repl", "JS REPL feature flag"),
    _e("features.web_search", "Web search feature"),
    _e("features.multi_agent", "Multi-agent feature"),
    _e("features.memories", "Memories feature"),
    _e("features.hooks", "Hooks feature"),
    _e("features.unified_exec", "Unified exec tool"),
    _e("features.shell_tool", "Shell tool feature"),
    _e("features.fast_mode", "Fast mode feature"),
    _e("features.personality", "Personality feature"),
    _e("features.goals", "Goals feature"),
    _e("analytics.enabled", "Analytics for this machine/profile"),
    _e("feedback.enabled", "Feedback system"),
    _e("windows.sandbox", "Windows sandbox mode (e.g. elevated)"),
    _e("desktop.conversationDetailMode", "Desktop conversation detail mode"),
    _e("desktop.ambient-suggestions-enabled", "Desktop ambient suggestions"),
    _e("desktop.followUpQueueMode", "Desktop follow-up queue mode"),
    _e("tui", "TUI preferences table"),
    _e("mcp_servers", "MCP server definitions (table of servers)"),
    _e("plugins", "Plugin enable flags (table)"),
    _e("skills.config", "Per-skill enable/disable entries"),
    _e("marketplaces", "Configured skill/plugin marketplaces"),
    _e("shell_environment_policy.set", "Env vars forced into shell policy"),
    _e("auto_review.policy", "Local Markdown policy for automatic review"),
    _e("background_terminal_max_timeout", "Background terminal poll window ms", "300000"),
    _e("compact_prompt", "Inline compaction prompt override"),
    _e("chatgpt_base_url", "Base URL for ChatGPT login flow"),
    _e("log_dir", "Directory for logs"),
)

# ── Cursor (cli-config.json + common agent files) ────────────────────

CURSOR: tuple[CatalogEntry, ...] = (
    _e("version", "cli-config.json schema version", "1"),
    _e("editor.vimMode", "Vim keybindings in CLI", "false"),
    _e("permissions.allow", "CLI allowlist of permitted operations"),
    _e("permissions.deny", "CLI denylist of forbidden operations"),
    _e("channel", "Release channel for CLI updates"),
    _e("model", "Selected model configuration object"),
    _e("maxMode", "Persisted max mode in model picker"),
    _e("notifications", "Terminal notification when agent finishes/needs input"),
    _e("hints", "Show CLI hints while agent works"),
    _e("rewind", "Enable /rewind to restore earlier messages"),
    _e("suggestNextPrompt", "Suggest follow-up prompt at end of turn"),
    _e("display.showLineNumbers", "Line numbers in code blocks"),
    _e("display.showThinkingBlocks", "Render thinking blocks"),
    _e("display.showStatusIndicators", "Terminal title status indicators"),
    _e("display.showStatusLineRunningTime", "Elapsed time in status line"),
    _e("approvalMode", "allowlist | auto-review | unrestricted"),
    _e("sandbox.mode", "Sandbox mode override"),
    _e("sandbox.networkAccess", "Sandbox network access setting"),
    _e("network.useHttp1ForAgent", "HTTP/1.1 instead of HTTP/2 for agent", "false"),
    _e("attribution.attributeCommitsToAgent", "Made with Cursor commit trailer", "true"),
    _e("attribution.attributePRsToAgent", "Made with Cursor PR footer", "true"),
    # Files / features discovered in the home inventory (not always in cli-config)
    _e("hooks.json", "User-level agent hooks file present under ~/.cursor"),
    _e("mcp.json", "User-level MCP servers file present under ~/.cursor"),
    _e("skills/", "User skills directory (~/.cursor/skills)"),
    _e("skills-cursor/", "Built-in Cursor skills directory"),
)

CATALOG: dict[str, tuple[CatalogEntry, ...]] = {
    "grok": GROK,
    "claude": CLAUDE,
    "codex": CODEX,
    "cursor": CURSOR,
}


def build_catalog_rows(
    agent_id: str,
    current: dict[str, str],
    *,
    extra_presence: dict[str, bool] | None = None,
) -> list[CatalogRow]:
    """Merge documented keys with flattened current values.

    ``extra_presence`` marks pseudo-keys (paths like ``hooks.json``) as set when
    the corresponding file/directory exists, even if not inside settings content.
    """
    presence = extra_presence or {}
    entries = CATALOG.get(agent_id, ())
    rows: list[CatalogRow] = []
    for entry in entries:
        if entry.key in presence:
            is_set = bool(presence[entry.key])
            cur = "present" if is_set else ""
        else:
            cur = _lookup_current(current, entry.key)
            is_set = cur != ""
        rows.append(
            CatalogRow(
                key=entry.key,
                description=entry.description,
                default=entry.default,
                current=cur if is_set else "",
                status="set" if is_set else "unset",
            )
        )
    return rows


def _lookup_current(current: dict[str, str], key: str) -> str:
    if key in current:
        return current[key]
    # Case-insensitive and suffix match for dotted keys
    lower = key.lower()
    for ck, cv in current.items():
        if ck.lower() == lower:
            return cv
    # Nested: permissions maps to permissions.* presence summary
    if key in {"hooks", "mcp_servers", "plugins", "tui", "env", "attribution", "worktree", "model"}:
        # If any child key exists, mark parent as set with a short summary
        prefix = key + "."
        children = {k: v for k, v in current.items() if k == key or k.startswith(prefix)}
        if key in current:
            return current[key]
        if children:
            return f"({len(children)} nested key{'s' if len(children) != 1 else ''})"
    # permissions.allow as parent of array stored under that exact key
    prefix = key + "."
    for ck, cv in current.items():
        if ck.startswith(prefix) or ck.lower().endswith("." + key.lower()):
            # Prefer exact dotted match already handled; only for section tables
            pass
    return ""


def catalog_summary(rows: list[CatalogRow]) -> tuple[int, int]:
    """Return (set_count, total)."""
    set_count = sum(1 for r in rows if r.status == "set")
    return set_count, len(rows)
