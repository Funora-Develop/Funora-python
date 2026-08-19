"""Соответствие вердиктов классификатора ошибкам.

Файл порождён из спецификации, править его руками нельзя: правка исчезнет при
следующей сборке. Источник - spec/protocol/response-classes.yaml в репозитории Funora-spec.
Перестроить: python tools/codegen.py

Ключ - пара из класса ответа и машиночитаемой причины. Значение - класс
ошибки либо None, если ответ пригоден для разбора.

Таблица порождается, а не пишется, потому что от неё зависит, повторит
клиент запрос или остановится навсегда. Шесть реализаций, составивших её
порознь, разойдутся именно на негативных ветках - там, где расхождение
дороже всего и заметно позже всего.
"""

from __future__ import annotations

from typing import Final

from .errors import (
    AccessBlockedError,
    ChallengeRequiredError,
    ProtocolChangedError,
    RateLimitedError,
    RemoteServerError,
    SessionExpiredError,
    UnexpectedResponseError,
)

__all__ = ["VERDICT_ERRORS", "RESPONSE_CLASSES"]

#: Классы ответа, объявленные спецификацией.
#:
#: Перечень нужен, чтобы проверить полноту таблицы: класс без единой
#: записи означает, что реализации выберут ошибку сами.
RESPONSE_CLASSES: Final[frozenset[str]] = frozenset(
    {
        "ok",
        "login_required",
        "challenge",
        "blocked",
        "rate_limited",
        "maintenance",
        "wrong_identity",
        "transport_error",
        "unknown",
    }
)

#: Пара «класс ответа, причина» и ошибка, которую она означает.
VERDICT_ERRORS: Final[dict[tuple[str, str], type[Exception] | None]] = {
    ("ok", "identity_confirmed"): None,
    ("ok", "no_signature_matched_identity_unchecked"): None,
    ("login_required", "http_401"): SessionExpiredError,
    ("login_required", "signature:guest_navbar"): SessionExpiredError,
    ("login_required", "signature:login_page"): SessionExpiredError,
    ("login_required", "signature:login_form"): SessionExpiredError,
    ("challenge", "signature:challenge_widget"): ChallengeRequiredError,
    ("challenge", "signature:challenge_text"): ChallengeRequiredError,
    ("blocked", "http_403"): AccessBlockedError,
    ("blocked", "signature:blocked_text"): AccessBlockedError,
    ("rate_limited", "http_429"): RateLimitedError,
    ("maintenance", "http_503"): RemoteServerError,
    ("maintenance", "signature:maintenance_text"): RemoteServerError,
    ("wrong_identity", "host_mismatch"): UnexpectedResponseError,
    ("transport_error", "http_5xx"): RemoteServerError,
    ("transport_error", "http_4xx"): UnexpectedResponseError,
    ("unknown", "empty_body"): UnexpectedResponseError,
    ("unknown", "identity_marker_absent"): ProtocolChangedError,
}
