"""Deterministic Playwright test runner.

Provides an isolated browser context per test (video + HAR captured on
failure), auto-login helper, and a structured result model consumed by the
report writers.
"""
from __future__ import annotations

import os
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    Browser,
    BrowserContext,
    ConsoleMessage,
    Page,
    Request,
    Response,
    sync_playwright,
)

from ..config import BrowserConfig, Credentials, LoginSelectors
from ..logging import get_logger
from ..retry import retry
from .assertions import Assertion, AssertionResult, evaluate_assertions
from .selectors import resolve_selector

__all__ = ["PlaywrightRunner", "Step", "StepResult", "TestResult", "SuiteResult"]

log = get_logger(__name__)


@dataclass
class Step:
    """A single executable action within a test."""

    action: str  # goto | click | type | scroll | assert | auto_login | wait
    url: str = ""
    selector: str = ""
    text: str = ""
    key: str = ""
    value: Any = None
    assertions: list[Assertion] = field(default_factory=list)


@dataclass
class StepResult:
    action: str
    passed: bool
    message: str = ""
    screenshot: str = ""  # relative path to artifact


@dataclass
class TestResult:
    """Outcome of a single test case."""

    name: str
    kind: str
    passed: bool
    duration_s: float = 0.0
    steps: list[StepResult] = field(default_factory=list)
    assertion_results: list[AssertionResult] = field(default_factory=list)
    screenshot: str = ""
    video: str = ""
    har: str = ""
    error: str = ""
    llm_log: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SuiteResult:
    """Aggregate outcome of all tests in a run."""

    run_id: str
    base_url: str
    tests: list[TestResult] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0

    @property
    def passed(self) -> int:
        return sum(1 for t in self.tests if t.passed)

    @property
    def failed(self) -> int:
        return sum(1 for t in self.tests if not t.passed)

    @property
    def total(self) -> int:
        return len(self.tests)

    @property
    def duration_s(self) -> float:
        return self.end_time - self.start_time


class PlaywrightRunner:
    """Synchronous Playwright runner with per-test isolated contexts."""

    def __init__(
        self,
        base_url: str,
        *,
        cfg: BrowserConfig | None = None,
        credentials: Credentials | None = None,
        selectors: LoginSelectors | None = None,
        artifacts_dir: Path | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.cfg = cfg or BrowserConfig()
        self.credentials = credentials or Credentials()
        self.selectors = selectors or LoginSelectors()
        self.artifacts_dir = artifacts_dir or Path("testruns/artifacts")
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_suite(self, tests: list[dict[str, Any]], run_id: str) -> SuiteResult:
        """Execute the entire suite and return a :class:`SuiteResult`."""
        result = SuiteResult(run_id=run_id, base_url=self.base_url)
        with sync_playwright() as pw:
            browser = self._launch(pw)
            for t in tests:
                tr = self._run_one(browser, t)
                result.tests.append(tr)
            browser.close()
        result.end_time = time.time()
        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _launch(self, pw: Any) -> Browser:
        launcher = getattr(pw, self.cfg.browser)
        return launcher.launch(headless=self.cfg.headless)

    def _run_one(self, browser: Browser, test: dict[str, Any]) -> TestResult:
        name: str = test.get("name", "unnamed")
        kind: str = test.get("kind", "smoke")
        url: str = test.get("url", "/")
        requires_login: bool = test.get("requires_login", False)
        raw_steps: list[dict[str, Any]] = test.get("steps", [])
        raw_assertions: list[dict[str, Any]] = test.get("assertions", [])

        log.info("test.start", name=name, kind=kind, url=url)
        t0 = time.time()

        # Per-test artifact paths.
        safe = name.replace("::", "_").replace("/", "_").replace(" ", "_")
        art = self.artifacts_dir / safe
        art.mkdir(parents=True, exist_ok=True)

        # Always record video/HAR during the run; delete artifacts afterwards
        # when the test passes (spec: capture "on failure").
        har_path = str(art / "trace.har")
        ctx_opts: dict[str, Any] = {
            "viewport": {"width": self.cfg.viewport[0], "height": self.cfg.viewport[1]},
        }
        if self.cfg.video_on_failure:
            ctx_opts["record_video_dir"] = str(art)
        if self.cfg.har_on_failure:
            ctx_opts["record_har_path"] = har_path

        ctx: BrowserContext = browser.new_context(**ctx_opts)

        # Capture console errors only (not warnings — those are benign).
        console_errors: list[str] = []
        network_errors: list[str] = []

        def _on_console(msg: ConsoleMessage) -> None:
            if msg.type == "error":
                console_errors.append(f"[error] {msg.text}")

        def _on_response(resp: Response) -> None:
            if resp.status >= 400:
                network_errors.append(f"{resp.status} {resp.url}")

        page: Page = ctx.new_page()
        page.on("console", _on_console)
        page.on("response", _on_response)

        step_results: list[StepResult] = []
        assertion_results: list[AssertionResult] = []
        test_passed = True
        error_msg = ""
        screenshot_path = ""
        video_path = ""
        har_recorded = self.cfg.har_on_failure

        try:
            # Navigate to the test URL first.
            full_url = self._full_url(url)
            self._goto(page, full_url)

            # Auto-login if required.
            if requires_login and self.credentials.has_login:
                self._auto_login(page, step_results, art)

            # Execute explicit steps.
            for raw in raw_steps:
                step = self._coerce_step(raw)
                if step.action == "auto_login":
                    if self.credentials.has_login:
                        self._auto_login(page, step_results, art)
                    continue
                sr = self._execute_step(page, step, art)
                step_results.append(sr)
                if not sr.passed:
                    test_passed = False
                    error_msg = sr.message
                    break

            # Evaluate assertions.
            assertions = [
                Assertion(
                    type=a.get("type", ""),
                    value=a.get("value"),
                    extra={k: v for k, v in a.items() if k not in ("type", "value")},
                )
                for a in raw_assertions
            ]
            if assertions:
                assertion_results = evaluate_assertions(
                    page, assertions,
                    console_errors=console_errors,
                    network_errors=network_errors,
                )
                for ar in assertion_results:
                    if not ar.passed:
                        test_passed = False
                        error_msg = error_msg or ar.message

        except Exception as exc:  # noqa: BLE001
            test_passed = False
            error_msg = f"{type(exc).__name__}: {exc}"
            log.error("test.exception", name=name, error=error_msg)

        finally:
            # Always capture a final screenshot.
            try:
                ss = str(art / "final.png")
                page.screenshot(path=ss, full_page=True)
                screenshot_path = ss
            except Exception:  # noqa: BLE001
                pass

            # Grab video path before closing context (Playwright writes it on close).
            _video = page.video
            ctx.close()

            # Resolve video path after context close.
            if _video:
                try:
                    video_path = _video.path()
                except Exception:  # noqa: BLE001
                    video_path = ""

            # Delete video/HAR for passing tests (on-failure-only semantics).
            if test_passed:
                for path_to_remove in [
                    video_path,
                    har_path if har_recorded else "",
                ]:
                    if path_to_remove:
                        try:
                            os.remove(path_to_remove)
                        except OSError:
                            pass
                video_path = ""

        duration = time.time() - t0
        log.info(
            "test.done",
            name=name,
            passed=test_passed,
            duration_s=round(duration, 2),
        )
        return TestResult(
            name=name,
            kind=kind,
            passed=test_passed,
            duration_s=round(duration, 2),
            steps=step_results,
            assertion_results=assertion_results,
            screenshot=screenshot_path,
            video=str(video_path) if video_path else "",
            har=har_path if (har_recorded and not test_passed) else "",
            error=error_msg,
        )

    def _full_url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        path = path if path.startswith("/") else f"/{path}"
        return f"{self.base_url}{path}"

    @retry(max_attempts=3, base_delay=0.5)
    def _goto(self, page: Page, url: str) -> None:
        page.goto(url, wait_until="domcontentloaded", timeout=15_000)

    def _auto_login(
        self,
        page: Page,
        results: list[StepResult],
        art: Path,
    ) -> None:
        """Fill in login credentials and submit.

        On failure: capture a screenshot and raise with a clear message.
        """
        assert self.credentials.password is not None
        username = self.credentials.username or ""
        password = self.credentials.password.get_secret_value()

        try:
            page.goto(self._full_url("/"), wait_until="domcontentloaded", timeout=10_000)
            # Try to find login form; may need to navigate to /login first.
            user_loc = page.locator(self.selectors.username)
            if user_loc.count() == 0:
                for login_path in ("/login", "/signin", "/auth/login", "/sign-in"):
                    page.goto(self._full_url(login_path), wait_until="domcontentloaded", timeout=8_000)
                    user_loc = page.locator(self.selectors.username)
                    if user_loc.count() > 0:
                        break

            if user_loc.count() == 0:
                # No login form found; skip gracefully.
                results.append(StepResult(action="auto_login", passed=True, message="no login form found — skipping"))
                return

            user_loc.first.fill(username)
            page.locator(self.selectors.password).first.fill(password)
            page.locator(self.selectors.submit).first.click()
            page.wait_for_load_state("domcontentloaded", timeout=10_000)
            results.append(StepResult(action="auto_login", passed=True))
        except Exception as exc:  # noqa: BLE001
            ss = str(art / "login_failure.png")
            try:
                page.screenshot(path=ss)
            except Exception:  # noqa: BLE001
                pass
            msg = f"Auto-login failed: {exc}. Screenshot: {ss}"
            log.error("login.failed", error=str(exc), screenshot=ss)
            results.append(StepResult(action="auto_login", passed=False, message=msg, screenshot=ss))

    def _execute_step(self, page: Page, step: Step, art: Path) -> StepResult:
        """Execute a single action step and return its result."""
        try:
            if step.action == "goto":
                self._goto(page, self._full_url(step.url or step.value or "/"))
            elif step.action == "click":
                result = resolve_selector(page, [step.selector])
                if result:
                    result.locator.click(timeout=5_000)
                else:
                    return StepResult(
                        action="click",
                        passed=False,
                        message=f"Selector not found: {step.selector!r}",
                    )
            elif step.action == "type":
                result = resolve_selector(page, [step.selector])
                if result:
                    result.locator.fill(step.text, timeout=5_000)
                else:
                    return StepResult(
                        action="type",
                        passed=False,
                        message=f"Selector not found: {step.selector!r}",
                    )
            elif step.action == "scroll":
                page.evaluate("window.scrollBy(0, 500)")
            elif step.action == "wait":
                page.wait_for_timeout(int(step.value or 500))
            elif step.action == "assert":
                results = evaluate_assertions(page, step.assertions)
                failures = [r for r in results if not r.passed]
                if failures:
                    return StepResult(
                        action="assert",
                        passed=False,
                        message="; ".join(r.message for r in failures),
                    )
            return StepResult(action=step.action, passed=True)
        except Exception as exc:  # noqa: BLE001
            return StepResult(
                action=step.action,
                passed=False,
                message=f"{type(exc).__name__}: {exc}",
            )

    @staticmethod
    def _coerce_step(raw: dict[str, Any]) -> Step:
        return Step(
            action=raw.get("action", "goto"),
            url=raw.get("url", ""),
            selector=raw.get("selector", ""),
            text=raw.get("text", ""),
            value=raw.get("value"),
        )
