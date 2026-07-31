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
        (
            "from pathlib import Path\n\ndef load(path: Path):\n    return path.read_text()",
            "python",
        ),
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


def test_decodes_html_entities_in_prose() -> None:
    source = "Use &lt;name&gt; as a placeholder and keep AT&amp;T unchanged."

    assert format_markdown_content(source) == "Use <name> as a placeholder and keep AT&T unchanged."


def test_decodes_nested_html_entities_in_tool_result() -> None:
    turn = make_turn(
        role="tool_result",
        text=(
            "{&amp;quot;html&amp;quot;:"
            "&amp;quot;&amp;lt;div&amp;gt;Done&amp;lt;/div&amp;gt;&amp;quot;}"
        ),
    )

    with app.app_context():
        rendered = str(
            app.jinja_env.get_template("partials/bubbles.html").module.render_bubbles([turn])
        )

    assert "{&#34;html&#34;:&#34;&lt;div&gt;Done&lt;/div&gt;&#34;}" in rendered
    assert "&amp;quot;" not in rendered
    assert "&amp;gt;" not in rendered


def test_markdown_hint_prevents_xml_fencing_of_system_instructions() -> None:
    source = (
        "<INSTRUCTIONS>\n# Project rules\n\n- Run tests\n- Keep changes focused\n</INSTRUCTIONS>"
    )

    assert detect_code_language(source) == "xml"
    assert format_markdown_content(source, assume_markdown=True) == source


def test_preserves_prose_around_an_existing_fence() -> None:
    source = "Try this:\n\n```python\nprint('hello')\n```\n\nThen continue."

    assert format_markdown_content(source) == source


def test_uses_a_longer_fence_when_code_contains_backticks() -> None:
    source = "const template = ```value```;\nconsole.log(template);"

    assert format_markdown_content(source) == "````javascript\n" + source + "\n````"


def test_shell_output_with_embedded_readme_fences_is_outer_fenced() -> None:
    """Regression: Codex shell dumps of source + README broke Markdown mode.

    call_Rgjj24g8CrcpLL42i54Cwp1q-style results start with Exit code metadata and
    embed README fence lines; without an outer fence, marked treats # comments
    and ## headings as live Markdown.
    """
    source = (
        "Exit code: 0\n"
        "Wall time: 4 seconds\n"
        "Total output lines: 40\n"
        "Output:\n"
        '"""Flask routes and local development server."""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "def run() -> None:\n"
        "    print('ok')\n"
        "\n"
        "# Agent Session Viewer\n"
        "\n"
        "## Installation\n"
        "\n"
        "```bash\n"
        "uv sync\n"
        "```\n"
        "\n"
        "A typical Grok session directory includes:\n"
        "\n"
        "```\n"
    )

    formatted = format_markdown_content(source)

    assert formatted.startswith("```")
    assert formatted.endswith("```")
    # Outer fence must be longer than the embedded ``` lines so they stay literal.
    assert (
        formatted.startswith("````")
        or formatted.startswith("```text")
        or formatted.startswith("```python")
    )
    assert "Exit code: 0" in formatted
    assert "## Installation" in formatted
    # The whole blob is one fenced block, not re-parsed as nested Markdown.
    assert formatted.count("\n") >= source.count("\n")
    body = formatted.strip("`").split("\n", 1)[1].rsplit("\n", 1)[0]
    # After stripping outer fence lines, body matches source (minus possible rstrip).
    assert body.rstrip() == source.rstrip()


def test_unbalanced_internal_fence_is_outer_fenced() -> None:
    source = "Captured log:\n\n```text\nhello\n"  # opener never closed

    formatted = format_markdown_content(source)

    assert formatted.startswith("```")
    assert formatted.rstrip().endswith("```")
    assert "```text\nhello" in formatted


def test_balanced_markdown_with_fences_still_preserved() -> None:
    source = "# Title\n\nSee:\n\n```python\nprint(1)\n```\n\nDone."

    assert format_markdown_content(source) == source


def test_markdown_export_fences_code_turns_but_not_prose() -> None:
    turns = [
        {"role": "assistant", "text": '{"ok": true}'},
        {"role": "user", "text": "Please explain the result."},
    ]

    exported = turns_to_markdown(turns, "Test", "codex", "session.jsonl")

    assert '```json\n{"ok": true}\n```' in exported
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

    # HTML escaping is expected in serialized markup; textarea.value contains
    # the decoded source in the browser.
    assert '<textarea class="raw-src" hidden readonly>&lt;?php' in rendered
    assert '<textarea class="md-src" hidden readonly>```php\n&lt;?php' in rendered


def test_plain_bubble_decodes_entities_without_interpreting_decoded_tags() -> None:
    turn = make_turn(
        role="assistant",
        text="Use &lt;strong&gt;care&lt;/strong&gt; &amp; stay safe.",
    )

    with app.app_context():
        rendered = str(
            app.jinja_env.get_template("partials/bubbles.html").module.render_bubbles([turn])
        )

    assert "Use &lt;strong&gt;care&lt;/strong&gt; &amp; stay safe." in rendered
    assert "Use &amp;lt;strong&amp;gt;" not in rendered


def test_system_template_treats_tag_wrapped_instructions_as_markdown() -> None:
    source = "<INSTRUCTIONS>\n# Project rules\n\n- Run tests\n</INSTRUCTIONS>"
    turn = make_turn(role="system", id="#1", text=source)

    with app.app_context():
        rendered = str(
            app.jinja_env.get_template("partials/bubbles.html").module.render_bubbles([turn])
        )

    assert (
        '<textarea class="md-src" hidden readonly>&lt;INSTRUCTIONS&gt;\n# Project rules' in rendered
    )
    assert "```xml" not in rendered


def test_markdown_artifact_hint_bypasses_code_detection() -> None:
    source = "<document>\n# Notes\n\n- First item\n</document>"

    with app.app_context():
        rendered = str(
            app.jinja_env.get_template("partials/bubbles.html").module.render_foldable(
                source, "", True
            )
        )

    assert '<textarea class="md-src" hidden readonly>&lt;document&gt;\n# Notes' in rendered
    assert "```xml" not in rendered


@pytest.mark.parametrize(
    ("title", "kind"),
    [
        ("AGENTS.md", ""),
        ("README.markdown", "document"),
        ("Instructions", "markdown"),
        ("Notes", "text/markdown"),
    ],
)
def test_markdown_artifact_metadata_enables_markdown_source(
    title: str,
    kind: str,
) -> None:
    source = "<document>\n# Notes\n\n- First item\n</document>"

    with app.test_request_context("/view"):
        rendered = app.jinja_env.get_template("view.html").render(
            title="Test",
            agent="codex",
            path="session.jsonl",
            turns=[],
            summary=None,
            resources=None,
            artifacts=[
                {
                    "id": "doc-1",
                    "title": title,
                    "kind": kind,
                    "text": source,
                }
            ],
            hunks=None,
            terminal_logs=None,
            recaps=None,
            updates=None,
        )

    assert '<textarea class="md-src" hidden readonly>&lt;document&gt;\n# Notes' in rendered
    assert "```xml" not in rendered


def test_system_export_preserves_markdown_in_tag_wrapped_instructions() -> None:
    source = "<INSTRUCTIONS>\n# Project rules\n\n- Run tests\n</INSTRUCTIONS>"
    turns = [make_turn(role="system", id="#1", text=source)]

    exported = turns_to_markdown(turns, "Test", "codex", "session.jsonl")

    assert source in exported
    assert "```xml" not in exported
