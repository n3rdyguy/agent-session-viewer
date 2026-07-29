from pathlib import Path

import pytest

from agent_session_viewer import util


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
