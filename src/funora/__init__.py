"""Funora - неофициальный SDK и framework для площадки FunPay.

Пакет находится в стадии наблюдения за протоколом. Публичного клиента ещё нет:
сейчас здесь инструмент, который отвечает на вопросы, без ответа на которые
спецификацию нельзя перевести из состояния draft.

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

from ._classify import DEFAULT_IDENTITY_CSS, ResponseClass, Signature, Verdict, classify
from ._observed import Confidence, Observed, Presence
from ._orders import (
    Completeness,
    Defect,
    OrderListEntry,
    OrdersPage,
    Severity,
    parse_orders_page,
)
from ._secret import (
    CallableSecretProvider,
    EnvSecretProvider,
    FileSecretProvider,
    Secret,
    SecretNotFoundError,
    SecretProvider,
)
from ._skeleton import SkeletonError, skeletonize
from ._transport import Fetcher, Observation, TransportSettings
from ._verdicts import error_for
from .capabilities import CAPABILITY_INITIAL, Capability, CapabilityState
from .errors import ERROR_BY_ABI_CODE, ERROR_BY_STABLE_ID, FunoraError

__version__ = "0.0.1.dev0"

__all__ = [
    "__version__",
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
    "error_for",
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
    # возможности
    "Capability",
    "CapabilityState",
    "CAPABILITY_INITIAL",
    # ошибки
    "FunoraError",
    "ERROR_BY_STABLE_ID",
    "ERROR_BY_ABI_CODE",
]
