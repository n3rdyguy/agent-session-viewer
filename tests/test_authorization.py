from __future__ import annotations

import json
from pathlib import Path

import pytest


def _jsonl(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "user", "message": {"content": "safe"}}) + "\n")
    return path


@pytest.mark.parametrize(
    ("agent", "relative"),
    [
        ("claude", Path("projects/project/session.jsonl")),
        ("codex", Path("sessions/2026/07/30/rollout-session.jsonl")),
        ("codex", Path("archived_sessions/rollout-archived.jsonl")),
    ],
)
def test_valid_file_session_layouts(client, agent_homes, agent: str, relative: Path) -> None:
    session = _jsonl(agent_homes[agent] / relative)
    response = client.get("/view", query_string={"agent": agent, "path": session})
    assert response.status_code == 200


def test_valid_grok_session_layout(client, agent_homes) -> None:
    session = agent_homes["grok"] / "sessions" / "project" / "session"
    _jsonl(session / "chat_history.jsonl")
    response = client.get("/view", query_string={"agent": "grok", "path": session})
    assert response.status_code == 200


@pytest.mark.parametrize("route", ["/view", "/export", "/raw"])
def test_routes_reject_unknown_or_missing_agent_before_path_lookup(client, route: str) -> None:
    missing = "Z:/definitely-not-a-session"
    assert client.get(route, query_string={"path": missing}).status_code == 400
    assert client.get(route, query_string={"agent": "other", "path": missing}).status_code == 400


def test_cross_agent_and_non_session_files_are_denied(client, agent_homes) -> None:
    claude = _jsonl(agent_homes["claude"] / "projects/project/session.jsonl")
    credential = agent_homes["claude"] / "credentials.json"
    credential.write_text('{"secret": true}')

    assert client.get("/raw", query_string={"agent": "codex", "path": claude}).status_code == 403
    assert (
        client.get("/raw", query_string={"agent": "claude", "path": credential}).status_code == 403
    )


@pytest.mark.parametrize(
    "relative",
    [
        Path("projects/session.jsonl"),
        Path("projects/project/nested/session.jsonl"),
        Path("projects/project/session.txt"),
    ],
)
def test_claude_requires_exact_recognized_layout(client, agent_homes, relative: Path) -> None:
    path = _jsonl(agent_homes["claude"] / relative)
    response = client.get("/view", query_string={"agent": "claude", "path": path})
    assert response.status_code == 403


def test_prefix_collision_and_traversal_are_denied(client, agent_homes, tmp_path) -> None:
    outside = _jsonl(tmp_path / "claude-other/projects/project/session.jsonl")
    traversal = agent_homes["claude"] / "projects" / ".." / ".." / outside.relative_to(tmp_path)
    for path in (outside, traversal):
        response = client.get("/raw", query_string={"agent": "claude", "path": path})
        assert response.status_code == 403


def test_symlink_escape_is_denied(client, agent_homes, tmp_path) -> None:
    outside = _jsonl(tmp_path / "outside/session.jsonl")
    link = agent_homes["claude"] / "projects" / "project"
    try:
        link.symlink_to(outside.parent, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    response = client.get(
        "/view",
        query_string={"agent": "claude", "path": link / outside.name},
    )
    assert response.status_code == 403


def test_raw_is_derived_from_session_and_prefers_grok_history(client, agent_homes) -> None:
    session = agent_homes["grok"] / "sessions/project/session"
    history = _jsonl(session / "chat_history.jsonl")
    (session / "summary.json").write_text('{"secret": "summary"}')
    response = client.get("/raw", query_string={"agent": "grok", "path": session})
    assert response.status_code == 200
    assert response.data == history.read_bytes()
    assert "filename=chat_history.jsonl" in response.headers["Content-Disposition"]


def test_media_requires_session_context_and_stays_within_it(client, agent_homes) -> None:
    session = agent_homes["grok"] / "sessions/project/session"
    _jsonl(session / "chat_history.jsonl")
    image = session / "image.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    sibling = agent_homes["grok"] / "sessions/project/other/secret.png"
    sibling.parent.mkdir()
    sibling.write_bytes(b"\x89PNG\r\n\x1a\nsecret")

    assert client.get("/media", query_string={"path": image}).status_code == 400
    valid = client.get(
        "/media",
        query_string={"agent": "grok", "session": session, "path": image},
    )
    assert valid.status_code == 200
    denied = client.get(
        "/media",
        query_string={"agent": "grok", "session": session, "path": sibling},
    )
    assert denied.status_code == 403


def test_svg_is_not_served_as_same_origin_media(client, agent_homes) -> None:
    session = agent_homes["grok"] / "sessions/project/session"
    _jsonl(session / "chat_history.jsonl")
    svg = session / "active.svg"
    svg.write_text("<svg><script>alert(1)</script></svg>")
    response = client.get(
        "/media",
        query_string={"agent": "grok", "session": session, "path": svg},
    )
    assert response.status_code == 403
