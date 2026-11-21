"""Process-local rate limit.

v0 counts authenticated requests per API key inside one process. It does not
coordinate across machines. Set MIDPLANE_RATE_LIMIT_PER_MINUTE=0 to disable it.
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key_id: str) -> tuple[bool, int]:
        """Return (allowed, remaining). remaining is 0 when the call is rejected."""

        if self.per_minute <= 0:
            return True, 0
        now = time.monotonic()
        with self._lock:
            recent = [stamp for stamp in self._hits.get(key_id, []) if now - stamp < 60]
            if len(recent) >= self.per_minute:
                self._hits[key_id] = recent
                return False, 0
            recent.append(now)
            self._hits[key_id] = recent
            return True, self.per_minute - len(recent)
