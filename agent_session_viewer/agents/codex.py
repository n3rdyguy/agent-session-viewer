"""Codex rollout scanning and transcript parsing."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..discovery import (
    codex_headline_was_aborted,
    load_codex_session_index,
    safe_codex_headline,
)
from ..images import (
    extract_text,
    extract_text_and_images,
    image_ref_data,
    image_ref_file,
)
from ..turns import make_turn
from ..util import (
    display_time,
    empty_token_usage,
    finalize_token_usage,
    human_time,
    iter_jsonl,
    pretty_json,
)








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
            collab = payload.get("collaboration_mode") if isinstance(payload.get("collaboration_mode"), dict) else {}
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
            elif ptype in ("function_call_output", "custom_tool_call_output"):
                counts["tool_result"] += 1
            elif ptype == "message" and (payload.get("role") or "").lower() == "user":
                text = extract_text(payload.get("content"))
                candidate = safe_codex_headline(text)
                if candidate and not first_user:
                    first_user = candidate
                    aborted = codex_headline_was_aborted(text)

        elif t == "event_msg":
            et = (payload.get("type") or "").lower()
            if et == "user_message":
                counts["user"] += 1
                msg = (payload.get("message") or "").strip()
                candidate = safe_codex_headline(msg)
                if candidate and not first_user:
                    first_user = candidate
                    aborted = codex_headline_was_aborted(msg)
            elif et == "agent_message":
                counts["assistant"] += 1
            elif et == "task_started":
                counts["task"] += 1
                events.append(make_turn(
                    role="event",
                    time=display_time(ts),
                    id=payload.get("turn_id") or "",
                    text=f"task_started\nid: {payload.get('turn_id') or ''}\nmodel_context_window: {payload.get('model_context_window') or ''}",
                    meta="task",
                ))
            elif et == "task_complete":
                events.append(make_turn(
                    role="event",
                    time=display_time(ts),
                    id=payload.get("turn_id") or "",
                    text=(
                        f"task_complete\nid: {payload.get('turn_id') or ''}\n"
                        f"duration_ms: {payload.get('duration_ms')}\n"
                        f"ttft_ms: {payload.get('time_to_first_token_ms')}\n"
                        f"{(payload.get('last_agent_message') or '')[:500]}"
                    ),
                    meta="task",
                ))
            elif et == "token_count":
                token_events += 1
                info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
                total = info.get("total_token_usage") if isinstance(info.get("total_token_usage"), dict) else {}
                last = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
                if total:
                    last_total = total
                if last:
                    sum_last["input"] += int(last.get("input_tokens") or 0)
                    sum_last["output"] += int(last.get("output_tokens") or 0)
                    sum_last["cached"] += int(last.get("cached_input_tokens") or 0)
                    sum_last["reasoning"] += int(last.get("reasoning_output_tokens") or 0)
                    sum_last["total"] += int(last.get("total_tokens") or 0)
                if info.get("model_context_window"):
                    context_window = int(info["model_context_window"])
                # Approximate context used from last step total
                if last.get("total_tokens"):
                    context_used = int(last["total_tokens"])
                rl = payload.get("rate_limits") if isinstance(payload.get("rate_limits"), dict) else {}
                if rl.get("plan_type"):
                    plan_type = rl.get("plan_type") or plan_type
            elif et == "thread_settings_applied":
                settings = payload.get("thread_settings") if isinstance(payload.get("thread_settings"), dict) else {}
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
                    patches.append({
                        "hunk_id": call_id or fpath,
                        "file_path": fpath,
                        "event": ch.get("type") or ("ok" if payload.get("success") else "error"),
                        "source": "patch_apply",
                        "added": None,
                        "removed": None,
                        "start": None,
                        "end": None,
                        "prompt_index": None,
                        "time": human_time(ts),
                        "author_id": "",
                        "diff": (ch.get("unified_diff") or "")[:400],
                    })
                stdout = (payload.get("stdout") or "")[:300]
                events.append(make_turn(
                    role="event",
                    time=display_time(ts),
                    id=call_id,
                    text=f"patch_apply_end · success={payload.get('success')}\n{stdout}\nfiles: {', '.join(list(changes)[:12])}",
                    meta="patch",
                ))
            elif et == "image_generation_end":
                events.append(make_turn(
                    role="event",
                    time=display_time(ts),
                    id=payload.get("call_id") or "",
                    text=(
                        f"image_generation_end · {payload.get('status')}\n"
                        f"saved: {payload.get('saved_path') or '—'}\n"
                        f"{(payload.get('revised_prompt') or '')[:400]}"
                    ),
                    meta="image",
                ))

    # Token usage: cumulative total from last token_count (Codex running total)
    tokens = empty_token_usage()
    if last_total:
        tokens["input"] = int(last_total.get("input_tokens") or 0)
        tokens["output"] = int(last_total.get("output_tokens") or 0)
        tokens["cached"] = int(last_total.get("cached_input_tokens") or 0)
        tokens["reasoning"] = int(last_total.get("reasoning_output_tokens") or 0)
        tokens["total"] = int(last_total.get("total_tokens") or 0)
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
        artifacts.append({
            "id": "agents-md",
            "title": "AGENTS.md",
            "subtitle": agents_md_dir or cwd or "",
            "kind": "markdown",
            "text": agents_md,
        })
    base_inst = meta.get("base_instructions")
    if isinstance(base_inst, dict) and base_inst.get("text"):
        artifacts.append({
            "id": "base-instructions",
            "title": "Base instructions",
            "subtitle": "session_meta",
            "kind": "markdown",
            "text": str(base_inst.get("text") or ""),
        })
    elif isinstance(base_inst, str) and base_inst.strip():
        artifacts.append({
            "id": "base-instructions",
            "title": "Base instructions",
            "subtitle": "session_meta",
            "kind": "markdown",
            "text": base_inst,
        })

    titles = load_codex_session_index()
    sid = meta.get("id") or meta.get("session_id") or path.stem
    m = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
        path.stem,
        re.I,
    )
    if m:
        sid = meta.get("id") or meta.get("session_id") or m.group(1)
    headline = safe_codex_headline(first_user)
    title = (
        safe_codex_headline(
            (titles.get(str(sid)) or {}).get("thread_name")
        )
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
        "todos": [],
        "scheduler_tasks": [],
        "reported_completions": [],
        "settings": settings_rows,
        "other_state": [],
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
    Full Codex rollout transcript:
    - event_msg user/agent messages (chat)
    - response_item reasoning / tools
    - developer / AGENTS.md injections as system
    - patch / image events
    """
    turns: list[dict] = []
    idx = 0
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

    try:
        for obj in records if records is not None else iter_jsonl(path):
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
                    turns.append(make_turn(
                        role="reasoning",
                        time=display_time(ts_raw),
                        id=rid,
                        text="\n".join(body),
                        meta="reasoning",
                    ))
                    continue

                if ptype == "message":
                    role = (payload.get("role") or "event").lower()
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
                            turns.append(make_turn(
                                role="system",
                                time=display_time(ts_raw),
                                id=seq,
                                text=text,
                                meta="project_instructions",
                                images=images,
                            ))
                        # else: prefer event_msg user_message
                        continue
                    if role == "developer":
                        turns.append(make_turn(
                            role="system",
                            time=display_time(ts_raw),
                            id=seq,
                            text=text,
                            meta="developer",
                            images=images,
                        ))
                        continue
                    turns.append(make_turn(
                        role=role,
                        time=display_time(ts_raw),
                        id=seq,
                        text=text,
                        images=images,
                    ))
                    continue

                if ptype in ("function_call", "custom_tool_call", "local_shell_call"):
                    name = payload.get("name") or "tool"
                    call_id = payload.get("call_id") or payload.get("id") or ""
                    args = payload.get("arguments") or payload.get("input") or ""
                    if not isinstance(args, str):
                        args = pretty_json(args)
                    body = f"{name}\nid: {call_id}\n{args}".strip()
                    _, imgs = content_pair(body)
                    turns.append(make_turn(
                        role="tool_call",
                        time=display_time(ts_raw),
                        id=call_id or seq,
                        text=body,
                        meta=name,
                        images=imgs,
                    ))
                    continue

                if ptype in ("function_call_output", "custom_tool_call_output"):
                    call_id = payload.get("call_id") or payload.get("id") or ""
                    out = tool_output_text(payload.get("output"))
                    text, imgs = content_pair(out)
                    turns.append(make_turn(
                        role="tool_result",
                        time=display_time(ts_raw),
                        id=call_id or seq,
                        text=text or "(empty tool result)",
                        images=imgs,
                    ))
                    continue

            elif t == "event_msg":
                et = (payload.get("type") or "").lower()

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
                        if isinstance(pth, str) and pth and not any(
                            (im.get("path") == pth or im.get("url") == pth) for im in images
                        ):
                            if pth.startswith("data:image"):
                                images.append(image_ref_data(pth))
                            else:
                                images.append(image_ref_file(pth, pth))
                    if text.strip() or images:
                        turns.append(make_turn(
                            role="user",
                            time=display_time(ts_raw),
                            id=seq,
                            text=text or "(image)",
                            images=images,
                        ))
                    continue

                if et == "agent_message":
                    msg = payload.get("message") or payload.get("text") or ""
                    phase = payload.get("phase") or ""
                    if msg:
                        turns.append(make_turn(
                            role="assistant",
                            time=display_time(ts_raw),
                            id=seq,
                            text=str(msg),
                            meta=phase,
                        ))
                    continue

                if et == "patch_apply_end":
                    call_id = payload.get("call_id") or seq
                    changes = payload.get("changes") if isinstance(payload.get("changes"), dict) else {}
                    lines = [
                        f"patch_apply · success={payload.get('success')}",
                        f"id: {call_id}",
                    ]
                    if payload.get("stdout"):
                        lines.append(str(payload.get("stdout"))[:800])
                    for fpath, ch in list(changes.items())[:30]:
                        ch = ch if isinstance(ch, dict) else {}
                        lines.append(f"\n{ch.get('type') or 'edit'}: {fpath}")
                        diff = ch.get("unified_diff") or ""
                        if diff:
                            lines.append(diff[:600] + ("…" if len(diff) > 600 else ""))
                    turns.append(make_turn(
                        role="event",
                        time=display_time(ts_raw),
                        id=call_id,
                        text="\n".join(lines),
                        meta="patch",
                    ))
                    continue

                if et == "image_generation_end":
                    call_id = payload.get("call_id") or seq
                    saved = payload.get("saved_path") or ""
                    text = (
                        f"image_generation · {payload.get('status')}\n"
                        f"id: {call_id}\n"
                        f"saved: {saved}\n"
                        f"{(payload.get('revised_prompt') or '')[:500]}"
                    )
                    images = []
                    if saved:
                        images.append(image_ref_file(str(saved), str(saved)))
                    turns.append(make_turn(
                        role="event",
                        time=display_time(ts_raw),
                        id=call_id,
                        text=text,
                        meta="image",
                        images=images,
                    ))
                    continue

                if et in ("task_started", "task_complete", "turn_aborted"):
                    turn_id = payload.get("turn_id") or seq
                    extra = ""
                    if et == "task_complete":
                        extra = (
                            f"\nduration_ms: {payload.get('duration_ms')}"
                            f"\nttft_ms: {payload.get('time_to_first_token_ms')}"
                        )
                        if payload.get("last_agent_message"):
                            extra += f"\n{(payload.get('last_agent_message') or '')[:400]}"
                    turns.append(make_turn(
                        role="event",
                        time=display_time(ts_raw),
                        id=turn_id,
                        text=f"{et}\nid: {turn_id}{extra}",
                        meta="task",
                    ))
                    continue

            elif t == "world_state" and payload.get("full"):
                state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
                amd = state.get("agents_md") if isinstance(state.get("agents_md"), dict) else {}
                if amd.get("text"):
                    turns.append(make_turn(
                        role="system",
                        time=display_time(ts_raw),
                        id=seq,
                        text=f"# AGENTS.md ({amd.get('directory') or ''})\n\n{amd.get('text')}",
                        meta="agents_md",
                    ))
                continue

    except Exception:
        pass

    return turns
