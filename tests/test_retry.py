"""Проверки решения о повторе и ворот возможности.

Набор проверяет не арифметику задержек, а условия отказа. Арифметика видна из
кода, а условия - нет: каждое из них отсекает свой вид беды, и выпадение любого
проявится не сразу и не в тестах.
"""

from __future__ import annotations

import pytest

from funora._gate import check_capability
from funora._retry import Safety, plan_attempt, policy_for
from funora.capabilities import CAPABILITY_INITIAL, Capability, CapabilityState
from funora.errors import (
    AccessBlockedError,
    ExperimentalCapabilityError,
    FunoraError,
    NetworkError,
    RateLimitedError,
    TimeoutError,
    UnsupportedCapabilityError,
)
from funora.retry import FALLBACK_POLICY, GLOBAL_MAX_ATTEMPTS, RETRY_POLICIES


def _no_jitter() -> float:
    """Возвращает множитель разброса, не меняющий задержку.

    Returns:
        float: Единица.
    """
    return 1.0


def test_unretryable_error_is_never_repeated() -> None:
    """Проверяет отказ по свойству класса ошибки.

    Returns:
        None
    """
    plan = plan_attempt(AccessBlockedError("x"), attempt=1, rand=_no_jitter)
    assert not plan.retry
    assert plan.reason == "error_not_retryable"


def test_provisional_verdict_is_not_repeated() -> None:
    """Проверяет отказ по признаку непроверенной сигнатуры.

    Признак означает, что страницы такого вида никто не видел и вердикт вынесен
    по догадке о тексте. Повторяться против догадки нельзя дважды: если она
    неверна, клиент долбится в исправную страницу, а если верна - подтверждает
    подозрение площадки.

    Проверка берёт повторяемую ошибку намеренно. На неповторяемой ветка
    provisional не сработала бы: отказ наступил бы раньше, и условие осталось бы
    непроверенным, а набор - зелёным.

    Returns:
        None
    """
    error = NetworkError("x")
    assert type(error).retryable, "проверка бессмысленна на неповторяемой ошибке"

    assert plan_attempt(error, attempt=1, rand=_no_jitter).retry

    error.provisional = True  # type: ignore[attr-defined]
    plan = plan_attempt(error, attempt=1, rand=_no_jitter)
    assert not plan.retry
    assert plan.reason == "verdict_provisional"


@pytest.mark.parametrize("safety", [Safety.IDEMPOTENT, Safety.UNSAFE])
def test_unsafe_operation_is_not_repeated(safety: Safety) -> None:
    """Проверяет отказ по безопасности операции.

    Повтор небезопасной операции может выполнить её дважды, и списанные вторым
    разом деньги не возвращаются.

    Args:
        safety (Safety): Безопасность операции.

    Returns:
        None
    """
    plan = plan_attempt(TimeoutError("x"), attempt=1, safety=safety, rand=_no_jitter)
    assert not plan.retry
    assert plan.reason.startswith("operation_not_safe")


def test_attempts_are_bounded() -> None:
    """Проверяет исчерпание попыток.

    Returns:
        None
    """
    policy = RETRY_POLICIES["funora.transport.timeout"]
    limit = min(policy.max_attempts, GLOBAL_MAX_ATTEMPTS)

    assert plan_attempt(TimeoutError("x"), attempt=limit - 1, rand=_no_jitter).retry
    plan = plan_attempt(TimeoutError("x"), attempt=limit, rand=_no_jitter)
    assert not plan.retry
    assert plan.reason.startswith("attempts_exhausted")


def test_retry_after_is_capped() -> None:
    """Проверяет верхнюю границу уважения заголовка Retry-After.

    Без границы битое или враждебное значение вида суток вешает цикл опроса, и
    снаружи это неотличимо от зависшего процесса.

    Returns:
        None
    """
    policy = RETRY_POLICIES["funora.transport.rate_limited"]
    plan = plan_attempt(
        RateLimitedError("x"),
        attempt=1,
        retry_after_ms=86_400_000,
        rand=_no_jitter,
    )
    assert plan.retry
    assert plan.delay_ms == policy.max_retry_after_ms
    assert plan.reason == "retry_after_header"


def test_delay_never_exceeds_cap() -> None:
    """Проверяет, что задержка не превышает потолок политики.

    Returns:
        None
    """
    policy = RETRY_POLICIES["funora.transport.timeout"]
    for attempt in range(1, GLOBAL_MAX_ATTEMPTS + 1):
        plan = plan_attempt(TimeoutError("x"), attempt=attempt, rand=_no_jitter)
        assert plan.delay_ms <= policy.cap_ms


def test_jitter_spreads_the_delay() -> None:
    """Проверяет, что разброс действительно применяется.

    Разброс полный, а не половинный: при нескольких клиентах на одном адресе
    детерминированное отступление синхронизирует их, и площадка видит не шесть
    вежливых клиентов, а один невежливый.

    Returns:
        None
    """
    full = plan_attempt(TimeoutError("x"), attempt=1, rand=lambda: 1.0)
    none = plan_attempt(TimeoutError("x"), attempt=1, rand=lambda: 0.0)
    assert none.delay_ms == 0
    assert full.delay_ms > 0


def test_unknown_error_gets_the_fallback_policy() -> None:
    """Проверяет подбор политики для класса без собственной записи.

    Запасная политика строже конкретных намеренно: неизвестный класс отказа не
    повод быть смелее. Реализация, подставляющая здесь самую щедрую политику,
    получает самое агрессивное поведение как раз тогда, когда меньше всего
    понимает происходящее.

    Returns:
        None
    """

    class СтранныйОтказ(FunoraError):
        """Ошибка, которой нет в спецификации."""

    assert policy_for(СтранныйОтказ("x")) is FALLBACK_POLICY


def test_policy_is_inherited_from_the_nearest_ancestor() -> None:
    """Проверяет, что подтип наследует политику родителя, а не запасную.

    Иначе добавление подтипа молча меняло бы поведение: у него не было бы своей
    записи, и он получил бы запасную политику вместо родительской.

    Returns:
        None
    """
    assert policy_for(TimeoutError("x")).stable_id == "funora.transport.timeout"
    assert policy_for(NetworkError("x")).stable_id == "funora.transport.network"


def test_gate_lets_unknown_through() -> None:
    """Проверяет, что невыясненное состояние не запрещает вызов.

    Состояние означает «ещё не выяснено», а не «нет». Блокировать по нему значило
    бы запрещать работу из-за собственной неуверенности, и SDK выглядел бы как не
    умеющий ничего.

    Returns:
        None
    """
    assert (
        check_capability(Capability.ORDERS_LIST, state=CapabilityState.UNKNOWN)
        is CapabilityState.UNKNOWN
    )


def test_gate_blocks_only_on_positive_evidence() -> None:
    """Проверяет, что отказ наступает только при свидетельстве отсутствия.

    Returns:
        None
    """
    with pytest.raises(UnsupportedCapabilityError):
        check_capability(Capability.ORDERS_LIST, state=CapabilityState.UNSUPPORTED)

    with pytest.raises(UnsupportedCapabilityError):
        check_capability(Capability.ORDERS_LIST, state=CapabilityState.UNSUPPORTED, opted_in=True)


def test_gate_asks_the_second_question_too() -> None:
    """Проверяет, что ворота задают оба вопроса, а не один.

    Ошибиться здесь легко ровно одним способом: спросить «работает ли
    возможность» вместо «можно ли звать её прямо сейчас». У состояния
    experimental ответы противоположны, и первая версия порождённого модуля
    возможностей на этом и споткнулась.

    Returns:
        None
    """
    with pytest.raises(ExperimentalCapabilityError):
        check_capability(Capability.ORDERS_LIST, state=CapabilityState.EXPERIMENTAL)

    assert (
        check_capability(Capability.ORDERS_LIST, state=CapabilityState.EXPERIMENTAL, opted_in=True)
        is CapabilityState.EXPERIMENTAL
    )


def test_gate_uses_initial_state_by_default() -> None:
    """Проверяет, что без переданного состояния берётся начальное.

    Returns:
        None
    """
    capability = Capability.ORDERS_LIST
    assert check_capability(capability) is CAPABILITY_INITIAL[capability]
