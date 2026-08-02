"""Read-only inventory of coding-agent home directories.

Scans allowlisted config, skills, hooks, plugins, MCP, and instruction files under
each agent home. Never opens session transcripts, auth files, or plugin source caches.
"""

from __future__ import annotations

from .scan import AgentHomeReport, inventory_all, inventory_one
from .specs import HOME_SPECS, HomeSpec

__all__ = [
    "AgentHomeReport",
    "HOME_SPECS",
    "HomeSpec",
    "inventory_all",
    "inventory_one",
]
