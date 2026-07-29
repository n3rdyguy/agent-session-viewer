"""Prepare code-like transcript content for Markdown rendering and export."""

from __future__ import annotations

import ast
import html
import json
import re


_FULL_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})[^\n]*\n[\s\S]*\n\1\s*$")
_FENCE_LINE_RE = re.compile(r"(?:^|\n)\s*(?:`{3,}|~{3,})(?:[A-Za-z0-9_+-]+)?\s*(?:\n|$)")
_FENCED_BLOCK_RE = re.compile(
    r"(?ms)^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})(?P<info>[^\n]*)\n"
    r"(?P<body>.*?)(?P<end>\n[ \t]*(?P=fence)[ \t]*)(?=\n|$)"
)

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
    ("diff", (r"^diff --git ", r"^@@\s+-\d", r"^\*\*\* (?:Begin|Update|Add|Delete) (?:Patch|File)")),
    (
        "yaml",
        (
            r"(?:^|\n)---\s*(?:\n|$)",
            r"(?:^|\n)[A-Za-z_][\w.-]*:\s*(?:\n|[|>]\s*\n)",
            r"(?:^|\n)\s+-\s+[A-Za-z_][\w.-]*:\s+",
        ),
    ),
    ("xml", (r"^\s*<\?xml\b", r"<[A-Za-z_][\w:.-]+(?:\s[^>]*)?>[\s\S]*</[A-Za-z_][\w:.-]+>")),
    ("csharp", (r"\busing\s+System(?:\.\w+)*;", r"\bnamespace\s+[\w.]+\s*[{;]", r"\bpublic\s+(?:static\s+)?class\s+\w+")),
    ("java", (r"\bpackage\s+[\w.]+;", r"\bimport\s+java\.[\w.*]+;", r"\bpublic\s+(?:final\s+)?class\s+\w+")),
    ("cpp", (r"#include\s*<(?:iostream|vector|string|memory)>", r"\bstd::\w+", r"\busing\s+namespace\s+std\s*;")),
    ("c", (r"#include\s*<(?:stdio|stdlib|string)\.h>", r"\b(?:int|void)\s+main\s*\(")),
    ("rust", (r"\bfn\s+main\s*\(", r"\b(?:pub\s+)?(?:struct|enum|trait|impl)\s+\w+", r"\blet\s+mut\s+\w+")),
    ("go", (r"^package\s+\w+", r"\bfunc\s+(?:\([^)]*\)\s*)?\w+\s*\(", r"\bimport\s+\(")),
    ("ruby", (r"(?:^|\n)\s*def\s+\w+[!?=]?", r"(?:^|\n)\s*class\s+\w+(?:\s*<\s*\w+)?", r"\b(?:require|require_relative)\s+['\"]")),
)


def detect_code_language(text: str) -> str | None:
    """Return a Markdown fence language when *text* is predominantly code."""
    source = str(text or "")
    stripped = source.strip()
    if not stripped or _FULL_FENCE_RE.match(stripped) or _FENCE_LINE_RE.search(stripped):
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
    if tree and len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr) and isinstance(
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
    ):
        return "python"

    for language, patterns in _LANGUAGE_RULES:
        matches = sum(bool(re.search(pattern, stripped, re.IGNORECASE | re.MULTILINE)) for pattern in patterns)
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


def format_markdown_content(text: str) -> str:
    """Decode code entities and fence complete code-like values for Markdown."""
    source = str(text or "")
    if _FENCE_LINE_RE.search(source):
        return _FENCED_BLOCK_RE.sub(
            lambda match: (
                f"{match.group('indent')}{match.group('fence')}{match.group('info')}\n"
                f"{html.unescape(match.group('body'))}{match.group('end')}"
            ),
            source,
        )

    decoded_source = html.unescape(source)
    language = detect_code_language(decoded_source)
    if not language:
        return source

    # Use a fence longer than any backtick run already present in the code.
    longest_run = max((len(run) for run in re.findall(r"`+", decoded_source)), default=0)
    fence = "`" * max(3, longest_run + 1)
    return f"{fence}{language}\n{decoded_source.rstrip()}\n{fence}"
