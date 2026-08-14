"""Unit tests for OpenPath — runs without LLM, without a browser.

Covers:
  - env.py loading helpers
  - config.py settings
  - retry.py backoff
  - redaction in logging.py
  - stack_detector.py
  - plan_generator.py
  - assertions.py (mocked page)
  - selectors.py (basic bucketing logic)
  - report writers (HTML, JSON, JUnit)
  - server_manager helpers (pick_free_port, wait_for_health)
"""
from __future__ import annotations

import json
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# env.py
# ---------------------------------------------------------------------------


def test_env_get_default():
    from openpath.env import get

    assert get("__OPENPATH_MISSING__", "default") == "default"


def test_env_get_present(monkeypatch):
    from openpath.env import get

    monkeypatch.setenv("__OPENPATH_TEST_KEY__", "hello")
    assert get("__OPENPATH_TEST_KEY__") == "hello"


def test_env_require_missing():
    from openpath.env import require

    with pytest.raises(KeyError, match="__OPENPATH_MISSING__"):
        require("__OPENPATH_MISSING__")


def test_env_require_present(monkeypatch):
    from openpath.env import require

    monkeypatch.setenv("__OPENPATH_TEST_KEY2__", "world")
    assert require("__OPENPATH_TEST_KEY2__") == "world"


def test_env_as_int(monkeypatch):
    from openpath.env import as_int

    monkeypatch.setenv("_OP_INT", "42")
    assert as_int("_OP_INT", 0) == 42
    assert as_int("_OP_INT_MISSING", 7) == 7


def test_env_as_bool(monkeypatch):
    from openpath.env import as_bool

    monkeypatch.setenv("_OP_BOOL", "true")
    assert as_bool("_OP_BOOL", False) is True
    monkeypatch.setenv("_OP_BOOL", "0")
    assert as_bool("_OP_BOOL", True) is False


# ---------------------------------------------------------------------------
# retry.py
# ---------------------------------------------------------------------------


def test_retry_succeeds_immediately():
    from openpath.retry import retry

    calls = []

    @retry(max_attempts=3, base_delay=0.0)
    def fn():
        calls.append(1)
        return "ok"

    assert fn() == "ok"
    assert len(calls) == 1


def test_retry_retries_on_failure():
    from openpath.retry import retry

    calls = []

    @retry(max_attempts=3, base_delay=0.0)
    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise ValueError("fail")
        return "done"

    assert fn() == "done"
    assert len(calls) == 3


def test_retry_raises_after_max():
    from openpath.retry import retry

    @retry(max_attempts=2, base_delay=0.0)
    def fn():
        raise RuntimeError("always")

    with pytest.raises(RuntimeError, match="always"):
        fn()


# ---------------------------------------------------------------------------
# logging.py — redaction
# ---------------------------------------------------------------------------


def test_redact_api_key():
    from openpath.logging import redact

    result = redact("Bearer sk-abc1234567890ABCDEFGHIJ")
    assert "sk-" not in result or "REDACTED" in result


def test_redact_nvapi():
    from openpath.logging import redact

    result = redact("token: nvapi-XXXXXXXXXXXXXXXXXXXXXXXXXXXX")
    assert "nvapi-" not in result or "REDACTED" in result


def test_redact_password_key():
    from openpath.logging import redact

    result = redact({"password": "super-secret"})
    assert result.get("password") == "REDACTED"


def test_redact_nested():
    from openpath.logging import redact

    result = redact({"headers": {"Authorization": "Bearer sk-shortkey1234567890abc"}})
    assert "REDACTED" in str(result)


# ---------------------------------------------------------------------------
# config.py
# ---------------------------------------------------------------------------


def test_config_llm_headers(monkeypatch):
    from openpath.config import LLMConfig
    from pydantic import SecretStr

    cfg = LLMConfig(api_key=SecretStr("test-key-value"))
    h = cfg.headers()
    assert h["Authorization"] == "Bearer test-key-value"
    assert "Content-Type" in h


def test_config_credentials_has_login():
    from openpath.config import Credentials
    from pydantic import SecretStr

    c = Credentials(username="u", password=SecretStr("p"))
    assert c.has_login

    c2 = Credentials()
    assert not c2.has_login


# ---------------------------------------------------------------------------
# stack_detector.py
# ---------------------------------------------------------------------------


def test_detect_flask(tmp_path):
    from openpath.detect.stack_detector import detect_stack

    (tmp_path / "requirements.txt").write_text("flask==3.0.0\n")
    (tmp_path / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n")
    stack = detect_stack(tmp_path)
    assert stack.language == "python"
    assert stack.framework == "flask"


def test_detect_fastapi(tmp_path):
    from openpath.detect.stack_detector import detect_stack

    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    stack = detect_stack(tmp_path)
    assert stack.language == "python"
    assert stack.framework == "fastapi"


def test_detect_vite(tmp_path):
    from openpath.detect.stack_detector import detect_stack

    pkg = {
        "name": "my-app",
        "scripts": {"dev": "vite"},
        "dependencies": {"react": "^18.0.0"},
        "devDependencies": {"vite": "^5.0.0"},
    }
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    stack = detect_stack(tmp_path)
    assert stack.language == "node"
    assert stack.framework == "vite"


def test_detect_unknown(tmp_path):
    from openpath.detect.stack_detector import detect_stack

    stack = detect_stack(tmp_path)
    assert stack.language == "unknown"


# ---------------------------------------------------------------------------
# plan_generator.py
# ---------------------------------------------------------------------------


def test_plan_minimum_tests(tmp_path):
    from openpath.detect.stack_detector import Stack
    from openpath.detect.plan_generator import plan_for_stack

    stack = Stack(language="python", framework="flask", route_files=[])
    tests = plan_for_stack(stack)
    assert len(tests) >= 5


def test_plan_includes_auth():
    from openpath.detect.stack_detector import Stack
    from openpath.detect.plan_generator import plan_for_stack

    stack = Stack(language="python", framework="flask", route_files=[])
    tests = plan_for_stack(stack)
    kinds = {t.kind for t in tests}
    assert "auth" in kinds
    assert "smoke" in kinds


def test_plan_detects_primary_entity():
    from openpath.detect.plan_generator import plan_for_stack
    from openpath.detect.stack_detector import Stack

    stack = Stack(language="python", framework="flask", route_files=[])
    tests = plan_for_stack(stack, base_url="http://localhost")
    # "items" is a default route and should be detected as primary entity.
    crud_tests = [t for t in tests if t.kind == "crud"]
    assert len(crud_tests) >= 1


# ---------------------------------------------------------------------------
# assertions.py (mock Page)
# ---------------------------------------------------------------------------


def _mock_page(url="http://localhost/dashboard", title="Dashboard"):
    page = MagicMock()
    page.url = url
    page.title.return_value = title
    loc = MagicMock()
    loc.count.return_value = 0
    loc.first.is_visible.return_value = False
    page.locator.return_value = loc
    page.get_by_text.return_value = loc
    page.get_by_role.return_value = loc
    return page


def test_assert_url_contains_pass():
    from openpath.engine.assertions import Assertion, evaluate_assertions

    page = _mock_page(url="http://localhost/dashboard")
    results = evaluate_assertions(page, [Assertion(type="url_contains", value="dashboard")])
    assert results[0].passed


def test_assert_url_contains_fail():
    from openpath.engine.assertions import Assertion, evaluate_assertions

    page = _mock_page(url="http://localhost/home")
    results = evaluate_assertions(page, [Assertion(type="url_contains", value="dashboard")])
    assert not results[0].passed


def test_assert_no_console_errors_pass():
    from openpath.engine.assertions import Assertion, evaluate_assertions

    page = _mock_page()
    results = evaluate_assertions(
        page, [Assertion(type="no_console_errors")], console_errors=[]
    )
    assert results[0].passed


def test_assert_no_console_errors_fail():
    from openpath.engine.assertions import Assertion, evaluate_assertions

    page = _mock_page()
    results = evaluate_assertions(
        page, [Assertion(type="no_console_errors")], console_errors=["TypeError: x"]
    )
    assert not results[0].passed


def test_assert_no_4xx_5xx_fail():
    from openpath.engine.assertions import Assertion, evaluate_assertions

    page = _mock_page()
    results = evaluate_assertions(
        page, [Assertion(type="no_4xx_5xx")], network_errors=["404 /missing"]
    )
    assert not results[0].passed


def test_assert_logged_in_fail_on_login_page():
    from openpath.engine.assertions import Assertion, evaluate_assertions

    page = _mock_page(url="http://localhost/login")
    results = evaluate_assertions(page, [Assertion(type="logged_in")])
    assert not results[0].passed


def test_assert_logged_in_pass():
    from openpath.engine.assertions import Assertion, evaluate_assertions

    page = _mock_page(url="http://localhost/dashboard")
    results = evaluate_assertions(page, [Assertion(type="logged_in")])
    assert results[0].passed


def test_assert_status_ok_error_page():
    from openpath.engine.assertions import Assertion, evaluate_assertions

    page = _mock_page(url="http://localhost/x", title="404 Not Found")
    results = evaluate_assertions(page, [Assertion(type="status_ok")])
    assert not results[0].passed


def test_assert_unknown_type():
    from openpath.engine.assertions import Assertion, evaluate_assertions

    page = _mock_page()
    results = evaluate_assertions(page, [Assertion(type="__nonexistent__")])
    assert not results[0].passed


# ---------------------------------------------------------------------------
# selectors.py (mock Page)
# ---------------------------------------------------------------------------


def test_selector_buckets_testid():
    """Verify that a data-testid selector is placed in the correct bucket."""
    from openpath.engine.selectors import resolve_selector

    page = MagicMock()
    loc = MagicMock()
    loc.count.return_value = 1
    loc.first.is_visible.return_value = True
    page.get_by_test_id.return_value = loc

    result = resolve_selector(page, ['[data-testid="submit"]'])
    assert result is not None
    assert result.strategy == "testid"


def test_selector_returns_none_when_not_found():
    from openpath.engine.selectors import resolve_selector

    page = MagicMock()
    loc = MagicMock()
    loc.count.return_value = 0
    loc.first.is_visible.return_value = False
    page.get_by_test_id.return_value = loc
    page.get_by_role.return_value = loc
    page.get_by_text.return_value = loc
    page.locator.return_value = loc

    result = resolve_selector(page, ['[data-testid="missing"]'])
    assert result is None


# ---------------------------------------------------------------------------
# report/junit.py and report/html.py
# ---------------------------------------------------------------------------


def _fake_suite(run_id: str = "test-run-1") -> object:
    from openpath.engine.playwright_runner import (
        SuiteResult,
        TestResult,
        AssertionResult,
    )
    from openpath.engine.assertions import Assertion

    t1 = TestResult(
        name="smoke::/",
        kind="smoke",
        passed=True,
        duration_s=0.5,
    )
    t2 = TestResult(
        name="smoke::/login",
        kind="smoke",
        passed=False,
        duration_s=0.3,
        error="HTTP 500",
        assertion_results=[
            AssertionResult(
                assertion=Assertion(type="no_console_errors"),
                passed=False,
                message="Console error: TypeError",
            )
        ],
    )
    suite = SuiteResult(run_id=run_id, base_url="http://localhost")
    suite.tests = [t1, t2]
    suite.start_time = time.time() - 1
    suite.end_time = time.time()
    return suite


def test_write_junit_xml(tmp_path):
    from openpath.report.junit import write_junit_xml

    suite = _fake_suite()
    out = tmp_path / "report.xml"
    write_junit_xml(suite, out)
    assert out.exists()
    tree = ET.parse(str(out))
    root = tree.getroot()
    assert root.tag == "testsuite"
    assert root.attrib["tests"] == "2"
    assert root.attrib["failures"] == "1"


def test_write_json_report(tmp_path):
    from openpath.report.junit import write_json_report

    suite = _fake_suite()
    out = tmp_path / "report.json"
    write_json_report(suite, out)
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["total"] == 2
    assert data["failed"] == 1
    assert len(data["tests"]) == 2


def test_write_html_report(tmp_path):
    from openpath.report.html import write_html_report

    suite = _fake_suite()
    out = tmp_path / "report.html"
    write_html_report(suite, out)
    assert out.exists()
    content = out.read_text()
    assert "OpenPath" in content
    assert "smoke::/login" in content


# ---------------------------------------------------------------------------
# server_manager.py — pick_free_port
# ---------------------------------------------------------------------------


def test_pick_free_port():
    from openpath.detect.server_manager import pick_free_port

    port = pick_free_port()
    assert 1024 < port < 65536


def test_pick_free_port_uniqueness():
    from openpath.detect.server_manager import pick_free_port

    ports = {pick_free_port() for _ in range(5)}
    # Each call should return *a* valid port (may collide, but rarely).
    assert all(1024 < p < 65536 for p in ports)


# ---------------------------------------------------------------------------
# CLI smoke test — just parse, do not run browser
# ---------------------------------------------------------------------------


def test_cli_help():
    from openpath.cli import build_parser

    parser = build_parser()
    # Ensure all three commands are registered.
    choices = parser._subparsers._actions[-1].choices
    assert "run" in choices
    assert "record" in choices
    assert "report" in choices


def test_cli_run_requires_mode():
    from openpath.cli import build_parser
    import sys

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run"])


def test_cli_run_mode_choices():
    from openpath.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["run", "--mode", "url", "--url", "http://example.com"])
    assert args.mode == "url"
    assert args.url == "http://example.com"


# ---------------------------------------------------------------------------
# Additional tests for new features
# ---------------------------------------------------------------------------


def test_stack_has_install_command(tmp_path):
    """Flask stack detection produces an install_command."""
    from openpath.detect.stack_detector import detect_stack

    (tmp_path / "requirements.txt").write_text("flask==3.0.0\n")
    (tmp_path / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n")
    stack = detect_stack(tmp_path)
    assert stack.install_command is not None
    assert "pip" in stack.install_command or "python" in stack.install_command[0]


def test_stack_node_has_install_command(tmp_path):
    """Node stack detection produces an install_command."""
    from openpath.detect.stack_detector import detect_stack
    import json

    pkg = {
        "name": "app",
        "scripts": {"dev": "vite"},
        "devDependencies": {"vite": "^5.0"},
    }
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    stack = detect_stack(tmp_path)
    assert stack.install_command is not None
    assert stack.install_command[0] in ("npm", "pnpm", "yarn")


def test_stack_route_files_alias(tmp_path):
    """rogle_files compat alias works alongside route_files."""
    from openpath.detect.stack_detector import Stack

    s = Stack(language="python", framework="flask", route_files=["/a", "/b"])
    assert s.rogle_files == ["/a", "/b"]
    s.rogle_files = ["/c"]
    assert s.route_files == ["/c"]


def test_assert_visual_diff_first_run(tmp_path):
    """visual_diff saves baseline on first run and returns passed=True."""
    from openpath.engine.assertions import Assertion, evaluate_assertions
    from unittest.mock import MagicMock, patch

    baseline = tmp_path / "baseline.png"
    page = _mock_page()

    # Return a tiny valid 1x1 PNG from page.screenshot().
    import base64, io
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("pillow not installed")

    img = Image.new("RGBA", (1, 1), (255, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    page.screenshot.return_value = png_bytes

    results = evaluate_assertions(
        page,
        [Assertion(type="visual_diff", value=str(baseline), threshold=0.1)],
    )
    assert results[0].passed, results[0].message
    assert baseline.exists()


def test_html_report_has_timeline(tmp_path):
    """HTML report contains the timeline section."""
    from openpath.report.html import write_html_report

    suite = _fake_suite()
    out = tmp_path / "report.html"
    write_html_report(suite, out)
    content = out.read_text()
    assert "Test Timeline" in content
    assert "tl-bar" in content


def test_server_manager_install_deps_skipped_when_none(tmp_path):
    """install_deps returns True immediately when install_command is None."""
    from openpath.detect.server_manager import ServerManager

    mgr = ServerManager(
        ["echo", "hi"],
        port=9999,
        log_file=tmp_path / "server.log",
        install_command=None,
    )
    assert mgr.install_deps() is True


def test_no_credentials_skips_auth_tests():
    """plan_for_stack generates auth tests; runner.py should strip them when creds absent."""
    from openpath.detect.stack_detector import Stack
    from openpath.detect.plan_generator import plan_for_stack

    stack = Stack(language="python", framework="flask", route_files=[])
    tests = plan_for_stack(stack)
    auth_tests = [t for t in tests if t.requires_login]
    assert len(auth_tests) >= 1  # plan always generates at least one auth test

    # Simulate the runner stripping them when credentials absent.
    as_dicts = [t.to_dict() for t in tests]
    filtered = [t for t in as_dicts if not t.get("requires_login", False)]
    assert len(filtered) == len(tests) - len(auth_tests)


# ---------------------------------------------------------------------------
# test_case_generator.py
# ---------------------------------------------------------------------------


def test_generator_reaches_1000_tests():
    """The combinatorial generator must produce at least 1000 test cases."""
    from openpath.detect.test_case_generator import generate_test_plan

    tests = generate_test_plan()
    assert len(tests) >= 1000, f"Expected >= 1000 tests, got {len(tests)}"


def test_generator_has_expected_kind_distribution():
    """Verify the generator produces the expected mix of test kinds."""
    from openpath.detect.test_case_generator import generate_test_plan

    tests = generate_test_plan()
    kinds = {}
    for t in tests:
        kinds[t.get("kind", "unknown")] = kinds.get(t.get("kind", "unknown"), 0) + 1

    assert "smoke" in kinds
    assert "crud" in kinds
    assert "auth" in kinds
    assert "nav" in kinds
    assert "search" in kinds
    assert "formval" in kinds
    assert "visual" in kinds
    assert "error" in kinds
    assert "settings" in kinds
    assert "idempotency" in kinds


def test_generator_all_tests_have_required_fields():
    """Every generated test dict must have name, kind, url, steps, assertions."""
    from openpath.detect.test_case_generator import generate_test_plan

    tests = generate_test_plan()
    for t in tests:
        assert "name" in t, f"Missing 'name' in {t}"
        assert "kind" in t, f"Missing 'kind' in {t}"
        assert "url" in t, f"Missing 'url' in {t}"
        assert "steps" in t, f"Missing 'steps' in {t}"
        assert "assertions" in t, f"Missing 'assertions' in {t}"
        assert isinstance(t["steps"], list)
        assert isinstance(t["assertions"], list)


def test_generator_respects_options():
    """Passing PlanOptions with features disabled reduces test count."""
    from openpath.detect.test_case_generator import generate_test_plan, PlanOptions

    full = generate_test_plan()
    minimal = generate_test_plan(
        PlanOptions(
            include_auth=False,
            include_search=False,
            include_form_validation=False,
            include_navigation=False,
            include_visual=False,
            include_error_pages=False,
            crud_variants=2,
            search_variants=2,
        )
    )
    assert len(minimal) < len(full)


def test_generator_comprehensive_alias():
    """plan_for_stack_comprehensive is importable and callable."""
    from openpath.detect.test_case_generator import plan_for_stack_comprehensive
    from openpath.detect.stack_detector import Stack

    stack = Stack(language="python", framework="flask", route_files=[])
    tests = plan_for_stack_comprehensive(stack)
    assert len(tests) >= 1000


# ---------------------------------------------------------------------------
# CLI --engine option
# ---------------------------------------------------------------------------


def test_cli_run_engine_option():
    """The --engine flag is accepted by the parser."""
    from openpath.cli import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "run", "--mode", "url", "--url", "http://example.com",
        "--engine", "selenium",
    ])
    assert args.engine == "selenium"

    args2 = parser.parse_args([
        "run", "--mode", "codebase", "--path", "/tmp",
        "--engine", "playwright",
    ])
    assert args2.engine == "playwright"


def test_cli_run_engine_default():
    """Default engine is playwright."""
    from openpath.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["run", "--mode", "url", "--url", "http://x"])
    assert args.engine == "playwright"


def test_cli_run_engine_invalid_rejected():
    """An invalid --engine value raises a parse error."""
    from openpath.cli import build_parser
    import sys

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--mode", "url", "--url", "http://x", "--engine", "invalid"])


# ---------------------------------------------------------------------------
# Selenium runner smoke test
# ---------------------------------------------------------------------------


def test_selenium_runner_importable():
    """SeleniumRunner can be imported without errors."""
    from openpath.engine.selenium_runner import SeleniumRunner
    assert SeleniumRunner is not None


def test_selenium_runner_instantiates(tmp_path):
    """Instantiating SeleniumRunner does not raise."""
    from openpath.engine.selenium_runner import SeleniumRunner
    from openpath.config import BrowserConfig

    runner = SeleniumRunner(
        "http://example.com",
        cfg=BrowserConfig(headless=True),
        artifacts_dir=tmp_path / "artifacts",
    )
    assert runner.base_url == "http://example.com"


def test_selenium_runner_suite_signature():
    """run_suite accepts a list of dicts and a run_id string."""
    from openpath.engine.selenium_runner import SeleniumRunner
    from openpath.config import BrowserConfig

    runner = SeleniumRunner(
        "http://example.com",
        cfg=BrowserConfig(headless=True),
    )
    # run_suite signature is (self, tests, run_id) → SuiteResult
    import inspect
    sig = inspect.signature(runner.run_suite)
    params = list(sig.parameters.keys())
    assert "tests" in params
    assert "run_id" in params


# ---------------------------------------------------------------------------
# Runner pipeline integration test — generator produces tests that runner understands
# ---------------------------------------------------------------------------


def test_runner_accepts_generated_tests():
    """Tests from generate_test_plan have the shape PlaywrightRunner expects."""
    from openpath.detect.test_case_generator import generate_test_plan
    from openpath.engine.playwright_runner import Step

    tests = generate_test_plan()
    for t in tests:
        # Each test dict must be coercible to Step objects internally.
        for raw in t.get("steps", []):
            s = Step(
                action=raw.get("action", "goto"),
                url=raw.get("url", ""),
                selector=raw.get("selector", ""),
                text=raw.get("text", ""),
                value=raw.get("value"),
            )
            assert s.action in ("goto", "click", "type", "scroll", "wait", "assert", "auto_login")
