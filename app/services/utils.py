from __future__ import annotations

import time
from typing import Any, Callable, TypeVar

import requests
from openai import RateLimitError

T = TypeVar("T")


def is_transient_error(exc: Exception) -> bool:
    """Determine if an error is transient (retryable) vs permanent."""
    # Timeout
    if isinstance(exc, requests.Timeout):
        return True
    if isinstance(exc, TimeoutError):
        return True

    # HTTP 5xx errors
    if isinstance(exc, requests.HTTPError):
        if 500 <= exc.response.status_code < 600:
            return True
        # 429 = rate limit
        if exc.response.status_code == 429:
            return True

    # OpenAI rate limit
    if isinstance(exc, RateLimitError):
        return True

    return False


def retry_with_backoff(
    func: Callable[..., T],
    *args: Any,
    max_retries: int = 2,
    backoff_base: float = 1.0,
    **kwargs: Any,
) -> T:
    """
    Retry a function with exponential backoff on transient errors.
    
    Args:
        func: The function to call
        *args: Positional arguments for func
        max_retries: Number of retries (0 = no retry, just fail fast)
        backoff_base: Base for exponential backoff (sleep = backoff_base ** attempt)
        **kwargs: Keyword arguments for func
    
    Returns:
        The result of func if successful
    
    Raises:
        The original exception if all retries exhausted or error is permanent
    """
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt == max_retries:
                # All retries exhausted, raise
                raise

            if not is_transient_error(exc):
                # Permanent error, don't retry
                raise

            # Transient error, backoff and retry
            wait_time = backoff_base ** attempt
            time.sleep(wait_time)

    # Should not reach here, but just in case
    raise last_exc or RuntimeError("Retry failed for unknown reason")
