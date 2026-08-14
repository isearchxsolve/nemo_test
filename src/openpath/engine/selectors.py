"""Self-healing selector resolution.

Tries strategies in priority order:
  1. data-testid attribute
  2. ARIA role + accessible name
  3. Visible text
  4. CSS selector
  5. AI vision coordinates (fallback — requires an :class:`AIRunner` instance)

Returns the first strategy that finds an *attached and visible* element.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from playwright.sync_api import Locator, Page

if TYPE_CHECKING:
    from .ai_runner import AIRunner

__all__ = ["SelectorStrategy", "SelectorResult", "resolve_selector"]

SelectorStrategy = Literal["testid", "role", "text", "css", "coords"]


@dataclass
class SelectorResult:
    """Outcome of a selector resolution attempt."""

    strategy: SelectorStrategy
    locator: Locator
    raw: str


def resolve_selector(
    page: Page,
    candidates: list[str],
    *,
    timeout_ms: int = 2_000,
    ai_runner: "AIRunner | None" = None,
    description: str = "",
) -> SelectorResult | None:
    """Try each candidate in *self-healing* order and return the first match.

    Strategies tried in order:
    1. ``[data-testid=…]``
    2. ARIA role query (``role=button[name="…"]``)
    3. Visible-text  (``text=…``)
    4. Raw CSS
    5. AI vision coordinates (only when ``ai_runner`` is supplied)
    """
    buckets: dict[SelectorStrategy, list[str]] = {
        "testid": [],
        "role": [],
        "text": [],
        "css": [],
    }
    for sel in candidates:
        s = sel.strip()
        if "data-testid" in s:
            buckets["testid"].append(s)
        elif s.startswith("role="):
            buckets["role"].append(s)
        elif s.startswith("text="):
            buckets["text"].append(s)
        else:
            buckets["css"].append(s)

    order: list[SelectorStrategy] = ["testid", "role", "text", "css"]
    for strategy in order:
        for sel in buckets[strategy]:
            try:
                loc = _locate(page, strategy, sel, timeout_ms)
                if loc is not None:
                    return SelectorResult(strategy=strategy, locator=loc, raw=sel)
            except Exception:  # noqa: BLE001
                continue

    # Strategy 5: AI vision coordinates fallback.
    if ai_runner is not None:
        coords = _ai_coords(page, ai_runner, description or (candidates[0] if candidates else ""))
        if coords:
            x, y = coords
            # Build a synthetic locator by creating a locator at the coords position.
            # Playwright doesn't expose a direct "click at coords" locator, so we
            # return a CSS locator that wraps the element at that position.
            loc = _locator_at_coords(page, x, y, timeout_ms)
            if loc is not None:
                return SelectorResult(strategy="coords", locator=loc, raw=f"coords({x},{y})")

    return None


def _locate(
    page: Page, strategy: SelectorStrategy, sel: str, timeout_ms: int
) -> Locator | None:
    """Return a visible, attached locator for ``sel`` or ``None``."""
    try:
        if strategy == "testid":
            attr_val = sel
            if 'data-testid="' in sel:
                attr_val = sel.split('data-testid="')[1].split('"')[0]
            elif "data-testid='" in sel:
                attr_val = sel.split("data-testid='")[1].split("'")[0]
            elif "data-testid=" in sel:
                attr_val = sel.split("data-testid=")[1].strip("\"'[]")
            loc = page.get_by_test_id(attr_val)
        elif strategy == "role":
            inner = sel[5:]
            if "[name=" in inner:
                role_name, rest = inner.split("[name=", 1)
                acc_name = rest.rstrip("]").strip("\"'")
                loc = page.get_by_role(role_name.strip(), name=acc_name)  # type: ignore[arg-type]
            else:
                loc = page.get_by_role(inner.strip())  # type: ignore[arg-type]
        elif strategy == "text":
            text = sel[5:].strip().strip('"\'')
            loc = page.get_by_text(text, exact=False)
        else:
            loc = page.locator(sel)

        loc.wait_for(state="attached", timeout=timeout_ms)
        if loc.count() > 0 and loc.first.is_visible():
            return loc.first
        return None
    except Exception:  # noqa: BLE001
        return None


def _ai_coords(
    page: Page, ai_runner: "AIRunner", description: str
) -> tuple[int, int] | None:
    """Ask the LLM to return viewport (x, y) coordinates for ``description``.

    Takes a screenshot first, then asks the LLM where the described element
    is located.  Returns ``None`` on any failure so callers fall back gracefully.
    Retried up to 3 times with exponential backoff.
    """
    import json
    import re

    from ..retry import retry as _retry

    # Capture the screenshot once, outside the retry loop.
    try:
        screenshot_b64 = base64.b64encode(page.screenshot()).decode()
    except Exception:  # noqa: BLE001
        return None

    @_retry(max_attempts=3, base_delay=0.5)
    def _call() -> tuple[int, int]:
        prompt = (
            f'Find the element described as: "{description}". '
            'Return ONLY a JSON object like {"x": 123, "y": 456} '
            "with the pixel coordinates of its centre. No other text."
        )
        body = {
            "model": ai_runner.cfg.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{screenshot_b64}",
                                "detail": "low",
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "max_tokens": 64,
            "temperature": 0,
        }
        r = ai_runner._client.post("/chat/completions", json=body)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        content = re.sub(r"^```[a-z]*\n?", "", content.strip(), flags=re.MULTILINE)
        content = re.sub(r"```$", "", content.strip())
        data = json.loads(content)
        return int(data["x"]), int(data["y"])

    try:
        return _call()
    except Exception:  # noqa: BLE001
        return None


def _locator_at_coords(
    page: Page, x: int, y: int, timeout_ms: int
) -> Locator | None:
    """Return the topmost visible element at (x, y) via JS, as a Playwright locator."""
    try:
        # Use elementFromPoint to get a selector for the element at these coords.
        result = page.evaluate(
            """([x, y]) => {
                const el = document.elementFromPoint(x, y);
                if (!el) return null;
                if (el.dataset && el.dataset.testid) return '[data-testid="' + el.dataset.testid + '"]';
                if (el.id) return '#' + el.id;
                return null;
            }""",
            [x, y],
        )
        if result:
            loc = page.locator(str(result))
            loc.wait_for(state="attached", timeout=timeout_ms)
            if loc.count() > 0 and loc.first.is_visible():
                return loc.first
        # Fallback: click at the raw coordinates using page.mouse.
        # We can't return a Locator here, so we use a synthetic wrapper.
        return _CoordLocator(page, x, y)  # type: ignore[return-value]
    except Exception:  # noqa: BLE001
        return None


class _CoordLocator:
    """Minimal Locator-like wrapper that delegates click/fill to mouse coords."""

    def __init__(self, page: Page, x: int, y: int) -> None:
        self._page = page
        self._x = x
        self._y = y

    def click(self, **kwargs: Any) -> None:
        self._page.mouse.click(self._x, self._y)

    def fill(self, text: str, **kwargs: Any) -> None:
        self._page.mouse.click(self._x, self._y)
        self._page.keyboard.type(text)

    def is_visible(self) -> bool:
        return True
