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
    file_artifacts: list[dict[str, Any]] | None = None,
    file_read_prefix: str | None = None,
) -> Turn:
    imgs = images or []
    turn: Turn = {
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
    if file_artifacts:
        turn["file_artifacts"] = file_artifacts
        if file_read_prefix is not None:
            turn["file_read_prefix"] = file_read_prefix
    return turn


def format_tool_args(arguments: Any) -> str:
    if arguments is None:
        return ""
    if isinstance(arguments, str):
        try:
            return pretty_json(json.loads(arguments))
        except json.JSONDecodeError:
            return arguments
    return pretty_json(arguments)
