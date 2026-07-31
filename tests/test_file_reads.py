"""Unit tests for shell file-read plan parsing and output splitting."""

from __future__ import annotations

from pathlib import Path

from agent_session_viewer.agents.codex import get_codex_conversation
from agent_session_viewer.file_reads import (
    build_file_artifacts,
    extract_shell_command,
    file_artifacts_for_tool_result,
    format_file_read_label,
    parse_file_read_plan,
    strip_shell_output_meta,
)
from agent_session_viewer.session import turns_to_markdown
from agent_session_viewer.turns import make_turn

FIXTURES = Path(__file__).parent / "fixtures" / "codex"


def test_parse_single_sed() -> None:
    plan = parse_file_read_plan("sed -n '1,240p' agent_session_viewer/app.py")
    assert plan is not None
    assert len(plan) == 1
    assert plan[0]["path"] == "agent_session_viewer/app.py"
    assert plan[0]["start"] == 1
    assert plan[0]["end"] == 240
    assert plan[0]["expected_lines"] == 240
    assert format_file_read_label(plan[0]) == "agent_session_viewer/app.py · lines 1–240"


def test_parse_multi_sed_chain() -> None:
    cmd = (
        "sed -n '1,240p' agent_session_viewer/app.py; "
        "sed -n '1,240p' agent_session_viewer/session.py; "
        "sed -n '1,360p' agent_session_viewer/util.py; "
        "sed -n '1,120p' agent_session_viewer/templates/list.html; "
        "sed -n '1,55p' agent_session_viewer/templates/view.html; "
        "sed -n '1,280p' README.md"
    )
    plan = parse_file_read_plan(cmd)
    assert plan is not None
    assert len(plan) == 6
    assert plan[0]["path"] == "agent_session_viewer/app.py"
    assert plan[5]["path"] == "README.md"
    assert plan[2]["expected_lines"] == 360
    assert sum(int(e["expected_lines"] or 0) for e in plan) == 1295


def test_reject_mixed_shell() -> None:
    assert parse_file_read_plan("rg pattern file; sed -n '1,2p' f") is None
    assert parse_file_read_plan("cat a | head -n 5") is None
    assert parse_file_read_plan("git status") is None
    assert parse_file_read_plan("sed -n '1,2p' a && sed -n '1,2p' b") is None


def test_parse_head_tail_cat() -> None:
    head = parse_file_read_plan("head -n 50 foo.py")
    assert head is not None
    assert head[0]["start"] == 1 and head[0]["end"] == 50

    tail = parse_file_read_plan("tail -n 20 bar.py")
    assert tail is not None
    assert tail[0]["range_kind"] == "tail"
    assert format_file_read_label(tail[0]) == "bar.py · last 20 lines"

    cat = parse_file_read_plan("cat README.md")
    assert cat is not None
    assert cat[0]["range_kind"] == "full"
    assert format_file_read_label(cat[0]) == "README.md · full file"


def test_reject_multi_arg_cat() -> None:
    assert parse_file_read_plan("cat a.py b.py") is None


def test_parse_powershell_get_content() -> None:
    plan = parse_file_read_plan("Get-Content file.txt -TotalCount 10")
    assert plan is not None
    assert plan[0]["path"] == "file.txt"
    assert plan[0]["expected_lines"] == 10

    plan2 = parse_file_read_plan("gc 'path with space.txt'")
    assert plan2 is not None
    assert plan2[0]["path"] == "path with space.txt"
    assert plan2[0]["range_kind"] == "full"

    plan3 = parse_file_read_plan("Get-Content foo.ps1 | Select-Object -First 15")
    assert plan3 is not None
    assert plan3[0]["end"] == 15

    plan4 = parse_file_read_plan("Get-Content log.txt -Tail 5")
    assert plan4 is not None
    assert plan4[0]["range_kind"] == "tail"


def test_strip_shell_meta() -> None:
    raw = (
        "Exit code: 0\nWall time: 4 seconds\nTotal output lines: 3\nOutput:\nline1\nline2\nline3\n"
    )
    header, body = strip_shell_output_meta(raw)
    assert "Exit code" in header
    assert body == "line1\nline2\nline3\n"


def test_confident_multi_split() -> None:
    plan = parse_file_read_plan("sed -n '1,2p' a.py; sed -n '1,3p' b.py")
    assert plan is not None
    body = "a1\na2\nb1\nb2\nb3\n"
    arts = build_file_artifacts(plan, body)
    assert len(arts) == 2
    assert all(a["split"] for a in arts)
    assert arts[0]["text"].startswith("a1")
    assert "b3" in arts[1]["text"]


def test_mismatch_best_effort_still_has_content() -> None:
    """Overshooting ranges still partition tool_result stdout into file cards."""
    plan = parse_file_read_plan("sed -n '1,10p' a.py; sed -n '1,10p' b.py")
    assert plan is not None
    body = "only\nthree\nlines\n"
    arts = build_file_artifacts(plan, body)
    assert len(arts) == 2
    assert all(a["split"] for a in arts)
    assert arts[0]["text"]
    assert "lines 1–10" in arts[0]["label"]
    joined = (arts[0]["text"] or "") + (arts[1]["text"] or "")
    assert "only" in joined
    assert "lines" in joined
    # Content is only from stdout (first budget takes all 3 lines here)
    assert "only" in (arts[0]["text"] or "")


def test_multi_full_cat_cannot_partition_without_budgets() -> None:
    plan = parse_file_read_plan("cat a.py; cat b.py")
    assert plan is not None
    arts = build_file_artifacts(plan, "line-a\nline-b\n")
    assert len(arts) == 2
    assert all(not a["split"] for a in arts)
    # High-level helper hides unpartitioned multi-cat (no empty cards)
    out_arts, prefix = file_artifacts_for_tool_result(
        tool_name="shell_command",
        arguments=None,
        command="cat a.py; cat b.py",
        output="line-a\nline-b\n",
    )
    assert out_arts == []
    assert prefix is None


def test_single_cat_always_splits() -> None:
    plan = parse_file_read_plan("cat README.md")
    assert plan is not None
    body = "hello\nworld\n"
    arts = build_file_artifacts(plan, body)
    assert len(arts) == 1
    assert arts[0]["split"] is True
    assert arts[0]["text"] == body


def test_extract_shell_command_from_json() -> None:
    cmd = extract_shell_command(
        "shell_command",
        '{"command":"sed -n \'1,2p\' x.py","workdir":"C:\\\\tmp"}',
    )
    assert cmd == "sed -n '1,2p' x.py"
    assert extract_shell_command("read_file", '{"path":"x.py"}') is None


def test_file_artifacts_for_tool_result_returns_prefix() -> None:
    cmd = "sed -n '1,1p' a; sed -n '1,1p' b"
    out = "Exit code: 0\nOutput:\nA\nB\n"
    arts, prefix = file_artifacts_for_tool_result(
        tool_name="shell_command", arguments=None, command=cmd, output=out
    )
    assert len(arts) == 2
    assert all(a["split"] for a in arts)
    assert prefix is not None
    assert "Exit code" in prefix
    assert "A" not in prefix  # body only on file cards
    assert arts[0]["text"].startswith("A")


def test_markdown_export_includes_file_artifacts() -> None:
    turns = [
        make_turn(
            role="tool_result",
            id="call-x",
            text="Exit code: 0\nOutput:\nprint(1)\nprint(2)\n",
            file_read_prefix="Exit code: 0\n",
            file_artifacts=[
                {
                    "path": "a.py",
                    "label": "a.py · lines 1–1",
                    "text": "print(1)\n",
                    "split": True,
                },
                {
                    "path": "b.py",
                    "label": "b.py · lines 1–1",
                    "text": "print(2)\n",
                    "split": True,
                },
            ],
        )
    ]
    md = turns_to_markdown(turns, "T", "codex", "x.jsonl")
    assert "Exit code: 0" in md
    assert "#### a.py · lines 1–1" in md
    assert "print(1)" in md
    assert "#### b.py · lines 1–1" in md
    # Full concatenated body should not appear twice as a dump before sections
    assert md.count("print(1)") == 1


def test_codex_fixture_file_reads() -> None:
    path = FIXTURES / "rollout-file-reads.jsonl"
    turns = get_codex_conversation(path)
    results = [t for t in turns if t["role"] == "tool_result"]
    # multi matching
    multi = next(t for t in results if t["id"] == "call-multi-ok")
    assert multi.get("file_artifacts")
    assert len(multi["file_artifacts"]) == 2
    assert all(a["split"] for a in multi["file_artifacts"])
    # Full stdout kept for flat mode
    assert "a1" in multi["text"]
    assert "Exit code" in multi["text"]
    # Prefix is top meta only
    assert "Exit code" in (multi.get("file_read_prefix") or "")
    assert "a1" not in (multi.get("file_read_prefix") or "")
    assert "a1" in multi["file_artifacts"][0]["text"]
    # multi mismatch → still split with content (best-effort)
    bad = next(t for t in results if t["id"] == "call-multi-short")
    assert bad.get("file_artifacts")
    assert all(a["split"] for a in bad["file_artifacts"])
    assert "short-a" in (bad["file_artifacts"][0].get("text") or "")
    # single cat
    single = next(t for t in results if t["id"] == "call-cat")
    assert single.get("file_artifacts")
    assert single["file_artifacts"][0]["split"] is True
    assert "hello cat" in single["file_artifacts"][0]["text"]
    assert "hello cat" in single["text"]


def test_tool_result_file_reads_template_order_and_modes() -> None:
    from agent_session_viewer.app import app

    turn = make_turn(
        role="tool_result",
        id="call-x",
        text="Exit code: 0\nOutput:\nline1\nline2\n",
        file_read_prefix="Exit code: 0\nOutput:\n",
        file_artifacts=[
            {
                "path": "a.py",
                "label": "a.py · lines 1–2",
                "text": "line1\nline2\n",
                "split": True,
            }
        ],
    )
    with app.app_context():
        html = str(
            app.jinja_env.get_template("partials/bubbles.html").module.render_bubbles([turn])
        )
    assert "file-reads-split" in html
    assert "file-reads-flat" in html
    assert "file-read-prefix" in html
    assert "inline-file-read" in html
    assert "artifact-doc-head" not in html
    assert "data-file-reads" in html
    assert "fold-header-btn" in html
    # Prefix block appears before file cards in the HTML
    assert html.index("file-read-prefix") < html.index("inline-file-reads")
    assert html.index("file-reads-split") < html.index("file-reads-flat")
