"""OpenPath command-line interface.

Commands:
    openpath run      Run the AI-native test suite against a codebase or live URL.
    openpath record   Launch a headed session and record a natural-language test.
    openpath report   Open the most recent HTML report in a browser.
"""
from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path
from typing import Sequence

from .logging import configure_logging, get_logger
from .env import load_env

__all__ = ["main", "build_parser"]

log = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openpath",
        description="OpenPath: AI-native test automation framework.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the test suite.")
    run.add_argument(
        "--mode",
        choices=["codebase", "url"],
        required=True,
        help="codebase boots the bundled/forced app server; url hits a live URL.",
    )
    run.add_argument("--path", help="Path to the repo to test (codebase mode).")
    run.add_argument("--url", help="URL to test (url mode).")
    run.add_argument(
        "--creds-env-prefix",
        default="",
        help="Environment-variable prefix for credentials (e.g. 'PROD').",
    )
    run.add_argument(
        "--ai-tests",
        default=None,
        help="Directory containing *.md natural-language tests (optional).",
    )
    run.add_argument(
        "--no-ai",
        action="store_true",
        help="Disable the AI natural-language runner (smoke tests only).",
    )
    run.add_argument(
        "--output",
        default=None,
        help="Output directory for reports and artifacts (default testruns/).",
    )
    run.add_argument(
        "--headed",
        action="store_true",
        help="Run the browser headed (default headless).",
    )
    run.add_argument(
        "--port", type=int, default=None, help="Override the dev-server port."
    )
    run.add_argument(
        "--engine",
        choices=["playwright", "selenium"],
        default="playwright",
        help="Browser automation engine (default: playwright).",
    )

    rec = sub.add_parser("record", help="Record a natural-language test.")
    rec.add_argument("--url", required=True, help="URL to record.")
    rec.add_argument(
        "--out",
        default="tests",
        help="Output directory for the recorded *.md file.",
    )

    rep = sub.add_parser("report", help="Open the most recent HTML report.")
    rep.add_argument(
        "--open",
        action="store_true",
        help="Open the report in the default browser.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    load_env()
    configure_logging()

    if args.command == "run":
        from .runner import run_pipeline

        return run_pipeline(
            mode=args.mode,
            path=args.path,
            url=args.url,
            creds_env_prefix=args.creds_env_prefix,
            ai_tests_dir=Path(args.ai_tests) if args.ai_tests else None,
            use_ai=not args.no_ai,
            output_dir=Path(args.output) if args.output else Path("testruns"),
            headed=args.headed,
            port=args.port,
            engine=args.engine,
        )

    if args.command == "record":
        from .engine.recorder import Recorder

        recorder = Recorder(args.url, output_dir=Path(args.out))
        return recorder.run()

    if args.command == "report":
        return _open_report(args.open)

    parser.print_help()
    return 2


def _open_report(open_it: bool) -> int:
    out_dir = Path("testruns")
    candidates = sorted(out_dir.glob("run-*/report.html"), reverse=True)
    if not candidates:
        print("No reports found in testruns/", file=sys.stderr)
        return 1
    latest = candidates[0]
    url = latest.resolve().as_uri()
    print(f"Latest report: {url}")
    if open_it:
        webbrowser.open(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
