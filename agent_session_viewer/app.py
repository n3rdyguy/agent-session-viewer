"""Flask routes and local development server."""

from __future__ import annotations

import logging
import mimetypes
import os
from io import BytesIO
from typing import NoReturn

from flask import Flask, Response, abort, render_template, request, send_file

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
from .config import CLAUDE_HOME, CODEX_HOME, GROK_HOME
from .discovery import all_sessions
from .markdown_output import format_markdown_content
from .session import load_session, summary_to_markdown, turns_to_markdown
from .util import decode_html_entities, decode_view_data

app = Flask(__name__)
app.jinja_env.filters["markdown_content"] = format_markdown_content
app.jinja_env.filters["decode_html_entities"] = decode_html_entities


@app.after_request
def add_security_headers(response: Response) -> Response:
    """Apply browser security boundaries to every response."""
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "base-uri 'none'; "
        "connect-src 'none'; "
        "font-src 'self'; "
        "form-action 'none'; "
        "frame-ancestors 'none'; "
        "img-src 'self' data:; "
        "object-src 'none'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.route("/")
def index():
    agent = request.args.get("agent")
    if agent in ("all", ""):
        agent = None
    q = (request.args.get("q") or "").strip().lower()

    sessions = decode_view_data(all_sessions(agent))

    if q:
        sessions = [
            s
            for s in sessions
            if q in (s.get("title") or "").lower()
            or q in (s.get("id") or "").lower()
            or q in (s.get("cwd") or "").lower()
            or q in (s.get("path") or "").lower()
            or q in (s.get("model") or "").lower()
            or q in (s.get("headline") or "").lower()
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
    if not path_str:
        abort(400, "Missing path or agent")
    authorized = _authorized_session(agent, path_str)
    path = authorized.path
    agent = authorized.agent

    session = decode_view_data(load_session(agent, path))
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


def _abort_authorization(exc: AuthorizationError) -> NoReturn:
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
    print(f"→ http://{host}:{port}")
    print(f"Grok   : {GROK_HOME / 'sessions'}")
    print(f"Claude : {CLAUDE_HOME / 'projects'}")
    print(f"Codex  : {CODEX_HOME / 'sessions'}")
    if debug:
        print("Debug  : on (ASV_DEBUG)")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run()
