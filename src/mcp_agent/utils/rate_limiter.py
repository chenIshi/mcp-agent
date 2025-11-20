"""
Async rate limiter utilities used to throttle outbound model calls.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Deque


class AsyncRateLimiter:
    """
    Simple token bucket style limiter supporting ``max_calls`` within ``period`` seconds.
    """

    def __init__(self, max_calls: int, period: float) -> None:
        if max_calls <= 0:
            raise ValueError("max_calls must be positive")
        if period <= 0:
            raise ValueError("period must be positive")

        self.max_calls = max_calls
        self.period = period
        self._events: Deque[float] = deque()
        self._lock: asyncio.Lock | None = None

    async def acquire(self) -> None:
        """
        Block until a call slot becomes available.
        """
        if self._lock is None:
            self._lock = asyncio.Lock()

        while True:
            async with self._lock:
                loop = asyncio.get_running_loop()
                now = loop.time()

                while self._events and now - self._events[0] >= self.period:
                    self._events.popleft()

                if len(self._events) < self.max_calls:
                    self._events.append(now)
                    return

                wait_time = self.period - (now - self._events[0])

            # Sleep outside the lock to avoid blocking other coroutines waiting on the limiter
            await asyncio.sleep(max(wait_time, 0))

    async def __aenter__(self) -> "AsyncRateLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None
