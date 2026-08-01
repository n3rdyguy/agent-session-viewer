from __future__ import annotations

import json
import re
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
    page.locator("label.md-toggle:has(#md-toggle)").click()
    expect(toggle).not_to_be_checked()
    expect(page.locator(".md-plain").first).to_be_visible()
    page.locator("label.md-toggle:has(#md-toggle)").click()
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


FIXTURES = Path(__file__).parent / "fixtures"


def test_tabs_fold_controls_and_both_copy_modes(
    browser: Browser,
    agent_homes: dict[str, Path],
) -> None:
    """Phase 7 regression row: tabs, fold controls, and both copy modes actually work."""
    # bubbles.html only folds bodies longer than 500 characters, so pad past that.
    session = agent_homes["claude"] / "projects" / "folding" / "session.jsonl"
    session.parent.mkdir(parents=True)
    body = HOSTILE_MARKDOWN + "\n\n" + ("Padding sentence to force the fold control. " * 20)
    session.write_text(
        json.dumps(
            {
                "type": "user",
                "timestamp": "2026-07-30T08:00:00Z",
                "message": {"role": "user", "content": body},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    page = browser.new_page()
    # Record clipboard writes instead of requesting real clipboard permissions, which
    # headless Chromium grants inconsistently across platforms.
    page.add_init_script(
        "window.__copied = [];"
        "navigator.clipboard.writeText = function (text) {"
        "  window.__copied.push(text); return Promise.resolve();"
        "};"
    )
    with _live_app() as base_url:
        query = urlencode({"agent": "claude", "path": str(session)})
        page.goto(f"{base_url}/view?{query}")
        _exercise_tabs_folds_and_copy(page)
    page.close()


def _exercise_tabs_folds_and_copy(page: Page) -> None:

    # Tabs: chat is active on load; switching swaps which panel is shown.
    chat_panel = page.locator("#tab-chat")
    expect(chat_panel).to_have_class(re.compile(r"\bactive\b"))
    updates_tab = page.locator('#view-tabs [data-tab="updates"]')
    if updates_tab.count():
        updates_tab.click()
        expect(page.locator("#tab-updates")).to_have_class(re.compile(r"\bactive\b"))
        expect(chat_panel).not_to_have_class(re.compile(r"\bactive\b"))
        page.locator('#view-tabs [data-tab="chat"]').click()
        expect(chat_panel).to_have_class(re.compile(r"\bactive\b"))

    # Fold controls: a long bubble gets the bubble-level collapse button.
    toggle = page.locator(".fold-header-btn").first
    expect(toggle).to_be_attached()
    expect(toggle).to_have_attribute("aria-expanded", "false")
    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "false")

    # Both copy modes put text on the clipboard. The menu items are hidden until the
    # block's "Copy" button opens the menu.
    for mode in ("markdown", "raw"):
        page.evaluate("window.__copied = []")
        page.locator(".copy-btn").first.click()
        page.locator(f'[data-copy="{mode}"]').first.click()
        page.wait_for_function("window.__copied.length > 0")
        copied = page.evaluate("window.__copied[0]")
        assert copied, f"{mode} copy produced nothing"
        assert "Safe heading" in copied, f"{mode} copy lost the document body"


def test_token_bar_widths_apply_without_inline_styles(
    browser: Browser,
    agent_homes: dict[str, Path],
) -> None:
    """Widths moved from style="" to data-pct when style-src dropped 'unsafe-inline'."""
    session = agent_homes["claude"] / "projects" / "rich" / "session-fixture.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text(
        (FIXTURES / "claude" / "session-fixture.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    page = browser.new_page()
    violations: list[str] = []
    page.on("console", lambda message: violations.append(message.text))
    try:
        with _live_app() as base_url:
            query = urlencode({"agent": "claude", "path": str(session)})
            page.goto(f"{base_url}/view?{query}")

            segment = page.locator(".token-bar .seg-cached")
            expect(segment).to_be_attached()
            # data-pct is 72.26 for this fixture; JS must turn that into a real width.
            width = page.evaluate(
                "document.querySelector('.token-bar .seg-cached').getBoundingClientRect().width"
            )
            # CSSOM writes reflect back into a style attribute, which is fine:
            # style-src governs markup, not element.style. The served HTML is
            # checked for inline styles in test_routes.py instead.
            assert width > 0, "token bar segment has no width - data-pct was not applied"
    finally:
        page.close()

    assert not [text for text in violations if "Content Security Policy" in text], violations
