from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright
from werkzeug.serving import BaseWSGIServer, make_server

from agent_session_viewer.app import app

pytestmark = pytest.mark.browser

HOSTILE_MARKDOWN = """# Safe heading

<img src=x onerror="window.__asvXss=1">
<svg><script>window.__asvXss=2</script></svg>
<math><mtext><img src=x onerror="window.__asvXss=3"></mtext></math>

[safe](https://example.com/docs)
[unsafe](javascript:window.__asvXss=4)
[encoded](java&#x73;cript:window.__asvXss=5)
![remote](https://example.invalid/tracker.png)

| A | B |
|---|---|
| 1 | 2 |

```js
const safe = true;
```
"""


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


def _write_session(path: Path) -> None:
    record = {
        "type": "user",
        "timestamp": "2026-07-30T08:00:00Z",
        "message": {"role": "user", "content": HOSTILE_MARKDOWN},
    }
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")


@contextmanager
def _live_app() -> Iterator[str]:
    server: BaseWSGIServer = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture
def browser_page(browser: Browser, agent_homes: dict[str, Path]) -> Iterator[tuple[Page, str]]:
    session = agent_homes["claude"] / "projects" / "hostile" / "session.jsonl"
    _write_session(session)
    page = browser.new_page()
    with _live_app() as base_url:
        query = urlencode({"agent": "claude", "path": str(session)})
        yield page, f"{base_url}/view?{query}"
    page.close()


def _assert_plain_fallback(page: Page) -> None:
    fallback = page.locator(".md-plain:visible, .md-rich pre.md-preserve:visible").first
    expect(fallback).to_be_visible()
    assert "<img src=x onerror=" in fallback.inner_text()
    assert page.locator(".md-rich img, .md-rich svg, .md-rich script").count() == 0
    assert page.evaluate("window.__asvXss") is None


def test_hostile_markdown_is_sanitized_and_remote_images_are_not_fetched(
    browser_page: tuple[Page, str],
) -> None:
    page, url = browser_page
    requested: list[str] = []
    page.on("request", lambda request: requested.append(request.url))

    page.goto(url)

    rich = page.locator(".md-rich").first
    expect(rich).to_be_visible()
    expect(rich.locator("h1")).to_have_text("Safe heading")
    expect(rich.locator("table")).to_be_visible()
    expect(rich.locator("pre code")).to_contain_text("const safe = true")
    assert rich.locator("img, svg, math, script, form, iframe, object").count() == 0
    assert rich.locator('a[href^="javascript:"]').count() == 0
    expect(rich.locator('a[href="https://example.invalid/tracker.png"]')).to_have_text("remote")
    assert not any("example.invalid" in request for request in requested)
    assert page.evaluate("window.__asvXss") is None

    toggle = page.locator("#md-toggle")
    page.locator('label.md-toggle:has(#md-toggle)').click()
    expect(toggle).not_to_be_checked()
    expect(page.locator(".md-plain").first).to_be_visible()
    page.locator('label.md-toggle:has(#md-toggle)').click()
    expect(toggle).to_be_checked()
    expect(rich).to_be_visible()
    expect(page.locator('[data-copy="markdown"]').first).to_be_attached()
    expect(page.locator('[data-copy="raw"]').first).to_be_attached()


@pytest.mark.parametrize(
    ("asset", "replacement"),
    [
        ("**/vendor/marked/marked.min.js", ""),
        ("**/vendor/dompurify/purify.min.js", ""),
        (
            "**/vendor/marked/marked.min.js",
            "window.marked={parse:function(){throw new Error('parser failed')}}",
        ),
        (
            "**/vendor/dompurify/purify.min.js",
            "window.DOMPurify={sanitize:function(){throw new Error('sanitizer failed')}}",
        ),
    ],
    ids=["missing-marked", "missing-dompurify", "parser-exception", "sanitizer-exception"],
)
def test_dependency_failures_render_plain_text(
    browser_page: tuple[Page, str],
    asset: str,
    replacement: str,
) -> None:
    page, url = browser_page
    page.route(asset, lambda route: route.fulfill(body=replacement, content_type="text/javascript"))

    page.goto(url)

    _assert_plain_fallback(page)
