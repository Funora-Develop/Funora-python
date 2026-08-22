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

    # Причины разные, и разница взята из матрицы, а не придумана. Идемпотентная
    # операция без ключа ведёт себя как небезопасная - так сказано в самой
    # матрице. Небезопасная при ошибке, допускающей побочный эффект, требует
    # сверки состояния: это тот случай, когда покупатель получает второе
    # сообщение.
    expected = {
        Safety.IDEMPOTENT: "idempotency_key_required",
        Safety.UNSAFE: "reconcile_first",
    }[safety]
    assert plan.reason == expected


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


def test_every_matrix_row_is_reachable() -> None:
    """Проверяет, что каждая строка матрицы решает хоть какое-то сочетание.

    Строка, до которой не доходит ни одно сочетание, - это правило, записанное и
    не действующее. Прежде реализация сводила пять строк к одному условию
    «повторяем только чтения», и три строки из пяти не действовали никогда, при
    том что были объявлены контрактом.

    Returns:
        None
    """
    from funora._retry import decide
    from funora.retry import DECISION_MATRIX

    class _Retryable(Exception):
        retryable = True
        side_effects_possible = True

    class _RetryableClean(Exception):
        retryable = True
        side_effects_possible = False

    class _Final(Exception):
        retryable = False
        side_effects_possible = False

    reached = set()
    for error in (_Retryable("x"), _RetryableClean("x"), _Final("x")):
        for safety in Safety:
            reached.add(decide(error, safety))

    declared = {row[-1] for row in DECISION_MATRIX}
    assert reached == declared, (
        f"недостижимые решения матрицы: {sorted(declared - reached)} - "
        "правило записано и не действует"
    )


def test_matrix_covers_every_combination() -> None:
    """Проверяет полноту матрицы.

    Сочетание без строки означает, что реализации решат сами, - и разойдутся
    именно там, где расхождение дороже всего. Реализация отказывается вслух,
    вместо того чтобы выбрать безопасное по умолчанию: молчаливый выбор скрыл бы
    дыру в контракте.

    Returns:
        None
    """
    from funora._retry import decide

    for retryable in (True, False):
        for effects in (True, False):
            error_type = type(
                "Проба",
                (Exception,),
                {"retryable": retryable, "side_effects_possible": effects},
            )
            for safety in Safety:
                decide(error_type("x"), safety)


def test_idempotent_operation_retries_with_a_key() -> None:
    """Проверяет строку матрицы, которая прежде не действовала никогда.

    Идемпотентная операция с ключом повторяема - так говорит контракт. Прежняя
    реализация отказывала ей наравне с небезопасной, то есть была строже
    контракта: безопасно, но расходится - второй SDK на той же трассе повторит.

    Returns:
        None
    """
    without = plan_attempt(TimeoutError("x"), attempt=1, safety=Safety.IDEMPOTENT, rand=_no_jitter)
    assert not without.retry
    assert without.reason == "idempotency_key_required"

    with_key = plan_attempt(
        TimeoutError("x"),
        attempt=1,
        safety=Safety.IDEMPOTENT,
        idempotency_key="ключ",
        rand=_no_jitter,
    )
    assert with_key.retry, "с ключом повтор разрешён матрицей"


def test_unsafe_operation_retries_when_no_side_effect_was_possible() -> None:
    """Проверяет вторую строку, которая прежде не действовала.

    Ошибка, гарантированно возникшая ДО отправки, побочного эффекта не имела -
    повторять можно даже небезопасную операцию. Пример из спецификации: местный
    отказ бюджета.

    Returns:
        None
    """
    from funora.errors import BudgetExhaustedError, NetworkError

    assert not BudgetExhaustedError.side_effects_possible
    assert NetworkError.side_effects_possible

    plan = plan_attempt(
        NetworkError("оборвалось"), attempt=1, safety=Safety.UNSAFE, rand=_no_jitter
    )
    assert not plan.retry
    assert plan.reason == "reconcile_first", (
        "сетевой отказ допускает побочный эффект: повторять небезопасную операцию по нему нельзя"
    )


def test_safety_is_looked_up_not_assumed() -> None:
    """Проверяет, что безопасность берётся из таблицы операций.

    Все выполняемые сегодня операции - чтения, и константа Safety.SAFE в цикле
    совпадала бы с таблицей. Совпадение это временное: первая же операция записи
    получила бы повтор наравне с чтением, потому что константа о ней не знает, -
    то есть покупателю ушло бы второе сообщение.

    Проверяются оба конца: возможность с объявленной безопасностью даёт
    объявленное, возможность без операции даёт unsafe. Второе важнее: запрос,
    которого контракт не описывает, повторять нельзя - неизвестное не
    повторяют.

    Returns:
        None
    """
    from funora._engine import _safety_of
    from funora.capabilities import Capability
    from funora.operations import OPERATIONS

    by_capability = {op.capability: op for op in OPERATIONS.values()}
    checked = 0
    for capability in Capability:
        operation = by_capability.get(capability.value)
        if operation is None:
            continue
        assert _safety_of(capability) is operation.safety, (
            f"{capability.value}: цикл считает операцию {_safety_of(capability)}, "
            f"спецификация объявляет {operation.safety}"
        )
        checked += 1

    assert checked > 5, "возможностей с операциями не набралось - проверять нечего"

    # Небезопасные операции в таблице есть, и на них константа Safety.SAFE
    # разошлась бы с объявленным.
    unsafe = [op for op in OPERATIONS.values() if op.safety is not Safety.SAFE]
    assert unsafe, "в таблице нет ни одной небезопасной операции - проверка слепа"
