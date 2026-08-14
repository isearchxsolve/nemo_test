"""Assertion primitives used by both the deterministic and AI runners.

Every assertion type maps to a pure function that inspects Playwright state
and returns an :class:`AssertionResult`. The functions are intentionally free
of side-effects so they are easy to unit-test.

Supported assertion types
-------------------------
text_visible, text_hidden, url_contains, no_console_errors, no_4xx_5xx,
status_ok, logged_in, element_state, visual_diff
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.sync_api import Page

__all__ = ["Assertion", "AssertionResult", "evaluate_assertions"]


@dataclass
class Assertion:
    """Declarative description of an assertion."""

    type: str  # see _HANDLERS keys
    value: str | None = None
    threshold: float = 0.1  # for visual diff assertions
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AssertionResult:
    """Outcome of evaluating one assertion."""

    assertion: Assertion
    passed: bool
    message: str = ""


def evaluate_assertions(
    page: Page,
    assertions: list[Assertion],
    *,
    console_errors: list[str] | None = None,
    network_errors: list[str] | None = None,
) -> list[AssertionResult]:
    """Evaluate each assertion against the current page state."""
    results: list[AssertionResult] = []
    for a in assertions:
        handler = _HANDLERS.get(a.type)
        if handler is None:
            results.append(
                AssertionResult(
                    assertion=a,
                    passed=False,
                    message=f"Unknown assertion type: {a.type!r}",
                )
            )
            continue
        try:
            passed, msg = handler(page, a, console_errors or [], network_errors or [])
            results.append(AssertionResult(assertion=a, passed=passed, message=msg))
        except Exception as exc:  # noqa: BLE001
            results.append(
                AssertionResult(
                    assertion=a, passed=False, message=f"Exception: {exc}"
                )
            )
    return results


# ---------------------------------------------------------------------------
# Handler implementations
# ---------------------------------------------------------------------------

def _assert_text_visible(
    page: Page,
    a: Assertion,
    _ce: list[str],
    _ne: list[str],
) -> tuple[bool, str]:
    text = a.value or ""
    loc = page.get_by_text(text, exact=False)
    visible = loc.count() > 0 and loc.first.is_visible()
    return visible, ("" if visible else f"Text not visible: {text!r}")


def _assert_text_hidden(
    page: Page,
    a: Assertion,
    _ce: list[str],
    _ne: list[str],
) -> tuple[bool, str]:
    text = a.value or ""
    loc = page.get_by_text(text, exact=False)
    hidden = loc.count() == 0 or not loc.first.is_visible()
    return hidden, ("" if hidden else f"Text unexpectedly visible: {text!r}")


def _assert_url_contains(
    page: Page,
    a: Assertion,
    _ce: list[str],
    _ne: list[str],
) -> tuple[bool, str]:
    value = a.value or ""
    url = page.url
    passed = value in url
    return passed, ("" if passed else f"URL {url!r} does not contain {value!r}")


def _assert_no_console_errors(
    _page: Page,
    _a: Assertion,
    console_errors: list[str],
    _ne: list[str],
) -> tuple[bool, str]:
    passed = len(console_errors) == 0
    return passed, ("" if passed else f"Console errors: {console_errors[:5]}")


def _assert_no_4xx_5xx(
    _page: Page,
    _a: Assertion,
    _ce: list[str],
    network_errors: list[str],
) -> tuple[bool, str]:
    # Filter out benign 404s for common static assets (favicon, etc.) that
    # many apps don't serve and that don't affect test outcomes.
    _IGNORED_SUFFIXES = (
        "/favicon.ico", "/favicon.png", "/apple-touch-icon.png",
        "/robots.txt", "/sitemap.xml",
    )
    real_errors = [
        e for e in network_errors
        if not any(e.endswith(suffix) or suffix in e for suffix in _IGNORED_SUFFIXES)
    ]
    passed = len(real_errors) == 0
    return passed, ("" if passed else f"Network errors: {real_errors[:5]}")


def _assert_status_ok(
    page: Page,
    _a: Assertion,
    _ce: list[str],
    _ne: list[str],
) -> tuple[bool, str]:
    # Verify the page didn't land on a well-known error page. We check only
    # specific numeric codes in the title/URL to avoid false positives on
    # legitimate words like "home", "settings", etc.
    url = page.url
    title = (page.title() or "").lower()
    # Numeric HTTP error codes — safe to check as exact tokens.
    numeric_errors = {"404", "403", "500", "502", "503"}
    text_errors = {"not found", "forbidden", "internal server error", "bad gateway"}
    for kw in numeric_errors:
        if kw in title.split() or kw in url.lower().split("/"):
            return False, f"Page appears to be an error page: title={title!r} url={url!r}"
    for kw in text_errors:
        if kw in title:
            return False, f"Page appears to be an error page: title={title!r} url={url!r}"
    return True, ""


def _assert_logged_in(
    page: Page,
    _a: Assertion,
    _ce: list[str],
    _ne: list[str],
) -> tuple[bool, str]:
    url = page.url.lower()
    # If we got redirected back to the login page we're not logged in.
    if re.search(r"/login|/signin|/auth|/sign-in", url):
        return False, f"Still on login page after auto-login: {url}"
    return True, ""


def _assert_element_state(
    page: Page,
    a: Assertion,
    _ce: list[str],
    _ne: list[str],
) -> tuple[bool, str]:
    selector = a.value or ""
    state = a.extra.get("state", "visible")
    loc = page.locator(selector)
    if state == "visible":
        ok = loc.count() > 0 and loc.first.is_visible()
    elif state == "hidden":
        ok = loc.count() == 0 or not loc.first.is_visible()
    elif state == "enabled":
        ok = loc.count() > 0 and loc.first.is_enabled()
    elif state == "disabled":
        ok = loc.count() > 0 and not loc.first.is_enabled()
    else:
        return False, f"Unknown element state: {state!r}"
    return ok, ("" if ok else f"Element {selector!r} not in state {state!r}")


def _assert_visual_diff(
    page: Page,
    a: Assertion,
    _ce: list[str],
    _ne: list[str],
) -> tuple[bool, str]:
    """Compare the current screenshot against a baseline using pixelmatch.

    ``a.value`` should be the path to the baseline PNG. If the baseline does
    not exist yet, the current screenshot is saved as the new baseline and the
    assertion passes (first-run calibration). ``a.threshold`` controls the
    per-pixel colour tolerance (0–1, default 0.1).
    """
    try:
        from pixelmatch.contrib.PIL import pixelmatch  # type: ignore[import]
        from PIL import Image
    except ImportError:
        return False, "visual_diff requires: pip install pixelmatch pillow"

    baseline_path = Path(a.value or "baseline.png")
    screenshot_bytes = page.screenshot()
    current = Image.open(io.BytesIO(screenshot_bytes)).convert("RGBA")

    if not baseline_path.exists():
        # First run — save as baseline and pass.
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        current.save(str(baseline_path))
        return True, f"Baseline saved to {baseline_path}"

    baseline = Image.open(str(baseline_path)).convert("RGBA")
    if baseline.size != current.size:
        return (
            False,
            f"Image size mismatch: baseline {baseline.size} vs current {current.size}",
        )

    diff = Image.new("RGBA", current.size)
    mismatch = pixelmatch(
        baseline, current, diff,
        threshold=a.threshold,
        includeAA=True,
    )
    total_pixels = current.size[0] * current.size[1]
    ratio = mismatch / total_pixels if total_pixels else 0.0
    passed = ratio <= a.threshold
    msg = "" if passed else f"Visual diff {ratio:.3%} exceeds threshold {a.threshold:.3%} ({mismatch} px)"
    return passed, msg


_HANDLERS = {
    "text_visible": _assert_text_visible,
    "text_hidden": _assert_text_hidden,
    "url_contains": _assert_url_contains,
    "no_console_errors": _assert_no_console_errors,
    "no_4xx_5xx": _assert_no_4xx_5xx,
    "status_ok": _assert_status_ok,
    "logged_in": _assert_logged_in,
    "element_state": _assert_element_state,
    "visual_diff": _assert_visual_diff,
}
