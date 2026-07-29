import pytest

from agent_session_viewer.app import app
from agent_session_viewer.markdown_output import (
    detect_code_language,
    format_markdown_content,
)
from agent_session_viewer.session import turns_to_markdown
from agent_session_viewer.turns import make_turn


@pytest.mark.parametrize(
    ("source", "language"),
    [
        ('{"name": "viewer", "enabled": true}', "json"),
        ("<?php\n$user = find_user(1);\necho $user->name;", "php"),
        ("from pathlib import Path\n\ndef load(path: Path):\n    return path.read_text()", "python"),
        ("print('hello')", "python"),
        ("const answer = items.map((item) => item.value);\nconsole.log(answer);", "javascript"),
        ("const answer = 42;", "javascript"),
        ("interface User {\n  id: number;\n}\nconst user: User = { id: 1 };", "typescript"),
        ("<!doctype html>\n<html><body>Hello</body></html>", "html"),
        ("SELECT id, name\nFROM users\nWHERE active = 1;", "sql"),
        ("diff --git a/app.py b/app.py\n@@ -1,1 +1,1 @@\n-old\n+new", "diff"),
        ("uv run pytest", "bash"),
    ],
)
def test_detects_code_language(source: str, language: str) -> None:
    assert detect_code_language(source) == language


@pytest.mark.parametrize(
    "source",
    [
        "This is an ordinary sentence with punctuation.",
        "A short explanation:\n\n- first item\n- second item",
        "2026-07-30 10:20:00 process finished successfully",
        "### Already Markdown\n\nThis should stay as written.",
        "In JavaScript, `const answer = 42;` declares a constant.",
    ],
)
def test_does_not_fence_prose_or_markdown(source: str) -> None:
    assert detect_code_language(source) is None
    assert format_markdown_content(source) == source


def test_preserves_existing_fenced_code() -> None:
    source = "```python\nprint('hello')\n```"

    assert format_markdown_content(source) == source


def test_decodes_html_entities_in_detected_code() -> None:
    source = "&lt;?php\necho &quot;Hello&quot;;\n?&gt;"

    assert format_markdown_content(source) == '```php\n<?php\necho "Hello";\n?>\n```'


def test_decodes_html_entities_inside_existing_fences() -> None:
    source = "Before\n\n```html\n&lt;strong&gt;Hello &amp; goodbye&lt;/strong&gt;\n```\n\nAfter"

    assert format_markdown_content(source) == (
        "Before\n\n```html\n<strong>Hello & goodbye</strong>\n```\n\nAfter"
    )


def test_does_not_decode_html_entities_in_prose() -> None:
    source = "Use &lt;name&gt; as a placeholder and keep AT&amp;T unchanged."

    assert format_markdown_content(source) == source


def test_preserves_prose_around_an_existing_fence() -> None:
    source = "Try this:\n\n```python\nprint('hello')\n```\n\nThen continue."

    assert format_markdown_content(source) == source


def test_uses_a_longer_fence_when_code_contains_backticks() -> None:
    source = "const template = ```value```;\nconsole.log(template);"

    assert format_markdown_content(source) == "````javascript\n" + source + "\n````"


def test_markdown_export_fences_code_turns_but_not_prose() -> None:
    turns = [
        {"role": "assistant", "text": '{"ok": true}'},
        {"role": "user", "text": "Please explain the result."},
    ]

    exported = turns_to_markdown(turns, "Test", "codex", "session.jsonl")

    assert "```json\n{\"ok\": true}\n```" in exported
    assert "Please explain the result." in exported
    assert "```text\nPlease explain the result." not in exported


def test_tool_result_export_decodes_entities_and_fences_code() -> None:
    turns = [
        make_turn(
            role="tool_result",
            id="call-1",
            text="&lt;?php\necho &quot;done&quot;;\n?&gt;",
        )
    ]

    exported = turns_to_markdown(turns, "Test", "codex", "session.jsonl")

    assert "### TOOL_RESULT" in exported
    assert '```php\n<?php\necho "done";\n?>\n```' in exported
    assert "&lt;?php" not in exported


def test_tool_result_template_keeps_raw_source_and_prepares_markdown_source() -> None:
    turn = make_turn(
        role="tool_result",
        id="call-1",
        text="&lt;?php\necho &quot;done&quot;;\n?&gt;",
    )

    with app.app_context():
        rendered = str(
            app.jinja_env.get_template("partials/bubbles.html").module.render_bubbles([turn])
        )

    # HTML escaping is expected in the serialized template. In the browser,
    # textarea.value decodes one layer: raw remains entity text while the
    # Markdown source contains actual PHP characters inside its fence.
    assert '<textarea class="raw-src" hidden readonly>&amp;lt;?php' in rendered
    assert '<textarea class="md-src" hidden readonly>```php\n&lt;?php' in rendered
