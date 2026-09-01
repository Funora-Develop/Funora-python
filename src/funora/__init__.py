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

from ._account import BalancePage, Transaction, WithdrawalChannel, WithdrawalOption
from ._aclient import AsyncChatsService, AsyncClient, AsyncOrdersService
from ._budget import Budget, Reservation
from ._calc import PaymentMethod, PriceCalculation
from ._catalog import CatalogGame, CatalogPage
from ._chats import ChatListEntry, ChatsPage, parse_chats_page
from ._chips import ChipsOffer, ChipsPage
from ._classify import (
    DEFAULT_CONTENT_MARKERS,
    DEFAULT_IDENTITY_CSS,
    ResponseClass,
    Signature,
    Verdict,
    classify,
)
from ._client import ChatsService, Client, OrdersService
from ._currency_switch import CurrencySwitch
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
from ._lot_form import LotForm, parse_lot_form
from ._market import MarketOffer, MarketPage
from ._money import CURRENCY_BY_SYMBOL, Money, currency_of_symbol
from ._observed import Confidence, Observed, Presence
from ._order import OrderParam, OrderView
from ._order_details import OrderDetails, OrderDetailsBatch
from ._orders import (
    Completeness,
    Defect,
    OrderListEntry,
    OrdersPage,
    Severity,
    parse_orders_page,
)
from ._own_lots import OwnLot, OwnLotsPage
from ._poll import Deduplicator, Schedule
from ._proxies import Proxy
from ._raise import RaiseResult
from ._refund import RefundResult
from ._retry import Attempt, Safety, plan_attempt, policy_for
from ._review_write import ReviewResult
from ._reviews import Review, ReviewsPage
from ._runner import SendResult
from ._secret import (
    CallableSecretProvider,
    EnvSecretProvider,
    FileSecretProvider,
    Secret,
    SecretNotFoundError,
    SecretProvider,
)
from ._showcase import ShowcasePage, ShowcaseSection
from ._skeleton import SkeletonError, skeletonize
from ._snapshot import MarketDiff, MarketSnapshot, SnapshotEntry, compare
from ._state import StateFile
from ._thread import Message, Origin, Thread, parse_thread
from ._transport import AsyncFetcher, Fetcher, Observation, TransportSettings
from ._updates import (
    MAX_SUBSCRIPTION,
    ChannelObject,
    UpdatesAnswer,
    build_subscription,
    parse_updates_answer,
)
from ._verdicts import error_for
from ._viewing import BuyerViewing
from ._watch import Router, StepResult, adispatch, dispatch
from ._whoami import Account, CapabilityProfile, SessionHealth
from .capabilities import CAPABILITY_INITIAL, Capability, CapabilityState
from .errors import ERROR_BY_ABI_CODE, ERROR_BY_STABLE_ID, FunoraError
from .events import EventType
from .extraction import (
    PRESENCE_BY_CLASS,
    ROW_MARKER_BY_STATUS,
    STATUS_BY_CELL_CLASS,
    OrderStatus,
)

__version__ = "0.0.1.dev0"

__all__ = [
    "__version__",
    # клиент
    "Client",
    "AsyncClient",
    "OrdersService",
    "AsyncOrdersService",
    "ChatsService",
    "AsyncChatsService",
    "BuyerViewing",
    "Budget",
    "Proxy",
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
    "AsyncFetcher",
    "Observation",
    "TransportSettings",
    # наблюдаемость
    "MarketSnapshot",
    "compare",
    "MarketDiff",
    "SnapshotEntry",
    "Money",
    "CURRENCY_BY_SYMBOL",
    "currency_of_symbol",
    "Observed",
    "Presence",
    "Confidence",
    # список заказов
    "parse_orders_page",
    "OrdersPage",
    "OrderStatus",
    "STATUS_BY_CELL_CLASS",
    "ROW_MARKER_BY_STATUS",
    "PRESENCE_BY_CLASS",
    "OrderListEntry",
    "Completeness",
    "Defect",
    "Severity",
    # То, что возвращают операции, и то, чем это перебирается.
    #
    # Прежде половина этих имён жила в приватных модулях, а операции ими уже
    # возвращали. Вызывающий, которому нужна аннотация типа, лез в модуль с
    # подчёркиванием - то есть в место, о котором пакет прямо говорит «менять
    # буду молча».
    "OrderView",
    "OrderDetails",
    "OrderDetailsBatch",
    "OrderParam",
    "SendResult",
    "Account",
    "SessionHealth",
    "CapabilityProfile",
    "BalancePage",
    "WithdrawalChannel",
    "WithdrawalOption",
    "Transaction",
    "ReviewsPage",
    "Review",
    "OwnLotsPage",
    "CurrencySwitch",
    "PaymentMethod",
    "PriceCalculation",
    "RaiseResult",
    "RefundResult",
    "ReviewResult",
    "OwnLot",
    "LotForm",
    "parse_lot_form",
    "ShowcasePage",
    "ShowcaseSection",
    "CatalogPage",
    "CatalogGame",
    "ChipsPage",
    "ChipsOffer",
    "MarketPage",
    "MarketOffer",
    # канал обновлений
    "UpdatesAnswer",
    "ChannelObject",
    "parse_updates_answer",
    "build_subscription",
    "MAX_SUBSCRIPTION",
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
    "adispatch",
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
