import os
import subprocess
import sys
from pathlib import Path

from agent_session_viewer import config


def test_load_dotenv_sets_unset_values_and_parses_common_forms(tmp_path: Path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# comment",
                "GROK_HOME=~/custom-grok",
                'CLAUDE_HOME="C:\\Users\\Example\\Claude Data"',
                "export CODEX_HOME='C:\\Codex Data'",
                "ASV_DEBUG=1 # local debugging",
                "NOT VALID=value",
                "line-without-equals",
            ]
        ),
        encoding="utf-8",
    )
    for key in ("GROK_HOME", "CLAUDE_HOME", "CODEX_HOME", "ASV_DEBUG"):
        monkeypatch.delenv(key, raising=False)

    loaded = config.load_dotenv(env_file)

    assert loaded == env_file
    assert config.os.environ["GROK_HOME"] == "~/custom-grok"
    assert config.os.environ["CLAUDE_HOME"] == "C:\\Users\\Example\\Claude Data"
    assert config.os.environ["CODEX_HOME"] == "C:\\Codex Data"
    assert config.os.environ["ASV_DEBUG"] == "1"
    assert "NOT VALID" not in config.os.environ


def test_load_dotenv_does_not_override_explicit_environment(tmp_path: Path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("CODEX_HOME=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", "from-shell")

    config.load_dotenv(env_file)

    assert config.os.environ["CODEX_HOME"] == "from-shell"


def test_load_dotenv_missing_file_is_optional(tmp_path: Path):
    assert config.load_dotenv(tmp_path / "missing.env") is None


def test_config_import_automatically_loads_dotenv_from_working_directory(
    tmp_path: Path,
):
    expected = tmp_path / "codex-from-dotenv"
    (tmp_path / ".env").write_text(f"CODEX_HOME={expected}\n", encoding="utf-8")
    process_env = os.environ.copy()
    process_env.pop("CODEX_HOME", None)
    project_root = Path(__file__).parent.parent
    process_env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(project_root), process_env.get("PYTHONPATH", "")) if part
    )

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from agent_session_viewer.config import CODEX_HOME; print(CODEX_HOME)",
        ],
        cwd=tmp_path,
        env=process_env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == str(expected)
