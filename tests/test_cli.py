from __future__ import annotations

import sys

from agent_session_viewer import cli


def test_cli_passes_host_and_port_to_server(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(sys, "argv", ["agent-session-viewer", "--host", "localhost", "--port", "0"])
    monkeypatch.setattr(cli, "run", lambda host, port: calls.append((host, port)))

    cli.main()

    assert calls == [("localhost", 0)]
