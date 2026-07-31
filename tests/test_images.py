from pathlib import Path

import pytest

from agent_session_viewer.images import (
    extract_data_images_from_text,
    extract_text_and_images,
    is_image_path,
    linkify_image_paths_html,
    media_href,
    rebind_turn_media_links,
    resolve_session_image_path,
)
from agent_session_viewer.session import load_session
from agent_session_viewer.turns import make_turn


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("photo.PNG", True),
        ("diagram.svg", False),
        ("archive.png.txt", False),
        ("no-extension", False),
    ],
)
def test_is_image_path(path: str, expected: bool) -> None:
    assert is_image_path(path) is expected


def test_extracts_explicit_data_image_and_preserves_text() -> None:
    data_url = "data:image/png;base64,aGVsbG8="
    text, images = extract_text_and_images(
        [
            {"type": "text", "text": "Before"},
            {"type": "input_image", "image_url": data_url, "alt": "fixture"},
            {"type": "text", "text": "After"},
        ]
    )

    assert text == "Before\nAfter"
    assert [(image["kind"], image["url"], image["label"]) for image in images] == [
        ("data", data_url, "fixture")
    ]


@pytest.mark.parametrize(
    "payload",
    [
        '{"type": "image", "url": "data:image/png;base64,aGVsbG8="}',
        '{"image_url":"data:image/png;base64,aGVsbG8="}',
        r'{\"type\": \"image\", \"url\": \"data:image/png;base64,aGVsbG8=\"}',
        "data:image/png;base64,aGVsbG8=",
        "data:image/svg+xml;base64,PHN2Zz4=",
        "![shot](data:image/png;base64,aGVsbG8=)",
        [{"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8="}],
        [{"type": "image", "image_url": {"url": "data:image/png;base64,aGVsbG8="}}],
        "data:image/svg+xml;charset=utf-8,<svg xmlns='http://www.w3.org/2000/svg'></svg>",
    ],
)
def test_extracts_common_data_image_shapes(payload: object) -> None:
    _text, images = extract_text_and_images(payload)
    assert len(images) >= 1
    assert images[0]["kind"] == "data"
    assert images[0]["url"].startswith("data:image/")


def test_whitespace_inside_base64_is_stripped() -> None:
    messy = "data:image/png;base64,aGVs\n bG8="
    text, images = extract_data_images_from_text(f"img {messy} done")
    assert len(images) == 1
    assert images[0]["url"] == "data:image/png;base64,aGVsbG8="
    assert "aGVs\n" not in text


@pytest.mark.parametrize(
    "text",
    [
        "![remote](https://example.invalid/output.png)",
        '<a href="https://example.invalid/output.jpg">output</a>',
        "href=https://example.invalid/output.webp",
    ],
)
def test_output_hrefs_are_not_image_attachments(text: str) -> None:
    extracted_text, images = extract_text_and_images(text)

    assert extracted_text == text
    assert images == []


def test_resolves_existing_session_image_only(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    assets = session_dir / "assets"
    assets.mkdir(parents=True)
    image = assets / "fixture.png"
    image.write_bytes(b"not a real image")
    text_file = assets / "fixture.txt"
    text_file.write_text("text", encoding="utf-8")

    assert resolve_session_image_path("fixture.png", session_dir=session_dir) == image.resolve()
    assert resolve_session_image_path("fixture.txt", session_dir=session_dir) is None
    assert resolve_session_image_path("missing.png", session_dir=session_dir) is None


def test_media_href_includes_agent_and_session() -> None:
    href = media_href(r"C:\tmp\a.png", agent="grok", session=r"C:\sess")
    assert href.startswith("/media?")
    assert "agent=grok" in href
    assert "session=C" in href
    assert "path=C" in href


def test_linkify_requires_agent_session_for_working_media_links() -> None:
    html = linkify_image_paths_html(
        "open C:/tmp/photo.png please",
        [{"kind": "file", "path": "C:/tmp/photo.png"}],
        agent="codex",
        session="C:/codex/rollout.jsonl",
    )
    assert 'class="img-path-link"' in html
    assert "agent=codex" in html
    assert "session=C" in html
    assert "path=C" in html


def test_rebind_turn_media_links_rewrites_html_and_file_hrefs() -> None:
    turn = make_turn(
        role="user",
        text="see C:/tmp/photo.png",
        images=[{"kind": "file", "path": "C:/tmp/photo.png", "label": "C:/tmp/photo.png"}],
    )
    # make_turn linkifies without agent/session initially.
    assert "agent=" not in (turn.get("html") or "")

    rebind_turn_media_links([turn], agent="grok", session="C:/sess")
    assert "agent=grok" in turn["html"]
    assert "session=C" in turn["html"]
    assert turn["images"][0]["href"].startswith("/media?")
    assert "agent=grok" in turn["images"][0]["href"]


def test_load_session_rebinds_media_links_for_grok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_session_viewer import config

    grok_home = tmp_path / "grok"
    session = grok_home / "sessions" / "proj" / "sess1"
    session.mkdir(parents=True)
    image = session / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    (session / "summary.json").write_text(
        '{"info":{"id":"sess1","cwd":"C:/p"},"generated_title":"t"}',
        encoding="utf-8",
    )
    (session / "chat_history.jsonl").write_text(
        '{"type":"user","content":"look at shot.png in assets","timestamp":"2026-07-30T08:00:00Z"}\n',
        encoding="utf-8",
    )
    # Put a path that extract_image_paths_from_text will notice
    (session / "chat_history.jsonl").write_text(
        '{"type":"user","content":"see '
        + str(image).replace("\\", "/")
        + '","timestamp":"2026-07-30T08:00:00Z"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "GROK_HOME", grok_home)

    loaded = load_session("grok", session)
    html = "\n".join(t.get("html") or "" for t in loaded["turns"])
    assert "agent=grok" in html
    assert "session=" in html
