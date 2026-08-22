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


def test_the_narrowest_bucket_governs_the_pace() -> None:
    """Проверяет, что темп задаёт самое узкое из вложенных вёдер.

    Прежняя редакция считала, сколько запросов проходит подряд, и ждала
    ёмкости узкого ведра. С появлением второго предела - залпа - это перестало
    быть правдой: подряд проходит burst, а не capacity, сколько бы ни было
    накоплено.

    Смысл проверки от этого не изменился. Вложенность значит, что ограничивает
    узкое: за долгий отрезок клиент получает столько, сколько отпускает
    account, а не host.

    Returns:
        None
    """
    budget = Budget()
    granted = 0
    now = 0.0
    step = 0.25

    # Сто виртуальных секунд с мелким шагом: шаг короче окна залпа, поэтому
    # право на залп успевает восстанавливаться, а запас нет.
    while now < 100.0:
        if budget.reserve(now).granted:
            granted += 1
        now += step

    account = BUCKETS["account"]
    expected = account.capacity + account.refill_per_second * 100.0
    assert abs(granted - expected) <= 2, (
        f"за сто секунд выдано {granted}, а узкое ведро отпускает около "
        f"{expected:.0f}. Значит темп задаёт не оно"
    )

    host = BUCKETS["host"]
    assert granted < host.capacity + host.refill_per_second * 100.0, (
        "выдано столько, сколько отпустило бы общее ведро: вложенность не работает"
    )


def test_burst_caps_what_goes_out_back_to_back() -> None:
    """Проверяет второй предел: залп не зависит от накопленного.

    Ведро, полное до краёв, всё равно не выпустит больше burst запросов подряд.
    Без этого предела клиент, простоявший минуту, выпускает шестьдесят запросов
    в одну секунду - и первым от собственного залпа страдает сам аккаунт.

    Returns:
        None
    """
    budget = Budget()
    granted = 0
    while budget.reserve(0.0).granted:
        granted += 1
        assert granted <= 1000, "залп не ограничивает ничего"

    assert granted == min(BUCKETS["host"].burst, BUCKETS["account"].burst), (
        f"подряд прошло {granted} запросов при объявленном залпе "
        f"{min(BUCKETS['host'].burst, BUCKETS['account'].burst)}"
    )


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
    """Проверяет, что отказ не тратит бюджет.

    Частичный расход означал бы, что отказавший запрос всё равно потратил чужой
    запас, и при частых отказах бюджет утекал бы в никуда.

    Returns:
        None
    """
    budget = Budget()
    while budget.reserve(0.0).granted:
        pass

    first = budget.reserve(0.0)
    for _ in range(20):
        budget.reserve(0.0)
    later = budget.reserve(0.0)

    assert not first.granted
    assert not later.granted
    assert later.wait_ms <= first.wait_ms, (
        "двадцать отказов удлинили ожидание: отказ тратит бюджет"
    )


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
    """Проверяет, что медленное ведро отказывает, а не заставляет ждать вечно.

    Returns:
        None
    """
    budget = Budget(names=("write",))
    now = 0.0
    while True:
        reservation = budget.reserve(now)
        if reservation.granted:
            continue
        if reservation.wait_ms > MAX_WAIT_MS:
            break
        # Ждём только право на залп: запас у этого ведра восстанавливается раз
        # в минуту, и до него дело дойдёт быстро.
        now += reservation.wait_ms / 1000

    with pytest.raises(BudgetExhaustedError) as exc:
        budget.require(now)
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
