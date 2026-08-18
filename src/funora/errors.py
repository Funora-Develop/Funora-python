"""Иерархия ошибок Funora.

Файл порождён из спецификации, править его руками нельзя: правка исчезнет при
следующей сборке. Источник - spec/errors/errors.yaml в репозитории Funora-spec.
Перестроить: python tools/codegen.py

Числовой код abi_code одинаков во всех шести SDK и не переиспользуется
никогда: код, освободившийся после удаления ошибки, остаётся занятым
навсегда, иначе старый клиент истолкует новую ошибку как прежнюю.

Имя TimeoutError затеняет встроенное. Это разрешено спецификацией и не
требует переименования: имена ошибок одинаковы во всех реализациях, и
уступать одному языку значило бы разойтись с пятью остальными. Внутри
пакета встроенное исключение доступно как builtins.TimeoutError.
"""

from __future__ import annotations

from typing import ClassVar, Final

__all__ = [
    "FunoraError",
    "ConfigurationError",
    "AuthenticationError",
    "ValidationError",
    "CapabilityError",
    "TransportError",
    "ProtocolError",
    "DomainError",
    "StateError",
    "HandlerError",
    "PluginError",
    "BudgetError",
    "InvalidCredentialsError",
    "SessionExpiredError",
    "AccessBlockedError",
    "ChallengeRequiredError",
    "CurrencyMismatchError",
    "UnsupportedCapabilityError",
    "ExperimentalCapabilityError",
    "UnsupportedLocaleError",
    "TimeoutError",
    "NetworkError",
    "RateLimitedError",
    "RemoteServerError",
    "ProtocolChangedError",
    "ParseError",
    "UnexpectedResponseError",
    "EntityNotFoundError",
    "ConflictError",
    "PreconditionFailedError",
    "CursorIncompatibleError",
    "StateSchemaIncompatibleError",
    "HandlerTimeoutError",
    "HandlerCancelledError",
    "BudgetExhaustedError",
    "ERROR_BY_STABLE_ID",
    "ERROR_BY_ABI_CODE",
]


class FunoraError(Exception):
    """Базовый тип всех ошибок Funora.

    Повтор не поможет.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.error".
        abi_code (int): Числовой код 1000, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: False.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.error"
    abi_code: ClassVar[int] = 1000
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = False
    since_spec: ClassVar[str] = "0.1.0"


class ConfigurationError(FunoraError):
    """Клиент собран неверно; повтор не поможет, нужно исправить конфигурацию.

    Повтор не поможет, исправляется тем, кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.configuration".
        abi_code (int): Числовой код 1100, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.configuration"
    abi_code: ClassVar[int] = 1100
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class AuthenticationError(FunoraError):
    """Не удалось подтвердить личность владельца сессии.

    Повтор не поможет, исправляется тем, кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.auth".
        abi_code (int): Числовой код 1200, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.auth"
    abi_code: ClassVar[int] = 1200
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class ValidationError(FunoraError):
    """Аргументы вызова не прошли проверку до отправки запроса.

    Повтор не поможет, исправляется тем, кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.validation".
        abi_code (int): Числовой код 1300, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.validation"
    abi_code: ClassVar[int] = 1300
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class CapabilityError(FunoraError):
    """Запрошенная возможность недоступна в текущем адаптере или для этого аккаунта.

    Повтор не поможет, исправляется тем, кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.capability".
        abi_code (int): Числовой код 1400, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.capability"
    abi_code: ClassVar[int] = 1400
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class TransportError(FunoraError):
    """Сетевой уровень. side_effects_possible=true по умолчанию: запрос мог дойти до
    площадки и быть выполненным до того, как оборвался ответ. Автоматический повтор
    допустим только для операций с safety=safe или при наличии ключа
    идемпотентности; в остальных случаях требуется сверка состояния.

    Повтор допустим, действие могло произойти несмотря на ошибку.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.transport".
        abi_code (int): Числовой код 1500, общий для всех SDK.
        retryable (bool): Допустим ли повтор: True.
        side_effects_possible (bool): Могло ли действие произойти: True.
        user_actionable (bool): Исправляется ли вызывающим: False.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.transport"
    abi_code: ClassVar[int] = 1500
    retryable: ClassVar[bool] = True
    side_effects_possible: ClassVar[bool] = True
    user_actionable: ClassVar[bool] = False
    since_spec: ClassVar[str] = "0.1.0"


class ProtocolError(FunoraError):
    """Ответ площадки не соответствует тому, что ожидает адаптер.

    Повтор не поможет.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.protocol".
        abi_code (int): Числовой код 1600, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: False.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.protocol"
    abi_code: ClassVar[int] = 1600
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = False
    since_spec: ClassVar[str] = "0.1.0"


class DomainError(FunoraError):
    """Запрос корректен, но противоречит состоянию предметной области.

    Повтор не поможет, исправляется тем, кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.domain".
        abi_code (int): Числовой код 1700, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.domain"
    abi_code: ClassVar[int] = 1700
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class StateError(FunoraError):
    """Проблема с персистентным состоянием клиента.

    Повтор не поможет, исправляется тем, кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.state".
        abi_code (int): Числовой код 1800, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.state"
    abi_code: ClassVar[int] = 1800
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class HandlerError(FunoraError):
    """Ошибка внутри пользовательского обработчика.

    Повтор не поможет, действие могло произойти несмотря на ошибку, исправляется тем,
    кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.handler".
        abi_code (int): Числовой код 1850, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: True.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.handler"
    abi_code: ClassVar[int] = 1850
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = True
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class PluginError(FunoraError):
    """Ошибка внутри стороннего плагина.

    Повтор не поможет, действие могло произойти несмотря на ошибку, исправляется тем,
    кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.plugin".
        abi_code (int): Числовой код 1860, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: True.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.plugin"
    abi_code: ClassVar[int] = 1860
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = True
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class BudgetError(FunoraError):
    """Локальное ограничение SDK не позволило выполнить операцию.

    Повтор не поможет, исправляется тем, кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.budget".
        abi_code (int): Числовой код 1900, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.budget"
    abi_code: ClassVar[int] = 1900
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class InvalidCredentialsError(AuthenticationError):
    """Ключ не принят площадкой. Повторять запрещено - серия попыток выглядит как подбор.

    Повтор не поможет, исправляется тем, кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.auth.invalid_credentials".
        abi_code (int): Числовой код 1201, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.auth.invalid_credentials"
    abi_code: ClassVar[int] = 1201
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class SessionExpiredError(AuthenticationError):
    """Сессия была валидной и перестала быть. Обнаруживается в том числе по ответу HTTP 200
    с формой логина - самый опасный случай, потому что для парсера он выглядит как
    обычная страница.

    Повтор не поможет, исправляется тем, кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.auth.session_expired".
        abi_code (int): Числовой код 1202, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.auth.session_expired"
    abi_code: ClassVar[int] = 1202
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class AccessBlockedError(AuthenticationError):
    """Площадка отказала в доступе аккаунту или адресу. Поведение fail-closed: опрос
    останавливается, автоматический короткий backoff запрещён.

    Повтор не поможет, исправляется тем, кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.auth.access_blocked".
        abi_code (int): Числовой код 1203, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.auth.access_blocked"
    abi_code: ClassVar[int] = 1203
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class ChallengeRequiredError(AuthenticationError):
    """Площадка требует прохождения проверки. Funora её не обходит и не решает - это явная
    не-цель проекта. Ошибка существует, чтобы отличить проверку от поломки разметки:
    без неё клиент продолжит стучаться и подтвердит подозрение.

    Повтор не поможет, исправляется тем, кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.auth.challenge_required".
        abi_code (int): Числовой код 1204, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.auth.challenge_required"
    abi_code: ClassVar[int] = 1204
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class CurrencyMismatchError(ValidationError):
    """Арифметика между суммами в разных валютах запрещена.

    Повтор не поможет, исправляется тем, кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.validation.currency_mismatch".
        abi_code (int): Числовой код 1301, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.validation.currency_mismatch"
    abi_code: ClassVar[int] = 1301
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class UnsupportedCapabilityError(CapabilityError):
    """Возможность отсутствует по позитивному свидетельству: страница получена, отпечаток
    совпал, элемента функции нет. Не выставляется по ошибке сети.

    Повтор не поможет, исправляется тем, кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.capability.unsupported".
        abi_code (int): Числовой код 1401, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.capability.unsupported"
    abi_code: ClassVar[int] = 1401
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class ExperimentalCapabilityError(CapabilityError):
    """Возможность помечена experimental и требует явного включения.

    Повтор не поможет, исправляется тем, кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.capability.experimental".
        abi_code (int): Числовой код 1402, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.capability.experimental"
    abi_code: ClassVar[int] = 1402
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class UnsupportedLocaleError(CapabilityError):
    """Интерфейс аккаунта отдан на локали, для которой у адаптера нет текстовых шаблонов.
    Возвращать пустой результат в этом случае запрещено: смена языка интерфейса тихо
    выключила бы распознавание событий заказа.

    Повтор не поможет, исправляется тем, кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.capability.unsupported_locale".
        abi_code (int): Числовой код 1403, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.capability.unsupported_locale"
    abi_code: ClassVar[int] = 1403
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class TimeoutError(TransportError):
    """Ответ не получен вовремя. Исход операции неизвестен - это ambiguous timeout, и
    повтор небезопасной операции без сверки состояния запрещён.

    Повтор допустим, действие могло произойти несмотря на ошибку.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.transport.timeout".
        abi_code (int): Числовой код 1501, общий для всех SDK.
        retryable (bool): Допустим ли повтор: True.
        side_effects_possible (bool): Могло ли действие произойти: True.
        user_actionable (bool): Исправляется ли вызывающим: False.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.transport.timeout"
    abi_code: ClassVar[int] = 1501
    retryable: ClassVar[bool] = True
    side_effects_possible: ClassVar[bool] = True
    user_actionable: ClassVar[bool] = False
    since_spec: ClassVar[str] = "0.1.0"


class NetworkError(TransportError):
    """Соединение не установлено или разорвано. Исход неизвестен, если разрыв произошёл
    после отправки: повтор небезопасной операции требует сверки состояния.

    Повтор допустим, действие могло произойти несмотря на ошибку.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.transport.network".
        abi_code (int): Числовой код 1502, общий для всех SDK.
        retryable (bool): Допустим ли повтор: True.
        side_effects_possible (bool): Могло ли действие произойти: True.
        user_actionable (bool): Исправляется ли вызывающим: False.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.transport.network"
    abi_code: ClassVar[int] = 1502
    retryable: ClassVar[bool] = True
    side_effects_possible: ClassVar[bool] = True
    user_actionable: ClassVar[bool] = False
    since_spec: ClassVar[str] = "0.1.0"


class RateLimitedError(TransportError):
    """Площадка ответила ограничением. Регистрируется не на запросе, а на бюджете аккаунта:
    источников запросов много, и по отдельности они друг о друге не знают.

    Повтор допустим.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.transport.rate_limited".
        abi_code (int): Числовой код 1503, общий для всех SDK.
        retryable (bool): Допустим ли повтор: True.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: False.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.transport.rate_limited"
    abi_code: ClassVar[int] = 1503
    retryable: ClassVar[bool] = True
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = False
    since_spec: ClassVar[str] = "0.1.0"


class RemoteServerError(TransportError):
    """Площадка вернула ошибку на своей стороне. Запрос мог быть частично выполнен, поэтому
    повтор небезопасной операции допустим только после сверки состояния.

    Повтор допустим, действие могло произойти несмотря на ошибку.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.transport.remote_server".
        abi_code (int): Числовой код 1504, общий для всех SDK.
        retryable (bool): Допустим ли повтор: True.
        side_effects_possible (bool): Могло ли действие произойти: True.
        user_actionable (bool): Исправляется ли вызывающим: False.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.transport.remote_server"
    abi_code: ClassVar[int] = 1504
    retryable: ClassVar[bool] = True
    side_effects_possible: ClassVar[bool] = True
    user_actionable: ClassVar[bool] = False
    since_spec: ClassVar[str] = "0.1.0"


class ProtocolChangedError(ProtocolError):
    """Отпечаток страницы не совпал: разметка изменилась. Выдаётся вместо пустого
    результата - тихий пустой список неотличим от «данных нет» и стоит дороже всего.
    Выставляется только если не сработала ни одна негативная сигнатура (логин,
    проверка, блокировка): иначе это состояние доступа, а не поломка.

    Повтор не поможет.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.protocol.changed".
        abi_code (int): Числовой код 1601, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: False.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.protocol.changed"
    abi_code: ClassVar[int] = 1601
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = False
    since_spec: ClassVar[str] = "0.1.0"


class ParseError(ProtocolError):
    """Структура найдена, но значение не приводится к доменному типу.

    Повтор не поможет.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.protocol.parse".
        abi_code (int): Числовой код 1602, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: False.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.protocol.parse"
    abi_code: ClassVar[int] = 1602
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = False
    since_spec: ClassVar[str] = "0.1.0"


class UnexpectedResponseError(ProtocolError):
    """Ответ не относится к ожидаемой странице или принадлежит другому аккаунту.

    Повтор не поможет.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.protocol.unexpected_response".
        abi_code (int): Числовой код 1603, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: False.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.protocol.unexpected_response"
    abi_code: ClassVar[int] = 1603
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = False
    since_spec: ClassVar[str] = "0.1.0"


class EntityNotFoundError(DomainError):
    """Сущность не найдена.

    Повтор не поможет, исправляется тем, кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.domain.not_found".
        abi_code (int): Числовой код 1701, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.domain.not_found"
    abi_code: ClassVar[int] = 1701
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class ConflictError(DomainError):
    """Состояние изменилось параллельно.

    Повтор не поможет, исправляется тем, кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.domain.conflict".
        abi_code (int): Числовой код 1702, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.domain.conflict"
    abi_code: ClassVar[int] = 1702
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class PreconditionFailedError(DomainError):
    """Не выполнено обязательное предусловие, например `expected_revision` при изменении
    цены. Защищает от перетирания параллельной правки.

    Повтор не поможет, исправляется тем, кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.domain.precondition_failed".
        abi_code (int): Числовой код 1703, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.domain.precondition_failed"
    abi_code: ClassVar[int] = 1703
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class CursorIncompatibleError(StateError):
    """Курсор принадлежит другой версии формата или другому семейству адаптера. Молчаливое
    чтение с начала запрещено: оно даёт лавину повторной обработки.

    Повтор не поможет, исправляется тем, кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.state.cursor_incompatible".
        abi_code (int): Числовой код 1801, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.state.cursor_incompatible"
    abi_code: ClassVar[int] = 1801
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class StateSchemaIncompatibleError(StateError):
    """Версия схемы сохранённого состояния не поддерживается этой версией SDK.

    Повтор не поможет, исправляется тем, кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.state.schema_incompatible".
        abi_code (int): Числовой код 1802, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.state.schema_incompatible"
    abi_code: ClassVar[int] = 1802
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class HandlerTimeoutError(HandlerError):
    """Обработчик не уложился в отведённое время. Существует отдельно, чтобы конфигурация
    «повторять при TransportError» физически не могла перезапустить частично
    выполненный обработчик вместе с уже совершёнными записями.

    Повтор не поможет, действие могло произойти несмотря на ошибку, исправляется тем,
    кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.handler.timeout".
        abi_code (int): Числовой код 1851, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: True.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.handler.timeout"
    abi_code: ClassVar[int] = 1851
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = True
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


class HandlerCancelledError(HandlerError):
    """Обработчик отменён кооперативно.

    Повтор не поможет, действие могло произойти несмотря на ошибку.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.handler.cancelled".
        abi_code (int): Числовой код 1852, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: True.
        user_actionable (bool): Исправляется ли вызывающим: False.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.handler.cancelled"
    abi_code: ClassVar[int] = 1852
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = True
    user_actionable: ClassVar[bool] = False
    since_spec: ClassVar[str] = "0.1.0"


class BudgetExhaustedError(BudgetError):
    """Бюджет запросов исчерпан, запрос не отправлялся. Возвращается в том числе при
    регистрации наблюдения, стоимость которого не помещается в бюджет.

    Повтор не поможет, исправляется тем, кто вызвал.

    Attributes:
        stable_id (str): Устойчивый идентификатор "funora.budget.exhausted".
        abi_code (int): Числовой код 1901, общий для всех SDK.
        retryable (bool): Допустим ли повтор: False.
        side_effects_possible (bool): Могло ли действие произойти: False.
        user_actionable (bool): Исправляется ли вызывающим: True.
        since_spec (str): Версия спецификации "0.1.0".
    """

    stable_id: ClassVar[str] = "funora.budget.exhausted"
    abi_code: ClassVar[int] = 1901
    retryable: ClassVar[bool] = False
    side_effects_possible: ClassVar[bool] = False
    user_actionable: ClassVar[bool] = True
    since_spec: ClassVar[str] = "0.1.0"


#: Поиск класса по устойчивому идентификатору.
#:
#: Нужен там, где ошибка приходит извне процесса: из журнала, из очереди,
#: от другой реализации. Идентификатор устойчив между версиями, имя класса
#: языка - нет.
ERROR_BY_STABLE_ID: Final[dict[str, type[Exception]]] = {
    "funora.error": FunoraError,
    "funora.configuration": ConfigurationError,
    "funora.auth": AuthenticationError,
    "funora.validation": ValidationError,
    "funora.capability": CapabilityError,
    "funora.transport": TransportError,
    "funora.protocol": ProtocolError,
    "funora.domain": DomainError,
    "funora.state": StateError,
    "funora.handler": HandlerError,
    "funora.plugin": PluginError,
    "funora.budget": BudgetError,
    "funora.auth.invalid_credentials": InvalidCredentialsError,
    "funora.auth.session_expired": SessionExpiredError,
    "funora.auth.access_blocked": AccessBlockedError,
    "funora.auth.challenge_required": ChallengeRequiredError,
    "funora.validation.currency_mismatch": CurrencyMismatchError,
    "funora.capability.unsupported": UnsupportedCapabilityError,
    "funora.capability.experimental": ExperimentalCapabilityError,
    "funora.capability.unsupported_locale": UnsupportedLocaleError,
    "funora.transport.timeout": TimeoutError,
    "funora.transport.network": NetworkError,
    "funora.transport.rate_limited": RateLimitedError,
    "funora.transport.remote_server": RemoteServerError,
    "funora.protocol.changed": ProtocolChangedError,
    "funora.protocol.parse": ParseError,
    "funora.protocol.unexpected_response": UnexpectedResponseError,
    "funora.domain.not_found": EntityNotFoundError,
    "funora.domain.conflict": ConflictError,
    "funora.domain.precondition_failed": PreconditionFailedError,
    "funora.state.cursor_incompatible": CursorIncompatibleError,
    "funora.state.schema_incompatible": StateSchemaIncompatibleError,
    "funora.handler.timeout": HandlerTimeoutError,
    "funora.handler.cancelled": HandlerCancelledError,
    "funora.budget.exhausted": BudgetExhaustedError,
}

#: Поиск класса по числовому коду.
#:
#: Код одинаков во всех шести SDK, поэтому по нему ошибка опознаётся при
#: передаче между реализациями.
ERROR_BY_ABI_CODE: Final[dict[int, type[Exception]]] = {
    1000: FunoraError,
    1100: ConfigurationError,
    1200: AuthenticationError,
    1300: ValidationError,
    1400: CapabilityError,
    1500: TransportError,
    1600: ProtocolError,
    1700: DomainError,
    1800: StateError,
    1850: HandlerError,
    1860: PluginError,
    1900: BudgetError,
    1201: InvalidCredentialsError,
    1202: SessionExpiredError,
    1203: AccessBlockedError,
    1204: ChallengeRequiredError,
    1301: CurrencyMismatchError,
    1401: UnsupportedCapabilityError,
    1402: ExperimentalCapabilityError,
    1403: UnsupportedLocaleError,
    1501: TimeoutError,
    1502: NetworkError,
    1503: RateLimitedError,
    1504: RemoteServerError,
    1601: ProtocolChangedError,
    1602: ParseError,
    1603: UnexpectedResponseError,
    1701: EntityNotFoundError,
    1702: ConflictError,
    1703: PreconditionFailedError,
    1801: CursorIncompatibleError,
    1802: StateSchemaIncompatibleError,
    1851: HandlerTimeoutError,
    1852: HandlerCancelledError,
    1901: BudgetExhaustedError,
}
