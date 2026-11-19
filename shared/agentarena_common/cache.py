"""A small async TTL cache with LRU eviction and per-key load coalescing."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable


class AsyncTTLCache[V]:
    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_entries: int = 1024,
        clock: Callable[[], float] = time.monotonic,
        on_lookup: Callable[[bool], None] | None = None,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._on_lookup = on_lookup or (lambda _: None)
        self._entries: OrderedDict[Hashable, tuple[float, V]] = OrderedDict()
        self._locks: dict[Hashable, asyncio.Lock] = {}
        self.hits = 0
        self.misses = 0

    def _fresh(self, key: Hashable) -> tuple[bool, V | None]:
        entry = self._entries.get(key)
        if entry is not None and entry[0] > self._clock():
            self._entries.move_to_end(key)
            return True, entry[1]
        return False, None

    def _record(self, hit: bool) -> None:
        if hit:
            self.hits += 1
        else:
            self.misses += 1
        self._on_lookup(hit)

    async def get_or_load(self, key: Hashable, load: Callable[[], Awaitable[V]]) -> V:
        found, value = self._fresh(key)
        if found:
            self._record(True)
            return value  # type: ignore[return-value]
        # One loader per key, so a burst of jobs makes a single downstream call.
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            found, value = self._fresh(key)
            if found:
                self._record(True)
                return value  # type: ignore[return-value]
            self._record(False)
            loaded = await load()
            self._entries[key] = (self._clock() + self.ttl_seconds, loaded)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        self._locks.pop(key, None)
        return loaded
