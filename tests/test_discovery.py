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
    rollout = codex_home / "sessions" / f"rollout-2026-07-30T08-00-00-{session_id}.jsonl"
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
    rollout = codex_home / "sessions" / f"rollout-2026-07-30T08-00-00-{session_id}.jsonl"
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
    rollout = codex_home / "sessions" / f"rollout-2026-07-30T08-00-00-{session_id}.jsonl"
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


def _write_claude_records(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Examine this project", "Examine this project"),
        ("  spaced   out  ", "spaced out"),
        ("<system-reminder>hidden</system-reminder>", ""),
        ("\n\nSecond line wins", "Second line wins"),
        ("", ""),
        (None, ""),
    ],
)
def test_safe_claude_headline(value: object, expected: str) -> None:
    assert discovery.safe_claude_headline(value) == expected


@pytest.mark.parametrize(
    ("records", "expected"),
    [
        ([{"type": "ai-title", "aiTitle": "Generated title"}], "Generated title"),
        ([{"type": "custom-title", "customTitle": "Custom title"}], "Custom title"),
        ([{"type": "last-prompt", "lastPrompt": "Prompt title"}], "Prompt title"),
        (
            [
                {"type": "custom-title", "customTitle": "Custom title"},
                {"type": "ai-title", "aiTitle": "Generated title"},
            ],
            "Generated title",
        ),
    ],
)
def test_claude_card_title_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    records: list[dict],
    expected: str,
) -> None:
    home = tmp_path / "claude"
    _write_claude_records(home / "projects" / "project" / "session.jsonl", records)
    monkeypatch.setattr(discovery, "CLAUDE_HOME", home)
    discovery.clear_discovery_cache()

    assert discovery.discover_claude()[0]["title"] == expected


def test_claude_card_prefers_recorded_cwd_over_encoded_folder_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "claude"
    encoded = "C--Users-Martin-projects-agent-session-viewer"
    _write_claude_records(
        home / "projects" / encoded / "session.jsonl",
        [
            {
                "type": "user",
                "timestamp": "2026-07-30T08:00:00Z",
                "cwd": r"C:\Users\Martin\projects\agent-session-viewer",
                "message": {"role": "user", "content": [{"type": "text", "text": "Hi there"}]},
            }
        ],
    )
    monkeypatch.setattr(discovery, "CLAUDE_HOME", home)
    discovery.clear_discovery_cache()

    card = discovery.discover_claude()[0]

    # The encoded folder name cannot round-trip drive letters, separators, or the
    # hyphens inside "agent-session-viewer".
    assert card["cwd"] == r"C:\Users\Martin\projects\agent-session-viewer"
    assert card["headline"] == "Hi there"


def test_claude_card_falls_back_to_encoded_name_without_a_recorded_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "claude"
    _write_claude_records(
        home / "projects" / "home--user--code" / "session.jsonl",
        [{"type": "ai-title", "aiTitle": "No cwd here"}],
    )
    monkeypatch.setattr(discovery, "CLAUDE_HOME", home)
    discovery.clear_discovery_cache()

    assert discovery.discover_claude()[0]["cwd"] == "home/user/code"


def test_claude_title_is_found_outside_the_bounded_edge_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Title records are rewritten mid-session and fall outside first/last-N."""
    home = tmp_path / "claude"
    filler = [
        {
            "type": "user",
            "timestamp": "2026-07-30T08:00:00Z",
            "message": {"role": "user", "content": [{"type": "text", "text": "filler"}]},
        }
    ] * 40
    records = [
        *filler,
        {"type": "ai-title", "aiTitle": "Buried title"},
        *filler,
    ]
    _write_claude_records(home / "projects" / "project" / "session.jsonl", records)
    monkeypatch.setattr(discovery, "CLAUDE_HOME", home)
    discovery.clear_discovery_cache()

    card = discovery.discover_claude()[0]

    assert card["title"] == "Buried title"
    assert card["messages"] == 81
