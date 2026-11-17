"""
In-memory per-IP rate limiting for uploads.

A dict of IP -> list of timestamps. Good enough for one small server; it
resets when the process restarts, which is fine for a class demo.
"""
from __future__ import annotations

import threading
import time

from .config import UPLOADS_PER_HOUR_PER_IP
from .errors import RateLimitedError

_WINDOW_SECONDS = 3600
_hits: dict[str, list[float]] = {}
_lock = threading.Lock()


def check_and_record(ip: str, limit: int = UPLOADS_PER_HOUR_PER_IP) -> None:
    """
    Count one upload for `ip`, or raise RateLimitedError if it is over the limit.

    The message tells the student how long to wait.
    """
    now = time.time()
    with _lock:
        recent = [t for t in _hits.get(ip, []) if now - t < _WINDOW_SECONDS]
        if len(recent) >= limit:
            wait_minutes = max(1, int((_WINDOW_SECONDS - (now - recent[0])) // 60) + 1)
            _hits[ip] = recent
            raise RateLimitedError(
                f"You have used your {limit} uploads for this hour. "
                f"Please try again in about {wait_minutes} minute(s)."
            )
        recent.append(now)
        _hits[ip] = recent


def reset() -> None:
    """Clear all counters (used by the tests)."""
    with _lock:
        _hits.clear()
