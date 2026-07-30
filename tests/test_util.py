from pathlib import Path

import pytest

from agent_session_viewer import util


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
