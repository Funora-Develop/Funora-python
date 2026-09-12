"""Числовой бюджет не зависит от начала отсчёта и числа промежуточных чтений."""

from fractions import Fraction

import pytest

from funora import Budget
from funora._budget import TokenBucket, wait_until_ms
from funora._identity import Identity
from funora.budget import BUCKETS, RATE_LIMIT_RESPONSE, RequestClass


@pytest.mark.parametrize("start", [0, 1, 29, 100, 1001, 1000001, 9000000000])
def test_burst_wait_has_an_exact_trace_at_any_clock_origin(start):
    budget = Budget(names=("account",))
    now = start
    sent = []
    for _ in range(10):
        result = budget.reserve(now / 1000)
        if not result.granted:
            now += result.wait_ms
            assert budget.reserve(now / 1000).granted
        sent.append(now - start)
    assert sent == [0, 0, 0, 0, 0, 201, 401, 601, 801, 1001]


def test_refill_does_not_depend_on_intermediate_observations():
    once = TokenBucket(BUCKETS["write"], tokens=0)
    often = TokenBucket(BUCKETS["write"], tokens=0)
    for step in range(1, 1001):
        often.wait_for(step / 1000)
    once.wait_for(1)
    assert often.tokens == once.tokens == Fraction(167, 10000)
    assert often.wait_for(1) == once.wait_for(1)


def test_fractional_charges_do_not_leave_negative_dust():
    bucket = TokenBucket(BUCKETS["host"], tokens=1)
    for _ in range(10):
        assert bucket.wait_for(0, cost=0.1) == 0
        bucket.take(0, cost=0.1)
    assert bucket.tokens == 0
    assert bucket.wait_for(0) == 1001


@pytest.mark.parametrize("start", [0, 0.001, 1.029, 1000000.029])
def test_suspension_wait_is_exact(start):
    budget = Budget()
    budget.suspend((RequestClass.POLL,), until=start + 1)
    assert budget.reserve(start, request_class=RequestClass.POLL).wait_ms == 1001


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), -float("inf")])
def test_invalid_cost_cannot_change_any_budget_state(value):
    budget = Budget()
    before = [(b.tokens, b.allowance, b.updated_at) for b in budget._buckets]
    with pytest.raises(ValueError):
        budget.reserve(1, cost=value)
    assert [(b.tokens, b.allowance, b.updated_at) for b in budget._buckets] == before
    assert not budget._demanded_at


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_invalid_clock_is_rejected_even_without_buckets(value):
    budget = Budget(names=())
    with pytest.raises(ValueError):
        budget.reserve(value)
    assert not budget._demanded_at


def test_capacity_recovery_keeps_exact_decimal_steps():
    identity = Identity("exact")
    identity.note_limit(0.029)
    for _ in range(RATE_LIMIT_RESPONSE.successes_per_step * 2):
        identity.note_success()
    expected = Fraction(121, 200)
    assert identity.capacity_factor == expected
    assert all(bucket.factor == expected for bucket in identity.budget._buckets)
    assert wait_until_ms(0.029, identity.cooldown_until) == RATE_LIMIT_RESPONSE.cooldown_ms + 1


@pytest.mark.parametrize("now,until,expected", [(1, 1, 0), (2, 1, 0), (0.029, 1.029, 1001)])
def test_cooldown_rounds_once(now, until, expected):
    assert wait_until_ms(now, until) == expected


@pytest.mark.parametrize("method", ["wait_for", "take"])
def test_negative_direct_charge_does_not_refill_the_bucket(method):
    bucket = TokenBucket(BUCKETS["account"], tokens=0)
    with pytest.raises(ValueError):
        getattr(bucket, method)(100, cost=-1)
    assert bucket.tokens == 0
    assert bucket.updated_at == 0


def test_codegen_rejects_unknown_budget_arithmetic(tmp_path, monkeypatch):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import codegen

    monkeypatch.setattr(codegen, "_load", lambda *_: {"waiting": {"arithmetic": "binary_float"}})
    with pytest.raises(SystemExit, match="арифметика бюджета"):
        codegen.render_budget(tmp_path)
