"""Build a full home-directory inventory report per agent."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import config
from .recommendations import Tip, tips_for
from .redact import redact_command_string, redact_json_text, redact_text
from .settings_catalog import CatalogRow, build_catalog_rows, catalog_summary
from .skills import SkillInfo, discover_skills, read_text_capped
from .specs import HOME_SPECS, HomeSpec

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = None  # type: ignore[assignment]


@dataclass
class SettingRow:
    """One flattened key/value from a settings file (table view)."""

    key: str
    value: str


@dataclass
class TextFileInfo:
    path: str
    exists: bool
    content: str = ""
    truncated: bool = False
    kind: str = ""  # settings | instruction | rule | statusline | mcp | hooks | other
    rows: list[SettingRow] = field(default_factory=list)
    """Flattened key/value pairs for settings table UI (empty for non-settings)."""


@dataclass
class HookEntry:
    event: str
    command: str
    source: str
    timeout: int | None = None
    matcher: str = ""
    extra: str = ""


@dataclass
class McpServerInfo:
    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str = ""
    enabled: bool | None = None
    env_keys: list[str] = field(default_factory=list)
    source: str = ""


@dataclass
class PluginInfo:
    name: str
    version: str = ""
    enabled: bool | None = None
    path: str = ""
    source: str = ""


@dataclass
class DirInfo:
    path: str
    exists: bool
    child_count: int | None = None
    note: str = ""


@dataclass
class TipView:
    title: str
    body: str
    key: str = ""
    current_value: str = ""
    permissive_note: str = ""
    placement: str = "recommended"


@dataclass
class AgentHomeReport:
    id: str
    label: str
    badge: str
    home: str
    home_display: str
    home_attr: str
    exists: bool
    version: str = ""
    settings: list[TextFileInfo] = field(default_factory=list)
    instructions: list[TextFileInfo] = field(default_factory=list)
    skills: list[SkillInfo] = field(default_factory=list)
    hooks: list[HookEntry] = field(default_factory=list)
    disabled_hooks: str = ""
    plugins: list[PluginInfo] = field(default_factory=list)
    mcp_servers: list[McpServerInfo] = field(default_factory=list)
    rules: list[TextFileInfo] = field(default_factory=list)
    agents: list[TextFileInfo] = field(default_factory=list)
    commands: list[TextFileInfo] = field(default_factory=list)
    statuslines: list[TextFileInfo] = field(default_factory=list)
    notable_dirs: list[DirInfo] = field(default_factory=list)
    tips: list[TipView] = field(default_factory=list)
    """Recommended-settings tips (placement recommended / both)."""

    settings_tips: list[TipView] = field(default_factory=list)
    """Tips that appear under the Settings panel (placement settings / both)."""

    highlights: dict[str, str] = field(default_factory=dict)
    """Flattened interesting keys for tip comparison and summary."""

    catalog: list[CatalogRow] = field(default_factory=list)
    """Documented settings vs current values (all-settings table)."""

    catalog_set: int = 0
    catalog_total: int = 0


def inventory_all() -> list[AgentHomeReport]:
    return [inventory_one(spec) for spec in HOME_SPECS]


def inventory_one(spec: HomeSpec) -> AgentHomeReport:
    home = spec.home()
    report = AgentHomeReport(
        id=spec.id,
        label=spec.label,
        badge=spec.badge,
        home=str(home),
        home_display=spec.home_display(),
        home_attr=spec.home_attr,
        exists=home.is_dir(),
    )
    if not report.exists:
        report.tips = [
            _tip_view(t, {}) for t in tips_for(spec.id, placement="recommended")
        ]
        report.settings_tips = [
            _tip_view(t, {}) for t in tips_for(spec.id, placement="settings")
        ]
        report.catalog = build_catalog_rows(spec.id, {})
        report.catalog_set, report.catalog_total = catalog_summary(report.catalog)
        return report

    highlights: dict[str, str] = {}

    # Settings files
    for rel in spec.settings_files:
        info = _read_settings(home, rel)
        if info.exists or rel == spec.settings_files[0]:
            report.settings.append(info)
        if info.exists and info.content:
            highlights.update(_extract_highlights(spec.id, rel, info.content))

    # Version
    for rel in spec.version_files:
        raw, _ = _read_raw(home / rel)
        if raw:
            report.version = _parse_version(raw)
            break

    # Instructions
    for name in spec.instruction_names:
        path = home / name
        if path.is_file():
            report.instructions.append(_read_text_file(home, name, "instruction"))

    # Skills
    for root in spec.skill_roots:
        report.skills.extend(discover_skills(home, root.rel, root.source))
    # Grok also loads Claude/Cursor user skill trees (and can load more via
    # [skills] paths / project dirs). Global homes only on this inventory page.
    if spec.id == "grok":
        report.skills.extend(_grok_compat_skills(highlights))

    # Hooks
    report.hooks.extend(_load_hooks(spec, home, highlights))
    if spec.disabled_hooks_file:
        dh = home / spec.disabled_hooks_file
        if dh.is_file():
            text, _ = read_text_capped(dh)
            report.disabled_hooks = redact_text(text)

    # Plugin metadata files
    for rel in spec.plugin_meta_files:
        report.plugins.extend(_load_plugin_meta(home, rel))

    # MCP JSON files (Cursor)
    for rel in spec.mcp_json_files:
        report.mcp_servers.extend(_load_mcp_json(home, rel))

    # MCP / plugins from TOML settings (Grok, Codex)
    for info in report.settings:
        if info.path.endswith(".toml") and info.content:
            report.mcp_servers.extend(_mcp_from_toml_text(info.content, info.path))
            report.plugins.extend(_plugins_from_toml_text(info.content, info.path))
            _notify_as_hooks(info.content, info.path, report)

    # Rules / agents / commands
    for rel in spec.rules_dirs:
        report.rules.extend(_list_md_files(home, rel, "rule"))
    for rel in spec.agents_dirs:
        report.agents.extend(_list_md_files(home, rel, "agent"))
    for rel in spec.commands_dirs:
        report.commands.extend(_list_md_files(home, rel, "command"))

    # Statusline scripts
    for rel in spec.statusline_files:
        path = home / rel
        if path.is_file():
            report.statuslines.append(_read_text_file(home, rel, "statusline"))

    # Notable dirs (shallow)
    for rel in spec.notable_dirs:
        report.notable_dirs.append(_dir_info(home, rel))

    # Claude hooks/plugins also live inside settings.json
    for info in report.settings:
        if info.path.endswith(".json") and info.content:
            try:
                data = json.loads(info.content)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if isinstance(data, dict):
                if "hooks" in data and not any(h.source.endswith(info.path) for h in report.hooks):
                    report.hooks.extend(_hooks_from_claude_settings(data["hooks"], info.path))
                # enabledPlugins in Claude settings
                plugins = data.get("enabledPlugins")
                if isinstance(plugins, dict):
                    for name, enabled in plugins.items():
                        report.plugins.append(
                            PluginInfo(
                                name=str(name),
                                enabled=bool(enabled) if enabled is not None else None,
                                source=info.path,
                            )
                        )
                highlights.update(_flatten_json_highlights(data))

    # Deduplicate hooks / plugins / mcp by simple key
    report.hooks = _dedupe_hooks(report.hooks)
    report.plugins = _dedupe_plugins(report.plugins)
    report.mcp_servers = _dedupe_mcp(report.mcp_servers)

    report.highlights = highlights

    # Flatten all settings file rows into one lookup for the docs catalog.
    current_values: dict[str, str] = {}
    for info in report.settings:
        for row in info.rows:
            current_values[row.key] = row.value
    # Also keep highlight keys (covers some TOML paths rows may miss).
    for hk, hv in highlights.items():
        current_values.setdefault(hk, hv)

    # Tips after values are known so includeCoAuthoredBy etc. show current state.
    tip_values = {**highlights, **current_values}
    report.tips = [
        _tip_view(t, tip_values) for t in tips_for(spec.id, placement="recommended")
    ]
    report.settings_tips = [
        _tip_view(t, tip_values) for t in tips_for(spec.id, placement="settings")
    ]

    extra_presence: dict[str, bool] = {}
    if spec.id == "cursor":
        extra_presence["hooks.json"] = (home / "hooks.json").is_file()
        extra_presence["mcp.json"] = (home / "mcp.json").is_file()
        extra_presence["skills/"] = (home / "skills").is_dir()
        extra_presence["skills-cursor/"] = (home / "skills-cursor").is_dir()
    # MCP / plugins presence for table agents that store them as tables
    if report.mcp_servers:
        current_values.setdefault(
            "mcp_servers",
            f"({len(report.mcp_servers)} server{'s' if len(report.mcp_servers) != 1 else ''})",
        )
    if report.plugins:
        current_values.setdefault(
            "plugins",
            f"({len(report.plugins)} plugin{'s' if len(report.plugins) != 1 else ''})",
        )
    if report.hooks and spec.id == "claude":
        current_values.setdefault(
            "hooks",
            f"({len(report.hooks)} hook event{'s' if len(report.hooks) != 1 else ''})",
        )

    report.catalog = build_catalog_rows(spec.id, current_values, extra_presence=extra_presence)
    report.catalog_set, report.catalog_total = catalog_summary(report.catalog)
    return report


def _grok_compat_skills(highlights: dict[str, str]) -> list[SkillInfo]:
    """Skills Grok may load from Claude/Cursor homes (user scope).

    Respects ``compat.claude.skills`` / ``compat.cursor.skills`` when present in
    the scanned Grok config (default: enabled). Project-local skill trees are
    not scanned here (global inventory only).
    """
    out: list[SkillInfo] = []
    claude_on = _compat_skills_enabled(highlights, "claude")
    cursor_on = _compat_skills_enabled(highlights, "cursor")

    if claude_on and config.CLAUDE_HOME.is_dir():
        for skill in discover_skills(config.CLAUDE_HOME, "skills", "claude-compat"):
            out.append(_skill_with_path(skill, f"~/.claude/{skill.path}"))
    if cursor_on and config.CURSOR_HOME.is_dir():
        for skill in discover_skills(config.CURSOR_HOME, "skills", "cursor-compat"):
            out.append(_skill_with_path(skill, f"~/.cursor/{skill.path}"))
    return out


def _compat_skills_enabled(highlights: dict[str, str], vendor: str) -> bool:
    """Default true; false only when config explicitly disables the compat cell."""
    for key in (f"compat.{vendor}.skills", f"compat.{vendor}.skills".lower()):
        raw = highlights.get(key)
        if raw is None:
            continue
        if str(raw).strip().lower() in {"false", "0", "no", "off"}:
            return False
        return True
    # Flat/fallback keys from incomplete TOML parse
    for hk, hv in highlights.items():
        if hk.lower().endswith(f"{vendor}.skills") or hk.lower() == f"compat.{vendor}.skills":
            if str(hv).strip().lower() in {"false", "0", "no", "off"}:
                return False
    return True


def _skill_with_path(skill: SkillInfo, display_path: str) -> SkillInfo:
    return SkillInfo(
        name=skill.name,
        description=skill.description,
        source=skill.source,
        path=display_path,
        body=skill.body,
        truncated=skill.truncated,
        when_to_use=skill.when_to_use,
        missing=skill.missing,
    )


# ─────────────────────────────────────────────
# Readers
# ─────────────────────────────────────────────


def _read_raw(path: Path) -> tuple[str, bool]:
    if not path.is_file():
        return "", False
    return read_text_capped(path)


def _read_text_file(home: Path, rel: str, kind: str) -> TextFileInfo:
    path = home / rel
    if not path.is_file():
        return TextFileInfo(path=rel.replace("\\", "/"), exists=False, kind=kind)
    text, truncated = read_text_capped(path)
    if rel.endswith(".json"):
        content = redact_json_text(text)
    else:
        content = redact_text(text)
    return TextFileInfo(
        path=rel.replace("\\", "/"),
        exists=True,
        content=content,
        truncated=truncated,
        kind=kind,
    )


def _read_settings(home: Path, rel: str) -> TextFileInfo:
    info = _read_text_file(home, rel, "settings")
    if info.exists and info.content:
        info.rows = _settings_rows_from_content(rel, info.content)
    return info


_VALUE_MAX = 240


def _settings_rows_from_content(rel: str, content: str) -> list[SettingRow]:
    """Parse redacted settings text into a flat key/value table."""
    data: Any = None
    if rel.endswith(".json"):
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError, ValueError):
            data = None
    elif rel.endswith(".toml"):
        if tomllib is not None:
            try:
                data = tomllib.loads(content)
            except Exception:
                data = None
        if data is None:
            # Fallback: section/key lines without full TOML semantics.
            rows: list[SettingRow] = []
            for table, values in _iter_toml_tables(content):
                for k, v in values.items():
                    key = f"{table}.{k}" if table else k
                    rows.append(SettingRow(key=key, value=_format_setting_value(v)))
            return rows

    if isinstance(data, dict):
        return [
            SettingRow(key=k, value=v)
            for k, v in _flatten_for_table(data)
        ]
    if data is not None:
        return [SettingRow(key="(root)", value=_format_setting_value(data))]
    return []


def _flatten_for_table(
    data: dict[str, Any],
    prefix: str = "",
) -> list[tuple[str, str]]:
    """Flatten nested maps into dotted keys; keep lists/complex leaves as JSON."""
    out: list[tuple[str, str]] = []
    for raw_key in data:
        key = f"{prefix}.{raw_key}" if prefix else str(raw_key)
        value = data[raw_key]
        if isinstance(value, dict):
            if not value:
                out.append((key, "{}"))
            elif _is_shallow_map(value):
                # Keep one-level maps as separate rows (permissions.defaultMode, etc.).
                out.extend(_flatten_for_table(value, key))
            else:
                # Deep / mixed structures (hooks, nested plugin trees): one compact cell.
                out.append((key, _format_setting_value(value)))
        else:
            out.append((key, _format_setting_value(value)))
    return out


def _is_shallow_map(value: dict[str, Any]) -> bool:
    """True when every child is a scalar or a list of scalars (expandable as rows)."""
    for child in value.values():
        if isinstance(child, dict):
            return False
        if isinstance(child, list) and any(isinstance(x, (dict, list)) for x in child):
            return False
    return True


def _format_setting_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ": "))
        except (TypeError, ValueError):
            text = str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Single-line for the table; full multi-line stays in the raw file dump.
    text = " ".join(text.split())
    if len(text) > _VALUE_MAX:
        return text[: _VALUE_MAX - 1] + "…"
    return text


def _list_md_files(home: Path, rel_dir: str, kind: str) -> list[TextFileInfo]:
    root = home / rel_dir
    if not root.is_dir():
        return []
    out: list[TextFileInfo] = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    for entry in entries:
        try:
            if entry.is_file() and entry.suffix.lower() in {".md", ".mdc", ".txt", ".toml"}:
                rel = f"{rel_dir}/{entry.name}".replace("\\", "/")
                out.append(_read_text_file(home, rel, kind))
            elif entry.is_file() and kind == "agent":
                # agent definitions sometimes lack .md
                if entry.suffix.lower() in {".md", ".json", ".toml", ".yaml", ".yml"}:
                    rel = f"{rel_dir}/{entry.name}".replace("\\", "/")
                    out.append(_read_text_file(home, rel, kind))
        except OSError:
            continue
    return out


def _dir_info(home: Path, rel: str) -> DirInfo:
    path = home / rel
    if not path.exists():
        return DirInfo(path=rel, exists=False)
    count: int | None = None
    note = ""
    if path.is_dir():
        try:
            # Shallow count only — do not walk session trees deeply.
            count = sum(1 for _ in path.iterdir())
        except OSError:
            count = None
        if rel in {"sessions", "projects", "extensions", "file-history", "logs", "debug"}:
            note = "contents not listed (session/runtime data)"
        if rel in {"plugins", "marketplace-cache", "cache"}:
            note = "source cache not dumped; metadata only"
    return DirInfo(path=rel, exists=True, child_count=count, note=note)


# ─────────────────────────────────────────────
# Hooks
# ─────────────────────────────────────────────


def _load_hooks(spec: HomeSpec, home: Path, highlights: dict[str, str]) -> list[HookEntry]:
    hooks: list[HookEntry] = []

    for rel in spec.hooks_json:
        path = home / rel
        if not path.is_file():
            continue
        text, _ = read_text_capped(path)
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(data, dict):
            block = data.get("hooks", data)
            if isinstance(block, dict):
                hooks.extend(_hooks_from_cursor_or_flat(block, rel))

    if spec.hooks_dir:
        hooks_dir = home / spec.hooks_dir
        if hooks_dir.is_dir():
            try:
                files = sorted(hooks_dir.glob("*.json"))
            except OSError:
                files = []
            for fp in files:
                text, _ = read_text_capped(fp)
                try:
                    data = json.loads(text)
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                rel = f"{spec.hooks_dir}/{fp.name}"
                if isinstance(data, dict):
                    block = data.get("hooks", data)
                    if isinstance(block, dict):
                        hooks.extend(_hooks_from_claude_settings(block, rel))

    return hooks


def _hooks_from_claude_settings(hooks_obj: Any, source: str) -> list[HookEntry]:
    """Claude-style: { Event: [ { matcher?, hooks: [ {type, command, timeout} ] } ] }."""
    if not isinstance(hooks_obj, dict):
        return []
    out: list[HookEntry] = []
    for event, groups in hooks_obj.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            matcher = str(group.get("matcher") or "")
            inner = group.get("hooks")
            if isinstance(inner, list):
                for h in inner:
                    if not isinstance(h, dict):
                        continue
                    cmd = h.get("command") or h.get("url") or h.get("type") or ""
                    out.append(
                        HookEntry(
                            event=str(event),
                            command=redact_command_string(str(cmd)),
                            source=source,
                            timeout=_as_int(h.get("timeout")),
                            matcher=matcher,
                            extra=str(h.get("type") or ""),
                        )
                    )
            elif "command" in group:
                out.append(
                    HookEntry(
                        event=str(event),
                        command=redact_command_string(str(group.get("command") or "")),
                        source=source,
                        timeout=_as_int(group.get("timeout")),
                        matcher=matcher,
                    )
                )
    return out


def _hooks_from_cursor_or_flat(hooks_obj: Any, source: str) -> list[HookEntry]:
    """Cursor-style: { event: [ { command, timeout } ] }."""
    if not isinstance(hooks_obj, dict):
        return []
    out: list[HookEntry] = []
    for event, entries in hooks_obj.items():
        if not isinstance(entries, list):
            continue
        # Detect Claude nested shape
        if entries and isinstance(entries[0], dict) and "hooks" in entries[0]:
            return _hooks_from_claude_settings(hooks_obj, source)
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            cmd = entry.get("command") or entry.get("url") or ""
            out.append(
                HookEntry(
                    event=str(event),
                    command=redact_command_string(str(cmd)),
                    source=source,
                    timeout=_as_int(entry.get("timeout")),
                )
            )
    return out


def _notify_as_hooks(toml_text: str, source: str, report: AgentHomeReport) -> None:
    """Surface Codex ``notify = [...]`` as pseudo-hook rows."""
    for line in toml_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("notify") and "=" in stripped:
            _, _, rhs = stripped.partition("=")
            report.hooks.append(
                HookEntry(
                    event="notify",
                    command=redact_command_string(rhs.strip()),
                    source=source,
                    extra="config notify",
                )
            )


# ─────────────────────────────────────────────
# Plugins / MCP
# ─────────────────────────────────────────────


def _load_plugin_meta(home: Path, rel: str) -> list[PluginInfo]:
    path = home / rel
    if not path.is_file():
        return []
    text, _ = read_text_capped(path)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    out: list[PluginInfo] = []
    if not isinstance(data, dict):
        return out

    # Claude installed_plugins.json: { version, plugins: { name: [ {version, installPath} ] } }
    plugins = data.get("plugins")
    if isinstance(plugins, dict):
        for name, entries in plugins.items():
            if isinstance(entries, list):
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    out.append(
                        PluginInfo(
                            name=str(name),
                            version=str(entry.get("version") or ""),
                            path=str(entry.get("installPath") or entry.get("path") or ""),
                            source=rel,
                            enabled=True,
                        )
                    )
            elif isinstance(entries, dict):
                out.append(
                    PluginInfo(
                        name=str(name),
                        version=str(entries.get("version") or ""),
                        path=str(entries.get("installPath") or ""),
                        source=rel,
                        enabled=True,
                    )
                )
        return out

    # known_marketplaces or other shapes: list names only
    if "marketplaces" in data and isinstance(data["marketplaces"], dict):
        for name in data["marketplaces"]:
            out.append(PluginInfo(name=str(name), source=rel))
    return out


def _load_mcp_json(home: Path, rel: str) -> list[McpServerInfo]:
    path = home / rel
    if not path.is_file():
        return []
    text, _ = read_text_capped(path)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        if isinstance(data, dict):
            servers = data
        else:
            return []
    out: list[McpServerInfo] = []
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        env = cfg.get("env") if isinstance(cfg.get("env"), dict) else {}
        args = cfg.get("args") if isinstance(cfg.get("args"), list) else []
        out.append(
            McpServerInfo(
                name=str(name),
                command=str(cfg.get("command") or ""),
                args=[str(a) for a in args],
                url=str(cfg.get("url") or ""),
                enabled=cfg.get("enabled") if "enabled" in cfg else None,
                env_keys=sorted(str(k) for k in env.keys()),
                source=rel,
            )
        )
    return out


_TOML_TABLE = re.compile(r"^\[([^\]]+)\]\s*$")
_TOML_ASSIGN = re.compile(r"^([A-Za-z0-9_.-]+)\s*=\s*(.+)$")


def _iter_toml_tables(text: str) -> list[tuple[str, dict[str, str]]]:
    """Minimal TOML table walker (assignments only, string-ish values)."""
    tables: list[tuple[str, dict[str, str]]] = []
    current = ""
    values: dict[str, str] = {}

    def flush() -> None:
        nonlocal values
        if current or values:
            tables.append((current, values))
        values = {}

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _TOML_TABLE.match(stripped)
        if m:
            flush()
            current = m.group(1).strip()
            continue
        am = _TOML_ASSIGN.match(stripped)
        if am:
            key, val = am.group(1), am.group(2).strip()
            if val.startswith("[") and not val.endswith("]"):
                # multi-line array — keep first line marker
                values[key] = val
            else:
                values[key] = _strip_toml_str(val)
    flush()
    return tables


def _strip_toml_str(val: str) -> str:
    val = val.strip()
    if val in {"true", "false"}:
        return val
    if len(val) >= 2 and val[0] == val[-1] and val[0] in {"'", '"'}:
        return val[1:-1]
    # Triple quotes etc. — leave as-is
    if val.startswith('"""') or val.startswith("'''"):
        return val
    return val


def _mcp_from_toml_text(text: str, source: str) -> list[McpServerInfo]:
    out: list[McpServerInfo] = []
    if tomllib is not None:
        try:
            data = tomllib.loads(text)
        except Exception:
            data = None
        if isinstance(data, dict):
            servers = data.get("mcp_servers")
            if isinstance(servers, dict):
                for name, cfg in servers.items():
                    if not isinstance(cfg, dict):
                        continue
                    env = cfg.get("env") if isinstance(cfg.get("env"), dict) else {}
                    args = cfg.get("args") if isinstance(cfg.get("args"), list) else []
                    enabled = cfg.get("enabled")
                    out.append(
                        McpServerInfo(
                            name=str(name),
                            command=str(cfg.get("command") or ""),
                            args=[str(a) for a in args],
                            url=str(cfg.get("url") or ""),
                            enabled=bool(enabled) if isinstance(enabled, bool) else None,
                            env_keys=sorted(str(k) for k in env.keys()),
                            source=source,
                        )
                    )
                return out

    # Fallback: table names like mcp_servers.foo
    for table, values in _iter_toml_tables(text):
        if table.startswith("mcp_servers."):
            name = table.split(".", 1)[1]
            if name.endswith(".env"):
                continue
            enabled_s = values.get("enabled")
            enabled = None
            if enabled_s == "true":
                enabled = True
            elif enabled_s == "false":
                enabled = False
            out.append(
                McpServerInfo(
                    name=name,
                    command=values.get("command", ""),
                    url=values.get("url", ""),
                    enabled=enabled,
                    source=source,
                )
            )
    return out


def _plugins_from_toml_text(text: str, source: str) -> list[PluginInfo]:
    out: list[PluginInfo] = []
    if tomllib is not None:
        try:
            data = tomllib.loads(text)
        except Exception:
            data = None
        if isinstance(data, dict):
            plugins = data.get("plugins")
            if isinstance(plugins, dict):
                for name, cfg in plugins.items():
                    if isinstance(cfg, dict):
                        en = cfg.get("enabled")
                        out.append(
                            PluginInfo(
                                name=str(name),
                                enabled=bool(en) if isinstance(en, bool) else None,
                                source=source,
                            )
                        )
                    else:
                        out.append(PluginInfo(name=str(name), source=source))
                return out

    for table, values in _iter_toml_tables(text):
        if table.startswith("plugins."):
            # plugins."name@market" or plugins.name
            name = table[len("plugins.") :].strip().strip('"')
            en = values.get("enabled")
            enabled = True if en == "true" else False if en == "false" else None
            out.append(PluginInfo(name=name, enabled=enabled, source=source))
    return out


# ─────────────────────────────────────────────
# Highlights / tips
# ─────────────────────────────────────────────


def _parse_version(raw: str) -> str:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return raw.strip()[:80]
    if isinstance(data, dict):
        for key in ("version", "latest_version", "stable_version"):
            if data.get(key):
                return str(data[key])
    return ""


def _extract_highlights(agent_id: str, rel: str, content: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if rel.endswith(".json"):
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError, ValueError):
            return out
        if isinstance(data, dict):
            out.update(_flatten_json_highlights(data))
        return out

    if rel.endswith(".toml"):
        if tomllib is not None:
            try:
                data = tomllib.loads(content)
            except Exception:
                data = None
            if isinstance(data, dict):
                out.update(_flatten_toml_highlights(data))
                return out
        # Fallback assignments
        for table, values in _iter_toml_tables(content):
            for k, v in values.items():
                key = f"{table}.{k}" if table else k
                out[key] = v
                out[k] = v
    return out


def _flatten_json_highlights(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in data.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            # permissions.defaultMode etc.
            if all(not isinstance(x, (dict, list)) for x in v.values()):
                for sk, sv in v.items():
                    out[f"{key}.{sk}"] = _stringify(sv)
            out.update(_flatten_json_highlights(v, key))
        elif not isinstance(v, (list, dict)):
            out[key] = _stringify(v)
    return out


def _flatten_toml_highlights(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in data.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            out.update(_flatten_toml_highlights(v, key))
        elif not isinstance(v, (list, dict)):
            out[key] = _stringify(v)
    return out


def _stringify(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _tip_view(tip: Tip, highlights: dict[str, str]) -> TipView:
    current = ""
    note = ""
    if tip.key:
        current = _lookup_highlight(highlights, tip.key)
        if not current and tip.note_if_unset:
            note = tip.note_if_unset
        elif current and tip.permissive_values:
            if current.strip().lower() in {p.lower() for p in tip.permissive_values}:
                note = tip.note_if_permissive
    return TipView(
        title=tip.title,
        body=tip.body,
        key=tip.key,
        current_value=current,
        permissive_note=note,
        placement=tip.placement,
    )


def _lookup_highlight(highlights: dict[str, str], key: str) -> str:
    if key in highlights:
        return highlights[key]
    # Try suffix match
    for hk, hv in highlights.items():
        if hk == key or hk.endswith("." + key) or hk.lower() == key.lower():
            return hv
    # permissions.defaultMode nested
    parts = key.split(".")
    if len(parts) >= 2:
        for hk, hv in highlights.items():
            if hk.lower().endswith(key.lower()) or hk.lower().endswith(parts[-1].lower()):
                if parts[-2].lower() in hk.lower() or parts[-1].lower() == hk.lower():
                    return hv
    return ""


def _as_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _dedupe_hooks(hooks: list[HookEntry]) -> list[HookEntry]:
    seen: set[tuple[str, str, str]] = set()
    out: list[HookEntry] = []
    for h in hooks:
        key = (h.event, h.command, h.source)
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


def _dedupe_plugins(plugins: list[PluginInfo]) -> list[PluginInfo]:
    seen: set[str] = set()
    out: list[PluginInfo] = []
    for p in plugins:
        key = p.name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _dedupe_mcp(servers: list[McpServerInfo]) -> list[McpServerInfo]:
    seen: set[str] = set()
    out: list[McpServerInfo] = []
    for s in servers:
        key = s.name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out
