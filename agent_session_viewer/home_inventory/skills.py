"""Discover SKILL.md packages under allowlisted skill roots."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .redact import redact_text
from .specs import MAX_TEXT_BYTES

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_FM_LINE = re.compile(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$")


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str
    source: str
    path: str
    """Path relative to the agent home, posix style."""

    body: str
    truncated: bool
    when_to_use: str = ""
    missing: bool = False
    """True when a skill path was listed but SKILL.md could not be read."""


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split optional YAML-ish frontmatter from a skill body.

    Only handles simple ``key: value`` and folded ``key: >-`` / ``|`` blocks enough
    for skill metadata — not a full YAML parser.
    """
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text
    meta_raw = match.group(1)
    body = text[match.end() :]
    meta: dict[str, str] = {}
    key: str | None = None
    acc: list[str] = []
    folded = False

    def _flush() -> None:
        nonlocal key, acc, folded
        if key is None:
            return
        value = "\n".join(acc).strip() if folded else " ".join(acc).strip()
        # Strip surrounding quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        meta[key] = value
        key = None
        acc = []
        folded = False

    for line in meta_raw.splitlines():
        if key is not None and (line.startswith("  ") or line.startswith("\t")):
            acc.append(line.strip())
            continue
        _flush()
        m = _FM_LINE.match(line.strip())
        if not m:
            continue
        key = m.group(1).lower()
        rest = m.group(2).strip()
        if rest in {">", ">-", "|", "|-", "|+"}:
            folded = True
            acc = []
        else:
            folded = False
            acc = [rest] if rest else []
    _flush()
    return meta, body


def read_text_capped(path: Path, max_bytes: int = MAX_TEXT_BYTES) -> tuple[str, bool]:
    """Read a UTF-8 text file, truncating after ``max_bytes``."""
    try:
        data = path.read_bytes()
    except OSError:
        return "", False
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]
    text = data.decode("utf-8", errors="replace")
    if truncated:
        text += "\n\n… [truncated]\n"
    return text, truncated


def discover_skills(home: Path, rel_root: str, source: str) -> list[SkillInfo]:
    """Find ``*/SKILL.md`` under ``home / rel_root`` (one level of packages)."""
    root = home / rel_root
    if not root.is_dir():
        return []

    skills: list[SkillInfo] = []
    try:
        children = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []

    for child in children:
        skill_md: Path | None = None
        package_name = child.name
        try:
            if child.is_dir():
                candidate = child / "SKILL.md"
                if candidate.is_file():
                    skill_md = candidate
            elif child.is_file() and child.name.upper() == "SKILL.MD":
                skill_md = child
            elif child.is_symlink() or child.is_file():
                # Grok sometimes stores a skill as a bare file/symlink named for the skill.
                if child.is_dir():
                    continue
                # Directory-less skill stubs: treat as missing body unless it is SKILL.md
                if child.suffix.lower() == ".md" and "skill" in child.name.lower():
                    skill_md = child
                else:
                    # Symlink to a skill dir or opaque name — try child/SKILL.md if dir
                    continue
        except OSError:
            continue

        if skill_md is None:
            # Resolve symlink-as-skill-dir
            try:
                if child.is_dir():
                    candidate = child / "SKILL.md"
                    if candidate.is_file():
                        skill_md = candidate
            except OSError:
                pass

        if skill_md is None:
            # Package dir without SKILL.md still listed as missing.
            if child.is_dir():
                skills.append(
                    SkillInfo(
                        name=package_name,
                        description="",
                        source=source,
                        path=f"{rel_root}/{package_name}".replace("\\", "/"),
                        body="",
                        truncated=False,
                        missing=True,
                    )
                )
            continue

        text, truncated = read_text_capped(skill_md)
        if not text and not skill_md.is_file():
            skills.append(
                SkillInfo(
                    name=package_name,
                    description="",
                    source=source,
                    path=_rel(home, skill_md),
                    body="",
                    truncated=False,
                    missing=True,
                )
            )
            continue

        meta, _body = parse_frontmatter(text)
        name = meta.get("name") or package_name
        description = meta.get("description") or ""
        when = meta.get("when-to-use") or meta.get("when_to_use") or ""
        skills.append(
            SkillInfo(
                name=name,
                description=description,
                source=source,
                path=_rel(home, skill_md),
                body=redact_text(text),
                truncated=truncated,
                when_to_use=when,
            )
        )

    return skills


def _rel(home: Path, path: Path) -> str:
    try:
        return path.relative_to(home).as_posix()
    except ValueError:
        return path.as_posix()
