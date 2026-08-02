"""Flask routes and local development server."""

from __future__ import annotations

import logging
import mimetypes
import os
from importlib.metadata import PackageNotFoundError, version
from io import BytesIO

from flask import Flask, Response, abort, render_template, request, send_file

from . import config
from .authorization import (
    AuthorizationError,
    AuthorizedSession,
    InvalidAgent,
    PathMissing,
    parse_agent,
    resolve_media_path,
    resolve_raw_path,
    resolve_session_path,
)
from .discovery import all_sessions
from .grouping import group_by_project
from .home_inventory import inventory_all
from .markdown_output import format_markdown_content
from .registry import AGENT_SPECS, spec_for
from .session import (
    load_session,
    summary_to_markdown,
    system_artifacts_to_markdown,
    turns_to_markdown,
)
from .util import decode_html_entities, decode_view_data, epoch_seconds, human_time, rel_time

app = Flask(__name__)
app.jinja_env.filters["markdown_content"] = format_markdown_content
app.jinja_env.filters["decode_html_entities"] = decode_html_entities
app.jinja_env.filters["human_time"] = human_time
app.jinja_env.filters["rel_time"] = rel_time
app.jinja_env.filters["epoch_seconds"] = epoch_seconds


@app.context_processor
def inject_agents() -> dict[str, object]:
    """Expose the agent registry to every template (filter links, labels, paths)."""
    return {"agents": list(AGENT_SPECS.values())}


@app.after_request
def add_security_headers(response: Response) -> Response:
    """Apply browser security boundaries to every response."""
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "base-uri 'none'; "
        "connect-src 'none'; "
        "font-src 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "img-src 'self' data:; "
        "object-src 'none'; "
        "script-src 'self'; "
        "style-src 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.route("/")
def index():
    agent = request.args.get("agent")
    if agent in ("all", ""):
        agent = None
    q = (request.args.get("q") or "").strip()
    search_term = q.lower()

    sessions = decode_view_data(all_sessions(agent))

    if search_term:
        sessions = [
            s
            for s in sessions
            if search_term in str(s.get("title") or "").lower()
            or search_term in str(s.get("id") or "").lower()
            or search_term in str(s.get("cwd") or "").lower()
            or search_term in str(s.get("path") or "").lower()
            or search_term in str(s.get("model") or "").lower()
            or search_term in str(s.get("headline") or "").lower()
        ]

    return render_template(
        "list.html",
        title="Sessions",
        sessions=sessions,
        projects=group_by_project(sessions),
        agent=agent,
        q=q,
    )


@app.route("/agents")
def agents():
    """Read-only inventory of each coding-agent home directory.

    Scans allowlisted settings, skills, hooks, plugins, MCP, and instruction
    files under configured homes. Secrets are redacted; session transcripts,
    auth files, and plugin source caches are never opened. Nothing is written.
    """
    return render_template(
        "agents.html",
        title="Agents",
        reports=inventory_all(),
    )


@app.route("/settings")
def settings():
    """Browser preferences, plus read-only facts about this install.

    Nothing here is written server-side: every preference lives in localStorage
    and is applied by settings.js. The server section is diagnostics only, which
    keeps the app read-only and the documented threat model unchanged.
    """
    counts: dict[str, int] = {}
    for card in all_sessions():
        agent_id = str(card.get("agent") or "")
        counts[agent_id] = counts.get(agent_id, 0) + 1

    agent_rows = []
    for spec in AGENT_SPECS.values():
        home = spec.home()
        agent_rows.append(
            {
                "id": spec.id,
                "label": spec.label,
                "home": str(home),
                "home_exists": home.is_dir(),
                "roots": [
                    {"path": str(root), "exists": root.is_dir(), "optional": optional}
                    for root, optional in spec.root_specs()
                ],
                "env_var": spec.home_attr,
                "sessions": counts.get(spec.id, 0),
            }
        )

    return render_template(
        "settings.html",
        title="Settings",
        agent_rows=agent_rows,
        version=_installed_version(),
        dotenv_path=str(config.DOTENV_PATH) if config.DOTENV_PATH else None,
        debug_flags=[
            ("ASV_DEBUG", _env_flag("ASV_DEBUG")),
            ("ASV_TIMING_DEBUG", _env_flag("ASV_TIMING_DEBUG")),
        ],
    )


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _installed_version() -> str:
    try:
        return version("agent-session-viewer")
    except PackageNotFoundError:
        # Running from a source checkout without an install.
        return "dev"


@app.route("/view")
def view():
    path_str = request.args.get("path")
    agent = request.args.get("agent")
    if not path_str:
        abort(400, "Missing path or agent")
    authorized = _authorized_session(agent, path_str)
    path = authorized.path
    agent = authorized.agent

    session = decode_view_data(load_session(agent, path))
    return render_template(
        "view.html",
        agent=agent,
        spec=spec_for(agent),
        path=str(path),
        title=session["title"],
        turns=session["turns"],
        summary=session["summary"],
        resources=session["resources"],
        artifacts=session["artifacts"],
        system_artifacts=session.get("system_artifacts"),
        hunks=session["hunks"],
        terminal_logs=session["terminal_logs"],
        recaps=session["recaps"],
        updates=session["updates"],
        subagents=session.get("subagents"),
        diagnostics=session["diagnostics"],
    )


@app.route("/export")
def export_md():
    path_str = request.args.get("path")
    agent = request.args.get("agent")
    if not path_str:
        abort(400)
    authorized = _authorized_session(agent, path_str)
    path = authorized.path
    agent = authorized.agent

    session = decode_view_data(load_session(agent, path))
    extra_parts = [
        summary_to_markdown(
            session["summary"],
            agent=agent,
            resources=session["resources"],
        ),
        system_artifacts_to_markdown(session.get("system_artifacts")),
    ]
    extra = "\n\n".join(part for part in extra_parts if part)
    md = turns_to_markdown(
        session["turns"],
        session["title"],
        agent,
        str(path),
        extra=extra,
    )

    filename = f"{agent}-{path.stem[:40]}.md"
    return send_file(
        BytesIO(md.encode("utf-8")),
        mimetype="text/markdown",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/raw")
def raw():
    path_str = request.args.get("path")
    agent = request.args.get("agent")
    if not path_str:
        abort(400)
    session = _authorized_session(agent, path_str)
    try:
        path = resolve_raw_path(session)
    except AuthorizationError as exc:
        _abort_authorization(exc)
    return send_file(
        path,
        mimetype="text/plain",
        as_attachment=True,
        download_name=path.name,
        conditional=True,
    )


def _abort_authorization(exc: AuthorizationError) -> None:
    if isinstance(exc, InvalidAgent):
        abort(400, str(exc))
    if isinstance(exc, PathMissing):
        abort(404, str(exc))
    abort(403, str(exc))


def _authorized_session(agent_value: str | None, path: str) -> AuthorizedSession:
    try:
        agent = parse_agent(agent_value)
        return resolve_session_path(agent, path)
    except AuthorizationError as exc:
        _abort_authorization(exc)
    raise AssertionError("unreachable")


@app.route("/media")
def media():
    """Serve passive image media associated with an authorized session."""
    path_str = request.args.get("path")
    session_path = request.args.get("session")
    agent = request.args.get("agent")
    if not path_str or not session_path:
        abort(400, "Missing path, session, or agent")
    session = _authorized_session(agent, session_path)
    try:
        path = resolve_media_path(session, path_str)
    except AuthorizationError as exc:
        _abort_authorization(exc)

    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return send_file(path, mimetype=mime, conditional=True, download_name=path.name)


def run(host: str = "127.0.0.1", port: int = 5050) -> None:
    """Start the local viewer (loopback only). Set ASV_DEBUG=1 for Flask debug mode."""
    debug = os.environ.get("ASV_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
    timing_debug = os.environ.get("ASV_TIMING_DEBUG", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO if timing_debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    print("Agent Session Viewer")
    print(f"-> http://{host}:{port}")
    for spec in AGENT_SPECS.values():
        print(f"{spec.label:<7}: {spec.looking_in()}")
    if debug:
        print("Debug  : on (ASV_DEBUG)")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run()
