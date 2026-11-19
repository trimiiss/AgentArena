from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from identity_service import security


class CountingHasher:
    """Records how many hashes run at the same moment."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def _work(self) -> None:
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(0.02)
        with self.lock:
            self.active -= 1

    def hash(self, _: str) -> str:
        self._work()
        return "hashed"

    def verify(self, *_: Any) -> bool:
        self._work()
        return True


@pytest.fixture()
def hasher(monkeypatch: pytest.MonkeyPatch) -> CountingHasher:
    counting = CountingHasher()
    monkeypatch.setattr(security, "_hasher", counting)
    monkeypatch.setattr(security, "_hash_slots", asyncio.Semaphore(security.MAX_CONCURRENT_HASHES))
    return counting


async def test_a_burst_of_logins_cannot_exhaust_memory(hasher: CountingHasher) -> None:
    """Regression: unbounded hashing got the pod OOM killed by 10 parallel logins."""
    await asyncio.gather(*(security.verify_password("stored", "password") for _ in range(30)))
    assert hasher.peak <= security.MAX_CONCURRENT_HASHES


async def test_registrations_share_the_same_limit(hasher: CountingHasher) -> None:
    await asyncio.gather(
        *(security.hash_password("password") for _ in range(15)),
        *(security.verify_password("stored", "password") for _ in range(15)),
    )
    assert hasher.peak <= security.MAX_CONCURRENT_HASHES


async def test_hashes_still_run_in_parallel(hasher: CountingHasher) -> None:
    await asyncio.gather(*(security.hash_password("password") for _ in range(8)))
    assert hasher.peak > 1


async def test_correct_password_verifies() -> None:
    stored = await security.hash_password("correct-horse-battery")
    assert await security.verify_password(stored, "correct-horse-battery") is True


async def test_wrong_password_fails() -> None:
    stored = await security.hash_password("correct-horse-battery")
    assert await security.verify_password(stored, "wrong") is False


async def test_unknown_user_fails_after_doing_the_same_work() -> None:
    assert await security.verify_password(None, "anything") is False
