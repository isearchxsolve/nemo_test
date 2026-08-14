"""Stack detection: identifies the project's language & framework from
the files present in a repository.

This is intentionally heuristic and conservative. We detect what we need to
boot a dev server; unknown stacks fall back to a generic Python/null runner.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["Stack", "detect_stack"]


@dataclass
class Stack:
    """Detected stack for a codebase under test."""

    language: str
    framework: str
    route_files: list[str] = field(default_factory=list)   # router/page files
    openapi_path: Path | None = None
    package_manager: str | None = None
    install_command: list[str] | None = None   # run before start_command
    start_command: list[str] | None = None
    health_path: str = "/"
    extra: dict[str, Any] = field(default_factory=dict)

    # Backwards-compatible alias so existing call sites using the old name work.
    @property
    def rogle_files(self) -> list[str]:  # noqa: D102 — intentional compat shim
        return self.route_files

    @rogle_files.setter
    def rogle_files(self, v: list[str]) -> None:
        self.route_files = v


def _read(path: Path, limit: int = 4096) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def detect_stack(repo: Path) -> Stack:
    """Inspect ``repo`` and return a :class:`Stack` description."""
    repo = repo.resolve()

    pkg_files = {
        "package.json": "npm",
        "pnpm-workspace.yaml": "pnpm",
        "requirements.txt": "pip",
        "pyproject.toml": "python",
        "Pipfile": "pipenv",
        "go.mod": "go",
        "pom.xml": "maven",
        "build.gradle": "gradle",
        "Gemfile": "bundler",
    }
    manager = None
    for fname, mgr in pkg_files.items():
        if (repo / fname).exists():
            manager = mgr
            break

    # Node.js apps
    pkg = repo / "package.json"
    if pkg.exists():
        data: dict[str, Any] = {}
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
        scripts = data.get("scripts") or {}

        framework = "node"
        if "vite" in deps:
            framework = "vite"
        if "next" in deps:
            framework = "next"
        if "react-scripts" in deps:
            framework = "react-scripts"
        if "@remix-run/dev" in deps:
            framework = "remix"
        if "astro" in deps:
            framework = "astro"
        if "svelte" in deps or "@sveltejs/kit" in deps:
            framework = "sveltekit"
        if "nuxt" in deps:
            framework = "nuxt"

        # Pick start script (prefer preview > dev > start).
        start: list[str] | None = None
        for key in ("preview", "dev", "start"):
            if key in scripts:
                start = ["npx", "--", *scripts[key].split()]
                break

        # Derive install command based on lock file.
        if (repo / "pnpm-lock.yaml").exists():
            install = ["pnpm", "install", "--frozen-lockfile"]
            mgr_name = "pnpm"
        elif (repo / "yarn.lock").exists():
            install = ["yarn", "install", "--frozen-lockfile"]
            mgr_name = "yarn"
        else:
            install = ["npm", "install", "--prefer-offline"]
            mgr_name = "npm"

        routes = _find_node_routes(repo)
        return Stack(
            language="node",
            framework=framework,
            route_files=[str(r) for r in routes],
            openapi_path=_find_openapi(repo),
            package_manager=manager or mgr_name,
            install_command=install,
            start_command=start,
            extra={"package_json": data},
        )

    # Python apps
    req = repo / "requirements.txt"
    pyproj = repo / "pyproject.toml"
    if req.exists() or pyproj.exists():
        contents = _read(req) + "\n" + _read(pyproj)
        lower = contents.lower()
        framework = "python"
        if "fastapi" in lower:
            framework = "fastapi"
        elif "flask" in lower:
            framework = "flask"
        elif "django" in lower:
            framework = "django"
        elif "starlette" in lower:
            framework = "starlette"

        # Build install command.
        py = _find_python(framework.replace("fastapi", "fastapi").replace("starlette", "starlette"))
        if req.exists():
            install = [py, "-m", "pip", "install", "-r", "requirements.txt", "-q"]
        elif pyproj.exists():
            install = [py, "-m", "pip", "install", "-e", ".", "-q"]
        else:
            install = None

        start = _python_start(repo, framework, py)
        routes = _find_python_routes(repo, framework)
        return Stack(
            language="python",
            framework=framework,
            route_files=[str(r) for r in routes],
            openapi_path=_find_openapi(repo),
            package_manager=manager or "pip",
            install_command=install,
            start_command=start,
        )

    # Go
    if (repo / "go.mod").exists():
        return Stack(
            language="go",
            framework="go",
            package_manager="go",
            install_command=["go", "mod", "download"],
            start_command=["go", "run", "."],
        )

    # Java (Maven/Gradle)
    if (repo / "pom.xml").exists():
        return Stack(
            language="java",
            framework="maven",
            package_manager="maven",
            install_command=["mvn", "dependency:resolve", "-q"],
            start_command=["mvn", "spring-boot:run"],
        )

    return Stack(language="unknown", framework="unknown", package_manager=None)


def _find_node_routes(repo: Path) -> list[Path]:
    routes: list[Path] = []
    for name in ("App.tsx", "App.jsx", "App.js", "Router.tsx", "router.ts", "router.js"):
        for found in repo.rglob(name):
            if "node_modules" in found.parts:
                continue
            routes.append(found)
    for d in ("app", "pages", "src/app", "src/pages"):
        full = repo / d
        if full.exists() and full.is_dir():
            routes.append(full)
    return routes[:32]


def _find_python(module_hint: str = "flask") -> str:
    """Return the first Python on PATH that can import ``module_hint``."""
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    for d in path_dirs:
        for name in ("python3.exe", "python.exe", "python3", "python"):
            candidate = Path(d) / name
            if not candidate.exists():
                continue
            try:
                r = subprocess.run(
                    [str(candidate), "-c", f"import {module_hint}"],
                    capture_output=True,
                    timeout=5,
                )
                if r.returncode == 0:
                    return str(candidate)
            except Exception:  # noqa: BLE001
                continue
    return sys.executable


def _python_start(repo: Path, framework: str, py: str) -> list[str] | None:
    if framework == "flask":
        for app_name in ("app.py", "main.py", "wsgi.py"):
            if (repo / app_name).exists():
                return [
                    py,
                    "-c",
                    f"from {app_name[:-3]} import app; "
                    "import os; app.run(host='0.0.0.0', port=int(os.environ['PORT']))",
                ]
    if framework in ("fastapi", "starlette"):
        for mod in ("app.py", "main.py"):
            if (repo / mod).exists():
                return [
                    "uvicorn",
                    f"{mod[:-3]}:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "__PORT__",
                ]
    if framework == "django":
        return [py, "manage.py", "runserver", "127.0.0.1:__PORT__"]
    return None


def _find_python_routes(repo: Path, framework: str) -> list[Path]:
    found: list[Path] = []
    if framework == "fastapi":
        for p in repo.rglob("*.py"):
            if "node_modules" in p.parts:
                continue
            text = _read(p)
            if "APIRouter" in text or "FastAPI(" in text or "@app.get" in text:
                found.append(p)
    elif framework == "django":
        for p in repo.rglob("urls.py"):
            if "node_modules" in p.parts:
                continue
            found.append(p)
    return found[:32]


def _find_openapi(repo: Path) -> Path | None:
    for pattern in ("openapi.json", "openapi.yaml", "openapi.yml"):
        for p in repo.rglob(pattern):
            if "node_modules" in p.parts:
                continue
            return p
    return None
