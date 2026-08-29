r"""Соответствие вердиктов классификатора ошибкам.

Файл порождён из спецификации, править его руками нельзя: правка исчезнет при
следующей сборке. Источник - spec/protocol/response-classes.yaml в репозитории Funora-spec.
Перестроить: .venv\Scripts\python.exe tools/codegen.py

Ключ - пара из класса ответа и машиночитаемой причины. Значение - класс
ошибки либо None, если ответ пригоден для разбора.

Таблица порождается, а не пишется, потому что от неё зависит, повторит
клиент запрос или остановится навсегда. Шесть реализаций, составивших её
порознь, разойдутся именно на негативных ветках - там, где расхождение
дороже всего и заметно позже всего.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from .errors import (
    AccessBlockedError,
    ChallengeRequiredError,
    NetworkError,
    ProtocolChangedError,
    RateLimitedError,
    RemoteServerError,
    SessionExpiredError,
    UnexpectedResponseError,
)

__all__ = [
    "VERDICT_ERRORS",
    "RESPONSE_CLASSES",
    "STATUS_CLASS",
    "Health",
    "INITIAL_HEALTH",
    "HEALTH_BY_VERDICT",
    "WRITES_PAUSED_IN",
]

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

#: Класс ответа по коду, когда тело разбирать бессмысленно.
#:
#: Прежде таблица жила рукописной копией в классификаторе. Правка
#: спецификации не давала ни одного признака: сборка зелёная,
#: спецификация зелёная, а расхождение обнаружилось бы в работе - и
#: ровно на той ошибке, от которой спецификация предостерегает
#: отдельным разделом: код 429, принятый за блокировку, навсегда
#: останавливает опрос.
STATUS_CLASS: Final[dict[int, str]] = {
    401: "login_required",
    403: "blocked",
    429: "rate_limited",
    503: "maintenance",
}

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
    ("wrong_identity", "host_unreadable"): UnexpectedResponseError,
    ("transport_error", "http_5xx"): RemoteServerError,
    ("transport_error", "http_4xx"): UnexpectedResponseError,
    ("transport_error", "body_truncated"): NetworkError,
    ("unknown", "empty_body"): UnexpectedResponseError,
    ("unknown", "identity_marker_absent"): ProtocolChangedError,
    ("unknown", "body_unparsable"): UnexpectedResponseError,
}


class Health(StrEnum):
    """Состояние доступа к площадке.

    От него зависит, приостановлена ли автоматика записи. Перечень
    объявлен схемой события protocol.health_changed и повторён в
    spec/protocol/response-classes.yaml вместе с правилами перехода.
    """

    AUTHENTICATED = "authenticated"
    DEGRADED = "degraded"
    PROTOCOL_CHANGED = "protocol_changed"
    RATE_LIMITED = "rate_limited"
    CHALLENGED = "challenged"
    BLOCKED = "blocked"


#: Начальное состояние.
#:
#: До первого ответа состояние не проверяется: клиент не знает о
#: площадке ничего, пока не сходил.
INITIAL_HEALTH: Final[Health] = Health.AUTHENTICATED

#: В какое состояние переводит класс ответа.
#:
#: None означает «состояние не меняется». Сетевой отказ и
#: неопознанный ответ говорят о нас и о дороге, а не о том, как
#: площадка к нам относится: менять по ним состояние доступа значило
#: бы объявлять аккаунт ограниченным из-за оборванного соединения.
HEALTH_BY_VERDICT: Final[dict[str, Health | None]] = {
    "ok": Health.AUTHENTICATED,
    "login_required": Health.DEGRADED,
    "challenge": Health.CHALLENGED,
    "blocked": Health.BLOCKED,
    "rate_limited": Health.RATE_LIMITED,
    "maintenance": Health.DEGRADED,
    "wrong_identity": Health.DEGRADED,
    "transport_error": None,
    "unknown": None,
}

#: Состояния, в которых автоматика записи приостановлена.
#:
#: Возобновление - только явным действием пользователя либо
#: возвратом в начальное состояние по успешному ответу. Сама по себе
#: пауза не истекает: истекающая означала бы, что клиент снова пишет
#: на площадку, которая только что отказала, и не спросил никого.
WRITES_PAUSED_IN: Final[frozenset[Health]] = frozenset(
    {
        Health.DEGRADED,
        Health.PROTOCOL_CHANGED,
        Health.RATE_LIMITED,
        Health.CHALLENGED,
        Health.BLOCKED,
    }
)
