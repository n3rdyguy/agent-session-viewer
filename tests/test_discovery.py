import importlib
import json
from pathlib import Path

import pytest

from agent_session_viewer import discovery
from agent_session_viewer.agents import codex
from agent_session_viewer.app import app


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Fix Codex session titles", "Fix Codex session titles"),
        ("  A useful headline!  ", "A useful headline!"),
        ("First line\nSecond line", "First line"),
        ("<user_action>Fix titles</user_action>", ""),
        ("Render {structured} output", ""),
        ("Use `inline code` here", ""),
        ("Emoji title 🚀", ""),
        ("", ""),
    ],
)
def test_safe_codex_headline(value: str, expected: str) -> None:
    assert discovery.safe_codex_headline(value) == expected


def _write_rollout(
    path: Path,
    session_id: str,
    headline: str,
    *,
    aborted: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "timestamp": "2026-07-30T08:00:00Z",
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": "C:/project"},
        },
        {
            "timestamp": "2026-07-30T08:00:01Z",
            "type": "turn_context",
            "payload": {"model": "gpt-test", "cwd": "C:/project"},
        },
        {
            "timestamp": "2026-07-30T08:00:02Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": headline},
        },
    ]
    if aborted:
        records.append(
            {
                "timestamp": "2026-07-30T08:00:03Z",
                "type": "event_msg",
                "payload": {"type": "turn_aborted", "turn_id": "turn-1"},
            }
        )
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def test_discover_codex_shows_index_title_and_safe_headline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "019f9edd-ea9c-7741-ad03-59daedd955a2"
    codex_home = tmp_path / "codex"
    rollout = (
        codex_home
        / "sessions"
        / f"rollout-2026-07-30T08-00-00-{session_id}.jsonl"
    )
    _write_rollout(
        rollout,
        session_id,
        "Add safe session headlines",
        aborted=True,
    )
    (codex_home / "session_index.jsonl").write_text(
        json.dumps(
            {
                "id": session_id,
                "thread_name": "Session title",
                "updated_at": "2026-07-30T09:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(discovery, "CODEX_HOME", codex_home)

    sessions = discovery.discover_codex()

    assert len(sessions) == 1
    assert sessions[0]["title"] == "Session title"
    assert sessions[0]["headline"] == "Add safe session headlines"
    assert sessions[0]["aborted"] is False


def test_discover_codex_rejects_unsafe_title_and_headline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "019f9edd-ea9c-7741-ad03-59daedd955a2"
    codex_home = tmp_path / "codex"
    rollout = (
        codex_home
        / "sessions"
        / f"rollout-2026-07-30T08-00-00-{session_id}.jsonl"
    )
    _write_rollout(rollout, session_id, "<user_action>hidden</user_action>")
    (codex_home / "session_index.jsonl").write_text(
        json.dumps(
            {
                "id": session_id,
                "thread_name": "<unsafe>",
                "updated_at": "",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(discovery, "CODEX_HOME", codex_home)

    session = discovery.discover_codex()[0]

    assert session["title"] == rollout.name
    assert session["headline"] == ""


def test_codex_summary_uses_only_safe_title_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rollout = tmp_path / "rollout-test.jsonl"
    _write_rollout(
        rollout,
        "codex-test",
        "Readable headline",
        aborted=True,
    )
    monkeypatch.setattr(
        codex,
        "load_codex_session_index",
        lambda: {
            "codex-test": {
                "thread_name": "<unsafe-title>",
                "updated_at": "",
            }
        },
    )

    summary = codex.codex_scan_session(rollout)["summary"]

    assert summary["title"] == "Readable headline"
    assert summary["session_summary"] == "Readable headline"
    assert summary["aborted"] is False


def test_aborted_badge_uses_only_the_message_selected_for_headline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "019f9edd-ea9c-7741-ad03-59daedd955a2"
    codex_home = tmp_path / "codex"
    rollout = (
        codex_home
        / "sessions"
        / f"rollout-2026-07-30T08-00-00-{session_id}.jsonl"
    )
    _write_rollout(
        rollout,
        session_id,
        "Interrupted request\nturn_aborted",
    )
    monkeypatch.setattr(discovery, "CODEX_HOME", codex_home)
    monkeypatch.setattr(codex, "load_codex_session_index", lambda: {})

    discovered = discovery.discover_codex()[0]
    summary = codex.codex_scan_session(rollout)["summary"]

    assert discovered["headline"] == "Interrupted request"
    assert discovered["aborted"] is True
    assert summary["session_summary"] == "Interrupted request"
    assert summary["aborted"] is True


def test_codex_summary_skips_unsafe_user_wrapper_before_headline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rollout = tmp_path / "rollout-test.jsonl"
    records = [
        {
            "timestamp": "2026-07-30T08:00:00Z",
            "type": "session_meta",
            "payload": {"id": "codex-test", "cwd": "C:/project"},
        },
        {
            "timestamp": "2026-07-30T08:00:01Z",
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": "<user_action>internal context</user_action>",
            },
        },
        {
            "timestamp": "2026-07-30T08:00:02Z",
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": "Show the real headline",
            },
        },
    ]
    rollout.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    monkeypatch.setattr(codex, "load_codex_session_index", lambda: {})

    summary = codex.codex_scan_session(rollout)["summary"]

    assert summary["title"] == "Show the real headline"
    assert summary["session_summary"] == "Show the real headline"


def test_session_list_places_aborted_badge_before_title() -> None:
    with app.test_request_context("/"):
        rendered = app.jinja_env.get_template("list.html").render(
            title="Sessions",
            sessions=[
                {
                    "agent": "codex",
                    "id": "codex-test",
                    "path": "rollout-test.jsonl",
                    "title": "Safe title",
                    "headline": "A useful headline",
                    "aborted": True,
                    "updated": "",
                    "created": "",
                    "messages": 3,
                    "model": "gpt-test",
                    "cwd": "C:/project",
                }
            ],
            agent="codex",
            q="",
            grok_path="",
            claude_path="",
            codex_path="",
        )

    assert rendered.index('<span class="badge aborted">aborted</span>') < rendered.index(
        '<strong title="Safe title">Safe title</strong>'
    )
    assert "session-headline" not in rendered
    assert "A useful headline</div>" not in rendered
def test_session_list_decodes_html_entities_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_module = importlib.import_module("agent_session_viewer.app")
    monkeypatch.setattr(
        app_module,
        "all_sessions",
        lambda _agent: [
            {
                "agent": "codex",
                "id": "codex-test",
                "path": "rollout-test.jsonl",
                "title": "Research &amp; Development",
                "updated": "",
                "created": "",
                "messages": 1,
                "model": "GPT &quot;Test&quot;",
                "cwd": "C:/A&amp;B",
            }
        ],
    )

    response = app.test_client().get("/")
    rendered = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Research &amp; Development" in rendered
    assert "Research &amp;amp; Development" not in rendered
    assert "GPT &#34;Test&#34;" in rendered
    assert "C:/A&amp;B" in rendered
