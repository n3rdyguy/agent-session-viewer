"""Codex rollout scanning and transcript parsing."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .. import config
from ..discovery import (
    codex_headline_was_aborted,
    load_codex_session_index,
    safe_codex_headline,
)
from ..file_reads import extract_shell_command, file_artifacts_for_tool_result
from ..images import (
    extract_text,
    extract_text_and_images,
    image_ref_data,
    image_ref_file,
)
from ..turns import make_turn
from ..types import SessionData, empty_session
from ..util import (
    display_time,
    empty_token_usage,
    finalize_token_usage,
    human_time,
    iter_jsonl,
    pretty_json,
    report_record_failure,
    safe_int,
)

# Bare object keys in Codex's `tools.update_plan({plan:[...]})` exec scripts.
_JS_BARE_KEY = re.compile(r"([{\[,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:")
_UPDATE_PLAN_CALL = re.compile(r"(?:tools\.)?update_plan\s*\(")


def _balanced_paren_slice(text: str, open_paren_index: int) -> str | None:
    """Return the inside of the (...) that starts at ``open_paren_index``."""
    if open_paren_index < 0 or open_paren_index >= len(text) or text[open_paren_index] != "(":
        return None
    depth = 0
    in_string = False
    quote = ""
    escape = False
    start = open_paren_index + 1
    for i in range(open_paren_index, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                in_string = False
            continue
        if ch in ('"', "'"):
            in_string = True
            quote = ch
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start:i]
    return None


def _parse_js_object_literal(text: str) -> Any | None:
    """Parse a JSON-ish JS object literal (double-quoted strings, bare keys)."""
    candidate = text.strip()
    if not candidate:
        return None
    # Quote bare identifiers used as keys: {plan:[...]} → {"plan":[...]}
    quoted = _JS_BARE_KEY.sub(r'\1"\2":', candidate)
    try:
        return json.loads(quoted)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _coerce_plan_payload(data: Any) -> list[dict[str, Any]] | None:
    """Normalize update_plan args into a list of plan step dicts."""
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("plan")
        if items is None:
            items = data.get("todos")
        if items is None:
            items = data.get("items")
        if items is None and any(
            isinstance(data.get(k), str) for k in ("step", "content", "text", "description")
        ):
            items = [data]
    else:
        return None
    if not isinstance(items, list) or not items:
        return None
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            out.append({"step": item.strip(), "status": "pending"})
            continue
        if not isinstance(item, dict):
            continue
        out.append(item)
    return out or None


def plan_items_to_todos(items: Any, *, explanation: str = "") -> list[dict[str, Any]]:
    """Map Codex ``update_plan`` steps onto the shared todo shape."""
    todos: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return todos
    note = str(explanation or "").strip()
    if len(note) > 140:
        note = note[:139].rstrip() + "…"
    for index, item in enumerate(items, 1):
        if isinstance(item, str):
            content = item.strip()
            status = "pending"
            item_id = str(index)
        elif isinstance(item, dict):
            content = (
                item.get("step")
                or item.get("content")
                or item.get("text")
                or item.get("description")
                or item.get("subject")
                or ""
            )
            status = item.get("status") or "unknown"
            item_id = str(item.get("id") or index)
        else:
            continue
        if not str(content).strip() and not str(status).strip():
            continue
        todos.append(
            {
                "id": item_id,
                "content": str(content),
                "status": str(status),
                # Codex plans have no priority; surface the latest explanation once.
                "priority": note if index == 1 and note else "",
            }
        )
    # Preserve plan order (checklist sequence), not status-sorted.
    return todos


def parse_update_plan_blob(blob: Any) -> list[dict[str, Any]] | None:
    """
    Parse an ``update_plan`` argument payload in either recorded shape:

    - JSON string / dict from classic ``function_call`` ``arguments``
    - JS ``tools.update_plan({...})`` text from newer ``custom_tool_call`` ``exec`` input
    """
    if blob is None:
        return None
    if isinstance(blob, (dict, list)):
        return _coerce_plan_payload(blob)
    if not isinstance(blob, str):
        return None
    text = blob.strip()
    if not text:
        return None

    # Classic JSON arguments: {"plan":[...], "explanation":"..."}
    if text[0] in "{[":
        try:
            return _coerce_plan_payload(json.loads(text))
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    # Newer exec scripts: const r = await tools.update_plan({plan:[...]});
    match = _UPDATE_PLAN_CALL.search(text)
    if match:
        open_paren = text.find("(", match.start())
        inner = _balanced_paren_slice(text, open_paren)
        if inner is not None:
            parsed = _parse_js_object_literal(inner)
            if parsed is not None:
                return _coerce_plan_payload(parsed)
            # Last resort: inner may already be JSON
            try:
                return _coerce_plan_payload(json.loads(inner))
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
    return None


def codex_prompt_history(session_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """Prompts recorded for this session in ``CODEX_HOME/history.jsonl``."""
    if not session_id:
        return []
    path = config.CODEX_HOME / "history.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for obj in iter_jsonl(path):
        sid = str(obj.get("session_id") or obj.get("sessionId") or "")
        if sid != session_id:
            continue
        display = obj.get("text") or obj.get("display") or obj.get("prompt") or obj.get("message")
        if not isinstance(display, str) or not display.strip():
            continue
        rows.append(
            {
                "display": display,
                "time": human_time(
                    obj.get("ts") if obj.get("ts") is not None else obj.get("timestamp")
                ),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def extract_update_plan_todos(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    """
    If a response_item payload is an ``update_plan`` call (any format), return todos.

    Supported:
    - ``function_call`` / ``custom_tool_call`` named ``update_plan``
    - ``exec`` / ``js`` / ``run`` custom tools whose input calls ``tools.update_plan``
    """
    if not isinstance(payload, dict):
        return None
    ptype = str(payload.get("type") or "")
    if ptype not in ("function_call", "custom_tool_call", "local_shell_call"):
        return None

    name = str(payload.get("name") or "").strip()
    args = payload.get("arguments")
    inp = payload.get("input")
    # Prefer the field Codex actually used for this call type.
    candidates: list[Any] = []
    if name == "update_plan":
        if args is not None:
            candidates.append(args)
        if inp is not None:
            candidates.append(inp)
    else:
        # Exec-style: only consider payloads that mention update_plan.
        for blob in (inp, args):
            if isinstance(blob, str) and "update_plan" in blob:
                candidates.append(blob)
            elif isinstance(blob, dict) and ("plan" in blob or "todos" in blob or "items" in blob):
                # Defensive: some runtimes may store a structured plan under exec input.
                candidates.append(blob)

    explanation = ""
    for blob in candidates:
        items = parse_update_plan_blob(blob)
        if not items:
            continue
        # Pull explanation when available (JSON form).
        if isinstance(blob, dict):
            explanation = str(blob.get("explanation") or "")
        elif isinstance(blob, str) and blob.strip().startswith("{"):
            try:
                data = json.loads(blob)
                if isinstance(data, dict):
                    explanation = str(data.get("explanation") or "")
            except (TypeError, ValueError, json.JSONDecodeError):
                explanation = ""
        elif isinstance(blob, str) and "explanation" in blob:
            # JS form: try to recover explanation from the object literal.
            match = _UPDATE_PLAN_CALL.search(blob)
            if match:
                inner = _balanced_paren_slice(blob, blob.find("(", match.start()))
                parsed = _parse_js_object_literal(inner) if inner is not None else None
                if isinstance(parsed, dict):
                    explanation = str(parsed.get("explanation") or "")
        todos = plan_items_to_todos(items, explanation=explanation)
        if todos:
            return todos
    return None


def codex_scan_session(path: Path, records: list[dict] | None = None) -> dict:
    """
    Single-pass scan of a Codex rollout file for summary, tokens, patches, settings.

    Parsed records may be supplied by ``load_session`` so the conversation and
    metadata views can reuse the same JSONL read.
    """
    meta: dict = {}
    git: dict = {}
    model = ""
    cwd = ""
    effort = ""
    personality = ""
    sandbox = ""
    approval = ""
    cli_version = ""
    originator = ""
    provider = ""
    created = ""
    updated = ""
    first_user = ""
    agents_md = ""
    agents_md_dir = ""
    plan_type = ""
    context_window = None
    context_used = None
    aborted = False

    # Token accounting: prefer final cumulative total_token_usage; also sum last_token_usage
    last_total: dict = {}
    sum_last = {"input": 0, "output": 0, "cached": 0, "reasoning": 0, "total": 0}
    token_events = 0

    counts = {
        "lines": 0,
        "user": 0,
        "assistant": 0,
        "reasoning": 0,
        "tool_call": 0,
        "tool_result": 0,
        "task": 0,
    }
    patches: list[dict] = []
    settings_rows: list[dict] = []
    events: list[dict] = []  # lightweight timeline for "updates" tab
    todos: list[dict] = []  # latest update_plan checklist (last non-empty wins)

    for obj in records if records is not None else iter_jsonl(path):
        counts["lines"] += 1
        ts = obj.get("timestamp") or ""
        if ts:
            created = created or ts
            updated = ts
        t = obj.get("type")
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}

        if t == "session_meta":
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else payload
            cwd = meta.get("cwd") or cwd
            cli_version = meta.get("cli_version") or cli_version
            originator = meta.get("originator") or originator
            provider = meta.get("model_provider") or provider
            if meta.get("timestamp"):
                created = meta.get("timestamp") or created
            git = meta.get("git") if isinstance(meta.get("git"), dict) else git

        elif t == "turn_context":
            model = payload.get("model") or model
            cwd = payload.get("cwd") or cwd
            effort = payload.get("effort") or effort
            personality = payload.get("personality") or personality
            sp = payload.get("sandbox_policy")
            if isinstance(sp, dict):
                sandbox = sp.get("type") or sandbox
            elif isinstance(sp, str):
                sandbox = sp
            approval = payload.get("approval_policy") or approval
            collab = (
                payload.get("collaboration_mode")
                if isinstance(payload.get("collaboration_mode"), dict)
                else {}
            )
            settings = collab.get("settings") if isinstance(collab.get("settings"), dict) else {}
            effort = settings.get("reasoning_effort") or effort
            model = settings.get("model") or model

        elif t == "world_state":
            state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
            amd = state.get("agents_md") if isinstance(state.get("agents_md"), dict) else {}
            if amd.get("text"):
                agents_md = amd.get("text") or agents_md
                agents_md_dir = amd.get("directory") or agents_md_dir

        elif t == "response_item":
            ptype = payload.get("type")
            if ptype == "reasoning":
                counts["reasoning"] += 1
            elif ptype in ("function_call", "custom_tool_call", "local_shell_call"):
                counts["tool_call"] += 1
                # Codex checklist: classic function_call update_plan + newer exec wrapper.
                plan_todos = extract_update_plan_todos(payload)
                if plan_todos:
                    todos = plan_todos
            elif ptype in ("function_call_output", "custom_tool_call_output"):
                counts["tool_result"] += 1
            elif ptype == "message" and str(payload.get("role") or "").lower() == "user":
                text = extract_text(payload.get("content"))
                candidate = safe_codex_headline(text)
                if candidate and not first_user:
                    first_user = candidate
                    aborted = codex_headline_was_aborted(text)

        elif t == "event_msg":
            et = str(payload.get("type") or "").lower()
            if et == "user_message":
                counts["user"] += 1
                msg = str(payload.get("message") or "").strip()
                candidate = safe_codex_headline(msg)
                if candidate and not first_user:
                    first_user = candidate
                    aborted = codex_headline_was_aborted(msg)
            elif et == "agent_message":
                counts["assistant"] += 1
            elif et == "task_started":
                counts["task"] += 1
                events.append(
                    make_turn(
                        role="event",
                        time=display_time(ts),
                        id=payload.get("turn_id") or "",
                        text=f"task_started\nid: {payload.get('turn_id') or ''}\nmodel_context_window: {payload.get('model_context_window') or ''}",
                        meta="task",
                    )
                )
            elif et == "task_complete":
                events.append(
                    make_turn(
                        role="event",
                        time=display_time(ts),
                        id=payload.get("turn_id") or "",
                        text=(
                            f"task_complete\nid: {payload.get('turn_id') or ''}\n"
                            f"duration_ms: {payload.get('duration_ms')}\n"
                            f"ttft_ms: {payload.get('time_to_first_token_ms')}\n"
                            f"{str(payload.get('last_agent_message') or '')[:500]}"
                        ),
                        meta="task",
                    )
                )
            elif et == "token_count":
                token_events += 1
                info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
                total = (
                    info.get("total_token_usage")
                    if isinstance(info.get("total_token_usage"), dict)
                    else {}
                )
                last = (
                    info.get("last_token_usage")
                    if isinstance(info.get("last_token_usage"), dict)
                    else {}
                )
                if total:
                    last_total = total
                if last:
                    sum_last["input"] += safe_int(
                        last.get("input_tokens"), path=path, field="input_tokens"
                    )
                    sum_last["output"] += safe_int(
                        last.get("output_tokens"), path=path, field="output_tokens"
                    )
                    sum_last["cached"] += safe_int(
                        last.get("cached_input_tokens"),
                        path=path,
                        field="cached_input_tokens",
                    )
                    sum_last["reasoning"] += safe_int(
                        last.get("reasoning_output_tokens"),
                        path=path,
                        field="reasoning_output_tokens",
                    )
                    sum_last["total"] += safe_int(
                        last.get("total_tokens"), path=path, field="total_tokens"
                    )
                if info.get("model_context_window"):
                    context_window = safe_int(
                        info["model_context_window"],
                        path=path,
                        field="model_context_window",
                    )
                # Approximate context used from last step total
                if last.get("total_tokens"):
                    context_used = safe_int(last["total_tokens"], path=path, field="total_tokens")
                rl = (
                    payload.get("rate_limits")
                    if isinstance(payload.get("rate_limits"), dict)
                    else {}
                )
                if rl.get("plan_type"):
                    plan_type = rl.get("plan_type") or plan_type
            elif et == "thread_settings_applied":
                settings = (
                    payload.get("thread_settings")
                    if isinstance(payload.get("thread_settings"), dict)
                    else {}
                )
                model = settings.get("model") or model
                cwd = settings.get("cwd") or cwd
                effort = settings.get("reasoning_effort") or effort
                personality = settings.get("personality") or personality
                approval = settings.get("approval_policy") or approval
                sp = settings.get("sandbox_policy") or settings.get("permission_profile")
                if isinstance(sp, dict):
                    sandbox = sp.get("type") or sandbox
            elif et == "patch_apply_end":
                call_id = payload.get("call_id") or ""
                changes = payload.get("changes") if isinstance(payload.get("changes"), dict) else {}
                for fpath, ch in changes.items():
                    ch = ch if isinstance(ch, dict) else {}
                    patches.append(
                        {
                            "hunk_id": call_id or fpath,
                            "file_path": fpath,
                            "event": ch.get("type")
                            or ("ok" if payload.get("success") else "error"),
                            "source": "patch_apply",
                            "added": None,
                            "removed": None,
                            "start": None,
                            "end": None,
                            "prompt_index": None,
                            "time": human_time(ts),
                            "author_id": "",
                            "diff": (ch.get("unified_diff") or "")[:400],
                        }
                    )
                stdout = (payload.get("stdout") or "")[:300]
                events.append(
                    make_turn(
                        role="event",
                        time=display_time(ts),
                        id=call_id,
                        text=f"patch_apply_end · success={payload.get('success')}\n{stdout}\nfiles: {', '.join(list(changes)[:12])}",
                        meta="patch",
                    )
                )
            elif et == "image_generation_end":
                events.append(
                    make_turn(
                        role="event",
                        time=display_time(ts),
                        id=payload.get("call_id") or "",
                        text=(
                            f"image_generation_end · {payload.get('status')}\n"
                            f"saved: {payload.get('saved_path') or '-'}\n"
                            f"{str(payload.get('revised_prompt') or '')[:400]}"
                        ),
                        meta="image",
                    )
                )

    # Token usage: cumulative total from last token_count (Codex running total)
    tokens = empty_token_usage()
    if last_total:
        tokens["input"] = safe_int(last_total.get("input_tokens"), path=path, field="input_tokens")
        tokens["output"] = safe_int(
            last_total.get("output_tokens"), path=path, field="output_tokens"
        )
        tokens["cached"] = safe_int(
            last_total.get("cached_input_tokens"),
            path=path,
            field="cached_input_tokens",
        )
        tokens["reasoning"] = safe_int(
            last_total.get("reasoning_output_tokens"),
            path=path,
            field="reasoning_output_tokens",
        )
        tokens["total"] = safe_int(last_total.get("total_tokens"), path=path, field="total_tokens")
        tokens["turns"] = token_events
        tokens["model_calls"] = token_events
        tokens["source"] = "rollout · last token_count.total_token_usage (cumulative)"
    elif sum_last["input"] or sum_last["output"]:
        tokens["input"] = sum_last["input"]
        tokens["output"] = sum_last["output"]
        tokens["cached"] = sum_last["cached"]
        tokens["reasoning"] = sum_last["reasoning"]
        tokens["total"] = sum_last["total"]
        tokens["turns"] = token_events
        tokens["source"] = "rollout · sum of token_count.last_token_usage"
    if context_window:
        tokens["context_window"] = context_window
    if context_used is not None:
        tokens["context_used"] = context_used
    tokens = finalize_token_usage(tokens)

    # Settings rows for resources panel
    for key, val in [
        ("model", model),
        ("provider", provider),
        ("originator", originator),
        ("cli_version", cli_version),
        ("approval_policy", approval),
        ("sandbox", sandbox),
        ("reasoning_effort", effort),
        ("personality", personality),
        ("plan_type", plan_type),
        ("cwd", cwd),
    ]:
        if val:
            settings_rows.append({"tool": "session", "key": key, "value": str(val)})

    if git:
        for key in ("branch", "commit_hash", "repository_url"):
            if git.get(key):
                settings_rows.append({"tool": "git", "key": key, "value": str(git[key])})

    # Documents for the artifacts panel (collapsible + markdown), not plain other_state dumps
    artifacts: list[dict] = []
    if agents_md:
        artifacts.append(
            {
                "id": "agents-md",
                "title": "AGENTS.md",
                "subtitle": agents_md_dir or cwd or "",
                "kind": "markdown",
                "text": agents_md,
            }
        )
    base_inst = meta.get("base_instructions")
    if isinstance(base_inst, dict) and base_inst.get("text"):
        artifacts.append(
            {
                "id": "base-instructions",
                "title": "Base instructions",
                "subtitle": "session_meta",
                "kind": "markdown",
                "text": str(base_inst.get("text") or ""),
            }
        )
    elif isinstance(base_inst, str) and base_inst.strip():
        artifacts.append(
            {
                "id": "base-instructions",
                "title": "Base instructions",
                "subtitle": "session_meta",
                "kind": "markdown",
                "text": base_inst,
            }
        )

    titles = load_codex_session_index()
    sid = meta.get("id") or meta.get("session_id") or path.stem
    m = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
        path.stem,
        re.I,
    )
    if m:
        sid = meta.get("id") or meta.get("session_id") or m.group(1)
    history = codex_prompt_history(str(sid))
    headline = safe_codex_headline(first_user)
    title = (
        safe_codex_headline((titles.get(str(sid)) or {}).get("thread_name"))
        or headline
        or path.name
    )

    summary = {
        "id": sid,
        "title": str(title),
        "session_summary": headline,
        "aborted": aborted,
        "cwd": cwd or meta.get("cwd") or "",
        "created": human_time(created or meta.get("timestamp")),
        "updated": human_time(updated),
        "model": model,
        "agent_name": originator or "codex",
        "sandbox_profile": sandbox,
        "reasoning_effort": effort,
        "num_messages": counts["lines"],
        "num_chat_messages": counts["user"] + counts["assistant"],
        "request_id": str(sid),
        "head_branch": (git.get("branch") or ""),
        "head_commit": (git.get("commit_hash") or "")[:12],
        "git_root_dir": git.get("repository_url") or "",
        "tokens": tokens,
        "personality": personality,
        "cli_version": cli_version,
        "plan_type": plan_type,
        "counts": counts,
    }

    resources = {
        "todos": todos,
        "scheduler_tasks": [],
        "reported_completions": [],
        "settings": settings_rows,
        "other_state": [],
        "prompt_history": history,
    }

    return {
        "summary": summary,
        "resources": resources,
        "hunks": patches,
        "events": events,
        "artifacts": artifacts,
        "meta": meta,
    }


def codex_summary_card(path: Path) -> dict:
    return codex_scan_session(path)["summary"]


def get_codex_conversation(
    path: Path,
    records: list[dict] | None = None,
    session_cwd: str | None = None,
) -> list[dict]:
    """
    Full Codex rollout transcript (chat only):
    - event_msg user/agent messages
    - response_item reasoning / tools
    - developer / AGENTS.md injections as system

    Task / patch / image lifecycle events belong on the Events timeline
    (``codex_scan_session``), not in Chat history.
    """
    turns: list[dict] = []
    idx = 0
    # call_id → shell command string for pairing tool_result file-read artifacts
    shell_commands_by_call: dict[str, str] = {}

    def content_pair(raw: Any, extra_images: Any = None) -> tuple[str, list[dict]]:
        return extract_text_and_images(
            raw,
            extra_images=extra_images,
            session_dir=path.parent if path.is_file() else path,
            cwd=session_cwd,
        )

    def tool_output_text(output: Any) -> str:
        if output is None:
            return ""
        if isinstance(output, str):
            return output
        if isinstance(output, list):
            return extract_text(output)
        if isinstance(output, dict):
            return extract_text(output.get("content") or output.get("text") or output)
        return str(output)

    record_iter = iter(records if records is not None else iter_jsonl(path))
    while True:
        try:
            obj = next(record_iter)
        except StopIteration:
            break
        try:
            idx += 1
            seq = f"#{idx}"
            ts_raw = obj.get("timestamp")
            t = obj.get("type")
            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}

            if t == "session_meta":
                meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else payload
                session_cwd = meta.get("cwd") or session_cwd

            elif t == "response_item":
                ptype = payload.get("type")

                if ptype == "reasoning":
                    rid = payload.get("id") or seq
                    summary_parts = []
                    for block in payload.get("summary") or []:
                        if isinstance(block, dict):
                            summary_parts.append(block.get("text") or extract_text(block))
                        else:
                            summary_parts.append(str(block))
                    summary_text = "\n".join(p for p in summary_parts if p and str(p).strip())
                    body = []
                    if summary_text:
                        body.append(summary_text)
                    if payload.get("encrypted_content"):
                        body.append("<encrypted>")
                    if not body:
                        body.append(
                            "<encrypted>"
                            if payload.get("encrypted_content") is not None
                            else "(empty reasoning)"
                        )
                    turns.append(
                        make_turn(
                            role="reasoning",
                            time=display_time(ts_raw),
                            id=rid,
                            text="\n".join(body),
                            meta="reasoning",
                        )
                    )
                    continue

                if ptype == "message":
                    role = str(payload.get("role") or "event").lower()
                    text, images = content_pair(payload.get("content"))
                    if not text.strip() and not images:
                        continue
                    # Skip assistant/user response_item duplicates of event_msg chat;
                    # still show developer + injected project instructions.
                    if role == "assistant":
                        continue
                    if role == "user":
                        stripped = text.lstrip()
                        if stripped.startswith(("# AGENTS.md", "<INSTRUCTIONS>", "# ")):
                            turns.append(
                                make_turn(
                                    role="system",
                                    time=display_time(ts_raw),
                                    id=seq,
                                    text=text,
                                    meta="project_instructions",
                                    images=images,
                                )
                            )
                        # else: prefer event_msg user_message
                        continue
                    if role == "developer":
                        turns.append(
                            make_turn(
                                role="system",
                                time=display_time(ts_raw),
                                id=seq,
                                text=text,
                                meta="developer",
                                images=images,
                            )
                        )
                        continue
                    turns.append(
                        make_turn(
                            role=role,
                            time=display_time(ts_raw),
                            id=seq,
                            text=text,
                            images=images,
                        )
                    )
                    continue

                if ptype in ("function_call", "custom_tool_call", "local_shell_call"):
                    name = payload.get("name") or (
                        "shell_command" if ptype == "local_shell_call" else "tool"
                    )
                    call_id = str(payload.get("call_id") or payload.get("id") or "")
                    raw_args = (
                        payload.get("arguments")
                        or payload.get("input")
                        or payload.get("command")
                        or ""
                    )
                    # local_shell_call sometimes puts command at payload.command
                    if (
                        isinstance(raw_args, str)
                        and not raw_args.strip()
                        and payload.get("command")
                    ):
                        raw_args = {"command": payload.get("command")}
                    cmd = extract_shell_command(str(name), raw_args)
                    if not cmd and ptype == "local_shell_call":
                        cmd = extract_shell_command("shell_command", raw_args)
                    if not cmd and str(name).lower() == "exec" and isinstance(raw_args, str):
                        # Retry with explicit exec handling (JS shell_command wrapper)
                        cmd = extract_shell_command("exec", raw_args)
                    if call_id and cmd:
                        shell_commands_by_call[call_id] = cmd
                    args = raw_args if isinstance(raw_args, str) else pretty_json(raw_args)
                    body = f"{name}\nid: {call_id}\n{args}".strip()
                    _, imgs = content_pair(body)
                    turns.append(
                        make_turn(
                            role="tool_call",
                            time=display_time(ts_raw),
                            id=call_id or seq,
                            text=body,
                            meta=name,
                            images=imgs,
                        )
                    )
                    continue

                if ptype in ("function_call_output", "custom_tool_call_output"):
                    call_id = str(payload.get("call_id") or payload.get("id") or "")
                    out = tool_output_text(payload.get("output"))
                    text, imgs = content_pair(out)
                    file_artifacts = None
                    file_read_prefix = None
                    cmd = shell_commands_by_call.get(call_id) if call_id else None
                    # Marker-based splits need only stdout; command helps pure sed/cat batches.
                    artifacts, prefix = file_artifacts_for_tool_result(
                        tool_name="shell_command",
                        arguments=None,
                        command=cmd,
                        output=out,
                    )
                    if artifacts:
                        # Keep full stdout on the turn for flat (toggle-off) view.
                        file_artifacts = list(artifacts)
                        file_read_prefix = prefix if prefix is not None else ""
                    if not (text or "").strip() and not imgs and not file_artifacts:
                        text = "(empty tool result)"
                    turns.append(
                        make_turn(
                            role="tool_result",
                            time=display_time(ts_raw),
                            id=call_id or seq,
                            text=text,
                            images=imgs,
                            file_artifacts=file_artifacts,
                            file_read_prefix=file_read_prefix,
                        )
                    )
                    continue

            elif t == "event_msg":
                et = str(payload.get("type") or "").lower()

                if et == "user_message":
                    msg = payload.get("message") or payload.get("text") or ""
                    imgs_raw = []
                    for key in ("images", "local_images"):
                        val = payload.get(key)
                        if isinstance(val, list):
                            imgs_raw.extend(val)
                    text, images = content_pair(msg, imgs_raw if imgs_raw else None)
                    # Also attach local file paths as file images
                    for pth in imgs_raw:
                        if (
                            isinstance(pth, str)
                            and pth
                            and not any(
                                (im.get("path") == pth or im.get("url") == pth) for im in images
                            )
                        ):
                            if pth.startswith("data:image"):
                                images.append(image_ref_data(pth))
                            else:
                                images.append(image_ref_file(pth, pth))
                    if text.strip() or images:
                        turns.append(
                            make_turn(
                                role="user",
                                time=display_time(ts_raw),
                                id=seq,
                                text=text or "(image)",
                                images=images,
                            )
                        )
                    continue

                if et == "agent_message":
                    msg = payload.get("message") or payload.get("text") or ""
                    phase = payload.get("phase") or ""
                    if msg:
                        turns.append(
                            make_turn(
                                role="assistant",
                                time=display_time(ts_raw),
                                id=seq,
                                text=str(msg),
                                meta=phase,
                            )
                        )
                    continue

                # task_started / task_complete / turn_aborted / patch_apply_end /
                # image_generation_end are collected for the Events timeline only.

            elif t == "world_state" and payload.get("full"):
                state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
                amd = state.get("agents_md") if isinstance(state.get("agents_md"), dict) else {}
                if amd.get("text"):
                    turns.append(
                        make_turn(
                            role="system",
                            time=display_time(ts_raw),
                            id=seq,
                            text=f"# AGENTS.md ({amd.get('directory') or ''})\n\n{amd.get('text')}",
                            meta="agents_md",
                        )
                    )
                continue

        except Exception as exc:
            report_record_failure(path, exc)
            continue

    return turns


def load_session(path: Path) -> SessionData:
    """Load a Codex session with the same shape Grok and Claude provide."""
    if not path.is_file():
        return empty_session("codex", path)

    # Parse and decode the rollout once, then reuse those records for both the
    # transcript and its summary/tokens/events/patches.
    records = list(iter_jsonl(path))
    scan = codex_scan_session(path, records)
    meta = scan.get("meta") if isinstance(scan.get("meta"), dict) else {}
    summary = scan["summary"]
    session = empty_session("codex", path)
    session.update(
        {
            "title": summary.get("title") or path.name,
            "turns": get_codex_conversation(path, records, session_cwd=meta.get("cwd")),
            "summary": summary,
            "resources": scan["resources"],
            "artifacts": scan.get("artifacts") or [],
            "hunks": scan["hunks"],
            # Reuse "updates" for the Codex task/patch/image timeline.
            "updates": scan["events"],
        }
    )
    return session
