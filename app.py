#!/usr/bin/env python3
"""
Agent Session Viewer – local Flask UI
Grok Build • Claude Code • Codex CLI
Search • Markdown export • Chat layout
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from flask import Flask, Response, abort, render_template, request, send_file

app = Flask(__name__)

HOME = Path.home()
GROK_HOME = Path(os.environ.get("GROK_HOME", HOME / ".grok")).expanduser()
CLAUDE_HOME = Path(os.environ.get("CLAUDE_HOME", HOME / ".claude")).expanduser()
CODEX_HOME = Path(os.environ.get("CODEX_HOME", HOME / ".codex")).expanduser()


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def human_time(ts: str | float | int | None) -> str:
    if ts is None or ts == "":
        return ""
    try:
        if isinstance(ts, (int, float)):
            # Grok updates use unix seconds; reject absurd values
            if ts > 1e12:  # ms
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        return (
            datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            .astimezone()
            .strftime("%Y-%m-%d %H:%M:%S")
        )
    except Exception:
        return str(ts)[:19]


def display_time(ts: str | float | int | None, fallback_id: str | None = None) -> str:
    """Prefer a real timestamp; otherwise show an id so the UI never shows bare '?'."""
    ht = human_time(ts)
    if ht:
        return ht
    if fallback_id:
        return str(fallback_id)
    return ""


def truncate(s: str, n: int = 140) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def format_tokens(n: int | float | None) -> str:
    if n is None:
        return "—"
    try:
        n = int(n)
    except Exception:
        return str(n)
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.2f}M".rstrip("0").rstrip(".") + f" ({n:,})"
    return f"{n:,}"


def pretty_json(obj: Any, max_len: int = 12000) -> str:
    try:
        if isinstance(obj, str):
            try:
                obj = json.loads(obj)
            except Exception:
                return obj
        text = json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    except Exception:
        text = str(obj)
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def path_allowed(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path
    roots = [GROK_HOME, CLAUDE_HOME, CODEX_HOME]
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except Exception:
            # Fallback for odd Windows path forms
            rs, rr = str(resolved).lower().replace("\\", "/"), str(root.resolve()).lower().replace("\\", "/")
            if rs == rr or rs.startswith(rr + "/"):
                return True
    return False


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", ".avif"}
IMAGE_PATH_RE = re.compile(
    r"(?P<path>"
    r"(?:[A-Za-z]:[\\/]|\\\\|/|\./|\.\./|assets[\\/])[^\s<>\"']+\."
    r"(?:png|jpe?g|gif|webp|bmp|svg|ico|avif)"
    r")",
    re.IGNORECASE,
)
IMAGE_FILES_BLOCK_RE = re.compile(
    r"<image_files>\s*(.*?)\s*</image_files>",
    re.IGNORECASE | re.DOTALL,
)
DATA_IMAGE_JSON_RE = re.compile(
    r'\{\s*"type"\s*:\s*"image"\s*,\s*"url"\s*:\s*"(data:image/[^"]+)"\s*\}',
    re.IGNORECASE,
)
DATA_IMAGE_URL_RE = re.compile(
    r"(data:image/(?:png|jpe?g|gif|webp|bmp|svg\+xml|x-icon|avif);base64,[A-Za-z0-9+/=\s]+)",
    re.IGNORECASE,
)


def is_image_path(path: str | Path) -> bool:
    try:
        return Path(str(path)).suffix.lower() in IMAGE_EXTS
    except Exception:
        return False


def truncate_data_url(url: str, head: int = 28) -> str:
    """Keep a short prefix of a data URL for display snippets."""
    url = (url or "").strip()
    if not url.startswith("data:"):
        return truncate(url, 96)
    if ";base64," in url:
        prefix, b64 = url.split(";base64,", 1)
        b64 = re.sub(r"\s+", "", b64)
        if len(b64) > head:
            return f"{prefix};base64,{b64[:head]}…"
        return f"{prefix};base64,{b64}"
    return truncate(url, 96)


def image_json_snippet(
    *,
    url: str | None = None,
    path: str | None = None,
    extra: dict | None = None,
) -> str:
    """
    Compact JSON-like snippet shown above image previews, e.g.
    {"type": "image", "url": "data:image/png;base64,iVBORw0…"}
    """
    obj: dict[str, Any] = {"type": "image"}
    if url:
        obj["url"] = truncate_data_url(url) if url.startswith("data:") else truncate(url, 96)
    if path:
        obj["path"] = path
    if extra:
        for k, v in extra.items():
            if k in obj or v is None:
                continue
            if isinstance(v, str) and len(v) > 96:
                obj[k] = truncate(v, 96)
            else:
                obj[k] = v
    return json.dumps(obj, ensure_ascii=False)


def image_ref_data(url: str, label: str = "", snippet: str | None = None) -> dict:
    mime = "image/png"
    url = url.strip()
    if url.startswith("data:"):
        try:
            mime = url.split(";", 1)[0].split(":", 1)[1] or mime
        except Exception:
            pass
    return {
        "kind": "data",
        "url": url,
        "label": label or "image",
        "mime": mime,
        "copyable": True,
        "snippet": snippet or image_json_snippet(url=url),
    }


def image_ref_file(path: str, label: str = "", snippet: str | None = None) -> dict:
    return {
        "kind": "file",
        "path": path,
        "label": label or path,
        "href": f"/media?path={path}",  # path urlencoded in template
        "copyable": False,
        "snippet": snippet or image_json_snippet(path=path),
    }


def collect_image_blocks(block: dict) -> list[dict]:
    """Pull image refs from a content block dict."""
    images: list[dict] = []
    t = (block.get("type") or "").lower()
    if t in ("image", "input_image", "output_image"):
        url = block.get("url") or block.get("image_url") or ""
        if isinstance(url, dict):
            url = url.get("url") or ""
        source = block.get("source")
        if not url and isinstance(source, dict):
            # Anthropic-style {type: base64, media_type, data}
            if source.get("type") == "base64" and source.get("data"):
                media = source.get("media_type") or "image/png"
                url = f"data:{media};base64,{source.get('data')}"
            else:
                url = source.get("url") or ""
        if isinstance(url, str) and url.startswith("data:image"):
            images.append(image_ref_data(
                url,
                block.get("alt") or "image",
                snippet=image_json_snippet(url=url),
            ))
        elif isinstance(url, str) and url.strip():
            # http(s) or path-like
            if url.startswith(("http://", "https://")):
                images.append({
                    "kind": "url",
                    "url": url,
                    "label": block.get("alt") or url,
                    "copyable": False,
                    "snippet": image_json_snippet(url=url),
                })
            else:
                images.append(image_ref_file(
                    url,
                    block.get("alt") or url,
                    snippet=image_json_snippet(url=url, path=url),
                ))
        path = block.get("path") or block.get("file_path") or block.get("filename")
        if path:
            images.append(image_ref_file(
                str(path),
                str(path),
                snippet=image_json_snippet(path=str(path)),
            ))
    return images


def extract_images_list(raw: Any) -> list[dict]:
    """Normalize top-level `images` fields (tool_result.images, etc.)."""
    images: list[dict] = []
    if not raw:
        return images
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return images
    for item in raw:
        if isinstance(item, str):
            if item.startswith("data:image"):
                images.append(image_ref_data(item))
            elif is_image_path(item) or "/" in item or "\\" in item:
                images.append(image_ref_file(item, item))
        elif isinstance(item, dict):
            images.extend(collect_image_blocks(item))
            # bare url field
            if not images and item.get("url"):
                url = item["url"]
                if isinstance(url, str) and url.startswith("data:image"):
                    images.append(image_ref_data(url))
    return images


def extract_image_paths_from_text(text: str) -> list[str]:
    """Paths from <image_files>…</image_files> and other image path mentions."""
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()

    def add(p: str) -> None:
        p = p.strip().strip("`'\"")
        # strip trailing punctuation from prose
        p = p.rstrip(".,;:)")
        if not p or p in seen:
            return
        if not is_image_path(p):
            return
        seen.add(p)
        found.append(p)

    for block in IMAGE_FILES_BLOCK_RE.findall(text):
        for m in IMAGE_PATH_RE.finditer(block):
            add(m.group("path"))
        # numbered lines: "1. C:\...\file.png"
        for line in block.splitlines():
            line = re.sub(r"^\s*\d+\.\s*", "", line).strip()
            if is_image_path(line) or IMAGE_PATH_RE.search(line):
                m = IMAGE_PATH_RE.search(line)
                if m:
                    add(m.group("path"))
                elif is_image_path(line):
                    add(line)

    # "Read image file: path" and loose path mentions that look like image assets
    for m in re.finditer(
        r"(?:Read image file|image file|saved to|Image\s*#\d+)\s*[:\-]?\s*(?P<path>[^\s]+?\.(?:png|jpe?g|gif|webp|bmp|svg|ico|avif))",
        text,
        re.IGNORECASE,
    ):
        add(m.group("path"))

    for m in IMAGE_PATH_RE.finditer(text):
        path = m.group("path")
        # Prefer session asset / explicit image paths to reduce false positives
        if "assets" in path.replace("\\", "/").lower() or "image-" in path.lower() or path.lower().startswith(("c:", "d:", "/", "~")):
            add(path)

    return found


def extract_data_images_from_text(text: str) -> tuple[str, list[dict]]:
    """
    Pull inline data-URL images (and JSON image objects) out of text so we can
    render them, leaving a short placeholder in the text.
    """
    if not text or "data:image" not in text:
        return text, []

    images: list[dict] = []
    out = text

    def keep_json(match: re.Match) -> str:
        url = match.group(1)
        url_clean = re.sub(r"\s+", "", url)
        if url_clean.startswith("data:image"):
            snippet = image_json_snippet(url=url_clean)
            images.append(image_ref_data(url_clean, snippet=snippet))
            # Keep a short JSON snippet in the transcript text
            return snippet
        return match.group(0)

    def keep_url(match: re.Match) -> str:
        url = re.sub(r"\s+", "", match.group(1))
        if url.startswith("data:image"):
            snippet = image_json_snippet(url=url)
            images.append(image_ref_data(url, snippet=snippet))
            return snippet
        return match.group(0)

    out = DATA_IMAGE_JSON_RE.sub(keep_json, out)
    # Only replace long base64 payloads still left in free text
    if "data:image" in out:
        out = DATA_IMAGE_URL_RE.sub(keep_url, out)

    return out, images


def resolve_session_image_path(
    path_str: str,
    session_dir: Path | None = None,
    cwd: str | None = None,
) -> Path | None:
    """Resolve absolute/relative image paths for media serving."""
    if not path_str:
        return None
    raw = path_str.strip().strip("`'\"")
    # Expand ~ 
    if raw.startswith("~"):
        raw = str(Path(raw).expanduser())

    candidates: list[Path] = []
    p = Path(raw)
    if p.is_absolute():
        candidates.append(p)
    else:
        if session_dir is not None:
            candidates.append(session_dir / raw)
            candidates.append(session_dir / "assets" / raw)
        if cwd:
            candidates.append(Path(cwd) / raw)

    for c in candidates:
        try:
            if c.is_file() and is_image_path(c):
                return c.resolve()
        except Exception:
            continue

    # Absolute path that exists even if Path quirks on encoded session folders
    try:
        if p.is_file() and is_image_path(p):
            return p.resolve()
    except Exception:
        pass
    return None


def extract_text_and_images(
    content: Any,
    *,
    extra_images: Any = None,
    session_dir: Path | None = None,
    cwd: str | None = None,
) -> tuple[str, list[dict]]:
    """Like extract_text, but also returns renderable image refs."""
    images: list[dict] = []
    images.extend(extract_images_list(extra_images))

    if content is None:
        text = ""
    elif isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                t = block.get("type", "")
                if t in ("text", "output_text", "input_text", "summary_text"):
                    parts.append(block.get("text") or block.get("content") or "")
                elif t == "thinking":
                    parts.append(f"[thinking]\n{block.get('thinking') or block.get('text') or ''}")
                elif t == "tool_use":
                    name = block.get("name", "?")
                    inp = block.get("input")
                    parts.append(f"[tool_use] {name}({truncate(json.dumps(inp, default=str), 120)})")
                elif t == "tool_result":
                    c = block.get("content")
                    if isinstance(c, list):
                        sub_text, sub_imgs = extract_text_and_images(c, session_dir=session_dir, cwd=cwd)
                        images.extend(sub_imgs)
                        c = sub_text
                    parts.append(f"[tool_result]\n{str(c)}")
                elif t in ("image", "input_image", "output_image"):
                    # Preview + JSON snippet rendered above the image in the UI
                    images.extend(collect_image_blocks(block))
                elif t == "content":
                    sub_text, sub_imgs = extract_text_and_images(
                        block.get("content"), session_dir=session_dir, cwd=cwd
                    )
                    images.extend(sub_imgs)
                    parts.append(sub_text)
                else:
                    nested = block.get("text") or block.get("content")
                    if nested is not None:
                        sub_text, sub_imgs = extract_text_and_images(
                            nested, session_dir=session_dir, cwd=cwd
                        )
                        images.extend(sub_imgs)
                        parts.append(sub_text)
                    else:
                        # Avoid dumping huge base64 blobs into the transcript text
                        dump = {
                            k: (v if k not in ("url", "data", "encrypted_content") else f"<{k}>")
                            for k, v in block.items()
                        }
                        parts.append(truncate(json.dumps(dump, default=str), 150))
        text = "\n".join(p for p in parts if str(p).strip())
    elif isinstance(content, dict):
        if (content.get("type") or "").lower() in ("image", "input_image", "output_image"):
            images.extend(collect_image_blocks(content))
            text = ""
        else:
            text, sub = extract_text_and_images(
                content.get("content")
                or content.get("text")
                or content.get("message")
                or content.get("output"),
                session_dir=session_dir,
                cwd=cwd,
            )
            images.extend(sub)
    else:
        text = str(content)

    # Inline data URLs / JSON image objects embedded in text
    text, inline = extract_data_images_from_text(text)
    images.extend(inline)

    # File paths from <image_files> and path mentions
    for path_str in extract_image_paths_from_text(text):
        resolved = resolve_session_image_path(path_str, session_dir=session_dir, cwd=cwd)
        if resolved is not None:
            images.append(image_ref_file(str(resolved), path_str))
        else:
            # Still link via /media with original path (route will 404 if missing)
            images.append(image_ref_file(path_str, path_str))

    # Deduplicate while preserving order
    deduped: list[dict] = []
    seen: set[str] = set()
    for img in images:
        key = img.get("url") or img.get("path") or json.dumps(img, sort_keys=True)
        # For huge data URLs, key on prefix + length
        if isinstance(key, str) and key.startswith("data:") and len(key) > 80:
            key = f"{key[:64]}:{len(key)}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(img)

    return text, deduped


def extract_text(content: Any) -> str:
    text, _ = extract_text_and_images(content)
    return text


def linkify_image_paths_html(text: str, images: list[dict] | None = None) -> str:
    """
    Escape text for HTML, then turn known image paths into clickable links.
    Returns safe HTML.
    """
    from html import escape

    if not text:
        return ""
    html = escape(text)
    paths: list[str] = []
    if images:
        for img in images:
            if img.get("kind") == "file" and img.get("path"):
                paths.append(img["path"])
                if img.get("label") and img["label"] != img["path"]:
                    paths.append(img["label"])
    # Also paths still visible in text
    paths.extend(extract_image_paths_from_text(text))

    # Longest first so nested prefixes don't break replacement
    for path in sorted(set(paths), key=len, reverse=True):
        if not path or path not in text:
            # label may appear even when resolved path differs
            pass
        esc_path = escape(path)
        if esc_path not in html:
            continue
        href = "/media?path=" + quote(path, safe="")
        link = (
            f'<a class="img-path-link" href="{escape(href)}" '
            f'target="_blank" rel="noopener">{esc_path}</a>'
        )
        html = html.replace(esc_path, link)
    return html


def make_turn(
    *,
    role: str,
    text: str = "",
    time: str = "",
    id: str = "",
    model: str = "",
    meta: str = "",
    images: list[dict] | None = None,
) -> dict:
    imgs = images or []
    return {
        "role": role,
        "time": time,
        "id": id,
        "text": text,
        "model": model,
        "meta": meta,
        "images": imgs,
        # Pre-rendered HTML with clickable image paths (safe/escaped)
        "html": linkify_image_paths_html(text, imgs),
    }


def format_tool_args(arguments: Any) -> str:
    if arguments is None:
        return ""
    if isinstance(arguments, str):
        try:
            return pretty_json(json.loads(arguments))
        except Exception:
            return arguments
    return pretty_json(arguments)


# ─────────────────────────────────────────────
# Discovery
# ─────────────────────────────────────────────

def discover_grok() -> list[dict]:
    sessions = []
    root = GROK_HOME / "sessions"
    if not root.exists():
        return sessions

    for group in sorted(root.iterdir()):
        if not group.is_dir():
            continue
        # Encoded cwd folder names: C%3A%5CUsers%5C...
        cwd_hint = unquote(group.name.replace("%2F", "/").replace("%3A", ":").replace("%5C", "\\"))
        cwd_file = group / ".cwd"
        if cwd_file.exists():
            try:
                cwd_hint = cwd_file.read_text(encoding="utf-8").strip() or cwd_hint
            except Exception:
                pass

        for sid_dir in group.iterdir():
            if not sid_dir.is_dir():
                continue
            summary_path = sid_dir / "summary.json"
            meta = load_json(summary_path) or {}
            info = meta.get("info") if isinstance(meta.get("info"), dict) else {}

            title = (
                meta.get("generated_title")
                or meta.get("session_summary")
                or meta.get("title")
                or meta.get("summary")
                or meta.get("name")
                or sid_dir.name[:12]
            )
            sessions.append({
                "agent": "grok",
                "id": info.get("id") or sid_dir.name,
                "path": str(sid_dir),
                "cwd": info.get("cwd") or meta.get("cwd") or cwd_hint,
                "title": str(title)[:120],
                "created": meta.get("created_at") or meta.get("created"),
                "updated": meta.get("updated_at") or meta.get("last_active_at") or meta.get("updated"),
                "model": meta.get("current_model_id") or meta.get("model") or meta.get("model_id"),
                "messages": meta.get("num_chat_messages") or meta.get("num_messages") or meta.get("message_count"),
            })
    return sessions


def discover_claude() -> list[dict]:
    sessions = []
    root = CLAUDE_HOME / "projects"
    if not root.exists():
        return sessions

    for proj in root.iterdir():
        if not proj.is_dir():
            continue
        encoded = proj.name
        cwd_hint = "/" + encoded.lstrip("-").replace("--", "/.").replace("-", "/")

        for f in proj.glob("*.jsonl"):
            if f.name.startswith("."):
                continue
            sid = f.stem
            created = updated = model = None
            msg_count = 0
            try:
                with f.open(encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()
                msg_count = len(lines)
                sample = lines[:8] + lines[-8:]
                for line in sample:
                    try:
                        obj = json.loads(line)
                        ts = obj.get("timestamp")
                        if ts:
                            created = created or ts
                            updated = ts
                        if obj.get("type") == "assistant":
                            model = (obj.get("message") or {}).get("model") or model
                    except Exception:
                        pass
            except Exception:
                pass

            sessions.append({
                "agent": "claude",
                "id": sid,
                "path": str(f),
                "cwd": cwd_hint,
                "title": sid[:18] + "…",
                "created": created,
                "updated": updated,
                "model": model,
                "messages": msg_count,
            })
    return sessions


def load_codex_session_index() -> dict[str, dict]:
    """Map session id → {thread_name, updated_at} from ~/.codex/session_index.jsonl."""
    index: dict[str, dict] = {}
    path = CODEX_HOME / "session_index.jsonl"
    if not path.exists():
        return index
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                sid = obj.get("id")
                if not sid:
                    continue
                # Later lines win (index may list the same id more than once)
                index[str(sid)] = {
                    "thread_name": obj.get("thread_name") or "",
                    "updated_at": obj.get("updated_at") or "",
                }
    except Exception:
        pass
    return index


def discover_codex() -> list[dict]:
    sessions = []
    titles = load_codex_session_index()

    for sub in ("sessions", "archived_sessions"):
        root = CODEX_HOME / sub
        if not root.exists():
            continue
        for f in root.rglob("rollout-*.jsonl"):
            sid = f.stem
            # Prefer UUID from filename suffix when present
            for part in f.stem.split("-"):
                if len(part) >= 32 and part.count("-") >= 0:
                    pass
            # rollout-2026-07-26T16-39-31-019f9edd-ea9c-7741-ad03-59daedd955a2
            m = re.search(
                r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
                f.stem,
                re.I,
            )
            if m:
                sid = m.group(1)

            created = updated = model = cwd = None
            msg_count = 0
            try:
                with f.open(encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh):
                        if not line.strip():
                            continue
                        msg_count += 1
                        # Meta usually at the start; still accept model updates early
                        if i > 120 and created and cwd and model:
                            # Fast-count the rest of the file without full JSON parse
                            for rest in fh:
                                if rest.strip():
                                    msg_count += 1
                            break
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        ts = obj.get("timestamp")
                        if ts:
                            created = created or ts
                            updated = ts
                        t = obj.get("type")
                        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
                        if t == "session_meta":
                            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else payload
                            cwd = meta.get("cwd") or cwd
                            for key in ("id", "session_id"):
                                if isinstance(meta.get(key), str) and meta[key]:
                                    sid = meta[key]
                                    break
                            if meta.get("timestamp"):
                                created = created or meta.get("timestamp")
                        elif t == "turn_context":
                            model = payload.get("model") or model
                            cwd = payload.get("cwd") or cwd
                        elif t == "event_msg" and (payload.get("type") == "thread_settings_applied"):
                            settings = payload.get("thread_settings") or {}
                            model = settings.get("model") or model
                            cwd = settings.get("cwd") or cwd
            except Exception:
                pass

            # Prefer index timestamp / file mtime for "updated" (early scan may miss the end)
            try:
                mtime_iso = datetime.fromtimestamp(f.stat().st_mtime).isoformat()
            except Exception:
                mtime_iso = None
            idx = titles.get(sid) or {}
            title = idx.get("thread_name") or f.name[:55]
            if idx.get("updated_at"):
                updated = idx["updated_at"]
            elif mtime_iso:
                updated = mtime_iso
            elif not updated and mtime_iso:
                updated = mtime_iso

            sessions.append({
                "agent": "codex",
                "id": sid,
                "path": str(f),
                "cwd": cwd or "?",
                "title": str(title)[:120],
                "created": created,
                "updated": updated,
                "model": model,
                "messages": msg_count,
            })
    return sessions


def all_sessions(agent: str | None = None) -> list[dict]:
    items = []
    if agent in (None, "grok", "all"):
        items.extend(discover_grok())
    if agent in (None, "claude", "all"):
        items.extend(discover_claude())
    if agent in (None, "codex", "all"):
        items.extend(discover_codex())

    def key(s):
        return s.get("updated") or s.get("created") or ""

    items.sort(key=key, reverse=True)
    return items


# ─────────────────────────────────────────────
# Grok session context (summary, todos, side files)
# ─────────────────────────────────────────────

def grok_token_usage(path: Path) -> dict:
    """
    Estimate session token usage by summing turn_completed.usage from updates.jsonl.
    Also pulls latest context-window stats from signals.json when present.
    """
    usage = {
        "input": 0,
        "output": 0,
        "total": 0,
        "cached": 0,
        "reasoning": 0,
        "model_calls": 0,
        "api_duration_ms": 0,
        "turns": 0,
        "uncached_input": 0,
        "by_model": {},
        "context_used": None,
        "context_window": None,
        "context_pct": None,
        "available": False,
        "input_fmt": "—",
        "output_fmt": "—",
        "total_fmt": "—",
        "cached_fmt": "—",
        "reasoning_fmt": "—",
        "uncached_fmt": "—",
        "source": "",
    }

    updates = path / "updates.jsonl"
    if updates.exists():
        try:
            with updates.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    # Fast reject — most lines are streaming chunks
                    if "turn_completed" not in line or "usage" not in line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    update = ((obj.get("params") or {}).get("update") or {})
                    if update.get("sessionUpdate") != "turn_completed":
                        continue
                    u = update.get("usage") or {}
                    if not isinstance(u, dict):
                        continue
                    usage["turns"] += 1
                    usage["input"] += int(u.get("inputTokens") or 0)
                    usage["output"] += int(u.get("outputTokens") or 0)
                    usage["total"] += int(u.get("totalTokens") or 0)
                    usage["cached"] += int(u.get("cachedReadTokens") or 0)
                    usage["reasoning"] += int(u.get("reasoningTokens") or 0)
                    usage["model_calls"] += int(u.get("modelCalls") or 0)
                    usage["api_duration_ms"] += int(u.get("apiDurationMs") or 0)

                    mu = u.get("modelUsage") or {}
                    if isinstance(mu, dict):
                        for model, stats in mu.items():
                            if not isinstance(stats, dict):
                                continue
                            bucket = usage["by_model"].setdefault(
                                model,
                                {"input": 0, "output": 0, "cached": 0, "reasoning": 0, "model_calls": 0},
                            )
                            bucket["input"] += int(stats.get("inputTokens") or 0)
                            bucket["output"] += int(stats.get("outputTokens") or 0)
                            bucket["cached"] += int(stats.get("cachedReadTokens") or 0)
                            bucket["reasoning"] += int(stats.get("reasoningTokens") or 0)
                            bucket["model_calls"] += int(stats.get("modelCalls") or 0)
        except Exception:
            pass

    if usage["turns"] > 0 or usage["input"] or usage["output"]:
        usage["available"] = True
        usage["source"] = "updates.jsonl · sum of turn_completed"
        usage["uncached_input"] = max(0, usage["input"] - usage["cached"])
        usage["input_fmt"] = format_tokens(usage["input"])
        usage["output_fmt"] = format_tokens(usage["output"])
        usage["total_fmt"] = format_tokens(usage["total"] or (usage["input"] + usage["output"]))
        usage["cached_fmt"] = format_tokens(usage["cached"])
        usage["reasoning_fmt"] = format_tokens(usage["reasoning"])
        usage["uncached_fmt"] = format_tokens(usage["uncached_input"])

        # Stacked bar shares (percent of in+out). Reasoning is part of output.
        bar_total = max(usage["input"] + usage["output"], 1)
        out_non_reason = max(usage["output"] - usage["reasoning"], 0)
        usage["bar"] = {
            "uncached_pct": round(100.0 * usage["uncached_input"] / bar_total, 2),
            "cached_pct": round(100.0 * usage["cached"] / bar_total, 2),
            "out_pct": round(100.0 * out_non_reason / bar_total, 2),
            "reason_pct": round(100.0 * usage["reasoning"] / bar_total, 2),
        }

        # Pre-format per-model rows for the template
        model_rows = []
        for model, stats in sorted(usage["by_model"].items(), key=lambda kv: -kv[1]["input"]):
            model_rows.append({
                "model": model,
                "input_fmt": format_tokens(stats["input"]),
                "output_fmt": format_tokens(stats["output"]),
                "cached_fmt": format_tokens(stats["cached"]),
                "reasoning_fmt": format_tokens(stats["reasoning"]),
                "model_calls": stats["model_calls"],
            })
        usage["by_model_rows"] = model_rows
    else:
        usage["by_model_rows"] = []
        usage["bar"] = {"uncached_pct": 0, "cached_pct": 0, "out_pct": 0, "reason_pct": 0}

    signals = load_json(path / "signals.json") or {}
    if isinstance(signals, dict):
        ctx_used = signals.get("contextTokensUsed")
        ctx_win = signals.get("contextWindowTokens")
        if ctx_used is not None:
            usage["context_used"] = int(ctx_used)
            usage["context_used_fmt"] = format_tokens(ctx_used)
        if ctx_win is not None:
            usage["context_window"] = int(ctx_win)
            usage["context_window_fmt"] = format_tokens(ctx_win)
        if usage["context_used"] is not None and usage["context_window"]:
            usage["context_pct"] = round(100.0 * usage["context_used"] / usage["context_window"], 1)

        # Fallback estimate when no turn_completed records exist
        if not usage["available"] and usage["context_used"]:
            usage["available"] = True
            usage["source"] = "signals.json · context only (no turn totals)"
            usage["input"] = usage["context_used"]
            usage["input_fmt"] = format_tokens(usage["context_used"])
            usage["total_fmt"] = format_tokens(usage["context_used"])

    return usage


def grok_summary_card(path: Path) -> dict:
    meta = load_json(path / "summary.json") or {}
    info = meta.get("info") if isinstance(meta.get("info"), dict) else {}
    tokens = grok_token_usage(path)
    return {
        "id": info.get("id") or path.name,
        "title": meta.get("generated_title") or meta.get("session_summary") or path.name,
        "session_summary": meta.get("session_summary") or "",
        "cwd": info.get("cwd") or "",
        "created": human_time(meta.get("created_at")),
        "updated": human_time(meta.get("updated_at") or meta.get("last_active_at")),
        "model": meta.get("current_model_id") or "",
        "agent_name": meta.get("agent_name") or "",
        "sandbox_profile": meta.get("sandbox_profile") or "",
        "reasoning_effort": meta.get("reasoning_effort") or "",
        "num_messages": meta.get("num_messages"),
        "num_chat_messages": meta.get("num_chat_messages"),
        "request_id": meta.get("request_id") or "",
        "head_branch": meta.get("head_branch") or "",
        "head_commit": (meta.get("head_commit") or "")[:12],
        "git_root_dir": meta.get("git_root_dir") or "",
        "tokens": tokens,
    }


def grok_resources(path: Path) -> dict:
    data = load_json(path / "resources_state.json") or {}
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    state = data.get("state") if isinstance(data.get("state"), dict) else {}

    todos: list[dict] = []
    todo_blob = state.get("grok_build.Todo") or state.get("Todo") or {}
    if isinstance(todo_blob, dict):
        items = todo_blob.get("todos") if isinstance(todo_blob.get("todos"), dict) else todo_blob
        if isinstance(items, dict):
            for tid, item in items.items():
                if not isinstance(item, dict):
                    continue
                todos.append({
                    "id": str(tid),
                    "content": item.get("content") or "",
                    "status": item.get("status") or "unknown",
                    "priority": item.get("priority") or "",
                })
            # Keep insertion order from file; status sort secondary
            status_rank = {"in_progress": 0, "pending": 1, "completed": 2, "cancelled": 3}
            todos.sort(key=lambda t: (status_rank.get(t["status"], 9), t["id"]))

    scheduler = state.get("grok_build.Scheduler") or {}
    tasks = []
    if isinstance(scheduler, dict):
        raw_tasks = scheduler.get("tasks") or []
        if isinstance(raw_tasks, list):
            for t in raw_tasks:
                if isinstance(t, dict):
                    tasks.append(t)
                else:
                    tasks.append({"id": str(t)})

    reported = []
    rtc = state.get("grok_build.ReportedTaskCompletions") or {}
    if isinstance(rtc, dict) and isinstance(rtc.get("reported"), list):
        reported = [str(x) for x in rtc["reported"]]

    # Settings: only show non-null / non-default-ish values for readability
    settings: list[dict] = []
    for tool_name, conf in params.items():
        if not isinstance(conf, dict):
            settings.append({"tool": tool_name, "key": "", "value": pretty_json(conf, 200)})
            continue
        for key, val in conf.items():
            if val is None:
                continue
            settings.append({
                "tool": tool_name.replace("grok_build.", ""),
                "key": key,
                "value": pretty_json(val, 200) if not isinstance(val, (str, int, float, bool)) else str(val),
            })

    other_state = []
    artifacts = []
    skip = {"grok_build.Todo", "Todo", "grok_build.Scheduler", "grok_build.ReportedTaskCompletions"}
    for k, v in state.items():
        if k in skip:
            continue
        label = k.replace("grok_build.", "")
        # Prefer collapsible artifacts for larger / document-like blobs
        if isinstance(v, str) and len(v) > 200:
            artifacts.append({
                "id": f"state-{label}",
                "title": label,
                "subtitle": "resources_state",
                "kind": "markdown",
                "text": v,
            })
        elif isinstance(v, (dict, list)) and len(pretty_json(v, 50000)) > 400:
            artifacts.append({
                "id": f"state-{label}",
                "title": label,
                "subtitle": "resources_state · json",
                "kind": "json",
                "text": pretty_json(v, 200000),
            })
        else:
            other_state.append({"key": label, "value": pretty_json(v, 400)})

    return {
        "todos": todos,
        "scheduler_tasks": tasks,
        "reported_completions": reported,
        "settings": settings,
        "other_state": other_state,
        "artifacts": artifacts,
    }


def grok_hunk_records(path: Path) -> list[dict]:
    f = path / "hunk_records.jsonl"
    if not f.exists():
        return []
    rows = []
    try:
        with f.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                rows.append({
                    "hunk_id": o.get("hunkId") or o.get("hunk_id") or "",
                    "file_path": o.get("filePath") or o.get("file_path") or "",
                    "event": o.get("eventType") or o.get("event") or "",
                    "source": o.get("sourceType") or "",
                    "added": o.get("linesAdded"),
                    "removed": o.get("linesRemoved"),
                    "start": o.get("hunkStart"),
                    "end": o.get("hunkEnd"),
                    "prompt_index": o.get("promptIndex"),
                    "time": human_time(o.get("timestamp")),
                    "author_id": o.get("authorId") or o.get("agentId") or "",
                })
    except Exception:
        pass
    return rows


def grok_terminal_logs(path: Path) -> list[dict]:
    td = path / "terminal"
    if not td.is_dir():
        return []
    logs = []
    for f in sorted(td.glob("*.log")):
        call_id = f.stem  # call-...
        size = f.stat().st_size
        preview = ""
        try:
            preview = f.read_text(encoding="utf-8", errors="replace")
            if len(preview) > 400:
                preview = preview[:400] + "…"
        except Exception:
            preview = ""
        logs.append({
            "id": call_id,
            "path": str(f),
            "size": size,
            "preview": preview,
        })
    return logs


def grok_recap_requests(path: Path) -> list[dict]:
    rd = path / "recap_requests"
    if not rd.is_dir():
        return []
    items = []
    for f in sorted(rd.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        meta = load_json(f) or {}
        items.append({
            "id": meta.get("request_id") or f.stem,
            "path": str(f),
            "created": human_time(meta.get("created_at")),
            "trigger": meta.get("trigger") or "",
            "model": meta.get("model") or "",
            "size": f.stat().st_size,
            "strip_reasoning": meta.get("strip_reasoning"),
            "x_grok_req_id": meta.get("x_grok_req_id") or "",
            "chat_len": len(meta.get("chat_history") or []) if isinstance(meta.get("chat_history"), list) else None,
        })
    return items


def grok_updates_timeline(path: Path, max_events: int = 400) -> list[dict]:
    """Aggregate streaming updates.jsonl into a readable timeline."""
    f = path / "updates.jsonl"
    if not f.exists():
        return []

    events: list[dict] = []
    # Open buffers for streaming chunks
    user_buf: list[str] = []
    thought_buf: list[str] = []
    message_buf: list[str] = []
    tool_final: dict[str, dict] = {}  # toolCallId -> last meaningful update
    last_ts = None

    def flush_buf(role: str, parts: list[str], ts) -> None:
        text = "".join(parts).strip()
        parts.clear()
        if not text:
            return
        events.append(make_turn(
            role=role,
            time=display_time(ts),
            text=text,
        ))

    try:
        with f.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                ts = obj.get("timestamp")
                last_ts = ts if ts is not None else last_ts
                params = obj.get("params") or {}
                update = params.get("update") or {}
                kind = update.get("sessionUpdate") or ""

                if kind == "user_message_chunk":
                    if thought_buf:
                        flush_buf("reasoning", thought_buf, last_ts)
                    if message_buf:
                        flush_buf("assistant", message_buf, last_ts)
                    user_buf.append(extract_text(update.get("content")))
                elif kind == "agent_thought_chunk":
                    if user_buf:
                        flush_buf("user", user_buf, last_ts)
                    if message_buf:
                        flush_buf("assistant", message_buf, last_ts)
                    thought_buf.append(extract_text(update.get("content")))
                elif kind == "agent_message_chunk":
                    if user_buf:
                        flush_buf("user", user_buf, last_ts)
                    if thought_buf:
                        flush_buf("reasoning", thought_buf, last_ts)
                    message_buf.append(extract_text(update.get("content")))
                elif kind == "tool_call":
                    if user_buf:
                        flush_buf("user", user_buf, last_ts)
                    if thought_buf:
                        flush_buf("reasoning", thought_buf, last_ts)
                    if message_buf:
                        flush_buf("assistant", message_buf, last_ts)
                    tcid = update.get("toolCallId") or update.get("tool_call_id") or ""
                    title = update.get("title") or update.get("kind") or "tool"
                    raw_in = update.get("rawInput") or update.get("raw_input") or update.get("input")
                    text = f"{title}\nid: {tcid}\n{format_tool_args(raw_in)}" if tcid else f"{title}\n{format_tool_args(raw_in)}"
                    events.append(make_turn(
                        role="tool_call",
                        time=display_time(ts, tcid),
                        id=tcid,
                        text=text,
                    ))
                    tool_final[tcid] = {"status": "started", "title": title}
                elif kind == "tool_call_update":
                    tcid = update.get("toolCallId") or update.get("tool_call_id") or ""
                    status = update.get("status") or update.get("kind") or ""
                    # Keep latest status / content snapshot; emit only terminal-ish states later
                    prev = tool_final.get(tcid) or {}
                    content = extract_text(update.get("content") or update.get("rawOutput") or update.get("raw_output"))
                    if content:
                        prev["content"] = content
                    if status:
                        prev["status"] = status
                    if update.get("title"):
                        prev["title"] = update["title"]
                    prev["ts"] = ts
                    tool_final[tcid] = prev
                    # Emit completed/failed updates inline
                    if str(status).lower() in ("completed", "failed", "error", "cancelled"):
                        body = prev.get("content") or ""
                        events.append(make_turn(
                            role="tool_result",
                            time=display_time(ts, tcid),
                            id=tcid,
                            text=f"status: {status}\nid: {tcid}\n{body}".strip(),
                        ))
                elif kind == "task_backgrounded":
                    if user_buf:
                        flush_buf("user", user_buf, last_ts)
                    if thought_buf:
                        flush_buf("reasoning", thought_buf, last_ts)
                    if message_buf:
                        flush_buf("assistant", message_buf, last_ts)
                    tid = update.get("task_id") or update.get("tool_call_id") or ""
                    cmd = update.get("command") or ""
                    out = update.get("output_file") or ""
                    events.append(make_turn(
                        role="event",
                        time=display_time(ts, tid),
                        id=tid,
                        text=f"task_backgrounded\nid: {tid}\ncommand: {cmd}\noutput_file: {out}",
                    ))
                elif kind == "task_completed":
                    snap = update.get("task_snapshot") or update
                    tid = snap.get("task_id") or update.get("task_id") or ""
                    out = snap.get("output") or ""
                    if len(str(out)) > 3000:
                        out = str(out)[:3000] + "…"
                    events.append(make_turn(
                        role="event",
                        time=display_time(ts, tid),
                        id=tid,
                        text=f"task_completed\nid: {tid}\ncommand: {snap.get('command') or ''}\n{out}",
                    ))
                elif kind == "turn_completed":
                    if user_buf:
                        flush_buf("user", user_buf, last_ts)
                    if thought_buf:
                        flush_buf("reasoning", thought_buf, last_ts)
                    if message_buf:
                        flush_buf("assistant", message_buf, last_ts)
                    usage = update.get("usage") or {}
                    usage_txt = pretty_json(usage, 800) if usage else ""
                    events.append(make_turn(
                        role="event",
                        time=display_time(ts, update.get("prompt_id")),
                        id=update.get("prompt_id") or "",
                        text=f"turn_completed · stop={update.get('stop_reason') or '?'}\n{usage_txt}",
                    ))

        if user_buf:
            flush_buf("user", user_buf, last_ts)
        if thought_buf:
            flush_buf("reasoning", thought_buf, last_ts)
        if message_buf:
            flush_buf("assistant", message_buf, last_ts)
    except Exception:
        pass

    if len(events) > max_events:
        head = events[: max_events // 2]
        tail = events[-(max_events // 2) :]
        marker = [make_turn(
            role="event",
            text=f"… {len(events) - max_events} updates omitted for display …",
        )]
        return head + marker + tail
    return events


def grok_terminal_map(path: Path) -> dict[str, str]:
    """Map tool/call id -> full log text (capped)."""
    td = path / "terminal"
    out: dict[str, str] = {}
    if not td.is_dir():
        return out
    for f in td.glob("*.log"):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            if len(text) > 20000:
                text = text[:20000] + "\n… [truncated]"
            out[f.stem] = text
        except Exception:
            continue
    return out


# ─────────────────────────────────────────────
# Conversation extractors
# ─────────────────────────────────────────────

def get_grok_conversation(path: Path) -> list[dict]:
    """Parse chat_history.jsonl with reasoning, tool calls, and terminal enrichment."""
    history = path / "chat_history.jsonl" if path.is_dir() else path
    if path.is_dir() and not history.exists():
        # fallback older layout
        for alt in (path / "updates.jsonl",):
            if alt.exists():
                return grok_updates_timeline(path)
        return []

    session_dir = path if path.is_dir() else path.parent
    term_map = grok_terminal_map(session_dir)
    session_cwd = None
    try:
        meta = load_json(session_dir / "summary.json") or {}
        info = meta.get("info") if isinstance(meta.get("info"), dict) else {}
        session_cwd = info.get("cwd") or meta.get("cwd")
    except Exception:
        pass

    turns: list[dict] = []
    idx = 0

    def content_pair(raw: Any, extra_images: Any = None) -> tuple[str, list[dict]]:
        return extract_text_and_images(
            raw,
            extra_images=extra_images,
            session_dir=session_dir,
            cwd=session_cwd,
        )

    try:
        with history.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue

                msg_type = (obj.get("type") or obj.get("role") or "event").lower()
                model = obj.get("model_id") or obj.get("model") or ""
                idx += 1
                seq = f"#{idx}"

                if msg_type == "reasoning":
                    rid = obj.get("id") or seq
                    summary_parts = []
                    for block in obj.get("summary") or []:
                        if isinstance(block, dict):
                            summary_parts.append(block.get("text") or extract_text(block))
                        else:
                            summary_parts.append(str(block))
                    summary_text = "\n".join(p for p in summary_parts if p and str(p).strip())
                    body_parts = []
                    if summary_text:
                        body_parts.append(summary_text)
                    if obj.get("encrypted_content"):
                        body_parts.append("<encrypted>")
                    if not body_parts:
                        body_parts.append("<encrypted>" if obj.get("encrypted_content") is not None else "(empty reasoning)")
                    status = obj.get("status") or ""
                    effort = obj.get("reasoning_effort") or ""
                    meta_bits = [b for b in (status, effort) if b]
                    turns.append(make_turn(
                        role="reasoning",
                        time=display_time(obj.get("timestamp"), rid),
                        id=rid,
                        text="\n".join(body_parts),
                        model=model,
                        meta=" · ".join(meta_bits),
                    ))
                    continue

                if msg_type == "assistant":
                    text, images = content_pair(obj.get("content"), obj.get("images"))
                    tool_calls = obj.get("tool_calls") or []
                    first_tc_id = None
                    if tool_calls and isinstance(tool_calls, list):
                        first_tc_id = (tool_calls[0] or {}).get("id")
                    aid = obj.get("id") or first_tc_id or seq
                    if text.strip() or images:
                        turns.append(make_turn(
                            role="assistant",
                            time=display_time(obj.get("timestamp"), aid),
                            id=aid if not text else (obj.get("id") or ""),
                            text=text,
                            model=model,
                            meta=obj.get("reasoning_effort") or "",
                            images=images,
                        ))
                    for tc in tool_calls:
                        if not isinstance(tc, dict):
                            continue
                        tcid = tc.get("id") or ""
                        name = tc.get("name") or "tool"
                        args = format_tool_args(tc.get("arguments") or tc.get("input"))
                        body = f"{name}\nid: {tcid}\n{args}".strip()
                        # Tool args may embed image paths
                        _, tc_images = content_pair(body)
                        turns.append(make_turn(
                            role="tool_call",
                            time=display_time(obj.get("timestamp"), tcid or seq),
                            id=tcid,
                            text=body,
                            model=model,
                            meta=name,
                            images=tc_images,
                        ))
                    if not text.strip() and not tool_calls and not images:
                        turns.append(make_turn(
                            role="assistant",
                            time=display_time(obj.get("timestamp"), seq),
                            id=seq,
                            text="(empty assistant message)",
                            model=model,
                        ))
                    continue

                if msg_type == "tool_result":
                    tcid = obj.get("tool_call_id") or obj.get("toolCallId") or ""
                    content, images = content_pair(obj.get("content"), obj.get("images"))
                    # Enrich from terminal log when result only points at a log / is thin
                    log_text = term_map.get(tcid) if tcid else None
                    if log_text:
                        if (not content.strip()
                                or "output-file" in content
                                or "<output-file>" in content
                                or len(content) < 80):
                            content = (content + "\n\n--- terminal log ---\n" + log_text).strip() if content.strip() else log_text
                            # re-scan log for image paths
                            _, more = content_pair(content)
                            images = images + more
                    turns.append(make_turn(
                        role="tool_result",
                        time=display_time(obj.get("timestamp"), tcid or seq),
                        id=tcid or seq,
                        text=content or "(empty tool result)",
                        images=images,
                    ))
                    continue

                if msg_type in ("user", "system"):
                    text, images = content_pair(obj.get("content"), obj.get("images"))
                    synthetic = obj.get("synthetic_reason") or ""
                    role = msg_type
                    if synthetic:
                        role = "system_reminder" if "reminder" in synthetic else f"user ({synthetic})"
                    if msg_type == "system":
                        role = "system"
                    uid = ""
                    if obj.get("prompt_index") is not None:
                        uid = f"prompt:{obj.get('prompt_index')}"
                    turns.append(make_turn(
                        role=role,
                        time=display_time(obj.get("timestamp"), uid or seq),
                        id=uid,
                        text=text or "(empty)",
                        model=model,
                        meta=synthetic,
                        images=images,
                    ))
                    continue

                if msg_type == "backend_tool_call":
                    kind = obj.get("kind") if isinstance(obj.get("kind"), dict) else {}
                    tool_type = kind.get("tool_type") or "backend_tool"
                    action = kind.get("action") if isinstance(kind.get("action"), dict) else {}
                    action_type = action.get("type") or ""
                    query = action.get("query") or ""
                    sources = action.get("sources") or []
                    lines = [f"{tool_type}" + (f" · {action_type}" if action_type else "")]
                    if query:
                        lines.append(f"query: {query}")
                    if sources:
                        lines.append("sources:")
                        for src in sources[:20]:
                            if isinstance(src, dict):
                                lines.append(f"  - {src.get('url') or src.get('type') or pretty_json(src, 120)}")
                            else:
                                lines.append(f"  - {src}")
                        if len(sources) > 20:
                            lines.append(f"  … +{len(sources) - 20} more")
                    rid = obj.get("id") or seq
                    turns.append(make_turn(
                        role="tool_call",
                        time=display_time(obj.get("timestamp"), rid),
                        id=rid,
                        text="\n".join(lines),
                        model=model,
                        meta=tool_type,
                    ))
                    continue

                # Unknown types — still show something useful
                rid = obj.get("id") or obj.get("tool_call_id") or seq
                text, images = content_pair(
                    obj.get("content") or obj.get("message") or obj.get("text"),
                    obj.get("images"),
                )
                if not text.strip() and not images:
                    dump = {k: v for k, v in obj.items() if k not in ("encrypted_content",)}
                    text = pretty_json(dump, 1200)
                turns.append(make_turn(
                    role=msg_type or "event",
                    time=display_time(obj.get("timestamp"), rid),
                    id=rid,
                    text=text,
                    model=model,
                    images=images,
                ))
    except Exception:
        pass

    return turns


# ─────────────────────────────────────────────
# Codex session context + conversation
# ─────────────────────────────────────────────

def _empty_token_usage() -> dict:
    return {
        "input": 0,
        "output": 0,
        "total": 0,
        "cached": 0,
        "reasoning": 0,
        "model_calls": 0,
        "api_duration_ms": 0,
        "turns": 0,
        "uncached_input": 0,
        "by_model": {},
        "by_model_rows": [],
        "context_used": None,
        "context_window": None,
        "context_pct": None,
        "available": False,
        "input_fmt": "—",
        "output_fmt": "—",
        "total_fmt": "—",
        "cached_fmt": "—",
        "reasoning_fmt": "—",
        "uncached_fmt": "—",
        "source": "",
        "bar": {"uncached_pct": 0, "cached_pct": 0, "out_pct": 0, "reason_pct": 0},
    }


def _finalize_token_usage(usage: dict) -> dict:
    if usage["turns"] > 0 or usage["input"] or usage["output"]:
        usage["available"] = True
        usage["uncached_input"] = max(0, usage["input"] - usage["cached"])
        usage["input_fmt"] = format_tokens(usage["input"])
        usage["output_fmt"] = format_tokens(usage["output"])
        usage["total_fmt"] = format_tokens(usage["total"] or (usage["input"] + usage["output"]))
        usage["cached_fmt"] = format_tokens(usage["cached"])
        usage["reasoning_fmt"] = format_tokens(usage["reasoning"])
        usage["uncached_fmt"] = format_tokens(usage["uncached_input"])
        bar_total = max(usage["input"] + usage["output"], 1)
        out_non_reason = max(usage["output"] - usage["reasoning"], 0)
        usage["bar"] = {
            "uncached_pct": round(100.0 * usage["uncached_input"] / bar_total, 2),
            "cached_pct": round(100.0 * usage["cached"] / bar_total, 2),
            "out_pct": round(100.0 * out_non_reason / bar_total, 2),
            "reason_pct": round(100.0 * usage["reasoning"] / bar_total, 2),
        }
        if usage.get("context_used") is not None and usage.get("context_window"):
            usage["context_used_fmt"] = format_tokens(usage["context_used"])
            usage["context_window_fmt"] = format_tokens(usage["context_window"])
            usage["context_pct"] = round(
                100.0 * usage["context_used"] / max(usage["context_window"], 1), 1
            )
    return usage


def iter_codex_rollout(path: Path):
    """Yield parsed JSON objects from a Codex rollout jsonl."""
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


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

    for obj in records if records is not None else iter_codex_rollout(path):
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
                if text and not first_user and not text.lstrip().startswith(("# AGENTS.md", "<INSTRUCTIONS>")):
                    first_user = text.strip()[:240]

        elif t == "event_msg":
            et = (payload.get("type") or "").lower()
            if et == "user_message":
                counts["user"] += 1
                msg = (payload.get("message") or "").strip()
                if msg and not first_user:
                    first_user = msg[:240]
            elif et == "agent_message":
                counts["assistant"] += 1
            elif et == "task_started":
                counts["task"] += 1
                events.append(make_turn(
                    role="event",
                    time=display_time(ts, payload.get("turn_id")),
                    id=payload.get("turn_id") or "",
                    text=f"task_started\nid: {payload.get('turn_id') or ''}\nmodel_context_window: {payload.get('model_context_window') or ''}",
                    meta="task",
                ))
            elif et == "task_complete":
                events.append(make_turn(
                    role="event",
                    time=display_time(ts, payload.get("turn_id")),
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
                    time=display_time(ts, call_id),
                    id=call_id,
                    text=f"patch_apply_end · success={payload.get('success')}\n{stdout}\nfiles: {', '.join(list(changes)[:12])}",
                    meta="patch",
                ))
            elif et == "image_generation_end":
                events.append(make_turn(
                    role="event",
                    time=display_time(ts, payload.get("call_id")),
                    id=payload.get("call_id") or "",
                    text=(
                        f"image_generation_end · {payload.get('status')}\n"
                        f"saved: {payload.get('saved_path') or '—'}\n"
                        f"{(payload.get('revised_prompt') or '')[:400]}"
                    ),
                    meta="image",
                ))

    # Token usage: cumulative total from last token_count (Codex running total)
    tokens = _empty_token_usage()
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
    tokens = _finalize_token_usage(tokens)

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
    title = (titles.get(str(sid)) or {}).get("thread_name") or first_user or path.name

    summary = {
        "id": sid,
        "title": str(title)[:160],
        "session_summary": first_user,
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
        for obj in records if records is not None else iter_codex_rollout(path):
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
                        time=display_time(ts_raw, rid),
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
                                time=display_time(ts_raw, seq),
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
                            time=display_time(ts_raw, seq),
                            id=seq,
                            text=text,
                            meta="developer",
                            images=images,
                        ))
                        continue
                    turns.append(make_turn(
                        role=role,
                        time=display_time(ts_raw, seq),
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
                        time=display_time(ts_raw, call_id or seq),
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
                        time=display_time(ts_raw, call_id or seq),
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
                            time=display_time(ts_raw, seq),
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
                            time=display_time(ts_raw, seq),
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
                        time=display_time(ts_raw, call_id),
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
                        time=display_time(ts_raw, call_id),
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
                        time=display_time(ts_raw, turn_id),
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
                        time=display_time(ts_raw, seq),
                        id=seq,
                        text=f"# AGENTS.md ({amd.get('directory') or ''})\n\n{amd.get('text')}",
                        meta="agents_md",
                    ))
                continue

    except Exception:
        pass

    return turns


def get_conversation(agent: str, path: Path) -> list[dict]:
    turns: list[dict] = []

    if agent == "grok":
        return get_grok_conversation(path)

    if agent == "claude":
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("type") not in ("user", "assistant"):
                    continue
                msg = obj.get("message") or {}
                role = (msg.get("role") or obj.get("type")).lower()
                text = extract_text(msg.get("content"))
                if text.strip():
                    turns.append(make_turn(
                        role=role,
                        time=display_time(obj.get("timestamp")),
                        text=text,
                        model=msg.get("model", "") or "",
                    ))
        return turns

    if agent == "codex":
        return get_codex_conversation(path)

    return turns


def turns_to_markdown(turns: list[dict], title: str, agent: str, path: str, extra: str = "") -> str:
    lines = [
        f"# {title}",
        "",
        f"**Agent:** {agent}  ",
        f"**Path:** `{path}`  ",
        f"**Exported:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    if extra:
        lines.extend([extra, ""])
    lines.extend(["---", ""])
    for t in turns:
        role = t["role"].upper()
        header = f"### {role}"
        if t.get("time"):
            header += f" · {t['time']}"
        if t.get("id"):
            header += f" · `{t['id']}`"
        lines.append(header)
        if t.get("model"):
            lines.append(f"*Model: {t['model']}*")
        if t.get("meta"):
            lines.append(f"*Meta: {t['meta']}*")
        lines.append("")
        lines.append(t["text"])
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)



# ─────────────────────────────────────────────
# Session load (shared by /view and /export)
# ─────────────────────────────────────────────

def load_session(agent: str, path: Path) -> dict:
    """
    Load everything the view/export routes need for one session.

    Returns turns, title, summary, resources, artifacts, hunks,
    terminal_logs, recaps, and updates (timeline / events tab).
    """
    title = path.name
    turns = []
    summary = None
    resources = None
    artifacts = None
    hunks = None
    terminal_logs = None
    recaps = None
    updates = None

    if agent == "codex" and path.is_file():
        # Parse and decode the rollout once, then reuse those records for both
        # the transcript and its summary/tokens/events/patches.
        records = list(iter_codex_rollout(path))
        scan = codex_scan_session(path, records)
        meta = scan.get("meta") if isinstance(scan.get("meta"), dict) else {}
        turns = get_codex_conversation(path, records, session_cwd=meta.get("cwd"))
        summary = scan["summary"]
        title = summary.get("title") or title
        resources = scan["resources"]
        artifacts = scan.get("artifacts") or []
        hunks = scan["hunks"]
        # Reuse "updates" tab for Codex task/patch/image timeline
        updates = scan["events"]
    else:
        turns = get_conversation(agent, path)

    if agent == "grok" and path.is_dir():
        summary = grok_summary_card(path)
        title = summary.get("title") or title
        resources = grok_resources(path)
        artifacts = (resources or {}).get("artifacts") or []
        hunks = grok_hunk_records(path)
        terminal_logs = grok_terminal_logs(path)
        recaps = grok_recap_requests(path)
        updates = grok_updates_timeline(path)
    return {
        "agent": agent,
        "path": path,
        "title": title,
        "turns": turns,
        "summary": summary,
        "resources": resources,
        "artifacts": artifacts,
        "hunks": hunks,
        "terminal_logs": terminal_logs,
        "recaps": recaps,
        "updates": updates,
    }


def summary_to_markdown(
    summary: dict | None,
    *,
    agent: str,
    resources: dict | None = None,
) -> str:
    """Shared export header (model/cwd/tokens/todos) for Grok and Codex."""
    if not summary:
        return ""

    if agent == "grok":
        lines = [
            f"**Model:** {summary.get('model') or '—'}  ",
            f"**CWD:** `{summary.get('cwd') or '—'}`  ",
            f"**Agent:** {summary.get('agent_name') or '—'}  ",
            f"**Reasoning effort:** {summary.get('reasoning_effort') or '—'}  ",
            f"**Session id:** `{summary.get('id')}`  ",
        ]
    elif agent == "codex":
        lines = [
            f"**Model:** {summary.get('model') or '—'}  ",
            f"**CWD:** `{summary.get('cwd') or '—'}`  ",
            f"**Originator:** {summary.get('agent_name') or '—'}  ",
            f"**Reasoning effort:** {summary.get('reasoning_effort') or '—'}  ",
            f"**Sandbox:** {summary.get('sandbox_profile') or '—'}  ",
            f"**Session id:** `{summary.get('id')}`  ",
        ]
    else:
        return ""

    tok = summary.get("tokens") or {}
    if tok.get("available"):
        lines.extend([
            "",
            "### Estimated token usage",
            f"- **Input:** {tok.get('input_fmt')}  ",
            f"- **Output:** {tok.get('output_fmt')}  ",
            f"- **Cached read:** {tok.get('cached_fmt')}  ",
            f"- **Reasoning:** {tok.get('reasoning_fmt')}  ",
            f"- **Uncached input:** {tok.get('uncached_fmt')}  ",
            f"- **Total:** {tok.get('total_fmt')}  ",
            f"- *Source: {tok.get('source')}*",
        ])

    if resources and resources.get("todos"):
        lines.append("")
        lines.append("### Todos")
        for t in resources["todos"]:
            mark = "x" if t.get("status") == "completed" else " "
            lines.append(
                f"- [{mark}] `{t.get('id')}` {t.get('content')} ({t.get('status')})"
            )

    return "\n".join(lines)


# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────

@app.route("/")
def index():
    agent = request.args.get("agent")
    if agent in ("all", ""):
        agent = None
    q = (request.args.get("q") or "").strip().lower()

    sessions = all_sessions(agent)

    if q:
        sessions = [
            s for s in sessions
            if q in (s.get("title") or "").lower()
            or q in (s.get("id") or "").lower()
            or q in (s.get("cwd") or "").lower()
            or q in (s.get("path") or "").lower()
            or q in (s.get("model") or "").lower()
        ]

    return render_template(
        "list.html",
        title="Sessions",
        sessions=sessions,
        agent=agent,
        q=q,
        grok_path=str(GROK_HOME / "sessions"),
        claude_path=str(CLAUDE_HOME / "projects"),
        codex_path=str(CODEX_HOME / "sessions"),
    )


@app.route("/view")
def view():
    path_str = request.args.get("path")
    agent = request.args.get("agent")
    if not path_str or not agent:
        abort(400, "Missing path or agent")

    path = Path(path_str)
    if not path.exists():
        abort(404, "Session not found")

    if not path_allowed(path):
        abort(403, "Path not allowed")

    session = load_session(agent, path)
    return render_template(
        "view.html",
        agent=agent,
        path=str(path),
        title=session["title"],
        turns=session["turns"],
        summary=session["summary"],
        resources=session["resources"],
        artifacts=session["artifacts"],
        hunks=session["hunks"],
        terminal_logs=session["terminal_logs"],
        recaps=session["recaps"],
        updates=session["updates"],
    )


@app.route("/export")
def export_md():
    path_str = request.args.get("path")
    agent = request.args.get("agent")
    if not path_str or not agent:
        abort(400)

    path = Path(path_str)
    if not path.exists():
        abort(404)

    if not path_allowed(path):
        abort(403)

    session = load_session(agent, path)
    extra = summary_to_markdown(
        session["summary"],
        agent=agent,
        resources=session["resources"],
    )
    md = turns_to_markdown(
        session["turns"],
        session["title"],
        agent,
        str(path),
        extra=extra,
    )

    filename = f"{agent}-{path.stem[:40]}.md"
    return Response(
        md,
        mimetype="text/markdown",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/raw")
def raw():
    path_str = request.args.get("path")
    if not path_str:
        abort(400)
    path = Path(path_str)
    if not path.exists():
        abort(404)

    if not path_allowed(path):
        abort(403, "Path not allowed")

    # For directories (Grok) prefer chat_history, else summary
    if path.is_dir():
        for name in ("chat_history.jsonl", "summary.json"):
            candidate = path / name
            if candidate.exists():
                path = candidate
                break
        else:
            abort(404, "No single raw file for this session")

    return Response(
        path.read_text(encoding="utf-8", errors="replace"),
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment; filename={path.name}"},
    )


def _media_path_allowed(path: Path) -> bool:
    if path_allowed(path):
        return True
    # Codex clipboard captures often live under the OS temp dir
    try:
        name = path.name.lower()
        if name.startswith("codex-clipboard-") and is_image_path(path):
            tmp = Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp").resolve()
            path.resolve().relative_to(tmp)
            return True
    except Exception:
        pass
    # Codex generated images dir is under CODEX_HOME (already allowed via path_allowed)
    return False


@app.route("/media")
def media():
    """Serve local image files under known agent home directories only."""
    path_str = request.args.get("path")
    if not path_str:
        abort(400, "Missing path")

    path = Path(path_str)
    # Also try unquoted forms
    if not path.exists():
        try:
            path = Path(unquote(path_str))
        except Exception:
            pass

    if not path.exists() or not path.is_file():
        abort(404, "Image not found")

    if not _media_path_allowed(path):
        abort(403, "Path not allowed")

    if not is_image_path(path):
        abort(400, "Not an image file")

    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return send_file(path, mimetype=mime, conditional=True)


def run(host: str = "127.0.0.1", port: int = 5050) -> None:
    """Start the local viewer (loopback only). Set ASV_DEBUG=1 for Flask debug mode."""
    debug = os.environ.get("ASV_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
    print("Agent Session Viewer")
    print(f"→ http://{host}:{port}")
    print(f"Grok   : {GROK_HOME / 'sessions'}")
    print(f"Claude : {CLAUDE_HOME / 'projects'}")
    print(f"Codex  : {CODEX_HOME / 'sessions'}")
    if debug:
        print("Debug  : on (ASV_DEBUG)")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run()
