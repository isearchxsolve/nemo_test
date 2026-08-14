"""Retry helpers with exponential backoff.

Kept tiny and dependency-free so every external action (HTTP, LLM, browser
action, health check) can be retried uniformly.
"""
from __future__ import annotations

import functools
import random
import time
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

__all__ = ["retry"]

P = ParamSpec("P")
T = TypeVar("T")


def retry(max_attempts: int = 3, base_delay: float = 0.5, max_delay: float = 8.0):
    """Decorator that retries the wrapped callable with exponential backoff.

    The retry covers any exception. Errors are allowed to propagate after the
    last attempt. Between attempts we sleep ``base_delay * 2**i`` (capped by
    ``max_delay``) with a small jitter.
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            last_exc: BaseException | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 - deliberately broad
                    last_exc = exc
                    if attempt == max_attempts:
                        break
                    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                    delay += random.uniform(0, 0.1) * delay
                    time.sleep(delay)
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
