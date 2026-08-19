"""Проверки бюджета исходящих запросов.

Времени здесь нет: оно передаётся числом. Бюджет, смотрящий на настоящие часы,
проверяется настоящими секундами, а такая проверка живёт ровно до первого раза,
когда она мешает.

Набор проверяет свойства, а не числа. Числа взяты из спецификации и там помечены
провизорными: закрепить их тестом значило бы превратить уточнение наблюдением в
поломку сборки.
"""

from __future__ import annotations

import pytest

from funora._budget import Budget, TokenBucket
from funora.budget import BUCKETS, MAX_WAIT_MS
from funora.errors import BudgetExhaustedError, TransportError


def test_full_bucket_grants_immediately() -> None:
    """Проверяет, что полное ведро выдаёт бюджет без ожидания.

    Returns:
        None
    """
    assert Budget().reserve(0.0).granted


def test_capacity_is_the_narrowest_bucket() -> None:
    """Проверяет, что подряд выдаётся не больше самого узкого ведра.

    Вёдра вложены, и общее ограничение задаёт то, которое кончится первым.

    Returns:
        None
    """
    budget = Budget()
    granted = 0
    while budget.reserve(0.0).granted:
        granted += 1
        assert granted <= 1000, "ведро не кончается, значит расход не работает"

    assert granted == min(BUCKETS["host"].capacity, BUCKETS["account"].capacity)


def test_refusal_names_the_bucket() -> None:
    """Проверяет, что отказ говорит, какое ведро кончилось.

    Без имени ведра непонятно, ограничивает нас общий предел или предел
    аккаунта, а это разные лечения: в первом случае мешают соседние аккаунты в
    том же процессе, во втором - собственная частота.

    Returns:
        None
    """
    budget = Budget()
    while budget.reserve(0.0).granted:
        pass

    refusal = budget.reserve(0.0)
    assert not refusal.granted
    assert refusal.bucket in BUCKETS
    assert refusal.wait_ms > 0


def test_nothing_is_spent_on_refusal() -> None:
    """Проверяет, что отказавший запрос не тратит чужой запас.

    Занимать надо либо во всех вёдрах сразу, либо ни в одном. Частичный расход
    означал бы утечку бюджета при частых отказах: общее ведро пустеет, а запросы
    при этом не уходят.

    Returns:
        None
    """
    budget = Budget()
    for _ in range(BUCKETS["account"].capacity):
        assert budget.reserve(0.0).granted

    first = budget.reserve(0.0)
    for _ in range(20):
        budget.reserve(0.0)
    later = budget.reserve(0.0)

    assert not first.granted
    assert not later.granted
    assert later.wait_ms == first.wait_ms, "отказы потратили запас общего ведра"


def test_bucket_refills_over_time() -> None:
    """Проверяет восполнение запаса.

    Returns:
        None
    """
    budget = Budget()
    while budget.reserve(0.0).granted:
        pass

    wait = budget.reserve(0.0).wait_ms
    assert budget.reserve(wait / 1000).granted


def test_refill_never_exceeds_capacity() -> None:
    """Проверяет, что ожидание не накапливает запас сверх ёмкости.

    Иначе сутки простоя дали бы право на залп в тысячи запросов, и первый же
    запуск после паузы выглядел бы для площадки как атака.

    Returns:
        None
    """
    bucket = TokenBucket(BUCKETS["account"])
    bucket.take(0.0, cost=float(BUCKETS["account"].capacity))
    assert bucket.wait_for(86_400.0) == 0
    assert bucket.tokens <= BUCKETS["account"].capacity


def test_clock_going_backwards_does_not_grant_free_budget() -> None:
    """Проверяет защиту от времени, идущего назад.

    Монотонные часы назад не идут, но отрицательный интервал молча выдал бы
    бесконечный бюджет, и разбирательство было бы долгим.

    Returns:
        None
    """
    bucket = TokenBucket(BUCKETS["account"])
    bucket.take(100.0, cost=float(BUCKETS["account"].capacity))
    before = bucket.tokens
    bucket.wait_for(50.0)
    assert bucket.tokens <= before


def test_reading_never_exhausts_the_budget_outright() -> None:
    """Проверяет свойство нынешних чисел: чтение только ждёт, но не отказывает.

    Ведро аккаунта восстанавливает такт быстрее, чем предел ожидания, поэтому
    при стоимости в один запрос отказ недостижим. Это свойство подобранных чисел,
    а не гарантия контракта: числа провизорные, и при их уточнении проверка
    сообщит, что свойство пропало.

    Returns:
        None
    """
    budget = Budget()
    while budget.reserve(0.0).granted:
        pass

    reservation = budget.require(0.0)
    assert not reservation.granted
    assert reservation.wait_ms <= MAX_WAIT_MS


def test_slow_bucket_refuses_instead_of_waiting_forever() -> None:
    """Проверяет отказ там, где ожидание было бы слишком долгим.

    У ведра записи такт восстанавливается около минуты, а ждать разрешено пять
    секунд. Ждать дольше означало бы, что вызов снаружи неотличим от зависшего.

    Returns:
        None
    """
    budget = Budget(names=("write",))
    while budget.reserve(0.0).granted:
        pass

    with pytest.raises(BudgetExhaustedError) as exc:
        budget.require(0.0)
    assert "не отправлен" in str(exc.value)


def test_budget_error_is_not_a_transport_error() -> None:
    """Проверяет, что исчерпание бюджета не выглядит как отказ площадки.

    Это локальное решение SDK не отправлять запрос. Попади оно под транспортную
    ветку, обработчик, ловящий отказы сети, начал бы повторять запросы ровно
    тогда, когда бюджет уже кончился.

    Returns:
        None
    """
    assert not issubclass(BudgetExhaustedError, TransportError)
