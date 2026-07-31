"""Image and mixed-content extraction helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .util import decode_html_entities, truncate

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".avif"}
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
# JSON object blobs that embed a data URL under url / image_url / src.
# Supports optional "type":"image", pretty-printed whitespace, and escaped quotes.
DATA_IMAGE_JSON_RE = re.compile(
    r"\{[^{}]*?"
    r'(?:\\?"type\\?"\s*:\s*\\?"image\\?"\s*,\s*)?'
    r'\\?"(?:url|image_url|src)\\?"\s*:\s*\\?"'
    r"(data:image/[^\"\\]+)"
    r'\\?"'
    r"[^{}]*?\}",
    re.IGNORECASE | re.DOTALL,
)
# Markdown image pointing at a data URL: ![alt](data:image/...)
MARKDOWN_DATA_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\(\s*(data:image/[^)\s]+)\s*\)",
    re.IGNORECASE,
)


def _normalize_data_image_url(raw: str) -> str | None:
    """Clean and validate a candidate data:image URL for <img src> use."""
    if not raw or not isinstance(raw, str):
        return None
    url = raw.strip().strip("`'\"")
    # Undo common JSON/string escaping of the data-URL body.
    if "\\/" in url:
        url = url.replace("\\/", "/")
    if not url.lower().startswith("data:image/"):
        return None
    comma = url.find(",")
    if comma < 0:
        return None
    header, payload = url[:comma], url[comma + 1 :]
    header_l = header.lower()
    if ";base64" in header_l:
        payload = re.sub(r"\s+", "", payload)
        # Trim trailing junk that often rides along from surrounding prose/JSON.
        payload = re.split(r"[^A-Za-z0-9+/=]+", payload, maxsplit=1)[0]
        if not payload:
            return None
        return f"{header},{payload}"
    # Non-base64 (e.g. svg+xml;charset=utf-8,<svg…>) - keep payload, strip wrappers.
    payload = payload.strip().rstrip(".,;:)")
    if not payload:
        return None
    return f"{header},{payload}"


def is_image_path(path: str | Path) -> bool:
    try:
        return Path(str(path)).suffix.lower() in IMAGE_EXTS
    except (TypeError, ValueError):
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
        except (IndexError, TypeError):
            pass
    return {
        "kind": "data",
        "url": url,
        "label": label or "image",
        "mime": mime,
        "copyable": True,
        "snippet": snippet or image_json_snippet(url=url),
    }


def media_href(path: str, *, agent: str | None = None, session: str | None = None) -> str:
    """Build a /media URL that includes the session authorization context."""
    q = [f"path={quote(str(path), safe='')}"]
    if agent:
        q.append(f"agent={quote(str(agent), safe='')}")
    if session:
        q.append(f"session={quote(str(session), safe='')}")
    return "/media?" + "&".join(q)


def image_ref_file(
    path: str,
    label: str = "",
    snippet: str | None = None,
    *,
    agent: str | None = None,
    session: str | None = None,
) -> dict:
    return {
        "kind": "file",
        "path": path,
        "label": label or path,
        "href": media_href(path, agent=agent, session=session),
        "copyable": True,  # UI can copy the media URL string (and bitmap when loaded)
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
        if isinstance(url, str):
            cleaned = _normalize_data_image_url(url)
            if cleaned:
                images.append(
                    image_ref_data(
                        cleaned,
                        block.get("alt") or "image",
                        snippet=image_json_snippet(url=cleaned),
                    )
                )
            elif url.strip():
                # http(s) or path-like
                if url.startswith(("http://", "https://")):
                    images.append(
                        {
                            "kind": "url",
                            "url": url,
                            "label": block.get("alt") or url,
                            "copyable": True,
                            "snippet": image_json_snippet(url=url),
                        }
                    )
                else:
                    images.append(
                        image_ref_file(
                            url,
                            block.get("alt") or url,
                            snippet=image_json_snippet(url=url, path=url),
                        )
                    )
        # Nested OpenAI-style image_url: { url: "..." }
        nested = block.get("image_url")
        if isinstance(nested, dict) and nested.get("url") and not images:
            images.extend(
                collect_image_blocks({"type": "image", "url": nested.get("url"), "alt": block.get("alt")})
            )
        path = block.get("path") or block.get("file_path") or block.get("filename")
        if path:
            images.append(
                image_ref_file(
                    str(path),
                    str(path),
                    snippet=image_json_snippet(path=str(path)),
                )
            )
    else:
        # Blocks that only carry image_url / url / source without a typed image role.
        candidate = block.get("url") or block.get("image_url") or ""
        if isinstance(candidate, dict):
            candidate = candidate.get("url") or candidate.get("image_url") or ""
        source = block.get("source") if isinstance(block.get("source"), dict) else None
        has_data = isinstance(candidate, str) and candidate.startswith("data:image")
        has_b64 = bool(source and source.get("data"))
        if has_data or has_b64:
            fake = dict(block)
            fake["type"] = "image"
            return collect_image_blocks(fake)
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
            cleaned = _normalize_data_image_url(item)
            if cleaned:
                images.append(image_ref_data(cleaned))
            elif is_image_path(item) or "/" in item or "\\" in item:
                images.append(image_ref_file(item, item))
        elif isinstance(item, dict):
            before = len(images)
            images.extend(collect_image_blocks(item))
            if len(images) == before:
                for key in ("url", "image_url", "src"):
                    val = item.get(key)
                    if isinstance(val, dict):
                        val = val.get("url") or val.get("image_url")
                    if isinstance(val, str):
                        cleaned = _normalize_data_image_url(val)
                        if cleaned:
                            images.append(image_ref_data(cleaned))
                            break
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
        if (
            "assets" in path.replace("\\", "/").lower()
            or "image-" in path.lower()
            or path.lower().startswith(("c:", "d:", "/", "~"))
        ):
            add(path)

    return found


def _scan_raw_data_image_urls(text: str) -> list[tuple[int, int, str]]:
    """Locate raw data:image spans in text as (start, end, raw_url)."""
    found: list[tuple[int, int, str]] = []
    lower = text.lower()
    start = 0
    while True:
        i = lower.find("data:image/", start)
        if i < 0:
            break
        comma = text.find(",", i)
        if comma < 0:
            break
        header = text[i:comma]
        header_l = header.lower()
        j = comma + 1
        if ";base64" in header_l:
            # Allow internal whitespace/newlines inside base64, but stop before
            # trailing prose. Padding '=' ends the payload.
            while j < len(text):
                ch = text[j]
                if ch.isalnum() or ch in "+/":
                    j += 1
                    continue
                if ch == "=":
                    j += 1
                    while j < len(text) and text[j] == "=":
                        j += 1
                    break
                if ch in "\r\n\t ":
                    k = j
                    while k < len(text) and text[k] in "\r\n\t ":
                        k += 1
                    if k < len(text) and (text[k].isalnum() or text[k] in "+/"):
                        j = k
                        continue
                    break
                break
        else:
            # Non-base64 payloads (often svg+xml). Prefer a complete <svg>…</svg>
            # block when present; otherwise stop at a clear delimiter.
            if j < len(text) and text[j] == "<":
                close = lower.find("</svg>", j)
                if close >= 0:
                    j = close + len("</svg>")
                else:
                    while j < len(text) and text[j] not in "\"'\n\r":
                        j += 1
            else:
                while j < len(text) and text[j] not in "\"'\n\r <>":
                    j += 1
        raw = text[i:j]
        if _normalize_data_image_url(raw):
            found.append((i, j, raw))
        start = max(j, i + 1)
    return found


def extract_data_images_from_text(text: str) -> tuple[str, list[dict]]:
    """
    Pull inline data-URL images (and JSON image objects) out of text so we can
    render them, leaving a short placeholder in the text.
    """
    if not text or "data:image" not in text.lower():
        return text, []

    images: list[dict] = []
    seen: set[str] = set()
    out = text

    def remember(url: str) -> str | None:
        cleaned = _normalize_data_image_url(url)
        if not cleaned:
            return None
        # Skip truncated display placeholders (ellipsis) so we don't invent a
        # second broken image from our own snippet text.
        if "…" in url or "..." in url:
            return image_json_snippet(url=cleaned) if cleaned in seen else None
        key = f"{cleaned[:80]}:{len(cleaned)}"
        snippet = image_json_snippet(url=cleaned)
        if key not in seen:
            seen.add(key)
            images.append(image_ref_data(cleaned, snippet=snippet))
        return snippet

    def keep_json(match: re.Match) -> str:
        snippet = remember(match.group(1))
        return snippet if snippet is not None else match.group(0)

    def keep_md(match: re.Match) -> str:
        snippet = remember(match.group(1))
        if snippet is None:
            return match.group(0)
        alt_end = match.group(0).find("]")
        alt = match.group(0)[2:alt_end] if alt_end > 2 else ""
        return f"![{alt}]({snippet})"

    out = DATA_IMAGE_JSON_RE.sub(keep_json, out)
    out = MARKDOWN_DATA_IMAGE_RE.sub(keep_md, out)

    # Scan remaining free-text data URLs (base64 and non-base64) right-to-left
    # so offsets stay valid while replacing.
    if "data:image" in out.lower():
        spans = _scan_raw_data_image_urls(out)
        for start, end, raw in reversed(spans):
            if "…" in raw or "..." in raw:
                continue
            snippet = remember(raw)
            if snippet is not None:
                out = out[:start] + snippet + out[end:]

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
        except (OSError, RuntimeError, ValueError):
            continue

    # Absolute path that exists even if Path quirks on encoded session folders
    try:
        if p.is_file() and is_image_path(p):
            return p.resolve()
    except (OSError, RuntimeError, ValueError):
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
                    parts.append(
                        f"[tool_use] {name}({truncate(json.dumps(inp, default=str), 120)})"
                    )
                elif t == "tool_result":
                    c = block.get("content")
                    if isinstance(c, list):
                        sub_text, sub_imgs = extract_text_and_images(
                            c, session_dir=session_dir, cwd=cwd
                        )
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

    # File paths from <image_files> and path mentions. Prose can also merely talk
    # about paths (placeholders like "/N.png"), so only mentions that resolve to a
    # real file become cards; the rest stay plain text.
    for path_str in extract_image_paths_from_text(text):
        resolved = resolve_session_image_path(path_str, session_dir=session_dir, cwd=cwd)
        if resolved is not None:
            images.append(image_ref_file(str(resolved), path_str))

    # The same rule applies to structured refs (image blocks carrying a path key,
    # tool_result `images` fields): resolve against the session so relative paths
    # can preview, and drop refs whose file does not exist — a card that can
    # neither preview nor serve is just noise.
    kept: list[dict] = []
    for img in images:
        if img.get("kind") == "file":
            resolved = resolve_session_image_path(
                str(img.get("path") or ""), session_dir=session_dir, cwd=cwd
            )
            if resolved is None:
                continue
            img["path"] = str(resolved)
            img["href"] = media_href(str(resolved))
        kept.append(img)
    images = kept

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


def linkify_image_paths_html(
    text: str,
    images: list[dict] | None = None,
    *,
    agent: str | None = None,
    session: str | None = None,
) -> str:
    """
    Escape text for HTML, then turn known image paths into clickable links.
    Returns safe HTML.

    ``agent`` and ``session`` are required for working /media links (authorization).
    """
    from html import escape

    if not text:
        return ""
    html = escape(text)
    paths: list[str] = []
    if images:
        for img in images:
            if img.get("kind") == "file" and img.get("path"):
                paths.append(str(img["path"]))
                if img.get("label") and img["label"] != img["path"]:
                    paths.append(str(img["label"]))
    # Only paths backed by an image card get linked; re-scanning the text here
    # would turn unresolved prose mentions into dead /media links.

    # Longest first so nested prefixes don't break replacement
    for path in sorted(set(paths), key=len, reverse=True):
        if not path:
            continue
        esc_path = escape(path)
        if esc_path not in html:
            continue
        href = media_href(path, agent=agent, session=session)
        link = (
            f'<a class="img-path-link" href="{escape(href)}" '
            f'target="_blank" rel="noopener">{esc_path}</a>'
        )
        html = html.replace(esc_path, link)
    return html


def rebind_turn_media_links(
    turns: list[dict] | None,
    *,
    agent: str,
    session: str | Path,
) -> list[dict] | None:
    """Rebuild path→/media HTML on turns now that agent/session are known."""
    if not turns:
        return turns
    session_str = str(session)
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        text = turn.get("text") or ""
        images = turn.get("images") if isinstance(turn.get("images"), list) else []
        # Refresh file hrefs on image refs too (used by some clients / exports).
        for img in images:
            if isinstance(img, dict) and img.get("kind") == "file" and img.get("path"):
                img["href"] = media_href(str(img["path"]), agent=agent, session=session_str)
                img["copyable"] = True
            elif isinstance(img, dict) and img.get("kind") in ("data", "url"):
                img["copyable"] = True
        turn["html"] = linkify_image_paths_html(
            decode_html_entities(str(text)),
            images,
            agent=agent,
            session=session_str,
        )
    return turns
