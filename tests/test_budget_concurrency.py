"""Общий бюджет выдерживает одновременные обращения нескольких клиентов."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from time import sleep

from funora._budget import Budget, TokenBucket
from funora.budget import BUCKETS


def test_parallel_reservations_cannot_spend_the_same_tokens(monkeypatch) -> None:
    original = TokenBucket.wait_for

    def interrupted_check(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        # Переключение потока между проверкой наличия и списанием токенов.
        sleep(0.001)
        return result

    monkeypatch.setattr(TokenBucket, "wait_for", interrupted_check)
    budget = Budget()
    start = Barrier(16)

    def reserve(_):
        start.wait(timeout=5)
        return budget.reserve(100.0).granted

    with ThreadPoolExecutor(max_workers=16) as pool:
        granted = sum(pool.map(reserve, range(16)))
    assert granted == min(BUCKETS["host"].burst, BUCKETS["account"].burst)
    assert all(bucket.tokens >= 0 and bucket.allowance >= 0 for bucket in budget._buckets)


def test_delayed_timestamp_does_not_refill_the_same_interval_twice() -> None:
    bucket = TokenBucket(BUCKETS["account"])
    bucket.take(100.0, cost=float(BUCKETS["account"].capacity))
    bucket.wait_for(50.0)
    bucket.wait_for(100.0)
    assert bucket.tokens == 0
