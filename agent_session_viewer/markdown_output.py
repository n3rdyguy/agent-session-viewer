"""Prepare code-like transcript content for Markdown rendering and export."""

from __future__ import annotations

import ast
import json
import re

from .file_reads import SHELL_OUTPUT_META_RE as _SHELL_OUTPUT_META_RE
from .util import decode_html_entities

_FULL_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})[^\n]*\n[\s\S]*\n\1\s*$")
_FENCE_LINE_RE = re.compile(r"(?:^|\n)\s*(?:`{3,}|~{3,})(?:[A-Za-z0-9_+-]+)?\s*(?:\n|$)")
_FENCE_OPENER_RE = re.compile(r"^(\s*)(`{3,}|~{3,})(.*)$")
# _SHELL_OUTPUT_META_RE: Codex / shell tool wrappers often prefix captured stdout.

_LANGUAGE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("php", (r"<\?php\b", r"\bnamespace\s+[\w\\]+;", r"\$\w+\s*=", r"->\w+\s*\(")),
    (
        "typescript",
        (
            r"(?:^|\n)\s*interface\s+\w+\s*{",
            r"(?:^|\n)\s*type\s+\w+\s*=",
            r"(?:^|\n)\s*(?:const|let|var)\s+\w+\s*:\s*[\w<>{}\[\]|]+",
            r"(?:^|\n)\s*(?:public|private|protected|readonly)\s+\w+",
        ),
    ),
    (
        "javascript",
        (
            r"(?:^|\n)\s*(?:const|let|var)\s+\w+\s*=",
            r"(?:^|\n)\s*(?:async\s+)?function\s+\w*\s*\(",
            r"(?:^|[=(,]\s*)(?:async\s+)?\([^)]*\)\s*=>",
            r"\b(?:import|export)\s+(?:default\s+|{|\*)",
            r"\bconsole\.(?:log|error|warn)\s*\(",
        ),
    ),
    ("html", (r"<!doctype\s+html", r"<html\b", r"<(?:div|main|section|body|head)\b[^>]*>")),
    (
        "css",
        (
            r"(?:^|\n)\s*[@.#]?[a-zA-Z][\w .#:[\]>+~-]*\s*{\s*\n?",
            r"\b(?:display|position|margin|padding|color|background)\s*:",
        ),
    ),
    (
        "sql",
        (
            r"^\s*SELECT\s+.+\s+FROM\b",
            r"^\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|CREATE\s+TABLE|ALTER\s+TABLE)\b",
        ),
    ),
    (
        "bash",
        (
            r"^#!\s*/(?:usr/)?bin/(?:env\s+)?(?:ba|z|k)?sh\b",
            r"(?:^|\n)\s*(?:if|for|while)\s+.+;\s*(?:then|do)\b",
            r"(?:^|\n)\s*(?:export\s+)?[A-Z_][A-Z0-9_]*=",
            r"^\s*(?:git|gh|npm|pnpm|yarn|uv|python|python3|pip|docker|kubectl|"
            r"cargo|go|make|cmake)\s+\S+",
        ),
    ),
    (
        "powershell",
        (
            r"(?:^|\n)\s*\$[A-Za-z_]\w*\s*=",
            r"(?:^|\n)\s*(?:Get|Set|New|Remove|Write)-[A-Za-z]+\b",
            r"(?:^|\n)\s*(?:param|function)\s*(?:\(|[\w-]+)",
        ),
    ),
    (
        "diff",
        (r"^diff --git ", r"^@@\s+-\d", r"^\*\*\* (?:Begin|Update|Add|Delete) (?:Patch|File)"),
    ),
    (
        "yaml",
        (
            r"(?:^|\n)---\s*(?:\n|$)",
            r"(?:^|\n)[A-Za-z_][\w.-]*:\s*(?:\n|[|>]\s*\n)",
            r"(?:^|\n)\s+-\s+[A-Za-z_][\w.-]*:\s+",
        ),
    ),
    ("xml", (r"^\s*<\?xml\b", r"<[A-Za-z_][\w:.-]+(?:\s[^>]*)?>[\s\S]*</[A-Za-z_][\w:.-]+>")),
    (
        "csharp",
        (
            r"\busing\s+System(?:\.\w+)*;",
            r"\bnamespace\s+[\w.]+\s*[{;]",
            r"\bpublic\s+(?:static\s+)?class\s+\w+",
        ),
    ),
    (
        "java",
        (
            r"\bpackage\s+[\w.]+;",
            r"\bimport\s+java\.[\w.*]+;",
            r"\bpublic\s+(?:final\s+)?class\s+\w+",
        ),
    ),
    (
        "cpp",
        (
            r"#include\s*<(?:iostream|vector|string|memory)>",
            r"\bstd::\w+",
            r"\busing\s+namespace\s+std\s*;",
        ),
    ),
    ("c", (r"#include\s*<(?:stdio|stdlib|string)\.h>", r"\b(?:int|void)\s+main\s*\(")),
    (
        "rust",
        (
            r"\bfn\s+main\s*\(",
            r"\b(?:pub\s+)?(?:struct|enum|trait|impl)\s+\w+",
            r"\blet\s+mut\s+\w+",
        ),
    ),
    ("go", (r"^package\s+\w+", r"\bfunc\s+(?:\([^)]*\)\s*)?\w+\s*\(", r"\bimport\s+\(")),
    (
        "ruby",
        (
            r"(?:^|\n)\s*def\s+\w+[!?=]?",
            r"(?:^|\n)\s*class\s+\w+(?:\s*<\s*\w+)?",
            r"\b(?:require|require_relative)\s+['\"]",
        ),
    ),
)


def _fences_are_balanced(text: str) -> bool:
    """Return True when every fence opener has a matching closer (CommonMark-ish)."""
    stack: list[tuple[str, int]] = []
    for line in str(text or "").splitlines():
        match = _FENCE_OPENER_RE.match(line)
        if not match:
            continue
        marker = match.group(2)
        info = match.group(3).strip()
        char = marker[0]
        length = len(marker)
        # A closer matches the same fence char, is at least as long, and has no info string.
        if stack and stack[-1][0] == char and length >= stack[-1][1] and not info:
            stack.pop()
        else:
            stack.append((char, length))
    return not stack


def _wrap_as_fenced(source: str, language: str) -> str:
    """Wrap *source* in a fence longer than any backtick run it already contains."""
    longest_run = max((len(run) for run in re.findall(r"`+", source)), default=0)
    fence = "`" * max(3, longest_run + 1)
    return f"{fence}{language}\n{source.rstrip()}\n{fence}"


def detect_code_language(text: str, *, allow_internal_fences: bool = False) -> str | None:
    """Return a Markdown fence language when *text* is predominantly code.

    When *allow_internal_fences* is true, embedded fence lines (for example a
    README dump inside shell output) no longer suppress detection.
    """
    source = str(text or "")
    stripped = source.strip()
    if not stripped or _FULL_FENCE_RE.match(stripped):
        return None
    if not allow_internal_fences and _FENCE_LINE_RE.search(stripped):
        return None

    if stripped[:1] in "[{":
        try:
            decoded = json.loads(stripped)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        else:
            if isinstance(decoded, (dict, list)):
                return "json"

    # Python's parser is a useful high-confidence detector, provided the tree
    # contains an actual statement rather than a lone word or number.
    try:
        tree = ast.parse(stripped)
    except SyntaxError:
        tree = None
    if tree and any(
        isinstance(
            node,
            (
                ast.Assign,
                ast.AnnAssign,
                ast.AugAssign,
                ast.Import,
                ast.ImportFrom,
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
                ast.For,
                ast.AsyncFor,
                ast.While,
                ast.If,
                ast.Try,
                ast.With,
                ast.AsyncWith,
                ast.Match,
            ),
        )
        for node in tree.body
    ):
        return "python"
    if (
        tree
        and len(tree.body) == 1
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(
            tree.body[0].value,
            (
                ast.Call,
                ast.Await,
                ast.Lambda,
                ast.Dict,
                ast.Set,
                ast.Tuple,
                ast.ListComp,
                ast.SetComp,
                ast.DictComp,
                ast.GeneratorExp,
            ),
        )
    ):
        return "python"

    for language, patterns in _LANGUAGE_RULES:
        matches = sum(
            bool(re.search(pattern, stripped, re.IGNORECASE | re.MULTILINE)) for pattern in patterns
        )
        threshold = 2 if language == "yaml" else 1
        if matches >= threshold:
            return language

    # A conservative generic fallback catches unfamiliar languages without
    # turning ordinary prose, logs, or Markdown lists into code blocks.
    lines = [line for line in stripped.splitlines() if line.strip()]
    if len(lines) >= 2:
        code_lines = sum(
            bool(
                re.search(
                    r"(?:[;{}]\s*$|^\s{2,}\S|^\s*(?:class|def|func|function|"
                    r"if|else|for|while|return)\b|:=|=>)",
                    line,
                )
            )
            for line in lines
        )
        if code_lines >= 2 and code_lines / len(lines) >= 0.5:
            return "text"
    return None


def format_markdown_content(text: str, assume_markdown: bool = False) -> str:
    """Decode and prepare Markdown content, optionally bypassing code detection."""
    source = decode_html_entities(text)

    # System instructions and Markdown documents often contain XML-like wrapper
    # tags around otherwise normal Markdown. Preserve their Markdown structure
    # instead of misclassifying and fencing the entire document as XML.
    if assume_markdown:
        return source

    stripped = source.strip()
    if not stripped:
        return source

    # Already a single complete fenced block - leave it alone.
    if _FULL_FENCE_RE.match(stripped):
        return source

    # Shell tool results often wrap captured files (including READMEs with their
    # own fences). Fence the whole blob so embedded Markdown is not re-parsed.
    shell_meta = _SHELL_OUTPUT_META_RE.match(stripped)
    if shell_meta:
        body = stripped[shell_meta.end() :]
        language = detect_code_language(body, allow_internal_fences=True) or "text"
        return _wrap_as_fenced(source, language)

    # Truncated dumps leave an open fence; that poisons Markdown rendering of the
    # whole bubble, so force a preformatted block with a longer outer fence.
    if _FENCE_LINE_RE.search(source) and not _fences_are_balanced(source):
        language = detect_code_language(source, allow_internal_fences=True) or "text"
        return _wrap_as_fenced(source, language)

    # Balanced internal fences usually mean intentional Markdown (prose + code).
    if _FENCE_LINE_RE.search(source):
        return source

    language = detect_code_language(source)
    if not language:
        return source

    return _wrap_as_fenced(source, language)
