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
from enum import StrEnum
from typing import Final

from ._verdicts import PROVISIONAL_ATTR
from .errors import FunoraError
from .retry import FALLBACK_POLICY, GLOBAL_MAX_ATTEMPTS, RETRY_POLICIES, RetryPolicy

__all__ = ["Safety", "Attempt", "policy_for", "plan_attempt"]


class Safety(StrEnum):
    """Безопасность операции при повторе.

    Значения взяты из spec/services: повтор допустим не для всякой операции, и
    решение об этом принимается по объявлению, а не по догадке вызывающего.
    """

    #: Чтение. Повторять можно свободно в пределах бюджета.
    SAFE = "safe"

    #: Изменение, повтор которого требует ключа идемпотентности.
    IDEMPOTENT = "idempotent"

    #: Изменение, повтор которого может выполнить операцию дважды.
    UNSAFE = "unsafe"


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


def plan_attempt(
    error: FunoraError,
    *,
    attempt: int,
    safety: Safety = Safety.SAFE,
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

    if safety is not Safety.SAFE:
        return Attempt(False, 0, f"operation_not_safe:{safety}", policy)

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
