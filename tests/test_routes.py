from __future__ import annotations

import importlib
import json
import re
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest


def _write_claude_session(path: Path) -> None:
    records = [
        {
            "type": "user",
            "timestamp": "2026-07-30T08:00:00Z",
            "message": {"role": "user", "content": "Hello from fixture"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-07-30T08:00:01Z",
            "message": {
                "role": "assistant",
                "model": "claude-fixture",
                "content": "Fixture response",
            },
        },
    ]
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


FIXTURES = Path(__file__).parent / "fixtures"


def _install_claude_fixture(agent_homes: dict[str, Path]) -> Path:
    """Copy the rich Claude fixture, including its subagent file, into a temp home."""
    source = FIXTURES / "claude" / "session-fixture.jsonl"
    project = agent_homes["claude"] / "projects" / "fixture-project"
    subagents = project / "session-fixture" / "subagents"
    subagents.mkdir(parents=True)
    session = project / "session-fixture.jsonl"
    session.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    agent_source = source.parent / "session-fixture" / "subagents" / "agent-fixture1.jsonl"
    (subagents / "agent-fixture1.jsonl").write_text(
        agent_source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return session


def test_claude_view_renders_summary_tokens_and_artifacts(
    client, agent_homes: dict[str, Path]
) -> None:
    session = _install_claude_fixture(agent_homes)

    response = client.get("/view", query_string={"agent": "claude", "path": session})
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Fixture session title" in html
    assert "Session summary" in html
    assert "Estimated token usage" in html
    assert "claude-opus-5" in html
    assert "acceptEdits" in html
    # Artifacts, file edits, and the events tab all render.
    assert "Available skills" in html
    assert "parser.py" in html
    # Claude has no updates.jsonl; its second tab is the transcript event timeline.
    assert "Events timeline" in html
    assert "Updates stream" not in html
    assert "updates.jsonl" not in html
    # Subagent turns are inlined and tagged rather than hidden.
    assert "subagent: Explore" in html


def test_claude_export_includes_the_session_header(client, agent_homes: dict[str, Path]) -> None:
    session = _install_claude_fixture(agent_homes)

    response = client.get("/export", query_string={"agent": "claude", "path": session})
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "**Model:** claude-opus-5" in body
    assert "**Permission mode:** acceptEdits" in body
    assert "### Estimated token usage" in body


def test_claude_list_card_shows_real_title_and_cwd(client, agent_homes: dict[str, Path]) -> None:
    _install_claude_fixture(agent_homes)

    html = client.get("/").get_data(as_text=True)

    assert "Fixture session title" in html
    assert "C:/fixture" in html


def test_index_uses_temporary_agent_homes(client, agent_homes: dict[str, Path]) -> None:
    session = agent_homes["claude"] / "projects" / "fixture-project" / "session.jsonl"
    session.parent.mkdir()
    _write_claude_session(session)

    response = client.get("/")

    assert response.status_code == 200
    assert b"session.jsonl" in response.data


def test_count_line_is_a_live_region(client, agent_homes: dict[str, Path]) -> None:
    """list.js rewrites the count line while filtering; screen readers need the announcement."""
    session = agent_homes["claude"] / "projects" / "fixture-project" / "session.jsonl"
    session.parent.mkdir()
    _write_claude_session(session)

    html = client.get("/").get_data(as_text=True)

    match = re.search(r"<p[^>]*id=\"count-line\"[^>]*>", html)
    assert match is not None, "count line missing from the session list"
    assert 'role="status"' in match.group(0)


def test_search_query_round_trips_through_agent_filters(client) -> None:
    query = 'A&B #tag="✓"'
    response = client.get("/", query_string={"q": query})

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    toolbar = re.search(r'<div class="filters">(.*?)</div>', html, re.DOTALL)
    assert toolbar is not None
    links = re.findall(r'href="([^"]+)"', toolbar.group(1))
    assert len(links) == 4
    for href in links:
        parsed = parse_qs(urlparse(unescape(href)).query, keep_blank_values=True)
        assert parsed["q"] == [query]


def test_index_groups_sessions_by_project(client, monkeypatch: pytest.MonkeyPatch) -> None:
    app_module = importlib.import_module("agent_session_viewer.app")
    monkeypatch.setattr(
        app_module,
        "all_sessions",
        lambda _agent: [
            {
                "agent": "claude",
                "id": "new-1",
                "path": "new-1.jsonl",
                "title": "Newest session",
                "cwd": "C:/proj-new",
                "updated": "2026-07-30T10:00:00Z",
                "messages": 4,
            },
            {
                "agent": "claude",
                "id": "new-2",
                "path": "new-2.jsonl",
                "title": "Second session",
                "cwd": "C:\\proj-new",
                "updated": "2026-07-29T10:00:00Z",
                "messages": 2,
            },
            {
                "agent": "grok",
                "id": "old-1",
                "path": "old-1",
                "title": "Older session",
                "cwd": "C:/proj-old",
                "updated": "2026-07-20T10:00:00Z",
                "messages": 1,
            },
            {
                "agent": "codex",
                "id": "stray",
                "path": "stray.jsonl",
                "title": "Stray session",
                "cwd": "?",
                "updated": "2026-07-25T10:00:00Z",
                "messages": None,
            },
        ],
    )

    html = client.get("/").get_data(as_text=True)

    assert html.count('class="project-group"') == 3
    assert "4 sessions in" in html
    assert "3 projects" in html
    assert "(no project)" in html
    # Windows and POSIX spellings of proj-new merge into one group, newest first.
    assert 'data-project-key="c:/proj-new"' in html
    assert html.index('data-project-key="c:/proj-new"') < html.index(
        'data-project-key="c:/proj-old"'
    )
    # Codex's unknown message count no longer renders a "- msgs" placeholder.
    assert "- msgs" not in html
    # Groups start collapsed; the Expand toggle is the way to open them all.
    assert " open>" not in html
    assert 'id="expand-toggle"' in html
    # Sort controls render, and rows carry the keys the client sorter uses.
    assert 'id="sort-field"' in html
    assert 'id="sort-dir"' in html
    assert '<option value="name">Name</option>' in html
    assert 'data-name="proj-new"' in html
    assert 'data-title="newest session"' in html
    # Every project summary carries a pin toggle.
    assert html.count('class="pin-btn"') == 3
    # Each summary shows a two-letter badge per agent detected in the project.
    assert '<span class="badge mini claude" title="claude">cl</span>' in html
    assert '<span class="badge mini codex" title="codex">co</span>' in html
    assert '<span class="badge mini grok" title="grok">gr</span>' in html
    assert html.count("badge mini") == 3  # one per single-agent group
    assert 'data-updated="1785405600.0"' in html  # 2026-07-30T10:00:00Z
    assert 'data-created="1785405600.0"' in html  # falls back to updated


def test_index_opens_groups_for_server_search_results(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_module = importlib.import_module("agent_session_viewer.app")
    monkeypatch.setattr(
        app_module,
        "all_sessions",
        lambda _agent: [
            {
                "agent": "claude",
                "id": "new-1",
                "path": "new-1.jsonl",
                "title": "Newest session",
                "cwd": "C:/proj-new",
                "updated": "2026-07-30T10:00:00Z",
            },
            {
                "agent": "grok",
                "id": "old-1",
                "path": "old-1",
                "title": "Older session",
                "cwd": "C:/proj-old",
                "updated": "2026-07-20T10:00:00Z",
            },
        ],
    )

    html = client.get("/", query_string={"q": "session"}).get_data(as_text=True)

    # Matches must stay visible without JS, so search results render expanded.
    assert html.count(" open>") == 2


def test_index_loads_list_script(client) -> None:
    response = client.get("/")

    assert b"/static/list.js" in response.data


def test_view_characterization(client, agent_homes: dict[str, Path]) -> None:
    session = agent_homes["claude"] / "projects" / "fixture-project" / "session.jsonl"
    session.parent.mkdir()
    _write_claude_session(session)

    response = client.get("/view", query_string={"agent": "claude", "path": session})

    assert response.status_code == 200
    assert b"Hello from fixture" in response.data
    assert b"Fixture response" in response.data
    assert b"cdn.jsdelivr.net" not in response.data
    assert b"/static/vendor/marked/marked.min.js" in response.data
    assert b"/static/vendor/dompurify/purify.min.js" in response.data


def test_html_responses_have_security_headers(client) -> None:
    response = client.get("/")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "connect-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "style-src 'self'" in csp
    # No inline style attributes remain in the templates; keep it that way.
    assert "'unsafe-inline'" not in csp
    assert "style=" not in response.get_data(as_text=True)


def test_view_markup_has_no_inline_styles(client, agent_homes: dict[str, Path]) -> None:
    """style-src 'self' blocks style attributes, so the templates must not emit any."""
    session = _install_claude_fixture(agent_homes)

    html = client.get("/view", query_string={"agent": "claude", "path": str(session)}).get_data(
        as_text=True
    )

    assert "data-pct=" in html, "token bar should ship percentages as data attributes"
    assert "style=" not in html


def test_export_characterization(client, agent_homes: dict[str, Path]) -> None:
    session = agent_homes["claude"] / "projects" / "fixture-project" / "session.jsonl"
    session.parent.mkdir()
    _write_claude_session(session)

    response = client.get("/export", query_string={"agent": "claude", "path": session})

    assert response.status_code == 200
    assert response.mimetype == "text/markdown"
    assert response.headers["Content-Disposition"].startswith("attachment;")
    assert b"Hello from fixture" in response.data


def test_raw_characterization(client, agent_homes: dict[str, Path]) -> None:
    session = agent_homes["claude"] / "projects" / "fixture-project" / "session.jsonl"
    session.parent.mkdir()
    _write_claude_session(session)

    response = client.get(
        "/raw",
        query_string={"agent": "claude", "path": session},
    )

    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    assert b"Hello from fixture" in response.data


def _install_grok_fixture(agent_homes: dict[str, Path]) -> Path:
    """Lay out the Grok fixture as <sessions>/<project>/<session>/chat_history.jsonl."""
    session = agent_homes["grok"] / "sessions" / "fixture-project" / "fixture-session"
    session.mkdir(parents=True)
    (session / "chat_history.jsonl").write_text(
        (FIXTURES / "grok" / "chat_history.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return session


def _install_codex_fixture(agent_homes: dict[str, Path]) -> Path:
    """Codex rollouts are named files anywhere under sessions/."""
    rollout = agent_homes["codex"] / "sessions" / "rollout-2026-07-30T08-00-00-codex-test.jsonl"
    rollout.write_text(
        (FIXTURES / "codex" / "rollout-test.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return rollout


def test_grok_view_export_and_raw_render_a_real_session(
    client, agent_homes: dict[str, Path]
) -> None:
    """Route-level coverage for Grok, which previously only reached these paths via parsers."""
    session = _install_grok_fixture(agent_homes)
    query = {"agent": "grok", "path": str(session)}

    view = client.get("/view", query_string=query)
    assert view.status_code == 200
    assert "Inspect the parser." in view.get_data(as_text=True)

    export = client.get("/export", query_string=query)
    assert export.status_code == 200
    assert export.mimetype == "text/markdown"
    assert export.headers["Content-Disposition"].startswith("attachment;")
    assert b"Inspect the parser." in export.data

    raw = client.get("/raw", query_string=query)
    assert raw.status_code == 200
    assert raw.mimetype == "text/plain"
    # Grok raw resolves to the history file inside the session directory.
    assert raw.data == (session / "chat_history.jsonl").read_bytes()


def test_codex_view_export_and_raw_render_a_real_session(
    client, agent_homes: dict[str, Path]
) -> None:
    """Route-level coverage for Codex, which previously only reached these paths via parsers."""
    rollout = _install_codex_fixture(agent_homes)
    query = {"agent": "codex", "path": str(rollout)}

    view = client.get("/view", query_string=query)
    assert view.status_code == 200
    html = view.get_data(as_text=True)
    assert "Inspect the parser." in html
    assert "The parser looks good." in html

    export = client.get("/export", query_string=query)
    assert export.status_code == 200
    assert export.mimetype == "text/markdown"
    assert export.headers["Content-Disposition"].startswith("attachment;")
    assert b"Inspect the parser." in export.data

    raw = client.get("/raw", query_string=query)
    assert raw.status_code == 200
    assert raw.mimetype == "text/plain"
    assert raw.data == rollout.read_bytes()


def test_codex_media_serves_generated_images(client, agent_homes: dict[str, Path]) -> None:
    """Codex media resolves against generated_images as well as the rollout's own directory."""
    rollout = _install_codex_fixture(agent_homes)
    generated = agent_homes["codex"] / "generated_images"
    generated.mkdir()
    image = generated / "diagram.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")

    response = client.get(
        "/media",
        query_string={"agent": "codex", "session": str(rollout), "path": str(image)},
    )

    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.data.startswith(b"\x89PNG")

    # An image outside both authorized roots stays denied.
    outside = agent_homes["codex"] / "elsewhere.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\nnope")
    denied = client.get(
        "/media",
        query_string={"agent": "codex", "session": str(rollout), "path": str(outside)},
    )
    assert denied.status_code == 403


def test_media_characterization(client, agent_homes: dict[str, Path]) -> None:
    image = agent_homes["grok"] / "sessions" / "project" / "session" / "image.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")

    session = image.parent
    response = client.get(
        "/media",
        query_string={"agent": "grok", "session": session, "path": image},
    )

    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.data.startswith(b"\x89PNG")
