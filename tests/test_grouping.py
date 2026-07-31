from agent_session_viewer.grouping import (
    NO_PROJECT_NAME,
    group_by_project,
    normalize_cwd,
)


def _card(cwd: object, *, sid: str = "s", updated: object = "") -> dict:
    return {"agent": "codex", "id": sid, "path": f"{sid}.jsonl", "cwd": cwd, "updated": updated}


def test_normalize_cwd_unifies_slashes_trailing_and_case() -> None:
    expected = "c:/users/martin/proj"
    assert normalize_cwd("C:\\Users\\Martin\\proj") == expected
    assert normalize_cwd("C:/Users/Martin/proj") == expected
    assert normalize_cwd("c:/users/martin/proj/") == expected


def test_normalize_cwd_maps_placeholders_to_no_project() -> None:
    assert normalize_cwd("") == ""
    assert normalize_cwd("?") == ""
    assert normalize_cwd(None) == ""
    assert normalize_cwd(42) == ""


def test_group_by_project_merges_windows_and_posix_paths() -> None:
    groups = group_by_project(
        [
            _card("C:\\Users\\Martin\\proj", sid="a"),
            _card("C:/Users/Martin/proj", sid="b"),
            _card("c:/users/martin/proj/", sid="c"),
        ]
    )

    assert len(groups) == 1
    assert groups[0]["name"] == "proj"
    assert groups[0]["cwd"] == "C:\\Users\\Martin\\proj"  # first-seen spelling
    assert groups[0]["count"] == 3


def test_group_by_project_no_project_bucket() -> None:
    groups = group_by_project(
        [
            _card("", sid="a"),
            {"agent": "grok", "id": "b", "path": "b"},  # cwd key missing entirely
            _card("?", sid="c"),
        ]
    )

    assert len(groups) == 1
    assert groups[0]["key"] == ""
    assert groups[0]["name"] == NO_PROJECT_NAME
    assert groups[0]["cwd"] == ""
    assert groups[0]["count"] == 3


def test_group_by_project_orders_groups_by_latest_desc() -> None:
    groups = group_by_project(
        [
            _card("C:/old", sid="a", updated="2026-07-01T10:00:00Z"),
            # Unix seconds for 2026-07-30T10:00:00Z — newer than both ISO cards.
            _card("C:/new", sid="b", updated=1785405600),
            _card("C:/old", sid="c", updated="2026-07-02T10:00:00Z"),
        ]
    )

    assert [g["name"] for g in groups] == ["new", "old"]


def test_group_by_project_orders_sessions_desc_within_group() -> None:
    groups = group_by_project(
        [
            _card("C:/proj", sid="oldest", updated="2026-07-01T10:00:00Z"),
            _card("C:/proj", sid="newest", updated="2026-07-03T10:00:00Z"),
            _card("C:/proj", sid="middle", updated="2026-07-02T10:00:00Z"),
        ]
    )

    assert [s["id"] for s in groups[0]["sessions"]] == ["newest", "middle", "oldest"]


def test_group_by_project_drive_root_name() -> None:
    groups = group_by_project([_card("C:/")])

    assert groups[0]["name"] == "C:"
