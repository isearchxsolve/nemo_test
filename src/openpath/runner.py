"""Top-level pipeline orchestrator for ``openpath run``.

Ties together: stack detection → dependency installation → server management →
test-plan generation → Playwright / Selenium execution → AI natural-language
tests → report writing.
"""
from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path
from typing import Any

from .config import load_settings, BrowserConfig
from .detect.plan_generator import plan_for_stack as plan_for_stack_lite
from .detect.server_manager import ServerManager, pick_free_port
from .detect.stack_detector import Stack, detect_stack
from .detect.test_case_generator import (
    PlanOptions,
    generate_test_plan,
    plan_for_stack as plan_for_stack_comprehensive,
)
from .engine.playwright_runner import PlaywrightRunner, SuiteResult
from .engine.selenium_runner import SeleniumRunner
from .env import load_env
from .logging import configure_logging, get_logger
from .report.html import write_html_report
from .report.junit import write_json_report, write_junit_xml

__all__ = ["run_pipeline"]

log = get_logger(__name__)


def run_pipeline(
    *,
    mode: str,
    path: str | None,
    url: str | None,
    creds_env_prefix: str = "",
    ai_tests_dir: Path | None = None,
    use_ai: bool = True,
    output_dir: Path = Path("testruns"),
    headed: bool = False,
    port: int | None = None,
    engine: str = "playwright",
) -> int:
    """Execute the full OpenPath pipeline and return an exit code (0 or 1).

    ``engine`` selects the browser automation backend:
    ``"playwright"`` (default) or ``"selenium"``.
    """
    load_env()
    configure_logging()

    run_id = f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    artifacts_dir = run_dir / "artifacts"
    log_file = run_dir / "run.log"

    configure_logging(log_file=str(log_file))
    log.info("pipeline.start", mode=mode, engine=engine, run_id=run_id)

    base_url_env, credentials, selectors, llm_cfg, browser_cfg = load_settings(
        creds_env_prefix
    )
    browser_cfg = BrowserConfig(headless=not headed)

    server_mgr: ServerManager | None = None
    base_url: str = ""

    try:
        if mode == "codebase":
            if not path:
                print("Error: --path is required in codebase mode.", file=sys.stderr)
                return 2
            repo = Path(path).resolve()
            if not repo.exists():
                print(f"Error: path {repo} does not exist.", file=sys.stderr)
                return 2

            stack = detect_stack(repo)
            log.info("stack.detected", language=stack.language, framework=stack.framework)
            print(f"[OpenPath] Stack: {stack.language}/{stack.framework}")

            chosen_port = pick_free_port(port)
            base_url = f"http://127.0.0.1:{chosen_port}"

            if stack.start_command:
                server_mgr = ServerManager(
                    stack.start_command,
                    port=chosen_port,
                    log_file=run_dir / "server.log",
                    cwd=repo,
                    env_overrides={"PORT": str(chosen_port)},
                    install_command=stack.install_command,
                )
                print(f"[OpenPath] Starting server on {base_url} …")
                server_mgr.start(health_path=stack.health_path, timeout=60.0)
                print(f"[OpenPath] Server healthy at {base_url}")
            else:
                log.warning("stack.no_start_command", framework=stack.framework)
                base_url = base_url_env or base_url

            plan = _build_plan(stack, base_url)

        else:  # url mode
            if not url:
                url = base_url_env or ""
            if not url:
                print("Error: --url or BASE_URL env var is required in url mode.", file=sys.stderr)
                return 2
            base_url = url.rstrip("/")
            print(f"[OpenPath] Testing live URL: {base_url}")

            dummy_stack = Stack(language="unknown", framework="unknown", route_files=[])
            plan = _build_plan(dummy_stack, base_url)

        # Skip auth tests when no credentials are configured.
        if not credentials.has_login:
            before = len(plan)
            plan = [t for t in plan if not t.get("requires_login", False)]
            skipped = before - len(plan)
            if skipped:
                log.info("auth.skipped", count=skipped, reason="no credentials configured")
                print(f"[OpenPath] Skipping {skipped} auth test(s) — no credentials in .env")

        # Load natural-language *.md tests.
        if use_ai:
            nl_dirs_to_try = []
            if ai_tests_dir:
                nl_dirs_to_try.append(ai_tests_dir)
            nl_dirs_to_try.append(Path("tests"))
            nl_dirs_to_try.append(Path("examples"))

            for nl_dir in nl_dirs_to_try:
                if nl_dir.exists() and any(nl_dir.glob("*.md")):
                    _append_nl_tests(plan, nl_dir, llm_cfg, run_dir)
                    break

        total = len(plan)
        print(f"[OpenPath] Running {total} tests against {base_url} (engine={engine}) …")

        # Choose the engine.
        if engine == "selenium":
            runner = SeleniumRunner(
                base_url,
                cfg=browser_cfg,
                credentials=credentials,
                selectors=selectors,
                artifacts_dir=artifacts_dir,
            )
            suite = runner.run_suite(plan, run_id=run_id)
        else:
            runner = PlaywrightRunner(
                base_url,
                cfg=browser_cfg,
                credentials=credentials,
                selectors=selectors,
                artifacts_dir=artifacts_dir,
            )
            suite = runner.run_suite(plan, run_id=run_id)

        suite.end_time = time.time()

    except Exception as exc:  # noqa: BLE001
        log.exception("pipeline.error", error=str(exc))
        print(f"[OpenPath] FATAL: {exc}", file=sys.stderr)
        return 1
    finally:
        if server_mgr:
            server_mgr.stop()

    # Write reports.
    report_html = run_dir / "report.html"
    report_json = run_dir / "report.json"
    report_xml = run_dir / "report.xml"

    write_html_report(suite, report_html)
    write_json_report(suite, report_json)
    write_junit_xml(suite, report_xml)

    _print_summary(suite, str(report_html))

    return 0 if suite.failed == 0 else 1


def _build_plan(stack: Stack, base_url: str) -> list[dict[str, Any]]:
    """Build the comprehensive test plan, falling back to the lite generator."""
    try:
        plan = plan_for_stack_comprehensive(stack, base_url=base_url)
        log.info("plan.generated_comprehensive", count=len(plan))
    except Exception as exc:  # noqa: BLE001
        log.warning("plan.comprehensive_failed", reason=str(exc))
        plan = plan_for_stack_lite(stack, base_url=base_url)
        log.info("plan.generated_lite", count=len(plan))
    return [t.to_dict() if hasattr(t, "to_dict") else t for t in plan]


def _append_nl_tests(
    tests: list[dict[str, Any]],
    nl_dir: Path,
    llm_cfg: Any,
    run_dir: Path,
) -> None:
    """Compile *.md tests; skip gracefully if the LLM is unavailable."""
    try:
        from .engine.ai_runner import NLTestCompiler

        compiler = NLTestCompiler(llm_cfg, cache_dir=run_dir / "nl_cache")
        nl_tests = compiler.compile_dir(nl_dir)
        tests.extend(nl_tests)
        log.info("nl_tests.loaded", count=len(nl_tests), dir=str(nl_dir))
    except Exception as exc:  # noqa: BLE001
        log.warning("nl_tests.skipped", reason=str(exc))


def _print_summary(suite: SuiteResult, report_path: str) -> None:
    icon = "✓" if suite.failed == 0 else "✗"
    print(
        f"\n[OpenPath] {icon} {suite.passed}/{suite.total} tests passed "
        f"({suite.duration_s:.1f}s)"
    )
    # Show first 20 and last 5 results to keep output manageable.
    for t in suite.tests[:20]:
        t_icon = "✓" if t.passed else "✗"
        suffix = f" — {t.error}" if t.error else ""
        print(f"  {t_icon} {t.name} ({t.duration_s:.2f}s){suffix}")
    if suite.total > 20:
        print(f"  … ({suite.total - 20} more tests, see report)")
        for t in suite.tests[-5:]:
            t_icon = "✓" if t.passed else "✗"
            suffix = f" — {t.error}" if t.error else ""
            print(f"  {t_icon} {t.name} ({t.duration_s:.2f}s){suffix}")
    print(f"\n[OpenPath] Report: {report_path}")
