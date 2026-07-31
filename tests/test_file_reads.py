"""Unit tests for shell file-read plan parsing and output splitting."""

from __future__ import annotations

from pathlib import Path

from agent_session_viewer.agents.codex import get_codex_conversation
from agent_session_viewer.file_reads import (
    build_file_artifacts,
    extract_command_from_exec_script,
    extract_shell_command,
    file_artifacts_for_tool_result,
    format_file_read_label,
    parse_file_read_plan,
    split_file_marker_output,
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
    # Structured read tools are not shell commands
    assert extract_shell_command("read_file", '{"path":"x.py"}') is None
    assert extract_shell_command("Bash", {"command": "cat a.py"}) == "cat a.py"
    assert extract_shell_command("run_terminal_command", {"command": "head -n 3 f"}) == (
        "head -n 3 f"
    )


def test_native_read_file_and_claude_read() -> None:
    from agent_session_viewer.file_reads import native_read_file_artifact, strip_cd_prefix

    grok = native_read_file_artifact(
        "read_file",
        {"target_file": "README.md", "offset": 185, "limit": 80},
        "185→ The server binds to loopback.\n186→ Next line.\n",
    )
    assert grok is not None
    assert grok["path"] == "README.md"
    assert grok["start"] == 185
    assert grok["end"] == 264
    assert "loopback" in (grok["text"] or "")

    claude = native_read_file_artifact(
        "Read",
        {"file_path": "parser.py"},
        "     1|parser contents\n     2|line two\n",
    )
    assert claude is not None
    assert claude["path"] == "parser.py"
    assert claude["label"] == "parser.py · lines 1–2"

    # Claude Code often uses "N\\t" line prefixes (tab), not pipe
    claude_tab = native_read_file_artifact(
        "Read",
        {"file_path": r"C:\proj\godmorgen_bot.py"},
        '1\t"""doc"""\n2\t\n3\timport argparse\n',
    )
    assert claude_tab is not None
    assert claude_tab["label"].endswith("· lines 1–3")
    assert claude_tab["start"] == 1 and claude_tab["end"] == 3

    arts, prefix = file_artifacts_for_tool_result(
        tool_name="read_file",
        arguments={"target_file": "a.py"},
        output="1→ hello\n",
    )
    assert len(arts) == 1
    assert arts[0]["path"] == "a.py"
    assert prefix == ""

    assert strip_cd_prefix('cd "/tmp/x" && cat a.py') == "cat a.py"
    plan = parse_file_read_plan('cd "C:/proj" && cat requirements.txt')
    assert plan is not None and plan[0]["path"] == "requirements.txt"
    # multi-arg cat still not partitioned
    assert parse_file_read_plan('cd "C:/proj" && cat a.txt b.txt') is None


def test_extract_command_from_codex_exec_script() -> None:
    script = (
        'const r = await tools.shell_command({command:"Get-Content a.py; Get-Content b.py",'
        '"workdir":"C:\\\\tmp","timeout_ms":10000}); text(r)'
    )
    assert extract_command_from_exec_script(script) == "Get-Content a.py; Get-Content b.py"
    assert extract_shell_command("exec", script) == "Get-Content a.py; Get-Content b.py"

    escaped = (
        r'const r = await tools.shell_command({command:"rg -n \"foo\" a.py; Get-Content a.py",'
        r'"workdir":"C:\\tmp"}); text(r)'
    )
    cmd = extract_shell_command("exec", escaped)
    assert cmd is not None
    assert 'rg -n "foo" a.py' in cmd
    assert "Get-Content a.py" in cmd


def test_split_file_marker_output() -> None:
    body = (
        "FILE agent_session_viewer/app.py\n"
        "   1: line one\n"
        "   2: line two\n"
        "FILE agent_session_viewer/config.py\n"
        "  10: cfg\n"
        "  11: more\n"
    )
    result = split_file_marker_output(body)
    assert result is not None
    preamble, arts = result
    assert preamble == ""
    assert len(arts) == 2
    assert arts[0]["path"] == "agent_session_viewer/app.py"
    assert arts[0]["label"] == "agent_session_viewer/app.py · lines 1–2"
    assert "line one" in (arts[0]["text"] or "")
    assert arts[1]["path"] == "agent_session_viewer/config.py"
    assert arts[1]["start"] == 10
    assert arts[1]["end"] == 11


def test_file_marker_artifacts_from_tool_result_without_command() -> None:
    out = (
        "Script completed\nWall time 1.0 seconds\nOutput:\n"
        "Exit code: 0\nOutput:\n"
        "FILE a.py\n"
        "   1: hello\n"
        "FILE b.py\n"
        "   1: world\n"
    )
    arts, prefix = file_artifacts_for_tool_result(
        tool_name="exec", arguments=None, command=None, output=out
    )
    assert len(arts) == 2
    assert arts[0]["path"] == "a.py"
    assert "hello" in (arts[0]["text"] or "")
    assert "world" in (arts[1]["text"] or "")
    assert prefix is not None
    assert "Exit code" in prefix or "Script completed" in prefix
    assert "hello" not in prefix


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
    # Codex exec + FILE markers
    exec_files = next(t for t in results if t["id"] == "call-exec-file-markers")
    assert exec_files.get("file_artifacts")
    assert len(exec_files["file_artifacts"]) == 2
    assert exec_files["file_artifacts"][0]["path"] == "a.py"
    assert "alpha" in exec_files["file_artifacts"][0]["text"]
    assert "beta" in exec_files["file_artifacts"][1]["text"]
    assert "FILE a.py" not in (exec_files.get("file_read_prefix") or "")


def test_grok_and_claude_fixtures_get_file_cards() -> None:
    from agent_session_viewer.agents.claude import get_claude_conversation
    from agent_session_viewer.agents.grok import get_grok_conversation

    grok_turns = get_grok_conversation(Path(__file__).parent / "fixtures" / "grok")
    grok_result = next(t for t in grok_turns if t["role"] == "tool_result")
    assert grok_result.get("file_artifacts")
    assert grok_result["file_artifacts"][0]["path"] == "parser.py"
    assert "parser contents" in grok_result["file_artifacts"][0]["text"]
    # Full body kept for flat mode
    assert "parser contents" in grok_result["text"]

    claude_path = Path(__file__).parent / "fixtures" / "claude" / "session-fixture.jsonl"
    claude_turns = get_claude_conversation(claude_path)
    claude_result = next(
        t for t in claude_turns if t["role"] == "tool_result" and t.get("id") == "toolu_read1"
    )
    assert claude_result.get("file_artifacts")
    assert claude_result["file_artifacts"][0]["path"] == "parser.py"
    assert "parser contents" in claude_result["file_artifacts"][0]["text"]


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
    # tool_result bodies start collapsed; single file card may still be open inside
    assert 'data-body-collapsed="true"' in html
    assert "inline-file-read" in html and " open" in html
    # Preview toggle lives on the view page (global collapsed-snippet control)
    # Prefix block appears before file cards in the HTML
    assert html.index("file-read-prefix") < html.index("inline-file-reads")
    assert html.index("file-reads-split") < html.index("file-reads-flat")
