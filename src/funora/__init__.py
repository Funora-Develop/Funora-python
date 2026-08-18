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

from ._classify import ResponseClass, Signature, Verdict, classify
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
    # скелет
    "skeletonize",
    "SkeletonError",
    # транспорт
    "Fetcher",
    "Observation",
    "TransportSettings",
]
