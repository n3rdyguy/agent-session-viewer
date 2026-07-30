"""Canonical turn construction and tool formatting."""

from __future__ import annotations

import json
from typing import Any

from .images import linkify_image_paths_html
from .types import ImageInfo, Turn
from .util import decode_html_entities, pretty_json


def make_turn(
    *,
    role: str,
    text: str = "",
    time: str = "",
    id: str = "",
    model: str = "",
    meta: str = "",
    images: list[ImageInfo] | None = None,
) -> Turn:
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
        "html": linkify_image_paths_html(decode_html_entities(text), imgs),
    }


def format_tool_args(arguments: Any) -> str:
    if arguments is None:
        return ""
    if isinstance(arguments, str):
        try:
            return pretty_json(json.loads(arguments))
        except json.JSONDecodeError:
            return arguments
    return pretty_json(arguments)
