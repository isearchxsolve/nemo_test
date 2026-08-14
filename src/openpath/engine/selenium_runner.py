"""Selenium WebDriver test runner.

Parallel to :mod:`openpath.engine.playwright_runner` but driven by Selenium.
Provides the same ``run_suite`` / ``SuiteResult`` interface so the pipeline
can switch engines with a single flag.
"""
from __future__ import annotations

import os
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ..config import BrowserConfig, Credentials, LoginSelectors
from ..logging import get_logger
from ..retry import retry
from .assertions import Assertion, AssertionResult, evaluate_assertions

__all__ = ["SeleniumRunner", "SuiteResult"]

log = get_logger(__name__)


@dataclass
class StepResult:
    action: str
    passed: bool
    message: str = ""
    screenshot: str = ""


# Re-use TestResult / SuiteResult from the Playwright runner for cross-engine
# report compatibility.
from ..engine.playwright_runner import TestResult, SuiteResult  # noqa: E402


class SeleniumRunner:
    """Selenium WebDriver runner with per-test isolated sessions."""

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
        self.artifacts_dir = artifacts_dir or Path("testruns/artifacts-sel")
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_suite(self, tests: list[dict[str, Any]], run_id: str) -> SuiteResult:
        """Execute the entire suite and return a :class:`SuiteResult`."""
        result = SuiteResult(run_id=run_id, base_url=self.base_url)
        driver = self._launch()
        try:
            for t in tests:
                tr = self._run_one(driver, t)
                result.tests.append(tr)
        finally:
            driver.quit()
        result.end_time = time.time()
        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _launch(self) -> WebDriver:
        """Start a headless Chrome / Chromium session."""
        options = webdriver.ChromeOptions()
        if self.cfg.headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        return webdriver.Chrome(options=options)

    def _run_one(self, driver: WebDriver, test: dict[str, Any]) -> TestResult:
        name: str = test.get("name", "unnamed")
        kind: str = test.get("kind", "smoke")
        url: str = test.get("url", "/")
        requires_login: bool = test.get("requires_login", False)
        raw_steps: list[dict[str, Any]] = test.get("steps", [])
        raw_assertions: list[dict[str, Any]] = test.get("assertions", [])

        log.info("test.start", name=name, kind=kind, url=url)
        t0 = time.time()

        safe = name.replace("::", "_").replace("/", "_").replace(" ", "_")
        art = self.artifacts_dir / safe
        art.mkdir(parents=True, exist_ok=True)

        step_results: list[StepResult] = []
        assertion_results: list[AssertionResult] = []
        test_passed = True
        error_msg = ""
        screenshot_path = ""

        try:
            full_url = self._full_url(url)
            self._goto(driver, full_url)

            if requires_login and self.credentials.has_login:
                self._auto_login(driver, step_results, art)

            for raw in raw_steps:
                step = self._coerce_step(raw)
                if step.action == "auto_login":
                    if self.credentials.has_login:
                        self._auto_login(driver, step_results, art)
                    continue
                sr = self._execute_step(driver, step, art)
                step_results.append(sr)
                if not sr.passed:
                    test_passed = False
                    error_msg = sr.message
                    break

            assertions = [
                Assertion(
                    type=a.get("type", ""),
                    value=a.get("value"),
                    extra={k: v for k, v in a.items() if k not in ("type", "value")},
                )
                for a in raw_assertions
            ]
            if assertions:
                assertion_results = evaluate_assertions(driver, assertions)
                for ar in assertion_results:
                    if not ar.passed:
                        test_passed = False
                        error_msg = error_msg or ar.message

        except Exception as exc:  # noqa: BLE001
            test_passed = False
            error_msg = f"{type(exc).__name__}: {exc}"
            log.error("test.exception", name=name, error=error_msg)

        finally:
            try:
                ss = str(art / "final.png")
                driver.save_screenshot(ss)
                screenshot_path = ss
            except Exception:  # noqa: BLE001
                pass

        duration = time.time() - t0
        log.info("test.done", name=name, passed=test_passed, duration_s=round(duration, 2))
        return TestResult(
            name=name,
            kind=kind,
            passed=test_passed,
            duration_s=round(duration, 2),
            steps=[
                StepResult(
                    action=s.action, passed=s.passed, message=s.message,
                    screenshot=s.screenshot,
                )
                for s in step_results
            ],
            assertion_results=assertion_results,
            screenshot=screenshot_path,
            error=error_msg,
        )

    def _full_url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        path = path if path.startswith("/") else f"/{path}"
        return f"{self.base_url}{path}"

    @retry(max_attempts=3, base_delay=0.5)
    def _goto(self, driver: WebDriver, url: str) -> None:
        driver.get(url)
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

    def _auto_login(
        self, driver: WebDriver, results: list[StepResult], art: Path
    ) -> None:
        assert self.credentials.password is not None
        username = self.credentials.username or ""
        password = self.credentials.password.get_secret_value()

        try:
            driver.get(self._full_url("/"))
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )

            try:
                user_loc = driver.find_element(By.CSS_SELECTOR, self.selectors.username)
            except Exception:
                for login_path in ("/login", "/signin", "/auth/login", "/sign-in"):
                    driver.get(self._full_url(login_path))
                    try:
                        user_loc = driver.find_element(By.CSS_SELECTOR, self.selectors.username)
                        break
                    except Exception:
                        user_loc = None
                else:
                    results.append(
                        StepResult(action="auto_login", passed=True,
                                   message="no login form found — skipping")
                    )
                    return

            if user_loc is None:
                results.append(
                    StepResult(action="auto_login", passed=True,
                               message="no login form found — skipping")
                )
                return

            user_loc.clear()
            user_loc.send_keys(username)

            pwd = driver.find_element(By.CSS_SELECTOR, self.selectors.password)
            pwd.clear()
            pwd.send_keys(password)

            driver.find_element(By.CSS_SELECTOR, self.selectors.submit).click()
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            results.append(StepResult(action="auto_login", passed=True))
        except Exception as exc:  # noqa: BLE001
            ss = str(art / "login_failure.png")
            try:
                driver.save_screenshot(ss)
            except Exception:  # noqa: BLE001
                pass
            msg = f"Auto-login failed: {exc}"
            log.error("login.failed", error=str(exc), screenshot=ss)
            results.append(
                StepResult(action="auto_login", passed=False, message=msg, screenshot=ss)
            )

    def _execute_step(self, driver: WebDriver, step: Any, art: Path) -> StepResult:
        try:
            if step.action == "goto":
                self._goto(driver, self._full_url(step.url or step.value or "/"))
            elif step.action == "click":
                self._click(driver, step.selector)
            elif step.action == "type":
                self._type(driver, step.selector, step.text)
            elif step.action == "scroll":
                driver.execute_script("window.scrollBy(0, 500)")
            elif step.action == "wait":
                time.sleep(float(step.value or 0.5))
            elif step.action == "assert":
                assertions = [
                    Assertion(
                        type=a.get("type", ""),
                        value=a.get("value"),
                        extra={k: v for k, v in a.items() if k not in ("type", "value")},
                    )
                    for a in (step.assertions or [])
                ]
                results = evaluate_assertions(driver, assertions)
                failures = [r for r in results if not r.passed]
                if failures:
                    return StepResult(
                        action="assert", passed=False,
                        message="; ".join(r.message for r in failures),
                    )
            return StepResult(action=step.action, passed=True)
        except Exception as exc:  # noqa: BLE001
            return StepResult(
                action=step.action, passed=False,
                message=f"{type(exc).__name__}: {exc}",
            )

    @staticmethod
    def _coerce_step(raw: dict[str, Any]) -> Any:
        """Create a simple namespace object that matches the Step shape."""
        from dataclasses import dataclass

        @dataclass
        class Step:
            action: str
            url: str = ""
            selector: str = ""
            text: str = ""
            value: Any = None
            assertions: list[dict[str, Any]] = field(default_factory=list)

        return Step(
            action=raw.get("action", "goto"),
            url=raw.get("url", ""),
            selector=raw.get("selector", ""),
            text=raw.get("text", ""),
            value=raw.get("value"),
            assertions=raw.get("assertions", []),
        )

    def _click(self, driver: WebDriver, selector: str) -> None:
        driver.find_element(By.CSS_SELECTOR, selector).click()
        time.sleep(0.2)

    def _type(self, driver: WebDriver, selector: str, text: str) -> None:
        elem = driver.find_element(By.CSS_SELECTOR, selector)
        elem.clear()
        elem.send_keys(text)
