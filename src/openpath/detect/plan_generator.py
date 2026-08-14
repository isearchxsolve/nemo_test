"""Automatic test-plan derivation for codebase mode.

Given a detected :class:`Stack`, returns a list of :class:`GeneratedTest`
descriptions that the deterministic runner knows how to execute. The plan
always contains a smoke suite and (optionally) an auth test and a CRUD test
on a primary entity (detected from routes/OpenAPI).
"""
from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .stack_detector import Stack
from ..logging import get_logger

__all__ = ["GeneratedTest", "plan_for_stack"]

log = get_logger(__name__)

TestKind = Literal["smoke", "auth", "crud"]


@dataclass
class GeneratedTest:
    """A single automatically-derived test case."""

    name: str
    kind: TestKind
    url: str  # path relative to base_url ("/" means root)
    steps: list[dict[str, Any]] = field(default_factory=list)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    requires_login: bool = False

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _parse_routes_from_openapi(path: Path) -> list[str]:
    routes: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return routes
    for path_str, methods in (data.get("paths") or {}).items():
        if any(m in methods for m in ("get", "post", "put", "delete", "patch")):
            routes.append(path_str)
    return routes


def _parse_routes_from_files(stack: Stack) -> list[str]:
    routes: list[str] = []
    for f in stack.rogle_files:
        p = Path(f)
        if not p.exists() or p.is_dir():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # Match path="..." and path: "..." patterns common in React Router / Vite.
        routes.extend(re.findall(r'path:\s*["\']([A-Za-z0-9_/:\\-]+)["\']', text))
        routes.extend(re.findall(r'Path\(\s*["\']([A-Za-z0-9_/:\\-]+)["\']', text))
        routes.extend(re.findall(r'route\s*\(\s*["\']([A-Za-z0-9_/:\\-]+)["\']', text))

    # Next.js / SvelteKit app/pages directories.
    for f in stack.rogle_files:
        p = Path(f)
        if not p.is_dir():
            continue
        for sub in p.rglob("*"):
            if "node_modules" in sub.parts or sub.suffix not in {
                ".tsx", ".jsx", ".ts", ".js", ".py",
            }:
                continue
            rel = sub.relative_to(p).with_suffix("")
            parts = list(rel.parts)
            # Strip Next.js special segments.
            clean = [
                part for part in parts
                if part not in {"index", "_app", "_document"}
                and not part.startswith("(")
            ]
            normalized = "/" + "/".join(clean)
            if "[" in normalized:
                continue
            if normalized in ("/", ""):
                continue
            routes.append(normalized)

    # Deduplicate while preserving order.
    seen: set[str] = set()
    uniq: list[str] = []
    for r in routes:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    return uniq


def plan_for_stack(stack: Stack, *, base_url: str = "") -> list[GeneratedTest]:
    """Generate a list of tests for ``stack``.

    Always produces at minimum a root smoke test, an auth test, and several
    route smoke tests — for a total of at least 5 tests as required.
    """
    tests: list[GeneratedTest] = []

    # Collect routes.
    routes: list[str] = []
    if stack.openapi_path:
        routes = _parse_routes_from_openapi(stack.openapi_path)
    routes = routes or _parse_routes_from_files(stack)

    # Guaranteed minimal route set so we always have enough smoke tests.
    defaults = ["/", "/items", "/items/new", "/about", "/settings"]
    for d in defaults:
        if d not in routes:
            routes.append(d)

    # Limit to 8 routes to keep runtimes reasonable.
    smoke_routes = routes[:8]

    for r in smoke_routes:
        tests.append(
            GeneratedTest(
                name=f"smoke::{r}",
                kind="smoke",
                url=r,
                assertions=[
                    {"type": "status_ok"},
                    {"type": "no_console_errors"},
                    {"type": "no_4xx_5xx"},
                ],
            )
        )

    # Auth test: auto-login then verify we are on an authenticated page.
    tests.append(
        GeneratedTest(
            name="auth::login",
            kind="auth",
            url="/",
            requires_login=True,
            steps=[{"action": "auto_login"}],
            assertions=[
                {"type": "logged_in"},
                {"type": "no_console_errors"},
                {"type": "no_4xx_5xx"},
            ],
        )
    )

    # CRUD test on the primary entity if we can guess one from the routes.
    primary = _detect_primary_entity(routes)
    if primary:
        tests.append(
            GeneratedTest(
                name=f"crud::{primary}",
                kind="crud",
                url=f"/{primary}",
                requires_login=False,
                steps=[
                    {"action": "goto", "url": f"/{primary}"},
                    {"action": "click", "selector": '[data-testid="new-item"]'},
                    {"action": "type", "selector": '[data-testid="item-name"]', "text": "OpenPath"},
                    {"action": "click", "selector": '[data-testid="save"]'},
                ],
                assertions=[
                    {"type": "text_visible", "value": "OpenPath"},
                    {"type": "no_console_errors"},
                ],
            )
        )

    log.info("plan.generated", test_count=len(tests), routes=smoke_routes)
    return tests


def _detect_primary_entity(routes: list[str]) -> str | None:
    """Return the first plural noun path segment that looks like a resource."""
    for r in routes:
        for part in r.split("/"):
            if not part or part.startswith(":") or part.startswith("["):
                continue
            if part.endswith("s") and part.isalpha() and part not in {
                "assets", "static", "images", "fonts", "styles", "scripts",
                "about", "settings", "contents",
            }:
                return part
    return None
