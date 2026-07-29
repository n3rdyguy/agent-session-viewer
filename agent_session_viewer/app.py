"""Flask routes and local development server."""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from urllib.parse import unquote

from flask import Flask, Response, abort, render_template, request, send_file

from .config import CLAUDE_HOME, CODEX_HOME, GROK_HOME
from .discovery import all_sessions
from .images import is_image_path
from .markdown_output import format_markdown_content
from .session import load_session, summary_to_markdown, turns_to_markdown
from .util import path_allowed

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent.parent / "templates"),
    static_folder=str(Path(__file__).parent.parent / "static"),
)
app.jinja_env.filters["markdown_content"] = format_markdown_content


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
    if not path_str or not agent:
        abort(400, "Missing path or agent")

    path = Path(path_str)
    if not path.exists():
        abort(404, "Session not found")

    if not path_allowed(path):
        abort(403, "Path not allowed")

    session = load_session(agent, path)
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
    )


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

    session = load_session(agent, path)
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
