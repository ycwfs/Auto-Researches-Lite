"""Shared HTTP helper with retry + backoff for paper-fetch and PDF calls.

Centralizes the resilience policy required across sources (req: robust paper
fetching):
  - retry transient network errors (timeouts, connection resets),
  - exponential backoff with jitter for HTTP 429 (honoring `Retry-After`) and 5xx,
  - a capped number of attempts with a clear final outcome,
  - never raises for normal non-2xx HTTP statuses — the Response is returned so
    callers can inspect `status_code` (only exhausted network errors raise).
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any

import requests

logger = logging.getLogger("far.http")

# Statuses worth retrying: rate-limit + transient server errors.
_RETRY_STATUSES = {429, 500, 502, 503, 504}


def _retry_after_seconds(resp: requests.Response, fallback: float) -> float:
    """Parse a `Retry-After` header (seconds form); fall back to `fallback`."""
    raw = resp.headers.get("Retry-After")
    if raw:
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return fallback
    return fallback


def request_with_retry(
    method: str,
    url: str,
    *,
    max_attempts: int = 4,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    timeout: float = 20.0,
    session: requests.Session | None = None,
    **kwargs: Any,
) -> requests.Response:
    """Issue an HTTP request, retrying transient failures with backoff.

    Args:
        method: HTTP verb (e.g. "GET", "POST").
        url: Target URL.
        max_attempts: Total attempts (>= 1) before giving up.
        base_delay: Initial backoff in seconds; doubles each retry.
        max_delay: Upper bound for a single backoff sleep.
        timeout: Per-request timeout in seconds.
        session: Optional requests.Session to reuse.
        **kwargs: Forwarded to requests (headers, params, json, ...).

    Returns:
        The final `requests.Response` (possibly a non-2xx the caller inspects).

    Raises:
        requests.RequestException: When every attempt raised a network error.
    """
    caller = session or requests
    delay = base_delay
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = caller.request(method, url, timeout=timeout, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            logger.warning(
                "%s %s network error (attempt %d/%d): %s",
                method, url, attempt, max_attempts, exc,
            )
            if attempt == max_attempts:
                raise
            time.sleep(min(delay, max_delay) + random.uniform(0, 0.5))
            delay *= 2
            continue
        except requests.RequestException as exc:
            # Non-transient request errors (e.g. invalid URL) — do not retry.
            logger.warning("%s %s request error: %s", method, url, exc)
            raise

        if resp.status_code in _RETRY_STATUSES and attempt < max_attempts:
            wait = _retry_after_seconds(resp, min(delay, max_delay))
            logger.info(
                "%s %s -> %d; retrying in %.1fs (attempt %d/%d)",
                method, url, resp.status_code, wait, attempt, max_attempts,
            )
            time.sleep(wait + random.uniform(0, 0.5))
            delay *= 2
            continue
        return resp

    # Loop only exits via return/raise above except when the last attempt was a
    # retryable status; surface that final response.
    if last_exc is not None:
        raise last_exc
    return resp
