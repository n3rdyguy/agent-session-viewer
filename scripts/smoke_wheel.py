"""Install built artifacts in isolation and verify package-owned runtime assets."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

PACKAGE_ASSETS = {
    "agent_session_viewer/__main__.py",
    "agent_session_viewer/app.py",
    "agent_session_viewer/cli.py",
    "agent_session_viewer/static/app.css",
    "agent_session_viewer/static/app.js",
    "agent_session_viewer/static/prefs.js",
    "agent_session_viewer/static/settings.js",
    "agent_session_viewer/static/agents.js",
    "agent_session_viewer/static/theme-boot.js",
    "agent_session_viewer/static/apple-touch-icon.png",
    "agent_session_viewer/static/favicon-16.png",
    "agent_session_viewer/static/favicon-32.png",
    "agent_session_viewer/static/favicon.ico",
    "agent_session_viewer/static/favicon.svg",
    "agent_session_viewer/static/vendor/README.md",
    "agent_session_viewer/static/vendor/dompurify/LICENSE",
    "agent_session_viewer/static/vendor/dompurify/LICENSE-MPL",
    "agent_session_viewer/static/vendor/dompurify/purify.min.js",
    "agent_session_viewer/static/vendor/marked/LICENSE.md",
    "agent_session_viewer/static/vendor/marked/marked.min.js",
    "agent_session_viewer/templates/base.html",
    "agent_session_viewer/templates/list.html",
    "agent_session_viewer/templates/partials/bubbles.html",
    "agent_session_viewer/templates/settings.html",
    "agent_session_viewer/templates/agents.html",
    "agent_session_viewer/templates/view.html",
}
FORBIDDEN_WHEEL_PREFIXES = ("app.py", "main.py", "templates/", "static/")
STATIC_URLS = (
    "/static/app.css",
    "/static/app.js",
    "/static/prefs.js",
    "/static/settings.js",
    "/static/agents.js",
    "/static/theme-boot.js",
    "/static/apple-touch-icon.png",
    "/static/favicon-16.png",
    "/static/favicon-32.png",
    "/static/favicon.ico",
    "/static/favicon.svg",
    "/static/vendor/dompurify/purify.min.js",
    "/static/vendor/marked/marked.min.js",
)


def built_artifacts() -> tuple[Path, Path]:
    wheels = sorted(Path("dist").glob("agent_session_viewer-*.whl"))
    sdists = sorted(Path("dist").glob("agent_session_viewer-*.tar.gz"))
    if not wheels or not sdists:
        raise SystemExit("Build both a wheel and source distribution before running smoke test")
    return wheels[-1].resolve(), sdists[-1].resolve()


def verify_artifact_contents(wheel: Path, sdist: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        wheel_names = set(archive.namelist())
    missing = sorted(PACKAGE_ASSETS - wheel_names)
    if missing:
        raise SystemExit(f"Wheel is missing runtime files: {', '.join(missing)}")
    collisions = sorted(
        name
        for name in wheel_names
        if any(name == prefix or name.startswith(prefix) for prefix in FORBIDDEN_WHEEL_PREFIXES)
    )
    if collisions:
        raise SystemExit(f"Wheel contains top-level runtime collisions: {', '.join(collisions)}")

    with tarfile.open(sdist, "r:gz") as archive:
        sdist_names = {name.partition("/")[2] for name in archive.getnames() if "/" in name}
    missing = sorted(PACKAGE_ASSETS - sdist_names)
    if missing:
        raise SystemExit(f"Source distribution is missing runtime files: {', '.join(missing)}")


def python_in(venv_dir: Path) -> Path:
    directory = "Scripts" if os.name == "nt" else "bin"
    executable = "python.exe" if os.name == "nt" else "python"
    return venv_dir / directory / executable


def console_script_in(venv_dir: Path) -> Path:
    directory = "Scripts" if os.name == "nt" else "bin"
    executable = "agent-session-viewer.exe" if os.name == "nt" else "agent-session-viewer"
    return venv_dir / directory / executable


def isolated_environment(directory: Path, wheel: Path) -> tuple[Path, dict[str, str]]:
    venv_dir = directory / "venv"
    subprocess.run(
        ["uv", "venv", "--python", sys.executable, str(venv_dir)],
        check=True,
    )
    python = python_in(venv_dir)
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), str(wheel)],
        check=True,
    )
    env = os.environ.copy()
    for name in ("GROK_HOME", "CLAUDE_HOME", "CODEX_HOME", "CURSOR_HOME"):
        env[name] = str(directory / name.lower())
    return python, env


def verify_test_client(python: Path, env: dict[str, str], cwd: Path) -> None:
    code = """
from agent_session_viewer.app import app
client = app.test_client()
response = client.get("/")
assert response.status_code == 200
assert b"Agent Session Viewer" in response.data
for url in %r:
    asset = client.get(url)
    assert asset.status_code == 200, (url, asset.status_code)
    assert asset.data, url
""" % (STATIC_URLS,)
    subprocess.run([str(python), "-c", code], check=True, env=env, cwd=cwd)


def verify_server(command: list[str], env: dict[str, str], cwd: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    command = [*command, "--port", str(port)]
    process = subprocess.Popen(
        command,
        env=env,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        for _ in range(100):
            if process.poll() is not None:
                output = process.communicate()[0]
                raise SystemExit(
                    f"Installed entrypoint exited early: {' '.join(command)}\n{output}"
                )
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=0.2) as response:
                    if response.status == 200:
                        return
            except OSError:
                time.sleep(0.05)
        raise SystemExit(f"Installed entrypoint did not start: {' '.join(command)}")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main() -> None:
    wheel, sdist = built_artifacts()
    verify_artifact_contents(wheel, sdist)
    # Windows can refuse to unlink extension modules the venv still holds open
    # (for example markupsafe's .pyd), which would fail the run during teardown
    # after every check has already passed.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        # Expand Windows 8.3/redirected temp paths before venv writes launcher metadata.
        directory = Path(os.path.realpath(temporary))
        python, env = isolated_environment(directory, wheel)
        verify_test_client(python, env, directory)

        console_script = console_script_in(directory / "venv")
        if not console_script.is_file():
            raise SystemExit("Installed console script was not created")
        verify_server([str(console_script)], env, directory)
        verify_server([str(python), "-m", "agent_session_viewer"], env, directory)


if __name__ == "__main__":
    main()
