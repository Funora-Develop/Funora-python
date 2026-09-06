"""У аккаунтов отдельные квоты, общий сетевой предел остаётся атомарным."""

from concurrent.futures import ThreadPoolExecutor

from funora._budget import Budget
from funora._identity import Identity
from funora.budget import RequestClass


def test_accounts_have_separate_bursts_under_the_same_host_limit():
    parent = Budget()
    first, second = parent.for_account("first"), parent.for_account("second")
    assert first is parent.for_account("first")
    assert first is not second
    assert all(first.reserve(0).granted for _ in range(5))
    assert not first.reserve(0).granted
    assert all(second.reserve(0).granted for _ in range(5))
    assert not parent.for_account("third").reserve(0).granted


def test_concurrent_accounts_cannot_exceed_the_shared_burst():
    parent = Budget()
    with ThreadPoolExecutor(max_workers=20) as pool:
        answers = list(pool.map(lambda n: parent.for_account(str(n)).reserve(0).granted, range(40)))
    assert sum(answers) == 10


def test_rate_limit_scales_existing_and_future_account_buckets():
    parent = Budget()
    child = parent.for_account("a")
    identity = Identity("unit", budget=parent)
    identity.note_limit(1)
    later = parent.for_account("b")
    for budget in (child, later):
        assert all(bucket.factor == identity.capacity_factor for bucket in budget._buckets)


def test_suspension_is_shared_across_account_views():
    parent = Budget()
    a, b = parent.for_account("a"), parent.for_account("b")
    a.suspend((RequestClass.MONITORING,), until=5)
    assert not b.reserve(1, request_class=RequestClass.MONITORING).granted
    assert b.reserve(1, request_class=RequestClass.INTERACTIVE).granted
