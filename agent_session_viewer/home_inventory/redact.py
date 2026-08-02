"""Scrub secrets from config text and structured values before display."""

from __future__ import annotations

import json
import re
from typing import Any

# Key names that almost always hold credentials.
_SECRET_KEY = re.compile(
    r"(api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|\btoken\b|"
    r"secret|password|passwd|authorization|credential|private[_-]?key|"
    r"client[_-]?secret|bearer)",
    re.IGNORECASE,
)

# PowerShell -EncodedCommand <base64> payloads (common in Cursor hooks).
_ENCODED_CMD = re.compile(
    r"(-EncodedCommand\s+)([A-Za-z0-9+/]{40,}={0,2})",
    re.IGNORECASE,
)

# Long base64-looking tokens that are unlikely to be prose.
# Cap length so a multi-hundred-KB skill body cannot match as one giant "token".
_LONG_B64 = re.compile(r"\b[A-Za-z0-9+/]{80,500}={0,2}\b")

# JSON/TOML-ish "key": "value" or key = "value" for secret keys in raw text.
_SECRET_ASSIGN = re.compile(
    r'(?P<prefix>(?P<quote>["\']?)(?P<key>[A-Za-z0-9_.-]+)(?P=quote)\s*[:=]\s*)'
    r'(?P<q>["\'])(?P<val>(?:\\.|(?!\4).)*)(?P=q)',
)


def redact_value(key: str, value: Any) -> Any:
    """Redact a single structured value when its key looks secret."""
    if _SECRET_KEY.search(key):
        if value is None or value == "":
            return value
        return "***"
    if isinstance(value, dict):
        return redact_mapping(value)
    if isinstance(value, list):
        return [redact_value(key, item) for item in value]
    if isinstance(value, str):
        return redact_command_string(value)
    return value


def redact_mapping(data: dict[str, Any]) -> dict[str, Any]:
    return {k: redact_value(str(k), v) for k, v in data.items()}


def redact_command_string(text: str) -> str:
    """Scrub encoded payloads and long base64 blobs from shell command strings."""
    if not text:
        return text
    text = _ENCODED_CMD.sub(r"\1[redacted command payload]", text)
    text = _LONG_B64.sub("[redacted blob]", text)
    return text


def redact_json_text(raw: str) -> str:
    """Parse JSON, redact, re-serialize; fall back to line-oriented redaction."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return redact_text(raw)
    redacted = _redact_any(data)
    return json.dumps(redacted, indent=2, ensure_ascii=False) + "\n"


def redact_text(raw: str) -> str:
    """Best-effort redaction of free-form config text (TOML, scripts, etc.).

    Large blobs (skill bodies, long markdown) only get cheap command/blob scrubbing
    so the page never ReDoS-hangs on multi-hundred-KB files.
    """
    if not raw:
        return raw

    # Regex assignment scanning is O(n) only on modest config files. Skill bodies
    # and dumps can be large; skip the assignment pass past this threshold.
    if len(raw) > 64 * 1024:
        return redact_command_string(raw)

    def _sub(match: re.Match[str]) -> str:
        key = match.group("key")
        if _SECRET_KEY.search(key):
            q = match.group("q")
            return f"{match.group('prefix')}{q}***{q}"
        # Still scrub encoded payloads inside values.
        val = redact_command_string(match.group("val"))
        q = match.group("q")
        return f"{match.group('prefix')}{q}{val}{q}"

    out = _SECRET_ASSIGN.sub(_sub, raw)
    out = redact_command_string(out)
    return out


def _redact_any(data: Any) -> Any:
    if isinstance(data, dict):
        return redact_mapping(data)
    if isinstance(data, list):
        return [_redact_any(item) for item in data]
    if isinstance(data, str):
        return redact_command_string(data)
    return data
