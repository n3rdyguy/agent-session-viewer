from datetime import datetime
from pathlib import Path

import pytest

from agent_session_viewer import util

# 2026-07-30T10:00:00Z
_EPOCH = 1785405600.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-07-30T10:00:00Z", _EPOCH),
        ("2026-07-30T12:00:00+02:00", _EPOCH),
        (1785405600, _EPOCH),
        (1785405600000.0, _EPOCH),  # ms heuristic, mirrors human_time
        ("1785405600", _EPOCH),
        ("", 0.0),
        (None, 0.0),
        (True, 0.0),
        ("garbage", 0.0),
    ],
)
def test_epoch_seconds(value: object, expected: float) -> None:
    assert util.epoch_seconds(value) == expected


def test_epoch_seconds_naive_iso_uses_local_time() -> None:
    assert util.epoch_seconds("2026-07-30T10:00:00") > 0.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (_EPOCH - 30, "just now"),
        (_EPOCH + 500, "just now"),  # clock skew
        (_EPOCH - 300, "5m ago"),
        (_EPOCH - 3 * 3600, "3h ago"),
        (_EPOCH - 2 * 86400, "2d ago"),
        ("", ""),
        (None, ""),
        ("garbage", ""),
    ],
)
def test_rel_time(value: object, expected: str) -> None:
    assert util.rel_time(value, now=_EPOCH) == expected


def test_rel_time_beyond_a_week_shows_local_date() -> None:
    ts = _EPOCH - 30 * 86400
    assert util.rel_time(ts, now=_EPOCH) == datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def test_rel_time_accepts_iso_strings() -> None:
    assert util.rel_time("2026-07-30T08:00:00Z", now=_EPOCH) == "2h ago"


def test_decode_view_data_decodes_display_values_but_preserves_sources() -> None:
    source = {
        "title": "Research &amp; Development",
        "nested": [{"label": "A &lt; B", "text": "Keep &amp;lt; once"}],
        "path": "C:/Research&amp;Development/session.jsonl",
        "url": "https://example.test/?a=1&amp;b=2",
        "html": "Already &amp; escaped",
    }

    decoded = util.decode_view_data(source)

    assert decoded["title"] == "Research & Development"
    assert decoded["nested"][0]["label"] == "A < B"
    assert decoded["nested"][0]["text"] == "Keep &amp;lt; once"
    assert decoded["path"] == source["path"]
    assert decoded["url"] == source["url"]
    assert decoded["html"] == source["html"]


def test_decode_html_entities_decodes_nested_layers() -> None:
    assert util.decode_html_entities("&amp;amp;quot;done&amp;amp;quot;") == '"done"'


@pytest.fixture
def agent_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    allowed = tmp_path / "grok"
    outside = tmp_path / "grok-other"
    allowed.mkdir()
    outside.mkdir()
    monkeypatch.setattr(util, "GROK_HOME", allowed)
    monkeypatch.setattr(util, "CLAUDE_HOME", tmp_path / "claude")
    monkeypatch.setattr(util, "CODEX_HOME", tmp_path / "codex")
    return allowed, outside


def test_path_allowed_accepts_root_and_descendants(agent_roots: tuple[Path, Path]) -> None:
    allowed, _ = agent_roots

    assert util.path_allowed(allowed)
    assert util.path_allowed(allowed / "sessions" / "session.jsonl")


def test_path_allowed_rejects_prefix_collision_and_parent_traversal(
    agent_roots: tuple[Path, Path],
) -> None:
    allowed, outside = agent_roots

    assert not util.path_allowed(outside / "session.jsonl")
    assert not util.path_allowed(allowed / ".." / outside.name / "session.jsonl")


def test_path_allowed_rejects_symlink_escape(
    agent_roots: tuple[Path, Path],
) -> None:
    allowed, outside = agent_roots
    link = allowed / "outside-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    assert not util.path_allowed(link / "session.jsonl")
