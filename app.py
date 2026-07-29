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

from flask import Flask, Response, abort, render_template_string, request, send_file

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
        events.append({
            "role": role,
            "time": display_time(ts),
            "id": "",
            "text": text,
            "model": "",
        })

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
                    events.append({
                        "role": "tool_call",
                        "time": display_time(ts, tcid),
                        "id": tcid,
                        "text": text,
                        "model": "",
                    })
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
                        events.append({
                            "role": "tool_result",
                            "time": display_time(ts, tcid),
                            "id": tcid,
                            "text": f"status: {status}\nid: {tcid}\n{body}".strip(),
                            "model": "",
                        })
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
                    events.append({
                        "role": "event",
                        "time": display_time(ts, tid),
                        "id": tid,
                        "text": f"task_backgrounded\nid: {tid}\ncommand: {cmd}\noutput_file: {out}",
                        "model": "",
                    })
                elif kind == "task_completed":
                    snap = update.get("task_snapshot") or update
                    tid = snap.get("task_id") or update.get("task_id") or ""
                    out = snap.get("output") or ""
                    if len(str(out)) > 3000:
                        out = str(out)[:3000] + "…"
                    events.append({
                        "role": "event",
                        "time": display_time(ts, tid),
                        "id": tid,
                        "text": f"task_completed\nid: {tid}\ncommand: {snap.get('command') or ''}\n{out}",
                        "model": "",
                    })
                elif kind == "turn_completed":
                    if user_buf:
                        flush_buf("user", user_buf, last_ts)
                    if thought_buf:
                        flush_buf("reasoning", thought_buf, last_ts)
                    if message_buf:
                        flush_buf("assistant", message_buf, last_ts)
                    usage = update.get("usage") or {}
                    usage_txt = pretty_json(usage, 800) if usage else ""
                    events.append({
                        "role": "event",
                        "time": display_time(ts, update.get("prompt_id")),
                        "id": update.get("prompt_id") or "",
                        "text": f"turn_completed · stop={update.get('stop_reason') or '?'}\n{usage_txt}",
                        "model": "",
                    })

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
        marker = [{
            "role": "event",
            "time": "",
            "id": "",
            "text": f"… {len(events) - max_events} updates omitted for display …",
            "model": "",
        }]
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


def codex_scan_session(path: Path) -> dict:
    """
    Single-pass scan of a Codex rollout file for summary, tokens, patches, settings.
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

    for obj in iter_codex_rollout(path):
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
                events.append({
                    "role": "event",
                    "time": display_time(ts, payload.get("turn_id")),
                    "id": payload.get("turn_id") or "",
                    "text": f"task_started\nid: {payload.get('turn_id') or ''}\nmodel_context_window: {payload.get('model_context_window') or ''}",
                    "model": "",
                    "meta": "task",
                    "images": [],
                    "html": "",
                })
            elif et == "task_complete":
                events.append({
                    "role": "event",
                    "time": display_time(ts, payload.get("turn_id")),
                    "id": payload.get("turn_id") or "",
                    "text": (
                        f"task_complete\nid: {payload.get('turn_id') or ''}\n"
                        f"duration_ms: {payload.get('duration_ms')}\n"
                        f"ttft_ms: {payload.get('time_to_first_token_ms')}\n"
                        f"{(payload.get('last_agent_message') or '')[:500]}"
                    ),
                    "model": "",
                    "meta": "task",
                    "images": [],
                    "html": "",
                })
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
                events.append({
                    "role": "event",
                    "time": display_time(ts, call_id),
                    "id": call_id,
                    "text": f"patch_apply_end · success={payload.get('success')}\n{stdout}\nfiles: {', '.join(list(changes)[:12])}",
                    "model": "",
                    "meta": "patch",
                    "images": [],
                    "html": "",
                })
            elif et == "image_generation_end":
                events.append({
                    "role": "event",
                    "time": display_time(ts, payload.get("call_id")),
                    "id": payload.get("call_id") or "",
                    "text": (
                        f"image_generation_end · {payload.get('status')}\n"
                        f"saved: {payload.get('saved_path') or '—'}\n"
                        f"{(payload.get('revised_prompt') or '')[:400]}"
                    ),
                    "model": "",
                    "meta": "image",
                    "images": [],
                    "html": "",
                })

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


def get_codex_conversation(path: Path) -> list[dict]:
    """
    Full Codex rollout transcript:
    - event_msg user/agent messages (chat)
    - response_item reasoning / tools
    - developer / AGENTS.md injections as system
    - patch / image events
    """
    turns: list[dict] = []
    idx = 0
    session_cwd = None

    # Light cwd for image path resolution
    try:
        for obj in iter_codex_rollout(path):
            if obj.get("type") == "session_meta":
                p = obj.get("payload") or {}
                session_cwd = p.get("cwd")
                break
    except Exception:
        pass

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
        for obj in iter_codex_rollout(path):
            idx += 1
            seq = f"#{idx}"
            ts_raw = obj.get("timestamp")
            t = obj.get("type")
            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}

            if t == "response_item":
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
# Templates
# ─────────────────────────────────────────────

BASE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ title }} – Agent Session Viewer</title>
  <style>
    :root {
      --bg: #0c0e12;
      --card: #161a22;
      --border: #2a2f3a;
      --text: #e8eaed;
      --muted: #8b95a8;
      --accent: #6c9eff;
      --user-bg: #1e3a5f;
      --assistant-bg: #1a2e28;
      /* Tool bubbles: cool slate-indigo (matches accent), not warm brown */
      --tool-bg: #1a2332;
      --tool-border: #3a4d6b;
      --tool-result-bg: #17242c;
      --tool-result-border: #35565c;
      --reason-bg: #1a1a2e;
      --reason-border: #3d3d6b;
      --system-bg: #1a1d24;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Inter", sans-serif;
      background: var(--bg); color: var(--text); line-height: 1.55;
    }
    header {
      background: var(--card); border-bottom: 1px solid var(--border);
      padding: 0.9rem 1.5rem; display: flex; align-items: center; gap: 1.5rem; flex-wrap: wrap;
      position: sticky; top: 0; z-index: 100;
    }
    header h1 { margin: 0; font-size: 1.2rem; font-weight: 600; }
    header a { color: var(--accent); text-decoration: none; font-size: 0.95rem; }
    header a:hover { text-decoration: underline; }
    .container { max-width: 980px; margin: 0 auto; padding: 1.5rem 1.25rem 3rem; }

    .toolbar { display: flex; gap: 0.75rem; margin-bottom: 1.5rem; flex-wrap: wrap; align-items: center; }
    .search-box {
      flex: 1; min-width: 200px; padding: 0.55rem 1rem; border-radius: 8px;
      border: 1px solid var(--border); background: var(--card); color: var(--text);
      font-size: 0.95rem;
    }
    .search-box:focus { outline: none; border-color: var(--accent); }
    .filters { display: flex; gap: 0.5rem; flex-wrap: wrap; }
    .filters a {
      padding: 0.4rem 0.85rem; border-radius: 999px; background: var(--card);
      border: 1px solid var(--border); color: var(--muted); text-decoration: none; font-size: 0.85rem;
    }
    .filters a.active, .filters a:hover {
      background: var(--accent); color: #0c0e12; border-color: var(--accent); font-weight: 600;
    }

    .session-card {
      background: var(--card); border: 1px solid var(--border); border-radius: 12px;
      padding: 1rem 1.2rem; margin-bottom: 0.7rem; transition: border-color 0.15s, transform 0.1s;
    }
    .session-card:hover { border-color: var(--accent); transform: translateY(-1px); }
    .session-card a { color: inherit; text-decoration: none; display: block; }
    .meta { font-size: 0.82rem; color: var(--muted); margin-top: 0.4rem; line-height: 1.4; }
    .badge {
      display: inline-block; padding: 0.18rem 0.55rem; border-radius: 5px;
      font-size: 0.72rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.03em; margin-right: 0.5rem;
    }
    .badge.grok   { background: #7c3aed33; color: #c4b5fd; }
    .badge.claude { background: #d9770633; color: #fbbf24; }
    .badge.codex  { background: #05966933; color: #6ee7b7; }

    .view-header { margin-bottom: 1.25rem; }
    .view-header h2 { margin: 0.6rem 0 0.3rem; font-size: 1.35rem; }
    .actions { display: flex; gap: 0.6rem; margin-top: 0.9rem; flex-wrap: wrap; }
    .btn {
      display: inline-flex; align-items: center; gap: 0.4rem;
      padding: 0.45rem 0.95rem; border-radius: 8px; font-size: 0.88rem; font-weight: 500;
      border: 1px solid var(--border); background: var(--card); color: var(--text);
      text-decoration: none; cursor: pointer;
    }
    .btn:hover { border-color: var(--accent); color: var(--accent); }
    .btn-primary { background: var(--accent); color: #0c0e12; border-color: var(--accent); }
    .btn-primary:hover { opacity: 0.9; color: #0c0e12; }

    .md-toggle {
      display: inline-flex; align-items: center; gap: 0.55rem;
      padding: 0.4rem 0.85rem; border-radius: 8px;
      border: 1px solid var(--border); background: var(--card);
      font-size: 0.88rem; color: var(--text); user-select: none; cursor: pointer;
    }
    .md-toggle:hover { border-color: var(--accent); }
    .md-toggle input { position: absolute; opacity: 0; width: 0; height: 0; }
    .md-toggle .track {
      width: 2.2rem; height: 1.2rem; border-radius: 999px;
      background: #2a2f3a; position: relative; transition: background 0.15s;
      flex-shrink: 0;
    }
    .md-toggle .track::after {
      content: ""; position: absolute; top: 2px; left: 2px;
      width: 0.95rem; height: 0.95rem; border-radius: 50%;
      background: #c5cad3; transition: transform 0.15s, background 0.15s;
    }
    .md-toggle input:checked + .track { background: var(--accent); }
    .md-toggle input:checked + .track::after {
      transform: translateX(1rem); background: #0c0e12;
    }
    .md-toggle .md-toggle-label { color: var(--muted); font-weight: 500; }
    .md-toggle input:checked ~ .md-toggle-label { color: var(--text); }

    .panel {
      background: var(--card); border: 1px solid var(--border); border-radius: 12px;
      padding: 1rem 1.15rem; margin-bottom: 1rem;
    }
    .panel h3 {
      margin: 0 0 0.75rem; font-size: 0.95rem; font-weight: 600;
      color: var(--accent); letter-spacing: 0.02em;
    }
    .panel details > summary {
      cursor: pointer; color: var(--muted); font-size: 0.88rem; user-select: none;
      list-style: none;
    }
    .panel details > summary::-webkit-details-marker { display: none; }
    .panel details > summary:hover { color: var(--accent); }
    .panel details[open] > summary { margin-bottom: 0.75rem; color: var(--text); }

    .summary-grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: 0.55rem 1rem; font-size: 0.85rem;
    }
    .summary-grid .label { color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em; }
    .summary-grid .value { word-break: break-word; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.82rem; }
    .summary-blurb {
      margin-top: 0.85rem; padding-top: 0.75rem; border-top: 1px solid var(--border);
      color: var(--text); font-size: 0.95rem;
    }

    .token-box {
      margin-top: 1rem; padding-top: 0.9rem; border-top: 1px solid var(--border);
    }
    .token-box .token-title {
      display: flex; align-items: baseline; justify-content: space-between; gap: 0.75rem;
      margin-bottom: 0.7rem; flex-wrap: wrap;
    }
    .token-box .token-title h4 {
      margin: 0; font-size: 0.88rem; font-weight: 600; color: var(--text);
    }
    .token-box .token-source { font-size: 0.75rem; color: var(--muted); }
    .token-stats {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
      gap: 0.55rem;
    }
    .token-stat {
      background: #0c0e12; border: 1px solid var(--border); border-radius: 10px;
      padding: 0.65rem 0.75rem;
    }
    .token-stat .tlabel {
      font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.04em;
      color: var(--muted); margin-bottom: 0.25rem;
    }
    .token-stat .tvalue {
      font-family: ui-monospace, Menlo, Consolas, monospace;
      font-size: 1.05rem; font-weight: 600; color: var(--text);
    }
    .token-stat.in .tvalue { color: #93c5fd; }
    .token-stat.out .tvalue { color: #6ee7b7; }
    .token-stat.cached .tvalue { color: #c4b5fd; }
    .token-stat.reason .tvalue { color: #fbbf24; }
    .token-bar {
      margin-top: 0.75rem; height: 10px; border-radius: 999px; overflow: hidden;
      background: #0c0e12; border: 1px solid var(--border); display: flex;
    }
    .token-bar > span { display: block; height: 100%; min-width: 0; }
    .token-bar .seg-in { background: #3b82f6; }
    .token-bar .seg-cached { background: #8b5cf6; }
    .token-bar .seg-out { background: #10b981; }
    .token-bar .seg-reason { background: #f59e0b; }
    .token-legend {
      display: flex; flex-wrap: wrap; gap: 0.75rem 1.1rem; margin-top: 0.55rem;
      font-size: 0.75rem; color: var(--muted);
    }
    .token-legend i {
      display: inline-block; width: 0.55rem; height: 0.55rem; border-radius: 2px;
      margin-right: 0.35rem; vertical-align: middle;
    }
    .token-legend .lg-in { background: #3b82f6; }
    .token-legend .lg-cached { background: #8b5cf6; }
    .token-legend .lg-out { background: #10b981; }
    .token-legend .lg-reason { background: #f59e0b; }
    .token-context {
      margin-top: 0.75rem; font-size: 0.82rem; color: var(--muted);
      font-family: ui-monospace, Menlo, Consolas, monospace;
    }
    .token-context .bar {
      margin-top: 0.35rem; height: 6px; border-radius: 999px; background: #0c0e12;
      border: 1px solid var(--border); overflow: hidden;
    }
    .token-context .bar > span {
      display: block; height: 100%; background: var(--accent); border-radius: 999px;
    }

    .todo-list { list-style: none; padding: 0; margin: 0; }
    .todo-list li {
      display: flex; gap: 0.6rem; align-items: flex-start;
      padding: 0.4rem 0; border-bottom: 1px solid var(--border); font-size: 0.88rem;
    }
    .todo-list li:last-child { border-bottom: none; }
    .todo-id {
      font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 0.75rem;
      color: var(--muted); min-width: 4.5rem;
    }
    .todo-status {
      font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
      padding: 0.12rem 0.4rem; border-radius: 4px; white-space: nowrap;
    }
    .todo-status.completed { background: #05966933; color: #6ee7b7; }
    .todo-status.in_progress { background: #2563eb33; color: #93c5fd; }
    .todo-status.pending { background: #6b728033; color: #d1d5db; }
    .todo-status.cancelled { background: #7f1d1d33; color: #fca5a5; }

    .settings-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
    .settings-table th, .settings-table td {
      text-align: left; padding: 0.35rem 0.5rem; border-bottom: 1px solid var(--border);
      vertical-align: top;
    }
    .settings-table th { color: var(--muted); font-weight: 600; font-size: 0.75rem; }
    .settings-table code { font-size: 0.8rem; color: #c4b5fd; }

    .artifact-list { list-style: none; padding: 0; margin: 0; font-size: 0.85rem; }
    .artifact-list li {
      padding: 0.45rem 0; border-bottom: 1px solid var(--border);
      font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 0.8rem;
    }
    .artifact-list li:last-child { border-bottom: none; }
    .artifact-list .sub { color: var(--muted); font-size: 0.75rem; margin-top: 0.15rem; font-family: inherit; }

    .artifact-docs { display: flex; flex-direction: column; gap: 0.85rem; margin-top: 0.65rem; }
    .artifact-doc {
      border: 1px solid var(--border); border-radius: 10px; background: #12161f;
      overflow: hidden;
    }
    .artifact-doc-head {
      display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
      gap: 0.5rem 0.75rem;
      padding: 0.55rem 0.85rem; border-bottom: 1px solid var(--border);
      background: #1a2030; font-size: 0.88rem;
    }
    .artifact-doc-head-main {
      display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 0.75rem; min-width: 0;
    }
    .artifact-doc-head .sub {
      color: var(--muted); font-size: 0.78rem;
      font-family: ui-monospace, Menlo, Consolas, monospace;
    }
    .artifact-doc-body { padding: 0.75rem 0.9rem 0.85rem; }

    .tabs-row {
      display: flex; align-items: center; justify-content: space-between;
      gap: 0.75rem; flex-wrap: wrap; margin: 1.25rem 0 1rem;
    }
    .tabs { display: flex; gap: 0.4rem; flex-wrap: wrap; margin: 0; }
    .tabs a, .tabs button {
      padding: 0.4rem 0.9rem; border-radius: 8px; border: 1px solid var(--border);
      background: var(--card); color: var(--muted); text-decoration: none; font-size: 0.85rem; cursor: pointer;
    }
    .tabs a.active, .tabs button.active {
      background: var(--accent); color: #0c0e12; border-color: var(--accent); font-weight: 600;
    }
    .chat-toolbar {
      display: flex; gap: 0.45rem; flex-wrap: wrap; margin-left: auto;
    }
    .chat-toolbar .btn { font-size: 0.82rem; padding: 0.35rem 0.75rem; }
    .tab-panel { display: none; }
    .tab-panel.active { display: block; }

    .chat { display: flex; flex-direction: column; gap: 1.1rem; }
    .bubble {
      max-width: 92%; padding: 0; border-radius: 14px;
      border: 1px solid var(--border); position: relative;
      /* Do not set overflow:hidden/clip — it breaks position:sticky headers */
    }
    .bubble-body { padding: 0.75rem 1.15rem 0.9rem; }
    .bubble.user { align-self: flex-end; background: var(--user-bg); border-bottom-right-radius: 4px; }
    .bubble.assistant, .bubble.agent_message {
      align-self: flex-start; background: var(--assistant-bg); border-bottom-left-radius: 4px;
    }
    .bubble.reasoning {
      align-self: stretch; max-width: 100%; background: var(--reason-bg);
      border-color: var(--reason-border); border-radius: 10px;
    }
    .bubble.system, .bubble.system_reminder {
      align-self: stretch; max-width: 100%; background: var(--system-bg);
      border-radius: 10px; font-size: 0.85rem; opacity: 0.92;
    }
    .bubble.tool_call, .bubble.event {
      align-self: stretch; max-width: 100%; background: var(--tool-bg);
      border-color: var(--tool-border); border-radius: 10px; font-size: 0.88rem;
    }
    .bubble.tool_result {
      align-self: stretch; max-width: 100%; background: var(--tool-result-bg);
      border-color: var(--tool-result-border); border-radius: 10px; font-size: 0.88rem;
    }
    .bubble-header {
      display: flex; gap: 0.75rem; align-items: center; justify-content: space-between;
      font-size: 0.78rem; color: var(--muted); flex-wrap: wrap;
      /* Stick under the site header while scrolling long blocks */
      position: sticky;
      top: 3.35rem;
      z-index: 8;
      padding: 0.55rem 1.15rem;
      border-bottom: 1px solid rgba(42, 47, 58, 0.85);
      box-shadow: 0 1px 0 rgba(0,0,0,0.15);
    }
    .bubble-header-main {
      display: flex; gap: 0.75rem; align-items: center; flex-wrap: wrap; min-width: 0;
    }
    .bubble-header-actions {
      display: flex; align-items: center; gap: 0.35rem; margin-left: auto; flex-shrink: 0;
    }
    .bubble.user .bubble-header { background: var(--user-bg); }
    .bubble.assistant .bubble-header, .bubble.agent_message .bubble-header { background: var(--assistant-bg); }
    .bubble.reasoning .bubble-header { background: var(--reason-bg); }
    .bubble.system .bubble-header, .bubble.system_reminder .bubble-header { background: var(--system-bg); }
    .bubble.tool_call .bubble-header, .bubble.event .bubble-header { background: var(--tool-bg); }
    .bubble.tool_result .bubble-header { background: var(--tool-result-bg); }
    .bubble-header .role { font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; }
    .bubble-header .msgid {
      font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 0.72rem;
      color: #a5b4c8; background: #0c0e1288; padding: 0.1rem 0.4rem; border-radius: 4px;
    }
    .bubble.user .role { color: #93c5fd; }
    .bubble.assistant .role { color: #6ee7b7; }
    .bubble.reasoning .role { color: #c4b5fd; }
    .bubble.tool_call .role, .bubble.event .role { color: #8eb6ff; }
    .bubble.tool_result .role { color: #7ec8c0; }
    .bubble.system .role, .bubble.system_reminder .role { color: #9ca3af; }

    .copy-menu { position: relative; }
    .copy-btn {
      display: inline-flex; align-items: center; gap: 0.25rem;
      padding: 0.2rem 0.55rem; border-radius: 6px;
      border: 1px solid var(--border); background: #0c0e1288;
      color: var(--muted); font-size: 0.72rem; font-weight: 600;
      cursor: pointer; letter-spacing: 0.02em;
    }
    .copy-btn:hover, .copy-menu.open .copy-btn {
      color: var(--accent); border-color: var(--accent);
    }
    /* Header expand/collapse chevron for foldable cards */
    .fold-header-btn {
      display: inline-flex; align-items: center; justify-content: center;
      width: 1.7rem; height: 1.55rem; padding: 0;
      border-radius: 6px; border: 1px solid var(--border);
      background: #0c0e1288; color: var(--muted);
      font-size: 0.72rem; cursor: pointer; line-height: 1;
      flex-shrink: 0;
    }
    .fold-header-btn:hover {
      color: var(--accent); border-color: var(--accent);
    }
    .fold-header-btn .fold-chevron {
      display: inline-block;
      transition: transform 0.15s ease;
      /* Point right when collapsed, down when expanded */
      transform: rotate(-90deg);
    }
    .fold-header-btn[aria-expanded="true"] .fold-chevron {
      transform: rotate(0deg);
    }
    .copy-menu-panel {
      position: absolute; right: 0; top: calc(100% + 4px);
      min-width: 9.5rem; z-index: 20;
      background: var(--card); border: 1px solid var(--border);
      border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.35);
      padding: 0.3rem; display: none;
    }
    .copy-menu.open .copy-menu-panel { display: block; }
    .copy-menu-panel button {
      display: block; width: 100%; text-align: left;
      padding: 0.4rem 0.6rem; border: none; border-radius: 6px;
      background: transparent; color: var(--text);
      font-size: 0.8rem; cursor: pointer;
    }
    .copy-menu-panel button:hover {
      background: #6c9eff22; color: var(--accent);
    }
    .copy-menu-panel .copy-hint {
      padding: 0.25rem 0.6rem 0.35rem; font-size: 0.68rem; color: var(--muted);
    }
    .bubble-text {
      white-space: pre-wrap; word-break: break-word;
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
      font-size: 0.9rem; line-height: 1.5;
    }
    .bubble.tool_call .bubble-text, .bubble.tool_result .bubble-text,
    .bubble.system .bubble-text, .bubble.system_reminder .bubble-text { font-size: 0.84rem; }

    /* Markdown rendered mode — soft palette (avoid pure white on pure black) */
    .bubble-text.markdown-body {
      white-space: normal;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Inter", sans-serif;
      font-size: 0.95rem; line-height: 1.65;
      color: #c5cad3;
    }
    .bubble.tool_call .bubble-text.markdown-body,
    .bubble.tool_result .bubble-text.markdown-body,
    .bubble.system .bubble-text.markdown-body,
    .bubble.system_reminder .bubble-text.markdown-body {
      font-size: 0.9rem;
    }
    .markdown-body > :first-child { margin-top: 0; }
    .markdown-body > :last-child { margin-bottom: 0; }
    .markdown-body p { margin: 0.55em 0; color: #c5cad3; }
    .markdown-body h1, .markdown-body h2, .markdown-body h3,
    .markdown-body h4, .markdown-body h5, .markdown-body h6 {
      margin: 1em 0 0.45em; line-height: 1.25; font-weight: 650;
      color: #d2d7e0;
    }
    .markdown-body h1 { font-size: 1.35em; }
    .markdown-body h2 { font-size: 1.2em; }
    .markdown-body h3 { font-size: 1.08em; }
    .markdown-body h4, .markdown-body h5, .markdown-body h6 { font-size: 1em; }
    .markdown-body ul, .markdown-body ol { margin: 0.45em 0; padding-left: 1.4em; color: #c5cad3; }
    .markdown-body li { margin: 0.2em 0; }
    .markdown-body li > p { margin: 0.25em 0; }
    .markdown-body blockquote {
      margin: 0.6em 0; padding: 0.35em 0.9em;
      border-left: 3px solid #6b8fc7; color: #9aa3b2;
      background: #1c2230;
      border-radius: 0 8px 8px 0;
    }
    .markdown-body a { color: #8eb6ff; }
    .markdown-body a:hover { color: #b4d0ff; }
    .markdown-body hr {
      border: none; border-top: 1px solid #3a4252; margin: 1em 0;
    }
    .markdown-body code {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.88em;
      background: #2a3142;
      color: #d0d6e0;
      border: 1px solid #3d4658;
      border-radius: 5px; padding: 0.12em 0.4em;
    }
    .markdown-body pre {
      margin: 0.65em 0; padding: 0.8rem 0.95rem; overflow: auto;
      background: #232936;
      border: 1px solid #3a4354;
      border-radius: 10px;
      font-size: 0.84rem; line-height: 1.5;
      color: #cfd5df;
    }
    .markdown-body pre code {
      background: none; border: none; padding: 0; font-size: inherit;
      color: #cfd5df;
    }
    .markdown-body pre.md-preserve {
      white-space: pre-wrap;
      word-break: break-word;
      margin: 0;
      background: #222834;
      color: #b8c0cc;
      border-color: #3a4354;
      font-size: 0.82rem;
      line-height: 1.5;
    }
    .markdown-body table {
      border-collapse: collapse; margin: 0.7em 0; width: 100%;
      font-size: 0.88rem; display: block; overflow-x: auto;
      color: #c5cad3;
    }
    .markdown-body th, .markdown-body td {
      border: 1px solid #3a4354; padding: 0.4rem 0.65rem; text-align: left;
    }
    .markdown-body th {
      background: #2a3142; color: #a8b0be; font-weight: 600;
    }
    .markdown-body td { background: #1e2430; }
    .markdown-body tr:nth-child(even) td { background: #222937; }
    .markdown-body img {
      max-width: 100%; height: auto; border-radius: 8px; border: 1px solid #3a4354;
    }
    .markdown-body input[type="checkbox"] { margin-right: 0.35rem; }
    .markdown-body strong { color: #d8dde6; font-weight: 650; }
    .markdown-body em { color: #c5cad3; }
    .encrypted-tag {
      display: inline-block; margin-top: 0.35rem; padding: 0.15rem 0.5rem;
      border-radius: 4px; background: #3d3d6b55; color: #c4b5fd; font-size: 0.78rem;
      font-family: ui-monospace, Menlo, Consolas, monospace;
    }

    .chat-images {
      display: flex; flex-direction: column; gap: 0.85rem; margin: 0.65rem 0 0.25rem;
    }
    .chat-image {
      margin: 0; max-width: min(520px, 100%);
      background: #0c0e12; border: 1px solid var(--border); border-radius: 10px;
      overflow: hidden;
    }
    .chat-image .image-snippet {
      padding: 0.45rem 0.65rem; border-bottom: 1px solid var(--border);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.78rem; color: #a5b4c8; word-break: break-all; white-space: pre-wrap;
      line-height: 1.4; background: #12151c;
    }
    .chat-image img {
      display: block; max-width: 100%; max-height: 360px; width: auto; height: auto;
      object-fit: contain; background: #111;
    }
    .chat-image img.copyable-image {
      cursor: copy;
    }
    .chat-image img.copyable-image:hover {
      outline: 2px solid var(--accent); outline-offset: -2px;
    }
    .chat-image figcaption {
      padding: 0.35rem 0.55rem; font-size: 0.72rem; color: var(--muted);
      word-break: break-all; line-height: 1.35;
    }
    .chat-image figcaption a { color: var(--accent); text-decoration: none; }
    .chat-image figcaption a:hover { text-decoration: underline; }
    .img-path-link { color: var(--accent); text-decoration: underline; word-break: break-all; }
    .copy-toast {
      position: fixed; bottom: 1.25rem; left: 50%; transform: translateX(-50%);
      background: #1e293b; color: #e2e8f0; border: 1px solid var(--accent);
      padding: 0.55rem 1rem; border-radius: 999px; font-size: 0.85rem;
      z-index: 200; opacity: 0; pointer-events: none; transition: opacity 0.2s;
    }
    .copy-toast.show { opacity: 1; }

    /* In-place expand/collapse — one content copy, no duplicate snippet */
    .fold { margin: 0; }
    .fold-body {
      position: relative;
      transition: max-height 0.15s ease;
    }
    .fold[data-collapsed="true"] .fold-body {
      max-height: 14rem;
      overflow: hidden;
      -webkit-mask-image: linear-gradient(to bottom, #000 0%, #000 62%, transparent 100%);
      mask-image: linear-gradient(to bottom, #000 0%, #000 62%, transparent 100%);
    }
    .fold[data-collapsed="false"] .fold-body {
      max-height: none;
      overflow: visible;
      -webkit-mask-image: none;
      mask-image: none;
    }
    .fold-toggle {
      display: inline-flex; align-items: center; gap: 0.4rem;
      margin-top: 0.55rem; padding: 0.3rem 0.7rem;
      border-radius: 999px; border: 1px solid var(--border);
      background: #0c0e1288; color: var(--accent);
      font-size: 0.8rem; font-weight: 500; cursor: pointer;
    }
    .fold-toggle:hover {
      border-color: var(--accent); background: #6c9eff22;
    }
    .fold-toggle .fold-chars {
      color: var(--muted); font-weight: 400; font-size: 0.75rem;
    }

    .empty { text-align: center; color: var(--muted); padding: 3.5rem 1rem; }
    footer { text-align: center; padding: 1.5rem; color: var(--muted); font-size: 0.8rem; }
  </style>
</head>
<body>
  <header>
    <h1><a href="/">Agent Session Viewer</a></h1>
    <nav style="display:flex; gap:1.1rem;">
      <a href="/">All</a>
      <a href="/?agent=grok">Grok</a>
      <a href="/?agent=claude">Claude</a>
      <a href="/?agent=codex">Codex</a>
    </nav>
  </header>
  <div class="container">
    {{ content|safe }}
  </div>
  <footer>Local only • Reads ~/.grok · ~/.claude · ~/.codex</footer>
</body>
</html>
"""

LIST_TEMPLATE = """
<div class="toolbar">
  <form method="get" style="display:flex; flex:1; gap:0.5rem; min-width:220px;">
    {% if agent %}<input type="hidden" name="agent" value="{{ agent }}">{% endif %}
    <input class="search-box" type="search" name="q" value="{{ q or '' }}"
           placeholder="Search title, ID, path…">
    <button class="btn" type="submit">Search</button>
  </form>
  <div class="filters">
    <a href="/?q={{ q or '' }}" class="{{ 'active' if not agent else '' }}">All</a>
    <a href="/?agent=grok&q={{ q or '' }}" class="{{ 'active' if agent == 'grok' else '' }}">Grok</a>
    <a href="/?agent=claude&q={{ q or '' }}" class="{{ 'active' if agent == 'claude' else '' }}">Claude</a>
    <a href="/?agent=codex&q={{ q or '' }}" class="{{ 'active' if agent == 'codex' else '' }}">Codex</a>
  </div>
</div>

{% if sessions %}
  <p style="color:var(--muted); margin:0 0 1rem; font-size:0.9rem;">
    {{ sessions|length }} session{{ 's' if sessions|length != 1 else '' }}
    {% if q %} matching “{{ q }}”{% endif %}
  </p>
  {% for s in sessions %}
  <div class="session-card">
    <a href="/view?path={{ s.path|urlencode }}&agent={{ s.agent }}">
      <span class="badge {{ s.agent }}">{{ s.agent }}</span>
      <strong>{{ s.title }}</strong>
      <div class="meta">
        {{ s.updated or s.created or s.id }}
        · {{ s.messages or '—' }} msgs
        {% if s.model %} · {{ s.model }}{% endif %}
        <br>{{ s.cwd }}
      </div>
    </a>
  </div>
  {% endfor %}
{% else %}
  <div class="empty">
    <h2>No sessions found</h2>
    {% if q %}<p>Try a different search term.</p>{% endif %}
    <p style="margin-top:1.5rem; font-size:0.9rem;">
      Looking in:<br>
      Grok → {{ grok_path }}<br>
      Claude → {{ claude_path }}<br>
      Codex → {{ codex_path }}
    </p>
  </div>
{% endif %}
"""

BUBBLE_PARTIAL = """
{% macro render_images(images) %}
  {% if images %}
  <div class="chat-images">
    {% for img in images %}
      {% if img.kind == 'data' %}
      <figure class="chat-image">
        {% if img.snippet %}<div class="image-snippet">{{ img.snippet }}</div>{% endif %}
        <img class="copyable-image" src="{{ img.url }}" alt="{{ img.label or 'image' }}"
             title="Click to copy image to clipboard" loading="lazy">
        <figcaption>Click image to copy · {{ img.mime or 'image' }}</figcaption>
      </figure>
      {% elif img.kind == 'url' %}
      <figure class="chat-image">
        {% if img.snippet %}<div class="image-snippet">{{ img.snippet }}</div>{% endif %}
        <a href="{{ img.url }}" target="_blank" rel="noopener">
          <img src="{{ img.url }}" alt="{{ img.label or 'image' }}" loading="lazy">
        </a>
        <figcaption><a href="{{ img.url }}" target="_blank" rel="noopener">{{ img.label or img.url }}</a></figcaption>
      </figure>
      {% else %}
      <figure class="chat-image">
        {% if img.snippet %}<div class="image-snippet">{{ img.snippet }}</div>{% endif %}
        <a href="/media?path={{ img.path|urlencode }}" target="_blank" rel="noopener">
          <img src="/media?path={{ img.path|urlencode }}" alt="{{ img.label or img.path }}" loading="lazy"
               onerror="this.style.display='none'; this.nextElementSibling && (this.nextElementSibling.style.display='block');">
        </a>
        <div style="display:none;padding:0.75rem;color:var(--muted);font-size:0.8rem;">Preview unavailable</div>
        <figcaption>
          <a href="/media?path={{ img.path|urlencode }}" target="_blank" rel="noopener">{{ img.label or img.path }}</a>
        </figcaption>
      </figure>
      {% endif %}
    {% endfor %}
  </div>
  {% endif %}
{% endmacro %}

{% macro render_md_block(text, html='', extra_class='') %}
  <div class="md-block{{ (' ' ~ extra_class) if extra_class else '' }}">
    {# Hidden textarea avoids <script> parsing issues with quotes/newlines in transcript text #}
    <textarea class="md-src" hidden readonly>{{ text }}</textarea>
    <div class="bubble-text md-plain">{% if html %}{{ html|safe }}{% else %}{{ text }}{% endif %}</div>
    <div class="bubble-text md-rich markdown-body" hidden></div>
  </div>
{% endmacro %}

{% macro render_foldable(text, html='') %}
  {# One full copy of the content; CSS clips when collapsed — no duplicate snippet. #}
  {% if text and text|length > 500 %}
  <div class="fold" data-collapsed="true">
    <div class="fold-body">
      {{ render_md_block(text, html) }}
    </div>
    <button type="button" class="fold-toggle" aria-expanded="false">
      <span class="fold-label-more">Show full</span>
      <span class="fold-label-less" hidden>Show less</span>
      <span class="fold-chars">{{ text|length }} chars</span>
    </button>
  </div>
  {% else %}
    {{ render_md_block(text, html) }}
  {% endif %}
{% endmacro %}

{% macro render_bubbles(turns) %}
<div class="chat">
  {% for t in turns %}
  {% set raw_text = t.text.replace('<encrypted>', '') if t.role == 'reasoning' else t.text %}
  {% set fold_text = raw_text if t.role == 'reasoning' else (t.text or '') %}
  {% set is_foldable = fold_text and fold_text|length > 500 %}
  <div class="bubble {{ t.role.split(' ')[0] }}"
       data-role={{ (t.role or '')|tojson }}
       data-time={{ (t.time or '')|tojson }}
       data-id={{ (t.id or '')|tojson }}
       data-model={{ (t.model or '')|tojson }}
       data-meta={{ (t.meta or '')|tojson }}>
    <div class="bubble-header">
      <div class="bubble-header-main">
        <span class="role">{{ t.role }}</span>
        {# skip time/meta when they only echo msgid (common when display_time falls back to id) #}
        {% if t.time and t.time != t.id %}<span>{{ t.time }}</span>{% endif %}
        {% if t.id %}<span class="msgid">{{ t.id }}</span>{% endif %}
        {% if t.model %}<span>{{ t.model }}</span>{% endif %}
        {% if t.meta and t.meta != t.id %}<span>{{ t.meta }}</span>{% endif %}
      </div>
      <div class="bubble-header-actions">
        {% if is_foldable %}
        <button type="button" class="fold-header-btn" aria-expanded="false" title="Expand">
          <span class="fold-chevron" aria-hidden="true">▾</span>
        </button>
        {% endif %}
        <div class="copy-menu">
          <button type="button" class="copy-btn" aria-haspopup="menu" aria-expanded="false" title="Copy block">
            Copy ▾
          </button>
          <div class="copy-menu-panel" role="menu">
            <div class="copy-hint">Copy this block</div>
            <button type="button" role="menuitem" data-copy="markdown">As Markdown</button>
            <button type="button" role="menuitem" data-copy="raw">As raw text</button>
          </div>
        </div>
      </div>
    </div>

    <div class="bubble-body">
      {{ render_images(t.images) }}

      {% if t.role == 'reasoning' %}
        {{ render_foldable(raw_text, '') }}
        {% if '<encrypted>' in t.text %}
          <span class="encrypted-tag">&lt;encrypted&gt;</span>
        {% endif %}
      {% else %}
        {{ render_foldable(t.text, t.html or '') }}
      {% endif %}
    </div>
  </div>
  {% endfor %}
</div>
{% endmacro %}
"""

VIEW_TEMPLATE = BUBBLE_PARTIAL + """
<div class="view-header">
  <a href="/{% if agent %}?agent={{ agent }}{% endif %}" style="color:var(--accent); font-size:0.9rem;">← Back to list</a>
  <h2>{{ title }}</h2>
  <div class="meta">
    <span class="badge {{ agent }}">{{ agent }}</span>
    {{ path }}
  </div>
  <div class="actions">
    <label class="md-toggle" title="Render message bodies as Markdown">
      <input type="checkbox" id="md-toggle" autocomplete="off">
      <span class="track" aria-hidden="true"></span>
      <span class="md-toggle-label">Markdown</span>
    </label>
    <a class="btn btn-primary" href="/export?path={{ path|urlencode }}&agent={{ agent }}">
      ↓ Export Markdown
    </a>
    <a class="btn" href="/raw?path={{ path|urlencode }}&agent={{ agent }}">Raw file</a>
  </div>
</div>

{% if summary %}
<div class="panel">
  <h3>Session summary</h3>
  <div class="summary-grid">
    <div><div class="label">ID</div><div class="value">{{ summary.id }}</div></div>
    <div><div class="label">Model</div><div class="value">{{ summary.model or '—' }}</div></div>
    <div><div class="label">Agent</div><div class="value">{{ summary.agent_name or '—' }}</div></div>
    <div><div class="label">Reasoning</div><div class="value">{{ summary.reasoning_effort or '—' }}</div></div>
    <div><div class="label">Sandbox</div><div class="value">{{ summary.sandbox_profile or '—' }}</div></div>
    <div><div class="label">Created</div><div class="value">{{ summary.created or '—' }}</div></div>
    <div><div class="label">Updated</div><div class="value">{{ summary.updated or '—' }}</div></div>
    <div><div class="label">Messages</div><div class="value">{{ summary.num_chat_messages or '—' }} chat / {{ summary.num_messages or '—' }} total</div></div>
    {% if summary.request_id %}
    <div><div class="label">Request</div><div class="value">{{ summary.request_id }}</div></div>
    {% endif %}
    {% if summary.head_branch %}
    <div><div class="label">Branch</div><div class="value">{{ summary.head_branch }}{% if summary.head_commit %} @ {{ summary.head_commit }}{% endif %}</div></div>
    {% endif %}
    {% if summary.personality %}
    <div><div class="label">Personality</div><div class="value">{{ summary.personality }}</div></div>
    {% endif %}
    {% if summary.cli_version %}
    <div><div class="label">CLI</div><div class="value">{{ summary.cli_version }}</div></div>
    {% endif %}
    {% if summary.plan_type %}
    <div><div class="label">Plan</div><div class="value">{{ summary.plan_type }}</div></div>
    {% endif %}
  </div>
  {% if summary.cwd %}
  <div class="summary-blurb"><span class="label">CWD</span><br><span class="value">{{ summary.cwd }}</span></div>
  {% endif %}
  {% if summary.session_summary and summary.session_summary != summary.title %}
  <div class="summary-blurb">{{ summary.session_summary }}</div>
  {% endif %}

  {% if summary.tokens and summary.tokens.available %}
  {% set tok = summary.tokens %}
  <div class="token-box">
    <div class="token-title">
      <h4>Estimated token usage</h4>
      <span class="token-source">{{ tok.source }}{% if tok.turns %} · {{ tok.turns }} turn{{ 's' if tok.turns != 1 else '' }}{% endif %}{% if tok.model_calls %} · {{ tok.model_calls }} model call{{ 's' if tok.model_calls != 1 else '' }}{% endif %}</span>
    </div>
    <div class="token-stats">
      <div class="token-stat in">
        <div class="tlabel">Input</div>
        <div class="tvalue">{{ tok.input_fmt }}</div>
      </div>
      <div class="token-stat out">
        <div class="tlabel">Output</div>
        <div class="tvalue">{{ tok.output_fmt }}</div>
      </div>
      <div class="token-stat cached">
        <div class="tlabel">Cached read</div>
        <div class="tvalue">{{ tok.cached_fmt }}</div>
      </div>
      <div class="token-stat reason">
        <div class="tlabel">Reasoning</div>
        <div class="tvalue">{{ tok.reasoning_fmt }}</div>
      </div>
      <div class="token-stat">
        <div class="tlabel">Uncached in</div>
        <div class="tvalue">{{ tok.uncached_fmt }}</div>
      </div>
      <div class="token-stat">
        <div class="tlabel">Total (in+out)</div>
        <div class="tvalue">{{ tok.total_fmt }}</div>
      </div>
    </div>

    <div class="token-bar" title="uncached in / cached in / output / reasoning">
      <span class="seg-in" style="width: {{ tok.bar.uncached_pct }}%"></span>
      <span class="seg-cached" style="width: {{ tok.bar.cached_pct }}%"></span>
      <span class="seg-out" style="width: {{ tok.bar.out_pct }}%"></span>
      <span class="seg-reason" style="width: {{ tok.bar.reason_pct }}%"></span>
    </div>
    <div class="token-legend">
      <span><i class="lg-in"></i>Uncached input</span>
      <span><i class="lg-cached"></i>Cached input</span>
      <span><i class="lg-out"></i>Output</span>
      <span><i class="lg-reason"></i>Reasoning</span>
    </div>

    {% if tok.context_used is not none and tok.context_window %}
    <div class="token-context">
      Context window (latest): {{ tok.context_used_fmt }} / {{ tok.context_window_fmt }}
      ({{ tok.context_pct }}%)
      <div class="bar"><span style="width: {{ tok.context_pct if tok.context_pct < 100 else 100 }}%"></span></div>
    </div>
    {% endif %}

    {% if tok.by_model_rows and tok.by_model_rows|length > 0 %}
    <details style="margin-top:0.75rem;">
      <summary>Per model</summary>
      <table class="settings-table" style="margin-top:0.5rem;">
        <thead>
          <tr><th>Model</th><th>In</th><th>Out</th><th>Cached</th><th>Reasoning</th><th>Calls</th></tr>
        </thead>
        <tbody>
        {% for m in tok.by_model_rows %}
          <tr>
            <td><code>{{ m.model }}</code></td>
            <td>{{ m.input_fmt }}</td>
            <td>{{ m.output_fmt }}</td>
            <td>{{ m.cached_fmt }}</td>
            <td>{{ m.reasoning_fmt }}</td>
            <td>{{ m.model_calls }}</td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    </details>
    {% endif %}
  </div>
  {% endif %}
</div>
{% endif %}

{% if resources and (resources.todos or resources.settings or resources.scheduler_tasks or resources.reported_completions) %}
<div class="panel">
  <h3>Resources · todos &amp; settings</h3>
  {% if resources.todos %}
  <p style="margin:0 0 0.5rem; color:var(--muted); font-size:0.85rem;">Todos ({{ resources.todos|length }})</p>
  <ul class="todo-list">
    {% for t in resources.todos %}
    <li>
      <span class="todo-id">{{ t.id }}</span>
      <span class="todo-status {{ t.status }}">{{ t.status }}</span>
      <span>{{ t.content }}{% if t.priority %} <span style="color:var(--muted)">({{ t.priority }})</span>{% endif %}</span>
    </li>
    {% endfor %}
  </ul>
  {% endif %}

  {% if resources.reported_completions %}
  <details style="margin-top:0.85rem;">
    <summary>Reported task completions ({{ resources.reported_completions|length }})</summary>
    <ul class="artifact-list">
      {% for id in resources.reported_completions %}
      <li>{{ id }}</li>
      {% endfor %}
    </ul>
  </details>
  {% endif %}

  {% if resources.scheduler_tasks %}
  <details style="margin-top:0.85rem;">
    <summary>Scheduler tasks ({{ resources.scheduler_tasks|length }})</summary>
    <ul class="artifact-list">
      {% for t in resources.scheduler_tasks %}
      <li>{% if t is mapping %}{% for k,v in t.items() %}<div class="sub"><strong>{{ k }}</strong>: {{ v }}</div>{% endfor %}{% else %}{{ t }}{% endif %}</li>
      {% endfor %}
    </ul>
  </details>
  {% endif %}

  {% if resources.settings %}
  <details style="margin-top:0.85rem;">
    <summary>Tool settings ({{ resources.settings|length }} non-null params)</summary>
    <table class="settings-table">
      <thead><tr><th>Tool</th><th>Key</th><th>Value</th></tr></thead>
      <tbody>
      {% for s in resources.settings %}
        <tr>
          <td><code>{{ s.tool }}</code></td>
          <td>{{ s.key }}</td>
          <td>{{ s.value }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </details>
  {% endif %}

  {% if resources.other_state %}
  <details style="margin-top:0.85rem;">
    <summary>Other resource state ({{ resources.other_state|length }})</summary>
    <ul class="artifact-list">
      {% for o in resources.other_state %}
      <li><strong>{{ o.key }}</strong><div class="sub">{{ o.value }}</div></li>
      {% endfor %}
    </ul>
  </details>
  {% endif %}
</div>
{% endif %}

{% if artifacts or hunks or terminal_logs or recaps %}
<div class="panel">
  <h3>Session artifacts</h3>

  {% if artifacts %}
  <details open>
    <summary>Documents ({{ artifacts|length }})</summary>
    <div class="artifact-docs">
      {% for a in artifacts %}
      {% set art_foldable = a.text and a.text|length > 500 %}
      <div class="artifact-doc"
           data-role={{ (a.title or 'document')|tojson }}
           data-time=""
           data-id={{ (a.id or '')|tojson }}
           data-model=""
           data-meta={{ (a.subtitle or a.kind or '')|tojson }}>
        <div class="artifact-doc-head">
          <div class="artifact-doc-head-main">
            <strong>{{ a.title }}</strong>
            {% if a.subtitle %}<span class="sub">{{ a.subtitle }}</span>{% endif %}
            {% if a.kind %}<span class="msgid">{{ a.kind }}</span>{% endif %}
          </div>
          <div class="bubble-header-actions">
            {% if art_foldable %}
            <button type="button" class="fold-header-btn" aria-expanded="false" title="Expand">
              <span class="fold-chevron" aria-hidden="true">▾</span>
            </button>
            {% endif %}
            <div class="copy-menu">
              <button type="button" class="copy-btn" aria-haspopup="menu" aria-expanded="false" title="Copy document">
                Copy ▾
              </button>
              <div class="copy-menu-panel" role="menu">
                <div class="copy-hint">Copy this document</div>
                <button type="button" role="menuitem" data-copy="markdown">As Markdown</button>
                <button type="button" role="menuitem" data-copy="raw">As raw text</button>
              </div>
            </div>
          </div>
        </div>
        <div class="artifact-doc-body">
          {{ render_foldable(a.text or '', '') }}
        </div>
      </div>
      {% endfor %}
    </div>
  </details>
  {% endif %}

  {% if hunks %}
  <details style="margin-top:0.6rem;" {% if hunks|length <= 12 and not artifacts %}open{% endif %}>
    <summary>{% if agent == 'codex' %}Patches{% else %}Hunk records{% endif %} ({{ hunks|length }}) · file edits</summary>
    <ul class="artifact-list">
      {% for h in hunks %}
      <li>
        <span class="msgid">{{ h.hunk_id }}</span>
        · {{ h.event or 'edit' }}
        · +{{ h.added or 0 }}/-{{ h.removed or 0 }}
        {% if h.time %} · {{ h.time }}{% endif %}
        <div class="sub">{{ h.file_path }}{% if h.start %} · lines {{ h.start }}–{{ h.end }}{% endif %}
        {% if h.prompt_index is not none %} · prompt {{ h.prompt_index }}{% endif %}
        {% if h.source %} · {{ h.source }}{% endif %}</div>
      </li>
      {% endfor %}
    </ul>
  </details>
  {% endif %}

  {% if terminal_logs %}
  <details style="margin-top:0.6rem;" {% if terminal_logs|length <= 8 %}open{% endif %}>
    <summary>Terminal logs ({{ terminal_logs|length }}) · matched by call id</summary>
    <ul class="artifact-list">
      {% for t in terminal_logs %}
      <li>
        <span class="msgid">{{ t.id }}</span> · {{ t.size }} bytes
        {% if t.preview %}<div class="sub">{{ t.preview }}</div>{% endif %}
      </li>
      {% endfor %}
    </ul>
  </details>
  {% endif %}

  {% if recaps %}
  <details style="margin-top:0.6rem;">
    <summary>Recap requests ({{ recaps|length }})</summary>
    <ul class="artifact-list">
      {% for r in recaps %}
      <li>
        <span class="msgid">{{ r.id }}</span>
        {% if r.created %} · {{ r.created }}{% endif %}
        {% if r.trigger %} · {{ r.trigger }}{% endif %}
        {% if r.model %} · {{ r.model }}{% endif %}
        · {{ r.size }} bytes
        {% if r.chat_len is not none %} · {{ r.chat_len }} msgs{% endif %}
        {% if r.x_grok_req_id %}<div class="sub">{{ r.x_grok_req_id }}</div>{% endif %}
      </li>
      {% endfor %}
    </ul>
  </details>
  {% endif %}
</div>
{% endif %}

<div class="tabs-row">
  <div class="tabs" id="view-tabs">
    <button type="button" class="active" data-tab="chat">Chat history ({{ turns|length }})</button>
    {% if updates is not none %}
    <button type="button" data-tab="updates">
      {% if agent == 'codex' %}Events timeline ({{ updates|length }}){% else %}Updates stream ({{ updates|length }}){% endif %}
    </button>
    {% endif %}
  </div>
  <div class="chat-toolbar">
    <button type="button" class="btn" id="expand-all" title="Expand all folded blocks">Expand all</button>
    <button type="button" class="btn" id="collapse-all" title="Collapse all folded blocks">Collapse all</button>
  </div>
</div>

<div id="tab-chat" class="tab-panel active">
{% if turns %}
  {{ render_bubbles(turns) }}
{% else %}
  <div class="empty">Could not parse conversation from this session.</div>
{% endif %}
</div>

{% if updates is not none %}
<div id="tab-updates" class="tab-panel">
  {% if updates %}
    <p style="color:var(--muted); font-size:0.85rem; margin:0 0 1rem;">
      {% if agent == 'codex' %}
      Task / patch / image events from the Codex rollout (not the full chat — see Chat history).
      {% else %}
      Aggregated from <code>updates.jsonl</code> (stream chunks collapsed; tool ids preserved).
      {% endif %}
    </p>
    {{ render_bubbles(updates) }}
  {% else %}
    <div class="empty">{% if agent == 'codex' %}No timeline events found.{% else %}No updates.jsonl events found.{% endif %}</div>
  {% endif %}
</div>
{% endif %}

<div class="copy-toast" id="copy-toast">Image copied to clipboard</div>
<script src="https://cdn.jsdelivr.net/npm/marked@15.0.12/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3.2.6/dist/purify.min.js"></script>
<script>
(function() {
  const MD_KEY = 'asv-markdown';
  const tabs = document.querySelectorAll('#view-tabs [data-tab]');
  tabs.forEach(btn => {
    btn.addEventListener('click', () => {
      tabs.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      const panel = document.getElementById('tab-' + btn.dataset.tab);
      if (panel) panel.classList.add('active');
    });
  });

  const toast = document.getElementById('copy-toast');
  function showToast(msg) {
    if (!toast) return;
    toast.textContent = msg;
    toast.classList.add('show');
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => toast.classList.remove('show'), 1800);
  }

  async function copyImageToClipboard(img) {
    try {
      const resp = await fetch(img.src);
      const blob = await resp.blob();
      const type = blob.type || 'image/png';
      if (navigator.clipboard && window.ClipboardItem) {
        // Chrome often wants image/png specifically
        let itemBlob = blob;
        if (type !== 'image/png' && typeof createImageBitmap === 'function') {
          try {
            const bitmap = await createImageBitmap(blob);
            const canvas = document.createElement('canvas');
            canvas.width = bitmap.width;
            canvas.height = bitmap.height;
            canvas.getContext('2d').drawImage(bitmap, 0, 0);
            itemBlob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
          } catch (_) { /* use original */ }
        }
        await navigator.clipboard.write([
          new ClipboardItem({ [itemBlob.type || 'image/png']: itemBlob })
        ]);
        showToast('Image copied to clipboard');
        return;
      }
      // Fallback: copy data URL as text
      await navigator.clipboard.writeText(img.src);
      showToast('Image data URL copied as text');
    } catch (err) {
      try {
        await navigator.clipboard.writeText(img.src);
        showToast('Image data URL copied as text');
      } catch (e2) {
        showToast('Copy failed — try right-click → Copy image');
      }
    }
  }

  document.addEventListener('click', (ev) => {
    const img = ev.target.closest('img.copyable-image');
    if (!img) return;
    ev.preventDefault();
    copyImageToClipboard(img);
  });


  // ── Fold / expand-all / copy (always — do not gate on marked CDN) ──
  function cardRootForFold(fold) {
    return fold ? fold.closest('.bubble, .artifact-doc') : null;
  }

  function headerBtnForFold(fold) {
    var root = cardRootForFold(fold);
    return root ? root.querySelector('.fold-header-btn') : null;
  }

  function setFoldCollapsed(fold, collapsed) {
    if (!fold) return;
    fold.setAttribute('data-collapsed', collapsed ? 'true' : 'false');
    var expanded = collapsed ? 'false' : 'true';
    var btn = fold.querySelector('.fold-toggle');
    if (btn) {
      btn.setAttribute('aria-expanded', expanded);
      var more = btn.querySelector('.fold-label-more');
      var less = btn.querySelector('.fold-label-less');
      if (more) more.hidden = !collapsed;
      if (less) less.hidden = collapsed;
    }
    var headerBtn = headerBtnForFold(fold);
    if (headerBtn) {
      headerBtn.setAttribute('aria-expanded', expanded);
      headerBtn.title = collapsed ? 'Expand' : 'Collapse';
    }
  }

  function toggleFold(fold) {
    if (!fold) return;
    var collapsed = fold.getAttribute('data-collapsed') !== 'false';
    if (collapsed) {
      // Expanding: content grows below — no scroll fix needed.
      setFoldCollapsed(fold, false);
    } else {
      // Collapsing: keep this card from yanking the page.
      var anchor = cardRootForFold(fold) || fold;
      preserveAnchorScroll(anchor, function() {
        setFoldCollapsed(fold, true);
      });
    }
  }

  // Keep the page from jumping when tall folds shrink. Pin a stable anchor's
  // viewport Y across the mutation; if we were scrolled deep into content that
  // collapsed away, bring that block back under the sticky header.
  function scrollPad() {
    return 72; // site header + a little breathing room
  }

  function preserveAnchorScroll(anchor, action) {
    if (!anchor) {
      action();
      return;
    }
    var before = anchor.getBoundingClientRect().top;
    action();
    var after = anchor.getBoundingClientRect().top;
    var delta = after - before;
    if (Math.abs(delta) > 0.5) window.scrollBy(0, delta);
    // If the anchor fully left the viewport (common when collapsing a block we
    // were scrolled into the middle of), pin its top under the header.
    var pad = scrollPad();
    var r = anchor.getBoundingClientRect();
    if (r.bottom < pad || r.top > window.innerHeight - 40) {
      window.scrollBy(0, r.top - pad);
    }
  }

  function firstVisibleBlock() {
    var root = document.querySelector('.tab-panel.active') || document;
    var nodes = root.querySelectorAll('.bubble, .artifact-doc');
    var pad = scrollPad();
    for (var i = 0; i < nodes.length; i++) {
      var r = nodes[i].getBoundingClientRect();
      if (r.bottom > pad && r.top < window.innerHeight) return nodes[i];
    }
    return null;
  }

  document.querySelectorAll('.fold-toggle').forEach(function(btn) {
    btn.addEventListener('click', function() {
      toggleFold(btn.closest('.fold'));
    });
  });

  document.querySelectorAll('.fold-header-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var root = btn.closest('.bubble, .artifact-doc');
      var fold = root ? root.querySelector('.fold') : null;
      toggleFold(fold);
    });
  });

  function foldsInActiveTab() {
    var active = document.querySelector('.tab-panel.active') || document;
    return active.querySelectorAll('.fold');
  }

  var expandAll = document.getElementById('expand-all');
  var collapseAll = document.getElementById('collapse-all');
  if (expandAll) {
    expandAll.addEventListener('click', function() {
      foldsInActiveTab().forEach(function(f) { setFoldCollapsed(f, false); });
    });
  }
  if (collapseAll) {
    collapseAll.addEventListener('click', function() {
      var anchor = firstVisibleBlock();
      preserveAnchorScroll(anchor, function() {
        foldsInActiveTab().forEach(function(f) { setFoldCollapsed(f, true); });
      });
    });
  }

  function getBlockRoot(el) {
    return el.closest('.bubble, .artifact-doc');
  }

  function getBubbleRawText(root) {
    if (!root) return '';
    var blocks = root.querySelectorAll('.md-block textarea.md-src, .md-block script.md-src');
    if (!blocks.length) return '';
    var best = '';
    blocks.forEach(function(el) {
      var t = '';
      if (el.tagName === 'TEXTAREA') {
        t = el.value || '';
      } else {
        try { t = JSON.parse(el.textContent || '""'); } catch (e) { t = el.textContent || ''; }
      }
      if (String(t).length >= best.length) best = String(t);
    });
    if (root.classList.contains('reasoning') && root.querySelector('.encrypted-tag')) {
      if (best.indexOf('<encrypted>') === -1) {
        best = best.replace(/\\s*$/, '') + '\\n<encrypted>';
      }
    }
    return best;
  }

  function formatBubbleMarkdown(root, raw) {
    if (!root) return raw || '';
    var role = (root.dataset.role || 'message');
    // Chat roles uppercase; document titles keep their casing
    if (!root.classList.contains('artifact-doc')) {
      role = String(role).toUpperCase();
    }
    var bits = [role];
    if (root.dataset.time) bits.push(root.dataset.time);
    if (root.dataset.id) bits.push('`' + root.dataset.id + '`');
    if (root.dataset.model) bits.push(root.dataset.model);
    if (root.dataset.meta) bits.push(root.dataset.meta);
    return '### ' + bits.join(' · ') + '\\n\\n' + (raw || '') + '\\n';
  }

  function copyText(text, label) {
    function ok() { showToast(label || 'Copied to clipboard'); }
    function fail() { showToast('Copy failed'); }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(ok).catch(function() {
        try {
          var ta = document.createElement('textarea');
          ta.value = text;
          ta.style.position = 'fixed';
          ta.style.left = '-9999px';
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
          ok();
        } catch (e2) { fail(); }
      });
    } else {
      try {
        var ta2 = document.createElement('textarea');
        ta2.value = text;
        ta2.style.position = 'fixed';
        ta2.style.left = '-9999px';
        document.body.appendChild(ta2);
        ta2.select();
        document.execCommand('copy');
        document.body.removeChild(ta2);
        ok();
      } catch (e3) { fail(); }
    }
  }

  function closeAllCopyMenus(except) {
    document.querySelectorAll('.copy-menu.open').forEach(function(m) {
      if (except && m === except) return;
      m.classList.remove('open');
      var b = m.querySelector('.copy-btn');
      if (b) b.setAttribute('aria-expanded', 'false');
    });
  }

  document.addEventListener('click', function(ev) {
    var btn = ev.target.closest('.copy-btn');
    if (btn) {
      ev.preventDefault();
      ev.stopPropagation();
      var menu = btn.closest('.copy-menu');
      var open = menu.classList.contains('open');
      closeAllCopyMenus();
      if (!open) {
        menu.classList.add('open');
        btn.setAttribute('aria-expanded', 'true');
      }
      return;
    }
    var item = ev.target.closest('.copy-menu-panel [data-copy]');
    if (item) {
      ev.preventDefault();
      ev.stopPropagation();
      var root = getBlockRoot(item);
      var mode = item.getAttribute('data-copy');
      var raw = getBubbleRawText(root);
      if (mode === 'markdown') {
        copyText(formatBubbleMarkdown(root, raw), 'Markdown copied');
      } else {
        copyText(raw, 'Raw text copied');
      }
      closeAllCopyMenus();
      return;
    }
    if (!ev.target.closest('.copy-menu')) closeAllCopyMenus();
  });

  // ── Markdown toggle (optional — CDN may be offline) ──────
  var mdToggle = document.getElementById('md-toggle');
  var markedOk = (typeof marked !== 'undefined');

  function isHashOnlyHref(href) {
    if (!href) return true;
    var h = String(href).trim();
    return h.charAt(0) === '#' || h.indexOf('javascript:') === 0;
  }

  function stripHashOnlyLinks(html) {
    return String(html)
      .replace(/<a\\b([^>]*?)href\\s*=\\s*(["'])#(?:(?!\\2).)*\\2([^>]*)>([\\s\\S]*?)<\\/a>/gi, '$4')
      .replace(/<a\\b([^>]*?)href\\s*=\\s*#([^\\s>]*)([^>]*)>([\\s\\S]*?)<\\/a>/gi, '$4');
  }

  function readSrc(block) {
    var el = block.querySelector('textarea.md-src, script.md-src');
    if (!el) return '';
    if (el.tagName === 'TEXTAREA') return el.value || '';
    try { return JSON.parse(el.textContent || '""'); }
    catch (e) { return el.textContent || ''; }
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function normalizeNewlines(src) {
    return String(src || '').replace(/\\r\\n/g, '\\n').replace(/\\r/g, '\\n');
  }

  var MD_HTML_TAGS = {
    a:1, abbr:1, b:1, blockquote:1, br:1, code:1, del:1, div:1, em:1,
    h1:1, h2:1, h3:1, h4:1, h5:1, h6:1, hr:1, i:1, img:1, input:1, li:1,
    ol:1, p:1, pre:1, s:1, span:1, strong:1, sub:1, sup:1, table:1,
    tbody:1, td:1, th:1, thead:1, tr:1, u:1, ul:1
  };

  function escapeAgentTags(src) {
    var re = new RegExp('</?([A-Za-z][\\\\w:-]*)\\\\b[^>]*>', 'g');
    return src.replace(re, function(match, name) {
      if (MD_HTML_TAGS[String(name).toLowerCase()]) return match;
      return escapeHtml(match);
    });
  }

  function renderMarkdown(src) {
    src = normalizeNewlines(src);
    var html = '';
    try {
      var prepared = escapeAgentTags(src);
      html = marked.parse(prepared);
      html = stripHashOnlyLinks(html);
    } catch (err) {
      html = '<pre class="md-preserve">' + escapeHtml(src) + '</pre>';
    }
    if (typeof DOMPurify !== 'undefined') {
      html = DOMPurify.sanitize(html, {
        USE_PROFILES: { html: true },
        ADD_ATTR: ['target', 'rel', 'class'],
        ADD_TAGS: ['pre']
      });
      html = stripHashOnlyLinks(html);
    }
    return html;
  }

  function setMarkdownMode(on) {
    document.body.classList.toggle('md-on', !!on);
    document.querySelectorAll('.md-block').forEach(function(block) {
      var plain = block.querySelector('.md-plain');
      var rich = block.querySelector('.md-rich');
      if (!plain || !rich) return;
      if (on && markedOk) {
        if (!rich.dataset.rendered) {
          rich.innerHTML = renderMarkdown(readSrc(block));
          rich.dataset.rendered = '1';
        }
        plain.hidden = true;
        rich.hidden = false;
      } else {
        plain.hidden = false;
        rich.hidden = true;
      }
    });
  }

  if (mdToggle) {
    if (markedOk && typeof marked.use === 'function') {
      marked.use({
        gfm: true,
        breaks: true,
        renderer: {
          link: function(token) {
            var href = token && token.href != null ? token.href : (arguments[0] || '');
            var title = token && token.title != null ? token.title : (arguments[1] || '');
            var text;
            if (token && token.tokens && this.parser) {
              text = this.parser.parseInline(token.tokens);
            } else {
              text = arguments[2] != null ? arguments[2] : String(href || '');
            }
            if (isHashOnlyHref(href)) return text;
            var t = title ? ' title="' + escapeHtml(String(title)) + '"' : '';
            return '<a href="' + escapeHtml(String(href)) + '"' + t + ' rel="noopener">' + text + '</a>';
          }
        }
      });
    } else if (markedOk && marked.setOptions) {
      marked.setOptions({ gfm: true, breaks: true });
    }

    var prefer = true;
    try {
      var stored = localStorage.getItem(MD_KEY);
      if (stored === '0') prefer = false;
      if (stored === '1') prefer = true;
    } catch (e) {}

    if (!markedOk) {
      prefer = false;
      mdToggle.disabled = true;
      mdToggle.title = 'Markdown library failed to load (CDN offline?)';
    }
    mdToggle.checked = prefer;
    setMarkdownMode(prefer && markedOk);
    mdToggle.addEventListener('change', function() {
      var on = mdToggle.checked && markedOk;
      try { localStorage.setItem(MD_KEY, mdToggle.checked ? '1' : '0'); } catch (e) {}
      setMarkdownMode(on);
    });
  }

})();
</script>
"""


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

    content = render_template_string(
        LIST_TEMPLATE,
        sessions=sessions,
        agent=agent,
        q=q,
        grok_path=str(GROK_HOME / "sessions"),
        claude_path=str(CLAUDE_HOME / "projects"),
        codex_path=str(CODEX_HOME / "sessions"),
    )
    return render_template_string(BASE, title="Sessions", content=content)


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

    turns = get_conversation(agent, path)
    title = path.name if path.is_file() else path.name

    summary = None
    resources = None
    artifacts = None
    hunks = None
    terminal_logs = None
    recaps = None
    updates = None

    if agent == "grok" and path.is_dir():
        summary = grok_summary_card(path)
        title = summary.get("title") or title
        resources = grok_resources(path)
        artifacts = (resources or {}).get("artifacts") or []
        hunks = grok_hunk_records(path)
        terminal_logs = grok_terminal_logs(path)
        recaps = grok_recap_requests(path)
        updates = grok_updates_timeline(path)
    elif agent == "codex" and path.is_file():
        scan = codex_scan_session(path)
        summary = scan["summary"]
        title = summary.get("title") or title
        resources = scan["resources"]
        artifacts = scan.get("artifacts") or []
        hunks = scan["hunks"]
        # Reuse "updates" tab for Codex task/patch/image timeline
        updates = scan["events"]
        # Ensure timeline turns have html for markdown toggle
        fixed = []
        for ev in updates:
            if "html" not in ev or not ev.get("html"):
                fixed.append(make_turn(
                    role=ev.get("role") or "event",
                    text=ev.get("text") or "",
                    time=ev.get("time") or "",
                    id=ev.get("id") or "",
                    model=ev.get("model") or "",
                    meta=ev.get("meta") or "",
                    images=ev.get("images"),
                ))
            else:
                fixed.append(ev)
        updates = fixed

    content = render_template_string(
        VIEW_TEMPLATE,
        agent=agent,
        path=str(path),
        title=title,
        turns=turns,
        summary=summary,
        resources=resources,
        artifacts=artifacts,
        hunks=hunks,
        terminal_logs=terminal_logs,
        recaps=recaps,
        updates=updates,
    )
    return render_template_string(BASE, title=title, content=content)


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

    turns = get_conversation(agent, path)
    title = path.name
    extra = ""

    if agent == "grok" and path.is_dir():
        summary = grok_summary_card(path)
        title = summary.get("title") or title
        resources = grok_resources(path)
        lines = [
            f"**Model:** {summary.get('model') or '—'}  ",
            f"**CWD:** `{summary.get('cwd') or '—'}`  ",
            f"**Agent:** {summary.get('agent_name') or '—'}  ",
            f"**Reasoning effort:** {summary.get('reasoning_effort') or '—'}  ",
            f"**Session id:** `{summary.get('id')}`  ",
        ]
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
        if resources.get("todos"):
            lines.append("")
            lines.append("### Todos")
            for t in resources["todos"]:
                mark = "x" if t["status"] == "completed" else " "
                lines.append(f"- [{mark}] `{t['id']}` {t['content']} ({t['status']})")
        extra = "\n".join(lines)
    elif agent == "codex" and path.is_file():
        summary = codex_summary_card(path)
        title = summary.get("title") or title
        lines = [
            f"**Model:** {summary.get('model') or '—'}  ",
            f"**CWD:** `{summary.get('cwd') or '—'}`  ",
            f"**Originator:** {summary.get('agent_name') or '—'}  ",
            f"**Reasoning effort:** {summary.get('reasoning_effort') or '—'}  ",
            f"**Sandbox:** {summary.get('sandbox_profile') or '—'}  ",
            f"**Session id:** `{summary.get('id')}`  ",
        ]
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
        extra = "\n".join(lines)

    md = turns_to_markdown(turns, title, agent, str(path), extra=extra)

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
