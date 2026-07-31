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
    assert "Updates stream" in html
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
