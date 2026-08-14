"""Headed browser session recorder.

Launches a headed Chromium window, intercepts page events (clicks, input,
navigation) and emits a natural-language ``tests/recorded-<timestamp>.md``
that humans can read and maintain.
"""
from __future__ import annotations

import time
from pathlib import Path

from playwright.sync_api import sync_playwright, Page, ElementHandle

from ..logging import get_logger

__all__ = ["Recorder"]

log = get_logger(__name__)

_PREAMBLE = """\
# Recorded test — {url}
# Recorded at: {ts}
# Edit the steps below and run with: openpath run --mode url --url {url}

"""


class Recorder:
    """Record user interactions and emit a Markdown test file."""

    def __init__(self, url: str, *, output_dir: Path | None = None) -> None:
        self.url = url
        self.output_dir = output_dir or Path("tests")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run(self) -> int:
        """Open the browser, record until the window is closed, write the file."""
        steps: list[str] = []
        ts = int(time.time())
        out_file = self.output_dir / f"recorded-{ts}.md"

        print(f"\n[OpenPath Recorder] Headed browser open at {self.url}")
        print("Interact with the page. Close the browser window to finish recording.")
        print(f"Output will be written to: {out_file}\n")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            ctx = browser.new_context()
            page = ctx.new_page()

            page.on("framenavigated", lambda frame: _on_navigate(frame, steps))
            page.expose_function(
                "_op_record_action",
                lambda action: steps.append(action),
            )
            # Inject a tiny recorder shim that relays click/input events back.
            page.add_init_script(_RECORDER_SHIM)

            page.goto(self.url)

            # Block until the page (window) is closed.
            page.wait_for_event("close", timeout=0)

            browser.close()

        # Write the Markdown file.
        content = _PREAMBLE.format(url=self.url, ts=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)))
        for step in steps:
            content += f"- {step}\n"

        out_file.write_text(content, encoding="utf-8")
        log.info("recorder.done", file=str(out_file), steps=len(steps))
        print(f"\n[OpenPath Recorder] Saved {len(steps)} steps to {out_file}")
        return 0


def _on_navigate(frame: object, steps: list[str]) -> None:
    url = getattr(frame, "url", "")
    if url and not url.startswith("about:"):
        steps.append(f"Navigate to {url}")


# Injected into every page load. Calls the exposed function for each recorded
# event so the Python side can build the step list.
_RECORDER_SHIM = r"""
(function() {
  function record(msg) {
    if (typeof window._op_record_action === 'function') {
      window._op_record_action(msg);
    }
  }

  document.addEventListener('click', function(e) {
    var t = e.target;
    var testid = t.getAttribute('data-testid');
    var ariaLabel = t.getAttribute('aria-label');
    var text = (t.innerText || '').trim().substring(0, 60);
    var tag = t.tagName.toLowerCase();
    // Prefer stable selectors in this order: data-testid > aria-label > text
    if (testid) {
      record('Click on element with data-testid "' + testid + '"');
    } else if (ariaLabel) {
      record('Click on element with aria-label "' + ariaLabel + '"');
    } else if (text) {
      record('Click on "' + text + '"');
    } else {
      record('Click on <' + tag + '> element');
    }
  }, true);

  document.addEventListener('change', function(e) {
    var t = e.target;
    var testid = t.getAttribute('data-testid');
    var name = t.getAttribute('name') || t.getAttribute('placeholder') || t.tagName;
    var val = t.type === 'password' ? '***' : (t.value || '').substring(0, 80);
    if (testid) {
      record('Type "' + val + '" into element with data-testid "' + testid + '"');
    } else {
      record('Type "' + val + '" into "' + name.trim() + '"');
    }
  }, true);
})();
"""
