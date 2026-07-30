from __future__ import annotations

import json
from pathlib import Path


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


def test_index_uses_temporary_agent_homes(client, agent_homes: dict[str, Path]) -> None:
    session = agent_homes["claude"] / "projects" / "fixture-project" / "session.jsonl"
    session.parent.mkdir()
    _write_claude_session(session)

    response = client.get("/")

    assert response.status_code == 200
    assert b"session.jsonl" in response.data


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

    response = client.get("/media", query_string={"path": image})

    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.data.startswith(b"\x89PNG")
