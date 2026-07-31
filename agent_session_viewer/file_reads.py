"""Detect shell file-pull commands and optionally split their stdout into file artifacts.

Used to render nested collapsible file cards inside tool_result bubbles when the
command is a pure file-read (cat/sed/head/Get-Content/…) rather than a mixed shell.
"""

from __future__ import annotations

import json
import re
import shlex
from typing import Any, TypedDict

# Used by markdown_output for "looks like shell output" fencing (dotall OK there).
SHELL_OUTPUT_META_RE = re.compile(
    r"(?is)\A(?:[ \t]*(?:"
    r"Exit code\s*:.*|"
    r"Wall time\s*:.*|"
    r"Total output lines\s*:.*|"
    r"Output\s*:|"
    r"Stdout\s*:|"
    r"Stderr\s*:|"
    r"Command (?:output|failed|succeeded)\b.*|"
    r"Process exited with (?:code|status)\s+\d+.*"
    r")[ \t]*\n)+"
)

# Line-anchored meta keys for stripping (must NOT use DOTALL — `.*` stays on one line).
_SHELL_META_LINE_RE = re.compile(
    r"(?i)^[ \t]*(?:"
    r"Exit code\s*:.*|"
    r"Wall time\s*:.*|"
    r"Total output lines\s*:.*|"
    r"Output\s*:|"
    r"Stdout\s*:|"
    r"Stderr\s*:|"
    r"Command (?:output|failed|succeeded)\b.*|"
    r"Process exited with (?:code|status)\s+\d+.*"
    r")[ \t]*$"
)

_SHELL_TOOL_NAMES = frozenset(
    {
        "shell_command",
        "shell",
        "bash",
        "local_shell",
        "local_shell_call",
        "run_terminal_command",
        "run_terminal",
        "terminal",
        "powershell",
        "pwsh",
        "cmd",
    }
)

# sed -n '1,240p' path  |  sed -n 1,240p path  |  sed.exe -n "10,20p" path
_SED_RANGE_RE = re.compile(
    r"(?is)^\s*(?:sed(?:\.exe)?)\s+-n\s+"
    r"(?P<range>'?\d+\s*,\s*\d+p'?|\"?\d+\s*,\s*\d+p\"?)\s+"
    r"(?P<path>.+?)\s*$"
)
_HEAD_RE = re.compile(r"(?is)^\s*(?:head(?:\.exe)?)\s+(?:-n\s+|-)?(?P<n>\d+)\s+(?P<path>.+?)\s*$")
_TAIL_RE = re.compile(r"(?is)^\s*(?:tail(?:\.exe)?)\s+(?:-n\s+)?(?P<n>\d+)\s+(?P<path>.+?)\s*$")
_CAT_RE = re.compile(r"(?is)^\s*(?:cat(?:\.exe)?|bat(?:\.exe)?|type)\s+(?P<path>.+?)\s*$")
# Get-Content / gc — simple forms only
_PS_GET_CONTENT_RE = re.compile(
    r"(?is)^\s*(?:Get-Content|gc)\s+(?P<path>(?:'[^']+'|\"[^\"]+\"|[^\s|]+))"
    r"(?P<rest>.*?)\s*$"
)
_PS_SELECT_FIRST_RE = re.compile(
    r"(?is)^\s*(?:Get-Content|gc)\s+(?P<path>(?:'[^']+'|\"[^\"]+\"|[^\s|]+))"
    r"\s*\|\s*Select-Object\s+-First\s+(?P<n>\d+)\s*$"
)


class FileReadEntry(TypedDict, total=False):
    path: str
    start: int
    end: int
    range_kind: str  # slice | full | tail
    expected_lines: int | None


class FileArtifact(TypedDict, total=False):
    path: str
    start: int
    end: int
    range_kind: str
    label: str
    text: str
    split: bool


def strip_shell_output_meta(text: str) -> tuple[str, str]:
    """Split Codex/shell meta prefix from stdout body. Returns (header, body)."""
    if not text:
        return "", ""
    # Prefer line-based strip so multi-line body after "Output:" is preserved.
    lines = text.splitlines(keepends=True)
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        bare = line.rstrip("\r\n")
        if not bare.strip():
            # blank line only counts as meta while still in the header block
            if idx == 0:
                break
            idx += 1
            continue
        if _SHELL_META_LINE_RE.match(bare):
            idx += 1
            continue
        break
    if idx == 0:
        return "", text
    header = "".join(lines[:idx])
    body = "".join(lines[idx:])
    return header, body


def _is_shell_tool_name(tool_name: str | None) -> bool:
    name = (tool_name or "").strip().lower()
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    if name in _SHELL_TOOL_NAMES:
        return True
    return any(token in name for token in ("shell", "terminal", "bash", "powershell", "pwsh"))


def extract_shell_command(tool_name: str | None, arguments: Any) -> str | None:
    """Return the shell command string from a tool call name + arguments, if any."""
    if not _is_shell_tool_name(tool_name):
        return None

    args = arguments
    if isinstance(args, str):
        stripped = args.strip()
        if not stripped:
            return None
        try:
            args = json.loads(stripped)
        except json.JSONDecodeError:
            return stripped

    if isinstance(args, dict):
        for key in ("command", "cmd", "script", "input"):
            val = args.get(key)
            if isinstance(val, str) and val.strip():
                return val
            if isinstance(val, list):
                parts = [str(p) for p in val if p is not None and str(p) != ""]
                if parts:
                    return " ".join(parts)
    return None


def _unquote_path(path: str) -> str:
    path = path.strip()
    if len(path) >= 2 and path[0] == path[-1] and path[0] in ("'", '"'):
        return path[1:-1]
    return path


def _path_is_single(path: str) -> bool:
    """Reject multi-file cat/type forms (space-separated paths)."""
    path = path.strip()
    if not path:
        return False
    if (path[0] in "'\"" and path[-1] == path[0]) or " " not in path:
        return True
    # Unquoted path with spaces is ambiguous; treat as multi-arg / reject
    try:
        tokens = shlex.split(path, posix=True)
    except ValueError:
        return False
    return len(tokens) == 1


def split_top_level_commands(command: str) -> list[str] | None:
    """
    Split on top-level `;` and newlines. Returns None if `&&`, `||`, or bare
    `|` pipelines appear at top level (not pure sequential file-read batch).
    """
    if not command or not command.strip():
        return None

    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    s = command
    n = len(s)

    def flush() -> None:
        piece = "".join(buf).strip()
        buf.clear()
        if piece:
            parts.append(piece)

    while i < n:
        ch = s[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                # handle escaped quote in double quotes (\" ) lightly
                quote = None
            elif ch == "\\" and quote == '"' and i + 1 < n:
                buf.append(s[i + 1])
                i += 2
                continue
            i += 1
            continue

        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue

        # Top-level separators
        if ch == ";":
            flush()
            i += 1
            continue
        if ch == "\n":
            flush()
            i += 1
            continue

        # Reject control operators at top level
        if ch == "&" and i + 1 < n and s[i + 1] == "&":
            return None
        if ch == "|" and i + 1 < n and s[i + 1] == "|":
            return None
        if ch == "|":
            # Pipeline — not a pure sequential file-read batch
            return None
        if ch == "&":
            return None

        buf.append(ch)
        i += 1

    flush()
    return parts or None


def _parse_sed_range(range_token: str) -> tuple[int, int] | None:
    token = range_token.strip().strip("'\"").strip()
    # 1,240p
    m = re.match(r"^(\d+)\s*,\s*(\d+)p$", token, re.I)
    if not m:
        return None
    start, end = int(m.group(1)), int(m.group(2))
    if start < 1 or end < start:
        return None
    return start, end


def _parse_one_file_read(segment: str) -> FileReadEntry | None:
    seg = segment.strip()
    if not seg:
        return None

    # Reject obvious non-reads early
    if any(op in seg for op in ("&&", "||", "`", "$(")):
        return None

    m = _SED_RANGE_RE.match(seg)
    if m:
        rng = _parse_sed_range(m.group("range"))
        path = _unquote_path(m.group("path"))
        if not rng or not path or not _path_is_single(m.group("path")):
            return None
        start, end = rng
        return {
            "path": path,
            "start": start,
            "end": end,
            "range_kind": "slice",
            "expected_lines": end - start + 1,
        }

    m = _HEAD_RE.match(seg)
    if m:
        n = int(m.group("n"))
        path = _unquote_path(m.group("path"))
        if n < 1 or not path or not _path_is_single(m.group("path")):
            return None
        return {
            "path": path,
            "start": 1,
            "end": n,
            "range_kind": "slice",
            "expected_lines": n,
        }

    m = _TAIL_RE.match(seg)
    if m:
        n = int(m.group("n"))
        path = _unquote_path(m.group("path"))
        if n < 1 or not path or not _path_is_single(m.group("path")):
            return None
        return {
            "path": path,
            "range_kind": "tail",
            "expected_lines": n,  # known count, but start unknown
            "end": n,  # store n in end for label "last n lines"
        }

    m = _PS_SELECT_FIRST_RE.match(seg)
    if m:
        n = int(m.group("n"))
        path = _unquote_path(m.group("path"))
        if n < 1 or not path:
            return None
        return {
            "path": path,
            "start": 1,
            "end": n,
            "range_kind": "slice",
            "expected_lines": n,
        }

    m = _PS_GET_CONTENT_RE.match(seg)
    if m:
        path = _unquote_path(m.group("path"))
        rest = (m.group("rest") or "").strip()
        if not path:
            return None
        # No pipeline leftovers besides known flags
        if "|" in rest:
            return None
        total = re.search(r"(?i)(?:-TotalCount|-First)\s+(\d+)", rest)
        tail = re.search(r"(?i)-Tail\s+(\d+)", rest)
        if total and not tail:
            n = int(total.group(1))
            if n < 1:
                return None
            return {
                "path": path,
                "start": 1,
                "end": n,
                "range_kind": "slice",
                "expected_lines": n,
            }
        if tail and not total:
            n = int(tail.group(1))
            if n < 1:
                return None
            return {
                "path": path,
                "range_kind": "tail",
                "expected_lines": n,
                "end": n,
            }
        # Other switches (Encoding, etc.) OK for full-file; reject unknown dense junk
        if re.search(r"(?i)-(TotalCount|First|Tail)\b", rest):
            return None
        return {
            "path": path,
            "range_kind": "full",
            "expected_lines": None,
        }

    m = _CAT_RE.match(seg)
    if m:
        raw_path = m.group("path")
        if not _path_is_single(raw_path):
            return None
        path = _unquote_path(raw_path)
        if not path:
            return None
        # Avoid matching `type` as verb in other languages via path-only check already
        return {
            "path": path,
            "range_kind": "full",
            "expected_lines": None,
        }

    return None


def parse_file_read_plan(command: str) -> list[FileReadEntry] | None:
    """
    If `command` is only pure file-read subcommands, return the plan.
    Otherwise return None.
    """
    if not command or not command.strip():
        return None

    # Single-command path first (allows simple PowerShell pipelines like
    # Get-Content x | Select-Object -First N that contain top-level `|`).
    single = _parse_one_file_read(command.strip())
    if single is not None and ";" not in command and "\n" not in command.rstrip("\n"):
        # Avoid treating multi-line / multi-seg as single if separators exist
        if "&&" not in command and "||" not in command:
            return [single]

    segments = split_top_level_commands(command)
    if not segments:
        return None
    plan: list[FileReadEntry] = []
    for seg in segments:
        entry = _parse_one_file_read(seg)
        if entry is None:
            return None
        plan.append(entry)
    return plan or None


def format_file_read_label(entry: FileReadEntry) -> str:
    path = entry.get("path") or "file"
    kind = entry.get("range_kind") or "full"
    if kind == "slice":
        start = entry.get("start")
        end = entry.get("end")
        if start is not None and end is not None:
            return f"{path} · lines {start}–{end}"
    if kind == "tail":
        n = entry.get("end") or entry.get("expected_lines")
        if n is not None:
            return f"{path} · last {n} lines"
    return f"{path} · full file"


def _body_lines(body: str) -> list[str]:
    """Split body into lines without keeping a trailing empty line from final newline."""
    if body == "":
        return []
    # Preserve content fidelity: splitlines keeps no line endings; join with \n later
    lines = body.splitlines()
    # If body ended with newline, splitlines already drops the final empty segment —
    # which matches "line count" as printed by tools that count physical lines.
    return lines


def _join_chunk(lines: list[str], body: str) -> str:
    chunk = "\n".join(lines)
    if chunk and (body.endswith("\n") or body.endswith("\r\n")) and not chunk.endswith("\n"):
        chunk += "\n"
    return chunk


def _greedy_chunk_sizes(budgets: list[int], line_count: int) -> list[int]:
    """
    Partition stdout lines using command budgets only (never filesystem).

    Non-final entries take min(budget, remaining); the last entry absorbs the
    remainder so every body line lands in some artifact when ranges overshoot
    short files mid-batch.
    """
    sizes: list[int] = []
    remaining = line_count
    last = len(budgets) - 1
    for i, budget in enumerate(budgets):
        if i == last:
            sizes.append(max(0, remaining))
        else:
            take = max(0, min(int(budget), remaining))
            sizes.append(take)
            remaining -= take
    return sizes


def allocate_chunk_sizes(plan: list[FileReadEntry], line_count: int) -> list[int] | None:
    """
    Decide how many tool_result body lines belong to each plan entry.

    Uses only the command's line budgets and the captured stdout length —
    never reads workspace files.
    """
    if not plan:
        return None

    if len(plan) == 1:
        return [line_count]

    requested = [e.get("expected_lines") for e in plan]
    if not all(s is not None for s in requested):
        # Multi full-file reads (e.g. cat a; cat b) have unknown sizes in stdout.
        return None

    sizes = [int(s or 0) for s in requested]
    total = sum(sizes)
    if total == line_count:
        return sizes
    # Ranges often overshoot short files; still partition stdout by budgets.
    return _greedy_chunk_sizes(sizes, line_count)


def _artifact_from_entry(
    entry: FileReadEntry,
    text: str,
    *,
    split: bool,
) -> FileArtifact:
    art: FileArtifact = {
        "path": entry.get("path") or "file",
        "range_kind": entry.get("range_kind") or "full",
        "label": format_file_read_label(entry),
        "text": text,
        "split": split,
    }
    if "start" in entry:
        art["start"] = entry["start"]
    if "end" in entry:
        art["end"] = entry["end"]
    return art


def build_file_artifacts(
    plan: list[FileReadEntry],
    body: str,
) -> list[FileArtifact]:
    """
    Build nested file artifacts from a pure file-read plan and tool_result stdout.

    Never reads the workspace filesystem — content comes only from ``body``.
    """
    if not plan:
        return []

    lines = _body_lines(body)
    line_count = len(lines)

    # Single pure file-read: always attach full body to the one artifact.
    if len(plan) == 1:
        return [_artifact_from_entry(plan[0], body, split=True)]

    sizes = allocate_chunk_sizes(plan, line_count)
    if sizes is not None:
        artifacts: list[FileArtifact] = []
        offset = 0
        for entry, size in zip(plan, sizes, strict=True):
            chunk_lines = lines[offset : offset + size]
            offset += size
            text = _join_chunk(chunk_lines, body)
            artifacts.append(_artifact_from_entry(entry, text, split=True))
        return artifacts

    # Multi full-file (no line budgets): cannot partition concatenated stdout.
    return [_artifact_from_entry(entry, "", split=False) for entry in plan]


def file_artifacts_for_tool_result(
    *,
    tool_name: str | None,
    arguments: Any,
    command: str | None = None,
    output: str,
) -> tuple[list[FileArtifact], str | None]:
    """
    High-level helper for agents.

    Returns ``(artifacts, prefix)`` when stdout was partitioned into file cards:
    - ``artifacts``: per-file bodies taken from the tool_result output
    - ``prefix``: non-file output that appeared *before* the file bodies
      (typically shell meta: Exit code / Wall time / Output:)

    Returns ``([], None)`` when the command is not a pure file-read batch or
    the body cannot be partitioned. Callers should keep the full ``output`` as
    the turn text for flat (toggle-off) display.
    """
    cmd = command
    if cmd is None:
        cmd = extract_shell_command(tool_name, arguments)
    if not cmd:
        return [], None

    plan = parse_file_read_plan(cmd)
    if not plan:
        return [], None

    header, body = strip_shell_output_meta(output)
    artifacts = build_file_artifacts(plan, body)
    if not artifacts:
        return [], None

    # Only enable split UI when every card has attributed stdout content.
    if not all(a.get("split") for a in artifacts):
        return [], None

    prefix = header.rstrip() + "\n" if header.strip() else ""
    return artifacts, prefix
