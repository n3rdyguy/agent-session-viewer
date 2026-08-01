"""Browser coverage for the settings page.

Preferences live in localStorage and are applied by client-side JavaScript, so
these need a real browser. Marked `browser` and run by the separate CI job.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlencode

import pytest
from playwright.sync_api import Browser, Page, expect

pytestmark = pytest.mark.browser

DARK_BG = "rgb(12, 14, 18)"
LIGHT_BG = "rgb(245, 247, 250)"


def _write_session(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "user",
                "timestamp": "2026-07-30T08:00:00Z",
                "message": {"role": "user", "content": "hello"},
            }
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def site(
    browser: Browser, agent_homes: dict[str, Path], live_app
) -> Iterator[tuple[Page, str, str]]:
    session = agent_homes["claude"] / "projects" / "proj" / "session.jsonl"
    _write_session(session)
    page = browser.new_page()
    with live_app() as base_url:
        query = urlencode({"agent": "claude", "path": str(session)})
        yield page, base_url, f"{base_url}/view?{query}"
    page.close()


def _body_bg(page: Page) -> str:
    return page.eval_on_selector("body", "el => getComputedStyle(el).backgroundColor")


def test_theme_defaults_to_dark_and_switches_to_light(site: tuple[Page, str, str]) -> None:
    page, base_url, _ = site
    page.goto(f"{base_url}/settings")

    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    assert _body_bg(page) == DARK_BG

    page.select_option("#pref-theme", "light")

    expect(page.locator("html")).to_have_attribute("data-theme", "light")
    page.wait_for_function(
        f"() => getComputedStyle(document.body).backgroundColor === '{LIGHT_BG}'"
    )


def test_theme_persists_across_pages_without_a_flash(site: tuple[Page, str, str]) -> None:
    page, base_url, view_url = site
    page.goto(f"{base_url}/settings")
    page.select_option("#pref-theme", "light")

    for url in (f"{base_url}/", view_url):
        page.goto(url)
        # theme-boot.js is blocking in <head>, so the attribute is present as soon
        # as the document exists - never a dark frame before the light theme lands.
        expect(page.locator("html")).to_have_attribute("data-theme", "light")
        assert _body_bg(page) == LIGHT_BG


def test_auto_theme_follows_the_operating_system(browser: Browser, agent_homes, live_app) -> None:
    with live_app() as base_url:
        page = browser.new_page(color_scheme="light")
        page.goto(f"{base_url}/settings")
        page.select_option("#pref-theme", "auto")
        expect(page.locator("html")).to_have_attribute("data-theme", "light")

        page.emulate_media(color_scheme="dark")
        expect(page.locator("html")).to_have_attribute("data-theme", "dark")
        page.close()


def test_view_toggles_round_trip_through_settings(site: tuple[Page, str, str]) -> None:
    page, base_url, view_url = site
    page.goto(f"{base_url}/settings")
    page.locator("label.md-toggle:has(#pref-markdown)").click()
    expect(page.locator("#pref-markdown")).to_be_checked()

    page.goto(view_url)

    expect(page.locator("#md-toggle")).to_be_checked()
    expect(page.locator("body")).to_have_class(re.compile(r"\bmd-on\b"))


def test_default_agent_filter_redirects_only_the_bare_index(site: tuple[Page, str, str]) -> None:
    page, base_url, _ = site
    page.goto(f"{base_url}/settings")
    page.select_option("#pref-agent", "codex")

    page.goto(f"{base_url}/")
    page.wait_for_url("**/?agent=codex")

    # An explicit filter or search must win over the preference.
    page.goto(f"{base_url}/?agent=grok")
    page.wait_for_timeout(300)
    assert page.url.endswith("/?agent=grok")


def test_absolute_timestamps_swap_the_row_time(site: tuple[Page, str, str]) -> None:
    page, base_url, _ = site
    page.goto(f"{base_url}/")
    relative = page.eval_on_selector(".session-row time", "el => el.textContent")

    page.goto(f"{base_url}/settings")
    page.select_option("#pref-time-format", "absolute")
    page.goto(f"{base_url}/")

    absolute = page.eval_on_selector(".session-row time", "el => el.textContent")
    assert absolute != relative
    assert absolute.startswith("20")
    # The relative value moves into the tooltip rather than being discarded.
    assert page.eval_on_selector(".session-row time", "el => el.title") == relative


def test_reset_clears_every_stored_preference(site: tuple[Page, str, str]) -> None:
    page, base_url, _ = site
    page.goto(f"{base_url}/settings")
    page.select_option("#pref-theme", "light")
    page.select_option("#pref-agent", "codex")
    page.locator("label.md-toggle:has(#pref-markdown)").click()
    assert page.evaluate("() => Object.keys(localStorage).filter(k => k.startsWith('asv-')).length")

    page.click("#prefs-reset")

    assert (
        page.evaluate("() => Object.keys(localStorage).filter(k => k.startsWith('asv-')).length")
        == 0
    )
    expect(page.locator("#pref-theme")).to_have_value("dark")
    expect(page.locator("#pref-markdown")).not_to_be_checked()
    page.wait_for_function(f"() => getComputedStyle(document.body).backgroundColor === '{DARK_BG}'")


def test_settings_page_raises_no_console_or_csp_errors(site: tuple[Page, str, str]) -> None:
    page, base_url, _ = site
    problems: list[str] = []
    page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda exc: problems.append(str(exc)))

    page.goto(f"{base_url}/settings")
    page.select_option("#pref-theme", "light")
    page.click("#prefs-clear-pins")
    page.wait_for_timeout(300)

    assert problems == []
