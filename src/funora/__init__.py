"""Funora - неофициальный SDK и framework для площадки FunPay.

В пакете есть первая операция чтения и инструмент наблюдения за протоколом.
Стабильным контрактом не является ничто: спецификация в состоянии draft.

Что уже есть:

  * :class:`~funora._secret.Secret` и источники секретов - тип, не раскрывающий
    значение ни в одном выходном канале;
  * :func:`~funora._classify.classify` - конвейер классификации ответа, который
    отличает страницу входа, проверку и блокировку от поломки разметки;
  * :func:`~funora._skeleton.skeletonize` - структурный скелет страницы, в котором
    персональных данных не может быть по построению;
  * ``funora-observe`` - инструмент однократного наблюдения.

Ничего из этого не является стабильным контрактом. Публичный API появится после
того, как спецификация перейдёт в состояние released.
"""

from __future__ import annotations

from ._budget import Budget, Reservation
from ._chats import ChatListEntry, ChatsPage, parse_chats_page
from ._classify import (
    DEFAULT_CONTENT_MARKERS,
    DEFAULT_IDENTITY_CSS,
    ResponseClass,
    Signature,
    Verdict,
    classify,
)
from ._client import ChatsService, Client, OrdersService
from ._diff import (
    Event,
    chats_cursor,
    diff_chats,
    diff_orders,
    diff_thread,
    orders_cursor,
    thread_cursor,
)
from ._gate import check_capability
from ._host import host_of, is_safe_hop, same_host
from ._observed import Confidence, Observed, Presence
from ._orders import (
    Completeness,
    Defect,
    OrderListEntry,
    OrdersPage,
    Severity,
    parse_orders_page,
)
from ._poll import Deduplicator, Schedule
from ._retry import Attempt, Safety, plan_attempt, policy_for
from ._secret import (
    CallableSecretProvider,
    EnvSecretProvider,
    FileSecretProvider,
    Secret,
    SecretNotFoundError,
    SecretProvider,
)
from ._skeleton import SkeletonError, skeletonize
from ._state import StateFile
from ._thread import Message, Origin, Thread, parse_thread
from ._transport import Fetcher, Observation, TransportSettings
from ._verdicts import error_for
from ._watch import Router, StepResult, dispatch
from .capabilities import CAPABILITY_INITIAL, Capability, CapabilityState
from .errors import ERROR_BY_ABI_CODE, ERROR_BY_STABLE_ID, FunoraError
from .events import EventType

__version__ = "0.0.1.dev0"

__all__ = [
    "__version__",
    # клиент
    "Client",
    "OrdersService",
    "ChatsService",
    "Budget",
    "Reservation",
    # секреты
    "Secret",
    "SecretProvider",
    "SecretNotFoundError",
    "EnvSecretProvider",
    "FileSecretProvider",
    "CallableSecretProvider",
    # классификация
    "classify",
    "ResponseClass",
    "Verdict",
    "Signature",
    "DEFAULT_IDENTITY_CSS",
    "DEFAULT_CONTENT_MARKERS",
    "error_for",
    "check_capability",
    "same_host",
    "is_safe_hop",
    "host_of",
    "plan_attempt",
    "policy_for",
    "Attempt",
    "Safety",
    # скелет
    "skeletonize",
    "SkeletonError",
    # транспорт
    "Fetcher",
    "Observation",
    "TransportSettings",
    # наблюдаемость
    "Observed",
    "Presence",
    "Confidence",
    # список заказов
    "parse_orders_page",
    "OrdersPage",
    "OrderListEntry",
    "Completeness",
    "Defect",
    "Severity",
    # список диалогов
    "parse_chats_page",
    "ChatsPage",
    "ChatListEntry",
    # переписка
    "parse_thread",
    "Thread",
    "Message",
    "Origin",
    # события
    "Event",
    "EventType",
    "diff_orders",
    "diff_chats",
    "diff_thread",
    "orders_cursor",
    "chats_cursor",
    "thread_cursor",
    "Schedule",
    "Deduplicator",
    "Router",
    "StepResult",
    "dispatch",
    "StateFile",
    # возможности
    "Capability",
    "CapabilityState",
    "CAPABILITY_INITIAL",
    # ошибки
    "FunoraError",
    "ERROR_BY_STABLE_ID",
    "ERROR_BY_ABI_CODE",
]
