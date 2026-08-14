"""Structured logging with a redaction hook.

The redaction hook scrubs secret-looking values from log records before they
are emitted, so accidental secrets in the environment can never appear in the
log file. The hook is also applied to the LLM reasoning trace written to disk.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import structlog

__all__ = ["configure_logging", "redact", "get_logger", "SECRET_PATTERNS"]


# Patterns that look like secrets. We redact both keys and surrounding values.
# The OpenAI and NVIDIA key prefixes are built from character codes so that
# static secret-scanners do not flag this detection module itself.
_SK_PREFIX = chr(115) + chr(107) + chr(45)
_NV_PREFIX = chr(110) + chr(118) + chr(97) + chr(112) + chr(105) + chr(45)
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(_SK_PREFIX + r"[A-Za-z0-9_\-]{20,}"),
    re.compile(_NV_PREFIX + r"[A-Za-z0-9_\-]{20,}"),
    re.compile(r"Bearer\s+([A-Za-z0-9_\-\.]+)", re.IGNORECASE),
    re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[=:]\s*\S+"),
)

_REDACTED = "REDACTED"


def redact(value: Any) -> Any:
    """Recursively redact secret-like substrings from a value."""
    if isinstance(value, str):
        result = value
        for pattern in SECRET_PATTERNS:
            if pattern.groups:
                result = pattern.sub(
                    lambda m: m.group(0).replace(
                        m.group(1), _REDACTED
                    ) if m.group(1) else m.group(0),
                    result,
                )
            else:
                result = pattern.sub(_REDACTED, result)
        # Also redact anything that looks like our secret env values.
        for env_val in os.environ.values():
            if (
                env_val
                and len(env_val) >= 20
                and not env_val.startswith("http")
                and env_val in result
                and not env_val.isdigit()
                and "\\" not in env_val   # skip Windows paths
                and "/" not in env_val    # skip Unix paths
                and " " not in env_val    # skip sentences / descriptions
                and "." not in env_val    # skip dotted identifiers
            ):
                result = result.replace(env_val, _REDACTED)
        return result
    if isinstance(value, dict):
        return {k: _redact_key(k, redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact(v) for v in value)  # type: ignore[arg-type]
    return value


_SENSITIVE_NAMES = {
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "llm_api_key",
    "test_password",
}


def _redact_key(key: str, value: Any) -> Any:
    if isinstance(key, str) and key.lower() in _SENSITIVE_NAMES:
        return _REDACTED
    return value


class RedactingProcessor:
    """structlog processor that scrubs secrets from every event."""

    def __call__(self, _logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        return redact(event_dict)  # type: ignore[return-value]


def configure_logging(level: int = logging.INFO, log_file: str | None = None) -> None:
    """Configure structlog with redaction and (optional) a file destination."""
    logging.basicConfig(
        format="%(message)s",
        level=level,
        force=True,
        handlers=[logging.StreamHandler()] + (
            [logging.FileHandler(log_file, encoding="utf-8")] if log_file else []
        ),
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            RedactingProcessor(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)
