"""Image and mixed-content extraction helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .util import truncate

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
            images.append(
                image_ref_data(
                    url,
                    block.get("alt") or "image",
                    snippet=image_json_snippet(url=url),
                )
            )
        elif isinstance(url, str) and url.strip():
            # http(s) or path-like
            if url.startswith(("http://", "https://")):
                images.append(
                    {
                        "kind": "url",
                        "url": url,
                        "label": block.get("alt") or url,
                        "copyable": False,
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
        path = block.get("path") or block.get("file_path") or block.get("filename")
        if path:
            images.append(
                image_ref_file(
                    str(path),
                    str(path),
                    snippet=image_json_snippet(path=str(path)),
                )
            )
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
        if (
            "assets" in path.replace("\\", "/").lower()
            or "image-" in path.lower()
            or path.lower().startswith(("c:", "d:", "/", "~"))
        ):
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
