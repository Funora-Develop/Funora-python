"""Решение о повторе запроса.

Модуль чистый и не спит. Он отвечает на вопрос «повторять ли и через сколько», а
ждать обязан вызывающий. Разделение нужно затем, что решение проверяется
тестами, а сон - нет: планировщик, который спит внутри, приходится проверять
часами вместо секунд, и в итоге не проверяют вовсе.

Условий для повтора три, и каждое отсекает свой вид беды.

Ошибка обязана быть помечена повторяемой. Это свойство класса ошибки, взятое из
спецификации: соединение не установилось - запрос не дошёл, повторять безопасно.

Решение не должно быть принято непроверенной сигнатурой. Признак ``provisional``
означает, что страницу такого вида никто не видел и вердикт вынесен по догадке
о тексте. Повторяться против догадки нельзя: если догадка неверна, клиент
долбится в исправную страницу, а если верна - подтверждает подозрение площадки.

Операция обязана быть безопасной. Повтор небезопасной операции может выполнить
её дважды, и списанные деньги вторым разом не возвращаются.

Разброс задержки полный, а не половинный, и это не мелочь. При нескольких
клиентах на одном адресе детерминированное отступление синхронизирует их, и
площадка видит не шесть вежливых клиентов, а один невежливый.
"""

from __future__ import annotations

import random as _random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from ._verdicts import PROVISIONAL_ATTR
from .errors import ConfigurationError, FunoraError
from .operations import Safety
from .retry import (
    DECISION_MATRIX,
    FALLBACK_POLICY,
    GLOBAL_MAX_ATTEMPTS,
    RETRY_POLICIES,
    RetryDecision,
    RetryPolicy,
)

__all__ = ["decide", "Safety", "Attempt", "policy_for", "plan_attempt"]


@dataclass(frozen=True, slots=True)
class Attempt:
    """Решение о следующей попытке.

    Attributes:
        retry (bool): Повторять ли запрос.
        delay_ms (int): Через сколько миллисекунд. Ноль, если повтора не будет.
        reason (str): Машиночитаемая причина решения. Уходит в журнал, поэтому
            по ней должно быть видно, какое из условий не выполнилось.
        policy (RetryPolicy): Политика, по которой принято решение.
    """

    retry: bool
    delay_ms: int
    reason: str
    policy: RetryPolicy


def policy_for(error: FunoraError) -> RetryPolicy:
    """Подбирает политику повторов для ошибки.

    Поиск идёт от самого точного к общему: сначала по устойчивому идентификатору
    самой ошибки, затем по идентификаторам её предков, затем запасная. Такой
    порядок нужен, чтобы добавление подтипа не меняло поведение молча: подтип без
    собственной политики наследует политику родителя, а не самую щедрую.

    Args:
        error (FunoraError): Экземпляр ошибки.

    Returns:
        RetryPolicy: Политика. Запасная, если ни один предок не описан.
    """
    for cls in type(error).__mro__:
        stable_id = getattr(cls, "stable_id", None)
        if stable_id in RETRY_POLICIES:
            return RETRY_POLICIES[stable_id]
    return FALLBACK_POLICY


def decide(error: Exception, safety: Safety) -> RetryDecision:
    """Находит строку матрицы, которая решает о повторе.

    Матрица - пересечение двух вещей: класса ошибки и безопасности операции.
    Реализация долго сводила её к одному условию «повторяем только чтения»: это
    строже контракта и потому безопасно, но расходится - второй SDK на той же
    трассе поступит иначе, и разойдутся они молча.

    Строки читаются сверху вниз, первая подошедшая решает. Порядок значим и
    задан спецификацией: первая строка отсекает неповторяемый класс ошибки
    независимо от операции.

    Args:
        error (Exception): Полученная ошибка.
        safety (Safety): Безопасность операции при повторе.
        idempotency_key (str | None): Ключ идемпотентности, если вызывающий его
            вычислил. Нужен идемпотентной операции: без него матрица считает её
            небезопасной.

    Returns:
        RetryDecision: Что матрица говорит о повторе.

    Raises:
        ConfigurationError: Если ни одна строка не подошла. Матрица обязана быть
            полной: сочетание без строки означает, что реализации решат сами.
    """
    retryable = bool(getattr(type(error), "retryable", False))
    effects = bool(getattr(type(error), "side_effects_possible", False))

    for row_retryable, row_safety, row_effects, result in DECISION_MATRIX:
        if row_retryable is not retryable:
            continue
        if row_safety is not None and row_safety != safety.value:
            continue
        if row_effects is not None and row_effects is not effects:
            continue
        return result

    raise ConfigurationError(
        f"матрица решения о повторе не покрывает сочетание: повторяемость "
        f"{retryable}, безопасность {safety.value}, побочный эффект {effects}. "
        "Неполная матрица означает, что реализации решат сами - и разойдутся"
    )


def plan_attempt(
    error: FunoraError,
    *,
    attempt: int,
    safety: Safety = Safety.SAFE,
    idempotency_key: str | None = None,
    retry_after_ms: int | None = None,
    rand: Callable[[], float] = _random.random,
) -> Attempt:
    """Решает, повторять ли запрос после ошибки.

    Args:
        error (FunoraError): Ошибка, из-за которой попытка не удалась.
        attempt (int): Номер завершившейся попытки, начиная с единицы.
        safety (Safety): Безопасность операции при повторе.
        retry_after_ms (int | None): Значение заголовка Retry-After в
            миллисекундах, если площадка его прислала.
        rand (Callable[[], float]): Источник случайности для разброса. Передаётся
            снаружи, чтобы решение можно было проверить: со случайностью внутри
            проверить нечего.

    Returns:
        Attempt: Решение вместе с причиной и применённой политикой.
    """
    policy = policy_for(error)

    if not type(error).retryable:
        return Attempt(False, 0, "error_not_retryable", policy)

    if getattr(error, PROVISIONAL_ATTR, False):
        return Attempt(False, 0, "verdict_provisional", policy)

    decision = decide(error, safety)
    if decision is RetryDecision.NEVER:
        return Attempt(False, 0, "error_not_retryable", policy)
    if decision is RetryDecision.RECONCILE_FIRST:
        # Повтор запрещён не потому, что бесполезен, а потому, что опасен:
        # операция небезопасна, и ошибка допускает побочный эффект. Прежде чем
        # повторять, надо прочитать фактическое положение дел. Это тот случай,
        # когда покупатель получает второе сообщение или второй ключ автовыдачи.
        return Attempt(False, 0, "reconcile_first", policy)
    if decision is RetryDecision.ALLOWED_WITH_KEY and idempotency_key is None:
        # Без ключа идемпотентная операция ведёт себя как небезопасная - так
        # сказано в самой матрице. Отказ здесь честнее повтора: ключ не
        # придумывается реализацией, его вычисляет вызывающий по составу,
        # объявленному у операции.
        return Attempt(False, 0, "idempotency_key_required", policy)

    limit = min(policy.max_attempts, GLOBAL_MAX_ATTEMPTS)
    if attempt >= limit:
        return Attempt(False, 0, f"attempts_exhausted:{limit}", policy)

    if retry_after_ms is not None and policy.respect_retry_after:
        # Верхняя граница обязательна: битое или враждебное значение вида
        # 86400 секунд вешает цикл опроса на сутки, и снаружи это неотличимо
        # от зависшего процесса.
        delay = min(retry_after_ms, policy.max_retry_after_ms)
        return Attempt(True, delay, "retry_after_header", policy)

    ideal = policy.base_ms * (policy.multiplier ** (attempt - 1))
    capped = min(int(ideal), policy.cap_ms)
    delay = int(capped * rand()) if policy.jitter == "full" else capped
    return Attempt(True, delay, "backoff", policy)


#: Значение, которым обозначается отсутствие ожидания.
NO_DELAY: Final[int] = 0
