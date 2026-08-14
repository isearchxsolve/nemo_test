"""AI computer-use loop and natural-language test compiler.

The AI runner drives Playwright through an LLM conversation:
  screenshot → prompt with current step → LLM returns one action JSON →
  execute → fresh screenshot → repeat until done or max_steps reached.

The :class:`NLTestCompiler` converts ``tests/*.md`` files into lists of
:class:`~openpath.detect.plan_generator.GeneratedTest` dicts that the
:class:`~openpath.engine.playwright_runner.PlaywrightRunner` can execute.
Results are cached keyed by file hash so LLM calls are not repeated on
unchanged test files.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

from ..config import LLMConfig
from ..logging import get_logger, redact
from ..retry import retry

__all__ = ["AIRunner", "NLTestCompiler"]

log = get_logger(__name__)

_SYSTEM_PROMPT = """\
You are a web test automation agent. Given a screenshot of the current browser
page and a natural-language test step, you must return exactly ONE JSON object
describing the next browser action. Choose from:

  {"action": "click",  "selector": "<css-or-text>"}
  {"action": "type",   "selector": "<css-or-text>", "text": "<value>"}
  {"action": "scroll", "direction": "down", "amount": 500}
  {"action": "goto",   "url": "<absolute-or-relative-url>"}
  {"action": "assert", "kind": "text_visible",  "value": "<text>"}
  {"action": "assert", "kind": "url_contains",  "value": "<fragment>"}
  {"action": "done"}

Rules:
- Output ONLY the JSON object — no markdown, no commentary.
- Prefer data-testid selectors. Fall back to visible text, then CSS.
- When the step is already satisfied, return {"action": "done"}.
- When in doubt, click the most prominent interactive element that matches the
  step description.
"""

_COMPILER_SYSTEM = """\
You are a test-plan compiler. Given a markdown checklist of natural-language
test steps convert it into a JSON array of test step objects with this schema:

  [
    {"action": "goto",  "url": "<path>"},
    {"action": "click", "selector": "<css-or-text>"},
    {"action": "type",  "selector": "<css-or-text>", "text": "<value>"},
    {"action": "assert","kind": "<assertion-type>",   "value": "<expected>"},
    {"action": "auto_login"}
  ]

Output ONLY the JSON array — no markdown, no commentary.
Assertion types: text_visible | url_contains | no_console_errors | logged_in
"""


class AIRunner:
    """Computer-use loop: drives Playwright with LLM guidance.

    Parameters
    ----------
    cfg:        LLM connection settings.
    max_steps:  Maximum actions per natural-language step before giving up.
    """

    def __init__(self, cfg: LLMConfig, *, max_steps: int | None = None) -> None:
        self.cfg = cfg
        self.max_steps = max_steps or cfg.max_steps
        self._client = httpx.Client(
            base_url=cfg.base_url,
            timeout=cfg.timeout,
            headers=cfg.headers(),
        )

    def execute_step(
        self,
        page: Any,  # playwright Page
        nl_step: str,
        *,
        llm_log: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Execute one natural-language step using the computer-use loop.

        Returns True when the step is judged complete, False when max_steps
        is exceeded without a ``done`` action.
        """
        for turn in range(self.max_steps):
            screenshot_b64 = self._screenshot_b64(page)
            action_json = self._ask_llm(nl_step, screenshot_b64, turn=turn)

            if llm_log is not None:
                llm_log.append({"turn": turn, "step": nl_step, "action": action_json})

            if not action_json:
                log.warning("ai.no_action", turn=turn, step=nl_step)
                break

            if action_json.get("action") == "done":
                log.info("ai.done", turn=turn, step=nl_step)
                return True

            self._apply_action(page, action_json)

        log.warning("ai.max_steps", max_steps=self.max_steps, step=nl_step)
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _screenshot_b64(page: Any) -> str:
        data = page.screenshot()
        return base64.b64encode(data).decode()

    @retry(max_attempts=3, base_delay=1.0)
    def _ask_llm(
        self, nl_step: str, screenshot_b64: str, *, turn: int = 0
    ) -> dict[str, Any]:
        """Send a chat completion request and parse the returned action JSON."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
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
                    {
                        "type": "text",
                        "text": (
                            f"Current step (turn {turn + 1}): {nl_step}\n"
                            "Return the next action JSON."
                        ),
                    },
                ],
            },
        ]
        body = {
            "model": self.cfg.model,
            "messages": messages,
            "max_tokens": 256,
            "temperature": 0,
        }
        r = self._client.post("/chat/completions", json=body)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        log.debug("ai.response", content=redact(content))
        return self._parse_json(content)

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        # Strip markdown fences if present.
        text = re.sub(r"^```[a-z]*\n?", "", text.strip(), flags=re.MULTILINE)
        text = re.sub(r"```$", "", text.strip(), flags=re.MULTILINE)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except ValueError:
                pass
        return {}

    @staticmethod
    def _apply_action(page: Any, action: dict[str, Any]) -> None:
        """Translate an LLM-returned action dict into a Playwright call."""
        kind = action.get("action", "")
        try:
            if kind == "click":
                sel = action.get("selector", "")
                loc = page.get_by_text(sel, exact=False) if not sel.startswith(
                    ("#", ".", "[", "input", "button", "a", "div", "span", "*")
                ) else page.locator(sel)
                loc.first.click(timeout=5_000)
            elif kind == "type":
                sel = action.get("selector", "")
                loc = page.locator(sel)
                loc.first.fill(action.get("text", ""), timeout=5_000)
            elif kind == "scroll":
                amount = action.get("amount", 300)
                page.evaluate(f"window.scrollBy(0, {amount})")
            elif kind == "goto":
                url = action.get("url", "/")
                page.goto(url, wait_until="domcontentloaded", timeout=10_000)
            elif kind == "assert":
                from .assertions import Assertion, evaluate_assertions
                a_kind = action.get("kind", "text_visible")
                a_val = action.get("value", "")
                results = evaluate_assertions(page, [Assertion(type=a_kind, value=a_val)])
                for r in results:
                    if not r.passed:
                        log.warning("ai.assert_failed", assertion=a_kind, value=a_val, msg=r.message)
        except Exception as exc:  # noqa: BLE001
            log.warning("ai.action_error", action=kind, error=str(exc))


class NLTestCompiler:
    """Compile ``*.md`` natural-language test files to step lists.

    Results are cached keyed by the SHA-256 of the file content so the LLM is
    only called when the file changes.
    """

    CACHE_SUFFIX = ".compiled.json"

    def __init__(self, cfg: LLMConfig, *, cache_dir: Path | None = None) -> None:
        self.cfg = cfg
        self.cache_dir = cache_dir or Path(".openpath_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.Client(
            base_url=cfg.base_url,
            timeout=cfg.timeout,
            headers=cfg.headers(),
        )

    def compile_file(self, md_path: Path) -> list[dict[str, Any]]:
        """Return compiled steps for the Markdown test at ``md_path``."""
        text = md_path.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode()).hexdigest()[:16]
        cache_file = self.cache_dir / f"{md_path.stem}_{digest}{self.CACHE_SUFFIX}"

        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text())
                log.info("compiler.cache_hit", file=str(md_path))
                return cached  # type: ignore[return-value]
            except (ValueError, OSError):
                pass

        steps = self._compile_via_llm(text, md_path.stem)
        try:
            cache_file.write_text(json.dumps(steps, indent=2))
        except OSError:
            pass
        return steps

    def compile_dir(self, dir_path: Path) -> list[dict[str, Any]]:
        """Compile all ``*.md`` files in ``dir_path`` into a flat step list."""
        tests: list[dict[str, Any]] = []
        for md in sorted(dir_path.glob("*.md")):
            steps = self.compile_file(md)
            tests.append(
                {
                    "name": f"nl::{md.stem}",
                    "kind": "nl",
                    "url": "/",
                    "steps": steps,
                    "assertions": [],
                }
            )
        return tests

    @retry(max_attempts=3, base_delay=1.0)
    def _compile_via_llm(self, markdown: str, name: str) -> list[dict[str, Any]]:
        log.info("compiler.llm_call", name=name)
        body = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": _COMPILER_SYSTEM},
                {"role": "user", "content": markdown},
            ],
            "max_tokens": 1024,
            "temperature": 0,
        }
        r = self._client.post("/chat/completions", json=body)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        content = re.sub(r"^```[a-z]*\n?", "", content.strip(), flags=re.MULTILINE)
        content = re.sub(r"```$", "", content.strip(), flags=re.MULTILINE)
        try:
            result = json.loads(content)
            if isinstance(result, list):
                return result
        except ValueError:
            pass
        log.warning("compiler.parse_failed", name=name, raw=redact(content[:200]))
        return []
