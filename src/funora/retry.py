"""Политики повторов.

Файл порождён из спецификации, править его руками нельзя: правка исчезнет при
следующей сборке. Источник - spec/protocol/retry-policy.yaml в репозитории Funora-spec.
Перестроить: python tools/codegen.py

Числа не выбираются реализацией. Отступление, синхронизированное между
шестью SDK, превращается в согласованную волну запросов, и площадка
видит не шесть вежливых клиентов, а один невежливый.

Запасная политика намеренно строже конкретных: неизвестный класс отказа
не повод быть смелее.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = ["RetryPolicy", "RETRY_POLICIES", "FALLBACK_POLICY", "GLOBAL_MAX_ATTEMPTS"]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Политика повторов для одного класса ошибки.

    Attributes:
        stable_id (str): Устойчивый идентификатор класса ошибки.
        max_attempts (int): Сколько попыток допустимо всего, включая первую.
        base_ms (int): Основа задержки, миллисекунды.
        multiplier (float): Во сколько раз растёт задержка.
        cap_ms (int): Потолок задержки, миллисекунды.
        jitter (str): Вид разброса.
        respect_retry_after (bool): Уважать ли заголовок Retry-After.
        max_retry_after_ms (int): Верхняя граница уважения заголовка.
        fail_closed (bool): Останавливать ли работу до вмешательства.
        account_scoped (bool): Действует ли ограничение на весь аккаунт.
    """

    stable_id: str
    max_attempts: int
    base_ms: int
    multiplier: float
    cap_ms: int
    jitter: str
    respect_retry_after: bool
    max_retry_after_ms: int
    fail_closed: bool
    account_scoped: bool


#: Политика по устойчивому идентификатору класса ошибки.
RETRY_POLICIES: Final[dict[str, RetryPolicy]] = {
    "funora.transport": RetryPolicy(
        stable_id="funora.transport",
        max_attempts=2,
        base_ms=1000,
        multiplier=2.0,
        cap_ms=15000,
        jitter="full",
        respect_retry_after=True,
        max_retry_after_ms=300000,
        fail_closed=False,
        account_scoped=False,
    ),
    "funora.transport.timeout": RetryPolicy(
        stable_id="funora.transport.timeout",
        max_attempts=3,
        base_ms=1000,
        multiplier=2.0,
        cap_ms=30000,
        jitter="full",
        respect_retry_after=False,
        max_retry_after_ms=300000,
        fail_closed=False,
        account_scoped=False,
    ),
    "funora.transport.network": RetryPolicy(
        stable_id="funora.transport.network",
        max_attempts=3,
        base_ms=500,
        multiplier=2.0,
        cap_ms=30000,
        jitter="full",
        respect_retry_after=False,
        max_retry_after_ms=300000,
        fail_closed=False,
        account_scoped=False,
    ),
    "funora.transport.rate_limited": RetryPolicy(
        stable_id="funora.transport.rate_limited",
        max_attempts=2,
        base_ms=5000,
        multiplier=3.0,
        cap_ms=120000,
        jitter="full",
        respect_retry_after=True,
        max_retry_after_ms=300000,
        fail_closed=False,
        account_scoped=True,
    ),
    "funora.transport.remote_server": RetryPolicy(
        stable_id="funora.transport.remote_server",
        max_attempts=3,
        base_ms=2000,
        multiplier=2.0,
        cap_ms=60000,
        jitter="full",
        respect_retry_after=True,
        max_retry_after_ms=300000,
        fail_closed=False,
        account_scoped=False,
    ),
    "funora.auth.invalid_credentials": RetryPolicy(
        stable_id="funora.auth.invalid_credentials",
        max_attempts=1,
        base_ms=0,
        multiplier=1.0,
        cap_ms=0,
        jitter="full",
        respect_retry_after=False,
        max_retry_after_ms=300000,
        fail_closed=False,
        account_scoped=False,
    ),
    "funora.auth.session_expired": RetryPolicy(
        stable_id="funora.auth.session_expired",
        max_attempts=1,
        base_ms=0,
        multiplier=1.0,
        cap_ms=0,
        jitter="full",
        respect_retry_after=False,
        max_retry_after_ms=300000,
        fail_closed=False,
        account_scoped=False,
    ),
    "funora.auth.access_blocked": RetryPolicy(
        stable_id="funora.auth.access_blocked",
        max_attempts=1,
        base_ms=0,
        multiplier=1.0,
        cap_ms=0,
        jitter="full",
        respect_retry_after=False,
        max_retry_after_ms=300000,
        fail_closed=True,
        account_scoped=False,
    ),
    "funora.auth.challenge_required": RetryPolicy(
        stable_id="funora.auth.challenge_required",
        max_attempts=1,
        base_ms=0,
        multiplier=1.0,
        cap_ms=0,
        jitter="full",
        respect_retry_after=False,
        max_retry_after_ms=300000,
        fail_closed=True,
        account_scoped=False,
    ),
    "funora.protocol.changed": RetryPolicy(
        stable_id="funora.protocol.changed",
        max_attempts=1,
        base_ms=0,
        multiplier=1.0,
        cap_ms=0,
        jitter="full",
        respect_retry_after=False,
        max_retry_after_ms=300000,
        fail_closed=False,
        account_scoped=False,
    ),
    "funora.budget.exhausted": RetryPolicy(
        stable_id="funora.budget.exhausted",
        max_attempts=1,
        base_ms=0,
        multiplier=1.0,
        cap_ms=0,
        jitter="full",
        respect_retry_after=False,
        max_retry_after_ms=300000,
        fail_closed=False,
        account_scoped=False,
    ),
}

#: Политика для классов ошибок без собственной записи.
#:
#: Строже конкретных намеренно: неизвестный класс отказа не повод быть
#: смелее. Реализация, подставляющая здесь самую щедрую политику,
#: получает самое агрессивное поведение как раз тогда, когда меньше
#: всего понимает происходящее.
FALLBACK_POLICY: Final[RetryPolicy] = RETRY_POLICIES["funora.transport"]

#: Потолок числа попыток независимо от политики класса ошибки.
GLOBAL_MAX_ATTEMPTS: Final[int] = 5
