"""Environment variable loading for OpenPath.

All secrets are read from the process environment, populated from a `.env` file
via python-dotenv. No secret is ever read from the repository tree directly,
and none are ever written into logs (see :mod:`openpath.logging`).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

__all__ = ["load_env", "get", "require", "as_int", "as_bool"]


def load_env(env_path: str | os.PathLike[str] | None = None) -> None:
    """Load environment variables from a ``.env`` file.

    Resolution order: an explicit path argument, then ``./.env``, then
    ``./.env.local``. Existing environment variables are *not* overridden by
    the file, so CI-injected secrets always win.
    """
    if env_path is not None:
        load_dotenv(env_path, override=False)
        return
    for candidate in (Path(".env"), Path(".env.local")):
        if candidate.exists():
            load_dotenv(candidate, override=False)


def get(key: str, default: Any = None) -> Any:
    """Return ``key`` from the environment or ``default`` if unset."""
    return os.environ.get(key, default)


def require(key: str) -> str:
    """Return ``key`` or raise ``KeyError`` with a helpful message."""
    value = os.environ.get(key)
    if not value:
        raise KeyError(
            f"Required environment variable {key!r} is not set. "
            f"Define it in your .env file (see .env.example)."
        )
    return value


def as_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def as_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
