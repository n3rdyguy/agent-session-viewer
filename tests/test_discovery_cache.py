from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_session_viewer import discovery


@pytest.fixture(autouse=True)
def empty_discovery_cache():
    discovery.clear_discovery_cache()
    yield
    discovery.clear_discovery_cache()


def _claude_session(home: Path, records: int = 3) -> Path:
    path = home / "projects" / "project" / "session.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        "".join(
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": f"2026-07-30T08:00:{number:02d}Z",
                    "message": {"model": "test-model"},
                }
            )
            + "\n"
            for number in range(records)
        ),
        encoding="utf-8",
    )
    return path


def test_cache_hit_modification_and_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "claude"
    path = _claude_session(home)
    monkeypatch.setattr(discovery, "CLAUDE_HOME", home)
    original = discovery._scan_claude_card
    calls = 0

    def counted(scan_path: Path) -> dict:
        nonlocal calls
        calls += 1
        return original(scan_path)

    monkeypatch.setattr(discovery, "_scan_claude_card", counted)
    assert discovery.discover_claude()[0]["messages"] == 3
    assert discovery.discover_claude()[0]["messages"] == 3
    assert calls == 1

    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"type": "user", "timestamp": "later"}) + "\n")
    assert discovery.discover_claude()[0]["messages"] == 4
    assert calls == 2

    path.unlink()
    assert discovery.discover_claude() == []
    assert not any(key[0] == "claude" for key in discovery._DISCOVERY_CACHE)


def test_cache_index_stays_one_to_one_with_the_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The index makes eviction O(1); it must not leak entries or fall out of sync."""
    home = tmp_path / "claude"
    project = home / "projects" / "proj"
    project.mkdir(parents=True)
    paths = []
    for index in range(25):
        path = project / f"session-{index}.jsonl"
        path.write_text(
            json.dumps({"type": "user", "timestamp": "2026-07-30T08:00:00Z"}) + "\n",
            encoding="utf-8",
        )
        paths.append(path)
    monkeypatch.setattr(discovery, "CLAUDE_HOME", home)

    discovery.discover_claude()
    assert len(discovery._DISCOVERY_CACHE) == 25
    assert len(discovery._CACHE_INDEX) == 25

    # Rewriting files must replace entries rather than accumulate stale ones.
    for path in paths:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "user", "timestamp": "later"}) + "\n")
    discovery.discover_claude()
    assert len(discovery._DISCOVERY_CACHE) == 25
    assert len(discovery._CACHE_INDEX) == 25

    # Deleting files must clear both structures.
    for path in paths:
        path.unlink()
    discovery.discover_claude()
    assert not discovery._DISCOVERY_CACHE
    assert not discovery._CACHE_INDEX


def test_corrupt_cache_entry_is_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "claude"
    path = _claude_session(home)
    monkeypatch.setattr(discovery, "CLAUDE_HOME", home)
    key = discovery._file_key("claude", path)
    assert key is not None
    discovery._DISCOVERY_CACHE[key] = "damaged"  # type: ignore[assignment]

    assert discovery.discover_claude()[0]["model"] == "test-model"
    assert isinstance(discovery._DISCOVERY_CACHE[key], dict)


def test_concurrent_discovery_populates_one_complete_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "claude"
    _claude_session(home, records=20)
    monkeypatch.setattr(discovery, "CLAUDE_HOME", home)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: discovery.discover_claude(), range(32)))

    assert all(result[0]["messages"] == 20 for result in results)
    assert sum(key[0] == "claude" for key in discovery._DISCOVERY_CACHE) == 1


def test_claude_large_discovery_retains_only_bounded_edge_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "claude"
    _claude_session(home, records=10_000)
    monkeypatch.setattr(discovery, "CLAUDE_HOME", home)
    decoded_lines: list[int] = []
    original = discovery.decode_jsonl_record

    def counted(path: Path, line_number: int, line: str):
        decoded_lines.append(line_number)
        return original(path, line_number, line)

    monkeypatch.setattr(discovery, "decode_jsonl_record", counted)
    session = discovery.discover_claude()[0]

    assert session["messages"] == 10_000
    assert len(decoded_lines) == discovery._CLAUDE_EDGE_RECORDS * 2
    assert decoded_lines[:8] == list(range(1, 9))
    assert decoded_lines[-8:] == list(range(9_993, 10_001))


def test_codex_discovery_is_bounded_and_index_is_cached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "codex"
    rollout = home / "sessions" / "rollout-test.jsonl"
    rollout.parent.mkdir(parents=True)
    records = [
        {
            "type": "session_meta",
            "timestamp": "2026-07-30T08:00:00Z",
            "payload": {"id": "session-id", "cwd": "C:/project"},
        },
        *({"type": "event_msg", "payload": {"type": "noise"}} for _ in range(1_000)),
    ]
    rollout.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    index = home / "session_index.jsonl"
    index.write_text(
        json.dumps({"id": "session-id", "thread_name": "Cached title"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(discovery, "CODEX_HOME", home)
    decoded = 0
    original = discovery.decode_jsonl_record

    def counted(path: Path, line_number: int, line: str):
        nonlocal decoded
        decoded += 1
        return original(path, line_number, line)

    monkeypatch.setattr(discovery, "decode_jsonl_record", counted)
    first = discovery.discover_codex()
    first_decoded = decoded
    second = discovery.discover_codex()

    assert first[0]["title"] == "Cached title"
    # Records are counted across the whole file, but decoding stays bounded.
    assert first[0]["messages"] == 1_001
    assert second == first
    assert first_decoded == discovery._DISCOVERY_HEAD_RECORDS
    assert decoded == first_decoded
