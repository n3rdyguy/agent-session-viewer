from pathlib import Path

import pytest

from agent_session_viewer.images import (
    extract_text_and_images,
    is_image_path,
    resolve_session_image_path,
)


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
