"""Ядро клиента: вся логика без единого обращения к сети и к часам ожидания.

Модуль появился, когда понадобился асинхронный клиент. Написать его вторым
файлом было дешевле всего и хуже всего: нормативный порядок шагов, политика
повторов, расход бюджета, сдвиг курсора и правила гашения существовали бы в двух
экземплярах. Копия расходится - это в проекте уже случалось трижды с правилом
хоста, и один раз ценой сессионного ключа.

Поэтому логика здесь одна, а способов её крутить два. Ядро не вызывает
ввод-вывод, а **просит** о нём: возвращает запрос и ждёт ответа. Синхронный
клиент удовлетворяет просьбу вызовом, асинхронный - ожиданием. Разница между
ними сводится к десятку строк, и разойтись им негде.

Просьб три:

``Fetch`` - сходить на страницу. В ответ ядро ждёт наблюдение либо брошенную
внутрь ошибку: политика повторов написана в ядре и должна видеть отказ сама.

``Pause`` - подождать. Ядро само не спит, потому что спать синхронно и
асинхронно - разные вещи, а решать, сколько ждать, - одна и та же.

``Deliver`` - раздать партию событий обработчикам. Обработчики принадлежат
вызывающему, и вызывать их асинхронно умеет только асинхронная сторона.

Порядок шагов нормативен и записан в spec/protocol/response-classes.yaml. Две
реализации, проверившие условия в разном порядке, разойдутся именно на той
странице, ради которой правило написано.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Generator
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Final, TypeVar
from urllib.parse import urlparse

from ._account import BalancePage, parse_balance_page
from ._budget import Budget
from ._catalog import CatalogPage, parse_catalog
from ._chats import ChatsPage, parse_chats_page
from ._classify import DEFAULT_IDENTITY_CSS, ResponseClass, Verdict, classify
from ._delivered import DeliveryLedger
from ._diff import (
    UNREAD_STATUS,
    Delivery,
    Event,
    chats_cursor,
    diff_chats,
    diff_orders,
    diff_thread,
    orders_cursor,
    thread_cursor,
)
from ._extract import observe_locale
from ._gate import check_capability
from ._host import host_of
from ._identity import REGISTRY, Identity, identity_of
from ._lot_form import SAVE_PATH, LotForm, parse_lot_form
from ._observed import Observed
from ._order import OrderView, parse_order_page
from ._orders import Completeness, OrdersPage, parse_orders_page
from ._outbound import UNSAFE_SENDS_WITHOUT_LEDGER, OutboundGovernor, OutboundRefusal
from ._own_lots import OwnLotsPage, parse_own_lots
from ._poll import Deduplicator, Schedule
from ._price_audit import UNSAFE_PRICE_CHANGES_WITHOUT_AUDIT, PriceAudit, PriceChange
from ._result import Defect, Severity
from ._retry import RETRY_REASON_ATTR, plan_attempt
from ._reviews import ReviewsPage, parse_reviews_page
from ._runner import (
    Anchor,
    SendResult,
    classify_send_response,
    parse_runner_context,
    reconcile,
    take_anchor,
)
from ._showcase import ShowcasePage, parse_showcase
from ._state import StateFile
from ._thread import Thread, parse_thread
from ._transport import Observation, TransportSettings
from ._verdicts import error_for
from ._watch import Router, StepResult, health_changed, incomplete, loss, primed
from ._whoami import Account, CapabilityProfile, SessionHealth, parse_account
from .budget import (
    COUNTS_REDIRECTS,
    COUNTS_RETRIES,
    MAX_QUEUE_DEPTH_PER_KEY,
    MIN_HEALTH_INTERVAL_MS,
    WAIT_ATTEMPTS,
    WAIT_GUARD_MS,
    RequestClass,
)
from .capabilities import CAPABILITY_INITIAL, Capability, CapabilityState
from .contract import SUPPORTED_LOCALES
from .errors import (
    AuthenticationError,
    BudgetError,
    BudgetExhaustedError,
    ConfigurationError,
    CursorIncompatibleError,
    FunoraError,
    NetworkError,
    PreconditionFailedError,
    ProtocolChangedError,
    RateLimitedError,
    TransportError,
    UnexpectedResponseError,
    UsageError,
    ValidationError,
)
from .operations import OPERATIONS, Safety
from .reconciliation import RECONCILE_DELAYS_MS, ReconcileVerdict
from .response_classes import (
    HEALTH_BY_VERDICT,
    INITIAL_HEALTH,
    WRITES_PAUSED_IN,
    Health,
)
from .retry import RETRY_POLICIES

__all__ = [
    "Fetch",
    "Submit",
    "Pause",
    "Deliver",
    "Request",
    "Engine",
    "ORDERS_PATH",
    "CHATS_PATH",
    "RUNNER_PATH",
    "LOT_EDIT_PATH",
    "OWN_LOTS_PATH",
    "PROFILE_PATH",
    "ORDER_PATH",
    "BALANCE_PATH",
    "SHOWCASE_PATH",
    "CATALOG_PATH",
    "ACCOUNT_PATH",
]

_log = logging.getLogger("funora.client")

#: Путь страницы списка заказов.
ORDERS_PATH: Final[str] = "/orders/trade"

#: Путь страницы списка диалогов.
CHATS_PATH: Final[str] = "/chat/"

#: Путь страницы отдельной переписки.
THREAD_PATH: Final[str] = "/chat/?node={node_id}"

#: Профиль продавца. Отзывы лежат на нём же: отдельной страницы у них нет.
PROFILE_PATH: Final[str] = "/users/{user_id}/"

#: Страница одного заказа.
ORDER_PATH: Final[str] = "/orders/{order_id}/"

#: Страница баланса. Несёт и балансы по валютам, и операции по счёту.
BALANCE_PATH: Final[str] = "/account/balance"

#: Страница собственных лотов раздела. Требует НОМЕРА РАЗДЕЛА: управление
#: лотами живёт по одному адресу на раздел, а не по одному на аккаунт.
OWN_LOTS_PATH: Final[str] = "/lots/{node_id}/trade"

#: Форма правки одного предложения.
LOT_EDIT_PATH: Final[str] = "/lots/offerEdit?node={node_id}&offer={offer_id}"

#: Канал обновлений. Тем же адресом площадка и опрашивается, и меняется.
RUNNER_PATH: Final[str] = "/runner/"

#: Заголовки обращения к каналу.
#:
#: Имена наблюдены на КАЖДОМ запросе канала. Заголовок x-requested-with
#: пропустить нельзя, пока не наблюдено обратное: площадка вправе отвечать на
#: запросы без него иначе.
#:
#: Значения не наблюдались - записи хранят имена, - но именно этот набор
#: площадка ПРИНЯЛА: сборщик отправил им и опрос, и два сообщения.
RUNNER_HEADERS: Final[dict[str, str]] = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
}

#: Страница, по которой читается собственный аккаунт и проверяется сессия.
#:
#: Это адрес переписки, и выбран он не случайно. Собственный идентификатор лежит
#: в атрибуте data-user, а тот есть только там, где есть виджет переписки: на
#: списке продаж и на странице баланса его нет вовсе. Из страниц с виджетом эта -
#: единственная с постоянным адресом, не требующим чужого идентификатора.
ACCOUNT_PATH: Final[str] = CHATS_PATH

#: Корень площадки. Несёт каталог целиком.
CATALOG_PATH: Final[str] = "/"

#: Профиль продавца. Несёт и отзывы, и витрину.
SHOWCASE_PATH: Final[str] = PROFILE_PATH


@dataclass(frozen=True, slots=True)
class Fetch:
    """Просьба сходить на страницу.

    Attributes:
        path (str): Путь запрашиваемой страницы.
    """

    path: str


@dataclass(frozen=True, slots=True)
class Submit:
    """Просьба отправить форму.

    Отдельная просьба, а не признак у Fetch, и это не оформление. У записи своё
    правило, которого у чтения нет: ПЕРЕХОД В ОТВЕТ НА ЗАПИСЬ НЕ ПОВТОРЯЕТСЯ.
    Чтение по переходу повторить безвредно, запись - нет.

    Признак у Fetch означал бы, что оба правила живут в одном месте и
    различаются условием. Условие однажды упростят.

    Attributes:
        path (str): Путь обращения.
        fields (dict[str, str]): Поля формы.
        headers (dict[str, str]): Заголовки, кроме Cookie: секрет ставит
            транспорт и только он.
    """

    path: str
    fields: dict[str, str]
    headers: dict[str, str]


@dataclass(frozen=True, slots=True)
class Pause:
    """Просьба подождать.

    Attributes:
        ms (int): Длительность паузы в миллисекундах.
    """

    ms: int


@dataclass(frozen=True, slots=True)
class Deliver:
    """Просьба раздать партию событий обработчикам.

    Attributes:
        events (tuple[Event, ...]): События партии в порядке порождения.
    """

    events: tuple[Event, ...]


#: Любая из трёх просьб.
Request = Fetch | Submit | Pause | Deliver

#: Что ядро получает в ответ на просьбу. Наблюдение - на Fetch, результат
#: раздачи - на Deliver, ничего - на Pause.
Reply = Observation | StepResult | None


@dataclass
class _State:
    """Состояние клиента, живущее между вызовами.

    Attributes:
        capabilities (dict[Capability, CapabilityState]): Текущие состояния
            возможностей. Начальные берутся из спецификации.
        session_ever_valid (bool): Подтверждалась ли сессия хоть раз. Отличает
            истёкшую сессию от неверного секрета, а это разные диагнозы с разным
            лечением.
        opted_in (frozenset[Capability]): Возможности, включённые вызывающим
            явно.
        health (Health): Состояние доступа к площадке. От него зависит,
            приостановлена ли автоматика записи.
        locale (Observed[str]): Локаль интерфейса, как её отдала площадка.
            Чтения не отменяет: разбор структурный и от смены языка не
            ломается. Но поля, приходящие текстом, возвращаются на этом языке,
            и вызывающий вправе знать, на каком.
    """

    #: Ограничитель исходящих сообщений.
    #:
    #: Живёт в состоянии клиента, а не создаётся операцией: пределы часовые, и
    #: ограничитель, рождающийся вместе с вызовом, не ограничивал бы ничего.
    outbound: OutboundGovernor = field(default_factory=OutboundGovernor)

    #: Вердикт последней классификации.
    #:
    #: Нужен проверке сессии: она ОТЧИТЫВАЕТСЯ о состоянии, а не падает от него,
    #: и потому обязана видеть вердикт даже тогда, когда чтение окончилось
    #: отказом. Поле обнуляется перед каждой проверкой: несвежий вердикт хуже
    #: отсутствующего, он выглядит свежим.
    last_verdict: Verdict | None = None

    #: Ответ последней проверки сессии и момент, когда он получен.
    #:
    #: Дроссель нужен затем, что проверку ставят в цикл ядра, а не зовут рукой.
    #: Без него реализация пошла бы на площадку каждый тик - ровно то, чего
    #: обещание операции запрещает.
    health_cached: SessionHealth | None = None
    health_checked_at: float = 0.0

    capabilities: dict[Capability, CapabilityState] = field(
        default_factory=lambda: dict(CAPABILITY_INITIAL)
    )
    session_ever_valid: bool = False
    opted_in: frozenset[Capability] = frozenset()
    health: Health = INITIAL_HEALTH
    locale: Observed[str] = field(default_factory=lambda: Observed.missing("not_read_yet"))


def integrity_verified(observation: Observation) -> bool:
    """Сообщает, удалось ли подтвердить, что тело ответа получено целиком.

    Прежде здесь стояла check_integrity, которая ничего не возвращала и не
    вызывалась НИОТКУДА: сравнение длин живёт в классификаторе, и функция была
    мёртвой копией его же правила. Копия проверялась своим тестом и создавала
    впечатление работающей защиты.

    Настоящая дыра была рядом. Классификатор ловит обрыв, только когда есть что
    сравнивать: при chunked-передаче объявленной длины нет вовсе, а при сжатии
    сравнивать нечего - объявлена длина сжатого тела, получена длина
    распакованного. В обоих случаях он предупреждает и пропускает, а разбор
    объявляет чтение ПОЛНЫМ.

    Замер на снимке списка продаж: из двух тысяч обрывов в случайной точке 128
    (6.4%) давали completeness=complete с числом строк меньше настоящего.

    Это худший исход из возможных - правдоподобный неверный ответ. Курсор
    снимается с полного чтения, недостающие заказы уходят из него, и при
    следующем целом чтении приходят заново как order.created. Бот, выдающий
    товар по этому событию, выдаёт его повторно.

    Args:
        observation (Observation): Результат обращения.

    Returns:
        bool: True, если целостность подтверждена сравнением длин. False, если
        сравнивать было нечем - это не обрыв, а незнание, и обходиться с ним
        надо как с незнанием.
    """
    if observation.content_encoding not in ("", "identity"):
        return False
    if observation.declared_length is None:
        return False
    return observation.content_length >= observation.declared_length


#: Прочитанное, у чего есть полнота: страница списка либо переписка.
PageT = TypeVar(
    "PageT",
    bound=(
        "OrdersPage | ChatsPage | Thread | ReviewsPage | BalancePage | ShowcasePage | CatalogPage"
    ),
)


def unverified(page: PageT) -> PageT:
    """Понижает полноту чтения, целостность которого не подтверждена.

    Полным объявляется чтение, о котором ИЗВЕСТНО, что оно целое. Когда
    сравнивать длины было нечем, это неизвестно, и объявлять полноту значит
    выдавать незнание за знание.

    Понижение - не отказ. Строки, которые прочитались, остаются на месте и
    доступны через rows(accept_incomplete=True); меняется одно - снимет ли цикл
    наблюдения с этого чтения курсор. Не снимет, и заказ, выпавший из
    оборванной страницы, не будет сочтён исчезнувшим.

    Args:
        page (страница со счётчиками строк либо None): Разобранная страница либо переписка.

    Returns:
        PageT: Она же, если полнота и без того не полная. Иначе - с полнотой
        partial, причиной integrity_unverified и повреждением уровня страницы.
    """
    if page.completeness is not Completeness.COMPLETE:
        return page
    return replace(
        page,
        completeness=Completeness.PARTIAL,
        reason="integrity_unverified",
        defects=(
            *page.defects,
            Defect(
                severity=Severity.PAGE,
                code="integrity_unverified",
                detail=(
                    "целостность тела не подтверждена: сравнивать длины было "
                    "нечем. Чтение могло оборваться посреди списка, и объявить "
                    "его полным значило бы выдать незнание за знание"
                ),
                field_name=None,
            ),
        ),
    )


#: Возможности, под которые у этой реализации есть операция.
#:
#: Таблица начальных состояний отвечает на вопрос о ПЛОЩАДКЕ: наблюдается ли
#: возможность там. Вызывающий спрашивает другое - «могу ли я это вызвать», - и
#: одиннадцать возможностей отвечали ему supported, то есть «подтверждена и
#: доступна», при том что метода под них в SDK нет вовсе.
#:
#: Код, который ветвится по состоянию (а состояние для того и заведено), уходил
#: в ветку «доступно» и падал на отсутствующем атрибуте - в лучшем случае. В
#: худшем ветка просто не делала ничего.
#:
#: Перечень - факт о реализации, а не о контракте, поэтому он здесь, а не в
#: порождённом файле. Проверка сверяет его с тем, что вправду вызывается.
IMPLEMENTED: Final[frozenset[Capability]] = frozenset(
    {
        Capability.ORDERS_LIST,
        Capability.CHATS_LIST,
        Capability.CHATS_HISTORY,
        Capability.REVIEWS_GET,
        Capability.ORDERS_GET,
        Capability.ACCOUNT_BALANCE,
        Capability.LOTS_SHOWCASE,
        Capability.CATALOG_CATEGORIES,
        Capability.ACCOUNT_PROFILE,
        Capability.CHATS_SEND_TEXT,
        Capability.LOTS_LIST_OWN,
    }
)

#: Возможности, которые ЦИКЛ НАБЛЮДЕНИЯ волен звать сам и повторять свободно.
#:
#: Множество отдельное от IMPLEMENTED, и разошлись они не случайно. До
#: 30.08.2026 всё выполняемое было чтением, и «выполняется» служило заменой
#: «повторяется свободно». Первая же операция записи это равенство сломала.
#:
#: Записи здесь нет и быть не может. Цикл повторяет свободно, а у отправки нет
#: отмены: повтор при неоднозначном исходе - второе сообщение покупателю.
POLLED: Final[frozenset[Capability]] = frozenset(
    {
        Capability.ORDERS_LIST,
        Capability.CHATS_LIST,
        Capability.CHATS_HISTORY,
        Capability.REVIEWS_GET,
        Capability.ORDERS_GET,
        Capability.ACCOUNT_BALANCE,
        Capability.LOTS_SHOWCASE,
        Capability.CATALOG_CATEGORIES,
        Capability.ACCOUNT_PROFILE,
        Capability.LOTS_LIST_OWN,
    }
)


def _scoped(error: Exception) -> bool:
    """Сообщает, отступает ли по этой ошибке вся идентичность.

    Признак объявлен политикой повторов. Источников запросов много - цикл
    опроса, планировщики наблюдений, пользовательские записи, - и по
    отдельности они друг о друге не знают. Отступи каждый только за себя,
    суммарное давление почти не упало бы.

    Args:
        error (Exception): Ошибка, вызвавшая отступление.

    Returns:
        bool: True, если политика объявила отступление общим для аккаунта.
    """
    policy = RETRY_POLICIES.get(getattr(error, "stable_id", ""))
    return bool(policy and policy.account_scoped)


def _digits(value: str, what: str) -> str:
    """Проверяет, что идентификатор состоит из одних цифр.

    Проверка идёт ДО сети: подставленный в адрес мусор отправил бы запрос
    неизвестно куда, а на странице правки цена ошибки - чужой лот.

    Args:
        value (str): Идентификатор.
        what (str): Чего именно, для сообщения.

    Returns:
        str: Идентификатор без краевых пробелов.

    Raises:
        ValidationError: Если идентификатор непригоден.
    """
    cleaned = value.strip()
    if not cleaned or not cleaned.isdigit():
        raise ValidationError(
            f"идентификатор {what} обязан состоять из цифр, получено "
            f"{len(cleaned)} знаков иного вида"
        )
    return cleaned


def _outbound_error(refusal: OutboundRefusal) -> FunoraError:
    """Превращает отказ ограничителя в ошибку нужного класса.

    Новых классов не заводится. Исчерпанная квота - это ровно «бюджет исчерпан,
    запрос не отправлялся». Холодное обращение без признака - ровно «SDK
    использован неверно, исправлять надо вызывающий код»: продавец не сказал,
    что пишет первым.

    ОТСУТСТВИЕ РЕЕСТРА - ТРЕТИЙ СЛУЧАЙ, и он не про квоту. Прежде он приходил
    как исчерпанный бюджет, а это подсказка «подожди и повтори» - подсказка
    ложная: ждать здесь бесполезно ни секунду, ни час. Настроить надо клиента,
    и класс отказа обязан говорить об этом сам, иначе вызывающий напишет цикл
    ожидания, который не кончится никогда.

    Args:
        refusal (OutboundRefusal): Отказ ограничителя.

    Returns:
        FunoraError: Ошибка с названным пределом и сроком.
    """
    where = f"ограничитель исходящих: {refusal.detail} (предел {refusal.limit}"
    where += f", освободится через {refusal.retry_after_ms} мс)" if refusal.retry_after_ms else ")"
    if refusal.limit == "cold_outreach_not_declared":
        return UsageError(where)
    if refusal.limit == "no_durable_ledger":
        return ConfigurationError(
            f"{where}. Передайте клиенту state_path - тот же файл, что и "
            "наблюдению, - либо, если вы понимаете цену, unsafe_sends_without_ledger"
        )
    exhausted: FunoraError = BudgetExhaustedError(where)
    return exhausted


def _class_of(capability: Capability) -> RequestClass:
    """Находит класс запроса по возможности, которой он требует.

    Класс решает, кого вытесняют при нехватке ёмкости. Проставляет его служба, а
    не пользователь: пользователь не знает, чем его вызов мешает соседнему.

    Прежде класс объявлялся у каждой операции и до бюджета не доходил вовсе -
    собственный мониторинг продавца вытеснял ответы покупателям на общих
    основаниях, ровно то, ради чего доли и придуманы.

    Args:
        capability (Capability): Возможность, под которую идёт запрос.

    Returns:
        RequestClass: Объявленный класс. Для возможности без операции -
        interactive: самый защищённый. Вызов, о котором контракт молчит, не
        должен из-за этого молчания уступить наблюдению за рынком.
    """
    for operation in OPERATIONS.values():
        if operation.capability == capability.value:
            return RequestClass(operation.request_class)
    return RequestClass.INTERACTIVE


def _safety_of(capability: Capability) -> Safety:
    """Находит безопасность операции по возможности, которой она требует.

    Решение о повторе - пересечение класса ошибки и безопасности операции.
    Вторая половина объявлена в spec/services и порождена в таблицу операций;
    брать её константой значило бы держать половину правила в коде.

    Args:
        capability (Capability): Возможность, под которую идёт запрос.

    Returns:
        Safety: Объявленная безопасность. Для возможности без операции -
        небезопасно: неизвестное не повторяют.
    """
    for operation in OPERATIONS.values():
        if operation.capability == capability.value:
            return operation.safety
    # Умолчание строгое намеренно. Возможность без операции означает, что
    # реализация делает запрос, которого контракт не описывает; повторять такой
    # запрос значило бы гадать о его последствиях.
    return Safety.UNSAFE


class Engine:
    """Логика клиента, отделённая от способа выполнять ввод-вывод.

    Args:
        settings (TransportSettings): Настройки транспорта. Нужны ядру ради
            ожидаемого хоста, а не ради сети.
        budget (Budget): Бюджет запросов.
        experimental (frozenset[Capability]): Возможности, включённые вызывающим
            явно.
        identity (Identity | None): Сетевая идентичность, через которую идут
            запросы. Нужна, чтобы ограничение частоты дошло до источника: оно
            про источник целиком, а не про один запрос. По умолчанию заводится
            прямое соединение к хосту из настроек.
    """

    __slots__ = (
        "_budget",
        "_health_changes",
        "_identity",
        "_delivered",
        "_price_audit",
        "_ledger",
        "_stored_account",
        "_settings",
        "_state",
        "_stopped",
        "_unsafe",
    )

    def __init__(
        self,
        settings: TransportSettings,
        budget: Budget,
        experimental: frozenset[Capability] = frozenset(),
        identity: Identity | None = None,
        state_path: Path | None = None,
        unsafe_sends_without_ledger: bool = False,
        unsafe_price_changes_without_audit: bool = False,
    ) -> None:
        self._settings = settings
        self._budget = budget
        self._state = _State(opted_in=experimental)

        #: Файл, в котором реестр отправок переживает перезапуск.
        #:
        #: Спецификация требует его прямо: без долговечного реестра защита
        #: снимается перезапуском процесса. Пределы часовые, а память
        #: обнуляется, и тридцать сообщений в час превращаются в тридцать на
        #: запуск. Бот под супервизором обходил бы ограничитель полностью.
        self._ledger: StateFile | None = StateFile(state_path) if state_path else None

        #: Снятые вызывающим защиты. Читаются состоянием здоровья.
        #:
        #: Отметка ставится по ФАКТУ, а не по просьбе: попросивший послабление и
        #: передавший файл состояния защиту не снимал, и говорить о нём обратное
        #: значило бы врать в отчёте о здоровье.
        self._unsafe: set[str] = set()
        if unsafe_sends_without_ledger and state_path is None:
            self._unsafe.add(UNSAFE_SENDS_WITHOUT_LEDGER)

        # Отправка без реестра ОТКАЗЫВАЕТ, и это не строгость ради строгости.
        # Послабление есть, оно называется вслух и оставляет след в состоянии
        # здоровья: снять защиту можно, снять её незаметно нельзя.
        self._state.outbound.durable = self._ledger is not None or unsafe_sends_without_ledger

        #: Что уже выдано по заказам. Живёт рядом с реестром отправок и в том же
        #: файле: у автовыдачи та же беда, что у пределов отправки, - память
        #: процесса обнуляется, а «этот заказ выдан» обязано жить, пока жив заказ.
        self._delivered = DeliveryLedger()

        #: Что стояло у лота до правки цены. Контракт требует этого аудита у
        #: одной-единственной операции, и требует не зря: у площадки нет ни
        #: истории цен, ни отката, и «как было» знать больше некому.
        self._price_audit = PriceAudit()

        # Правка цены без долговечного журнала ОТКАЗЫВАЕТ, и довод тот же, что
        # у отправки без реестра: память процесса обнуляется, а «какая цена
        # стояла до бота» обязано жить, пока жив лот. Послабление есть, оно
        # называется вслух и оставляет след в состоянии здоровья.
        self._price_audit.durable = self._ledger is not None or unsafe_price_changes_without_audit
        if unsafe_price_changes_without_audit and state_path is None:
            self._unsafe.add(UNSAFE_PRICE_CHANGES_WITHOUT_AUDIT)

        #: Чей аккаунт записан в файле состояния. Пустая строка означает, что
        #: файла нет либо он записан прежней редакцией, не знавшей привязки.
        self._stored_account = ""

        if self._ledger is not None:
            stored = self._ledger.load()
            self._state.outbound.restore(stored.get("outbound") or {})
            self._delivered.restore(stored.get("delivery") or {})
            self._price_audit.restore(stored.get("price_audit") or {})
            self._stored_account = str(stored.get("account") or "")
        #: Смены состояния доступа, ждущие выдачи партией.
        self._health_changes: list[tuple[Health, Health, str]] = []

        #: Ошибка, остановившая клиента, если он остановлен.
        #:
        #: Полная остановка, а не отказ одного запроса. Отказ в доступе и
        #: страница проверки - не сбой, а ответ площадки на поведение клиента:
        #: продолжать стучаться после них означает подтверждать подозрение.
        #: Цена ошибки несимметрична - лишняя остановка стоит задержки, лишний
        #: стук стоит аккаунта.
        self._stopped: FunoraError | None = None
        self._identity = (
            identity
            if identity is not None
            else REGISTRY.get(identity_of(None, host_of(settings.base_url) or settings.base_url))
        )

    def capability(self, capability: Capability) -> CapabilityState:
        """Возвращает текущее состояние возможности.

        Возможность, под которую у реализации нет операции, отвергается вслух.
        Таблица начальных состояний отвечает на вопрос о площадке - наблюдается
        ли возможность там, - а вызывающий спрашивает другое: «могу ли я это
        вызвать». Одиннадцать возможностей отвечали ему supported при том, что
        метода под них в SDK нет вовсе.

        Тот же довод, по которому отвергается подписка на непорождаемое
        событие: молчаливое «доступно» отправляет вызывающего в ветку, которой
        не существует.

        Args:
            capability (Capability): Возможность.

        Returns:
            CapabilityState: Состояние, каким его видит клиент сейчас.

        Raises:
            ConfigurationError: Если под возможность нет операции.
        """
        if capability not in IMPLEMENTED:
            raise ConfigurationError(
                f"возможность {capability.value} эта реализация не выполняет: "
                f"операции под неё нет. Выполняются: "
                f"{', '.join(sorted(item.value for item in IMPLEMENTED))}"
            )
        return self._state.capabilities[capability]

    def read_orders(self) -> Generator[Request, Reply, OrdersPage]:
        """Читает список заказов по нормативному порядку шагов.

        Yields:
            Request: Просьбы о вводе-выводе.

        Returns:
            OrdersPage: Разобранная страница.

        Raises:
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        observation = yield from self.fetch_ok(Capability.ORDERS_LIST, ORDERS_PATH)
        page = parse_orders_page(observation.html, observed_at=datetime.now(UTC))
        if not integrity_verified(observation):
            page = unverified(page)
        self._note_success(Capability.ORDERS_LIST, page.completeness, page)
        return page

    def read_account(self) -> Generator[Request, Reply, Account]:
        """Читает собственный аккаунт.

        Балансов не читает: они на другой странице, и брать её ради профиля
        значило бы ходить на площадку дважды за одним ответом.

        Yields:
            Request: Просьбы о вводе-выводе.

        Returns:
            Account: Собственный идентификатор, имя и метка языка.

        Raises:
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        observation = yield from self.fetch_ok(Capability.ACCOUNT_PROFILE, ACCOUNT_PATH)
        account = parse_account(observation.html, observed_at=datetime.now(UTC))
        if account.user_id.is_observed:
            self._bind_account(account.user_id.value)
        damaged = any(one.severity is Severity.PAGE for one in account.defects)
        self._note_success(
            Capability.ACCOUNT_PROFILE,
            Completeness.PARTIAL if damaged else Completeness.COMPLETE,
            None,
        )
        return account

    def read_health(self) -> Generator[Request, Reply, SessionHealth]:
        """Проверяет пригодность сессии самым дешёвым доступным способом.

        ОТЧИТЫВАЕТСЯ, А НЕ ПАДАЕТ. Отказ площадки здесь - это ответ, а не
        происшествие: проверку зовут именно затем, чтобы узнать о нём заранее.

        Результат держится в кэше на объявленный срок. Проверку ставят в цикл
        ядра, а не зовут рукой, и без дросселя она ходила бы на площадку каждый
        тик - ровно то, чего обещание операции запрещает.

        Yields:
            Request: Просьбы о вводе-выводе.

        Returns:
            SessionHealth: Класс ответа, годность сессии и признак кэша.
        """
        now = monotonic()
        cached = self._state.health_cached
        if (
            cached is not None
            and (now - self._state.health_checked_at) * 1000 < MIN_HEALTH_INTERVAL_MS
        ):
            return replace(cached, from_cache=True)

        # Несвежий вердикт хуже отсутствующего: он выглядит свежим.
        self._state.last_verdict = None
        # Отказ - это ответ. Вердикт уже записан классификатором, и по нему
        # проверка и отчитывается: гасится здесь ровно иерархия Funora, чужие
        # исключения проходят насквозь.
        with suppress(FunoraError):
            yield from self.fetch_ok(Capability.ACCOUNT_PROFILE, ACCOUNT_PATH)

        verdict = self._state.last_verdict
        checked_at = datetime.now(UTC)
        if verdict is None:
            # До классификации дело не дошло вовсе: ответа не было. Объявлять по
            # этому «сессия негодна» нельзя - негодна может быть сеть.
            health = SessionHealth(
                response_class=ResponseClass.TRANSPORT_ERROR,
                is_usable=False,
                reason="no_response",
                provisional=False,
                checked_at=checked_at,
                from_cache=False,
                unsafe_marks=frozenset(self._unsafe),
            )
        else:
            health = SessionHealth.of(
                verdict, checked_at, from_cache=False, unsafe_marks=frozenset(self._unsafe)
            )

        self._state.health_cached = health
        self._state.health_checked_at = monotonic()
        return health

    def capability_profile(self) -> CapabilityProfile:
        """Собирает профиль возможностей БЕЗ СЕТИ.

        Профиль отвечает на «что этот клиент умеет прямо сейчас», а это уже
        известно из того, что наблюдалось. Ходить за этим на площадку незачем.

        Ключ объявляется ровно для КАЖДОЙ возможности контракта: профиль,
        умалчивающий о возможности, читался бы как «её нет», а это другой ответ.

        Returns:
            CapabilityProfile: Состояние каждой возможности.
        """
        return CapabilityProfile(
            observed_at=datetime.now(UTC),
            _states={
                one: self._state.capabilities.get(one, CAPABILITY_INITIAL[one])
                for one in Capability
            },
        )

    def send_text(
        self, node_id: str, text: str, *, declared_cold: bool = False
    ) -> Generator[Request, Reply, SendResult]:
        """Отправляет текстовое сообщение в переписку.

        ИСКЛЮЧЕНИЕ ОЗНАЧАЕТ, ЧТО СООБЩЕНИЕ НЕ УШЛО. Всё, что случилось ПОСЛЕ
        ухода запроса, возвращается исходом, а не бросается: иначе
        неоднозначность выражать нечем, и вызывающий прочтёт её как неудачу.

        Порядок шагов:

        1. Чтение страницы диалога. Оттуда берётся всё нужное для запроса, и
           оттуда же снимается опора сверки - лишнего обращения нет.
        2. Ограничитель исходящих. Спрашивается ДО вёдер и до отправки.
        3. Запись попытки - ВПЕРЕДИ запроса, а не после ответа.
        4. Обращение к каналу с подпиской на узел этого диалога. Подписка нужна
           не ради отправки, а ради ответа: канал подтверждает только
           подписанное.
        5. Установление исхода по нормативному порядку.

        Args:
            node_id (str): Числовой идентификатор диалога.
            text (str): Текст сообщения.
            declared_cold (bool): Признание, что переписка холодная и вы пишете
                первым. Без него холодное обращение отвергается.

        Yields:
            Request: Просьбы о вводе-выводе.

        Returns:
            SendResult: Исход, причина и прочитанное из ответа.

        Raises:
            ValidationError: Если идентификатор либо текст непригодны.
            BudgetExhaustedError: Если упёрлись в предел исходящих.
            UsageError: Если холодное обращение не объявлено.
            FunoraError: Если страница непригодна для отправки.
        """
        cleaned = node_id.strip()
        if not cleaned or not cleaned.isalnum():
            raise ValidationError(
                "идентификатор диалога обязан состоять из букв и цифр, получено "
                f"{len(cleaned)} знаков иного вида. Проверка идёт до сети"
            )
        if not text.strip():
            raise ValidationError(
                "текст сообщения пуст. Отправка пустого не наблюдалась, и что с ней "
                "сделает площадка - неизвестно"
            )

        capability = Capability.CHATS_SEND_TEXT
        observation = yield from self.fetch_ok(capability, THREAD_PATH.format(node_id=cleaned))

        context = parse_runner_context(observation.html)
        if not context.can_send:
            raise ProtocolChangedError(
                "страница диалога не годится для отправки: "
                f"{[one.code for one in context.defects]}. Отправить, не прочитав "
                "имени диалога, его метки и защитного токена, нельзя"
            )
        anchor = take_anchor(observation.html)
        self._bind_account(anchor.own_href)

        # Ограничитель спрашивается ПЕРВЫМ среди пределов. Ждать он не умеет и
        # не должен: его пределы часовые.
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        refusal = self._state.outbound.check(
            cleaned, now_ms=now_ms, now_s=monotonic(), declared_cold=declared_cold
        )
        if refusal is not None:
            raise _outbound_error(refusal)

        # Попытка записывается ВПЕРЕДИ запроса. Форма отказа канала не
        # наблюдалась, и «не засчитаем, раз не подтвердилось» означало бы не
        # считать ровно те отправки, которые могли уйти.
        self._state.outbound.record(cleaned, now_ms=now_ms, now_s=monotonic())
        # Реестр сохраняется СРАЗУ, а не в конце шага. Перезапуск между
        # отправкой и концом шага иначе терял бы её из реестра - то есть ровно
        # в том случае, ради которого реестр и заведён.
        self._save_ledger(now_ms=now_ms)

        node_name = context.node_name.value
        data = {
            "node": node_name,
            "last_message": int(context.last_message.value),
            "content": text,
        }
        token = context.csrf_token
        if token is None:  # pragma: no cover - can_send уже это проверил
            raise ProtocolChangedError("защитного токена на странице нет")

        yield from self.spend_budget(_class_of(capability), cost=1.0)
        reply = yield Submit(
            RUNNER_PATH,
            {
                # Подписка ровно на один узел - тот самый диалог. Канал
                # подтверждает только подписанное, а полная подписка недостижима:
                # метка закладок не наблюдалась.
                "objects": json.dumps(
                    [
                        {
                            "type": "chat_node",
                            "id": node_name,
                            "tag": context.chat_tag.value,
                            "data": data,
                        }
                    ],
                    ensure_ascii=False,
                ),
                "request": json.dumps({"action": "chat_message", "data": data}, ensure_ascii=False),
                "csrf_token": token.reveal(),
            },
            dict(RUNNER_HEADERS),
        )
        if not isinstance(reply, Observation):
            raise TypeError(f"на просьбу Submit ожидалось наблюдение, получено {type(reply)}")

        result = classify_send_response(reply.html, sent_to=node_name)

        # СВЕРКА ДЕЛАЕТСЯ ТОЛЬКО ТАМ, ГДЕ ИСХОДА НЕ НАБЛЮДАЛИ. При подтверждённом
        # ответ канала сам несёт новое сообщение, и читать историю незачем:
        # стоимость чтения несимметрична, и на этом весь механизм и держится.
        #
        # Сверка НИЧЕГО НЕ ОТПРАВЛЯЕТ. Решение о повторной отправке принимает
        # вызывающий: у отправленного сообщения нет отмены.
        if not result.is_confirmed:
            verdict = yield from self._reconcile_send(cleaned, anchor)
            result = replace(result, reconciled=str(verdict))

        self._note_success(
            capability,
            Completeness.COMPLETE if result.is_confirmed else Completeness.PARTIAL,
            None,
        )
        return result

    def _reconcile_send(
        self, node_id: str, anchor: Anchor
    ) -> Generator[Request, Reply, ReconcileVerdict]:
        """Сверяется с историей переписки по объявленному расписанию.

        Читает и только читает. Число чтений и паузы объявлены контрактом:
        сообщение появляется в истории не мгновенно, и одно чтение сразу после
        отправки объявило бы отсутствие раньше времени.

        Первый определённый вердикт прекращает чтения. Неопределённый - нет:
        он означает, что сверка ответа не дала, и следующее чтение вправе дать.

        Args:
            node_id (str): Идентификатор диалога.
            anchor (Anchor): Опора, снятая до отправки.

        Yields:
            Request: Просьбы о вводе-выводе.

        Returns:
            ReconcileVerdict: Вердикт последней сверки.
        """
        verdict = ReconcileVerdict.UNDETERMINED
        for delay_ms in RECONCILE_DELAYS_MS:
            yield Pause(delay_ms)
            try:
                thread = yield from self.read_thread(node_id)
            except FunoraError:
                # Отказ чтения - не свидетельство об отправке. Сверка о ней и
                # молчит: вердикт остаётся неопределённым.
                continue
            outcome = reconcile(thread, anchor)
            verdict = outcome.verdict
            if verdict is not ReconcileVerdict.UNDETERMINED:
                return verdict
        return verdict

    def read_lot_form(self, node_id: str, offer_id: str) -> Generator[Request, Reply, LotForm]:
        """Читает форму правки одного предложения.

        ЕДИНСТВЕННОЕ МЕСТО, где виден признак показа лота в выдаче. На странице
        своих лотов его нет ни одного, и модель Lot из-за этого не собиралась
        вовсе.

        Цена известна и записана: одна страница на предложение. У продавца с
        двумя сотнями лотов это две сотни запросов, и потому список своих лотов
        признака по-прежнему не отдаёт.

        Args:
            node_id (str): Идентификатор раздела.
            offer_id (str): Идентификатор предложения.

        Yields:
            Request: Просьбы о вводе-выводе.

        Returns:
            LotForm: Прочитанная форма.

        Raises:
            ValidationError: Если идентификатор непригоден для подстановки.
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        node = _digits(node_id, "раздела")
        offer = _digits(offer_id, "предложения")

        observation = yield from self.fetch_ok(
            Capability.LOTS_LIST_OWN,
            LOT_EDIT_PATH.format(node_id=node, offer_id=offer),
        )
        return parse_lot_form(observation.html, observed_at=datetime.now(UTC))

    def update_price(
        self, node_id: str, offer_id: str, price: str, *, expected_revision: str
    ) -> Generator[Request, Reply, LotForm]:
        """Меняет цену предложения, не трогая ничего другого.

        ПОРЯДОК ЗДЕСЬ И ЕСТЬ ОПЕРАЦИЯ. Форма читается заново, отпечаток
        сверяется с ожидаемым, и отправляется ПРОЧИТАННОЕ - с заменой одной
        цены. Собрать запрос из перечня нужных полей значило бы стереть
        описание лота и сообщение покупателю.

        Args:
            node_id (str): Идентификатор раздела.
            offer_id (str): Идентификатор предложения.
            price (str): Новая цена, как её пишут в поле.
            expected_revision (str): Отпечаток, полученный чтением формы.
                Обязателен: без него параллельная правка перетирается молча.

        Yields:
            Request: Просьбы о вводе-выводе.

        Returns:
            LotForm: Форма, перечитанная ПОСЛЕ сохранения.

        Raises:
            PreconditionFailedError: Если лот успели изменить.
            UsageError: Если лот выключен: поведение снятого флажка не
                наблюдалось, и отправка могла бы включить его молча.
            FunoraError: Если сохранение не состоялось.
        """
        if not expected_revision:
            raise UsageError(
                "нужен expected_revision: без него параллельная правка - из "
                "другого процесса, из приложения, из веб-интерфейса - будет "
                "перетёрта молча. Возьмите его чтением формы"
            )

        # УСЛОВИЕ ОТКАЗА ЧИТАЕТСЯ ИЗ КОНТРАКТА, а не стоит здесь литералом.
        # Спецификация объявляет у операции аудит и его вид - fail_closed, -
        # и если объявление снимут, отказ обязан исчезнуть вместе с ним. Правило,
        # записанное в коде отдельно, пережило бы снятие объявления и осталось
        # бы отказывать неизвестно по чьему требованию.
        #
        # Отказ РАНЬШЕ чтения формы: настройка клиента от страницы не зависит,
        # и ходить за ней ради заведомого отказа значит тратить чужой запрос.
        contract = OPERATIONS["lots.update_price"]
        if contract.audit_fail_closed and not self._price_audit.durable:
            raise ConfigurationError(
                "правка цены отказывает без долговечного журнала: у площадки "
                "нет ни истории цен, ни отката, и вернуть как было можно только "
                "по нашей записи. Передайте клиенту state_path - тот же файл, "
                "что и наблюдению, - либо, если вы понимаете цену, "
                "unsafe_price_changes_without_audit"
            )

        before = yield from self.read_lot_form(node_id, offer_id)
        if before.revision != expected_revision:
            raise PreconditionFailedError(
                f"лот изменился с тех пор, как вы его читали: ожидался отпечаток "
                f"{expected_revision}, на странице {before.revision}. Перечитайте "
                "форму и решите заново"
            )

        if not before.is_active:
            # Что уходит при СНЯТОМ флажке, никто не наблюдал. Отправив форму,
            # мы отправили бы флажок отмеченным - то есть включили бы лот,
            # которого не просили включать.
            raise UsageError(
                "лот выключен, и менять ему цену эта операция отказывается. "
                "Поведение снятого флажка не наблюдалось, а отправка формы "
                "включила бы лот молча - вместе с ценой"
            )

        cleaned = price.strip()
        if not cleaned:
            raise ValidationError("цена пуста: пустое поле стирает цену, а не оставляет прежнюю")

        # Вид аудита тоже из контракта: before_state - сохранить состояние ДО
        # правки. Появись у операции аудит другого вида, здесь станет видно,
        # что исполняется по-прежнему прежний.
        if contract.audit != "before_state":
            raise ConfigurationError(
                f"контракт требует у правки цены аудита {contract.audit!r}, а "
                "реализован before_state. Что именно сохранять - решает "
                "спецификация, и молча исполнять не то нельзя"
            )

        # ЗАПИСЬ ВПЕРЕДИ ОТПРАВКИ. «Запишем, когда подтвердится» означает не
        # записать ровно те правки, которые могли уйти: ответ теряется, процесс
        # падает, а цена на площадке уже новая. Прежней после этого не знает
        # никто.
        self._price_audit.record(
            PriceChange(
                offer_id=offer_id,
                node_id=node_id,
                price_before=before.price_text,
                price_after=cleaned,
                revision_before=before.revision,
                at_ms=int(datetime.now(UTC).timestamp() * 1000),
            )
        )
        self._save_price_audit()

        reply = yield Submit(SAVE_PATH, before.to_request(price=cleaned), {})
        if not isinstance(reply, Observation):
            raise TypeError(f"на просьбу Submit ожидалось наблюдение, получено {type(reply)}")

        # Успех виден ПЕРЕХОДОМ на список своих предложений раздела. Тела
        # ответа страница не получает - его забирает браузер, - и наблюдено
        # именно это.
        landed = urlparse(reply.final_url).path
        # Раздел берётся ИЗ ЗАПРОСА, а не из прочитанной формы. Запрос уходил
        # по нему же, и сверять надо с тем, куда шли: значение в форме - то,
        # что сказала площадка, и подставлять его сюда значило бы сверять её с
        # ней самой.
        expected = OWN_LOTS_PATH.format(node_id=_digits(node_id, "раздела"))
        if landed != expected:
            raise UnexpectedResponseError(
                f"сохранение привело на {landed!r}, а наблюдался переход на "
                f"{expected!r}. Что случилось с лотом - неизвестно, и объявлять "
                "успех по чужому адресу нельзя"
            )

        return (yield from self.read_lot_form(node_id, offer_id))

    def read_own_lots(self, node_id: str) -> Generator[Request, Reply, OwnLotsPage]:
        """Читает собственные лоты продавца в одном разделе.

        РАДИ ИДЕНТИФИКАТОРА ПРЕДЛОЖЕНИЯ. Витрина на профиле показывает те же
        лоты и даже больше полей, но идентификатора не даёт: там он лежит в
        строке запроса ссылки. Всем четырём операциям записи над лотами он нужен,
        и взять его больше неоткуда.

        Args:
            node_id (str): Номер раздела. Управление лотами живёт по одному
                адресу на раздел, а не по одному на аккаунт.

        Yields:
            Request: Просьбы о вводе-выводе.

        Returns:
            OwnLotsPage: Лоты раздела и доводы кнопки поднятия.

        Raises:
            ValidationError: Если номер раздела непригоден для подстановки.
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        cleaned = node_id.strip()
        if not cleaned or not cleaned.isdigit():
            raise ValidationError(
                "номер раздела обязан состоять из цифр, получено "
                f"{len(cleaned)} знаков иного вида. Проверка идёт до сети: "
                "подставленный в адрес мусор отправил бы запрос неизвестно куда"
            )

        capability = Capability.LOTS_LIST_OWN
        observation = yield from self.fetch_ok(capability, OWN_LOTS_PATH.format(node_id=cleaned))
        page = parse_own_lots(observation.html, observed_at=datetime.now(UTC))
        self._note_success(capability, page.completeness, None)
        return page

    def read_catalog(self) -> Generator[Request, Reply, CatalogPage]:
        """Читает каталог с корня площадки.

        Yields:
            Request: Просьбы о вводе-выводе.

        Returns:
            CatalogPage: Игры каталога с их вариантами и разделами.

        Raises:
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        observation = yield from self.fetch_ok(Capability.CATALOG_CATEGORIES, CATALOG_PATH)
        page = parse_catalog(observation.html, observed_at=datetime.now(UTC))
        if not integrity_verified(observation):
            page = unverified(page)
        self._note_success(Capability.CATALOG_CATEGORIES, page.completeness, None)
        return page

    def read_balance(self) -> Generator[Request, Reply, BalancePage]:
        """Читает страницу баланса: балансы по валютам и операции по счёту.

        Операции приходят тем же чтением, потому что лежат на той же странице.
        Отдельный запрос за ними означал бы два похода на площадку за одним
        ответом.

        Yields:
            Request: Просьбы о вводе-выводе.

        Returns:
            BalancePage: Балансы и операции.

        Raises:
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        observation = yield from self.fetch_ok(Capability.ACCOUNT_BALANCE, BALANCE_PATH)
        page = parse_balance_page(observation.html, observed_at=datetime.now(UTC))
        if not integrity_verified(observation):
            page = unverified(page)
        self._note_success(Capability.ACCOUNT_BALANCE, page.completeness, None)

        # Состояние возможности чтения операций выставляется ОТДЕЛЬНО и не
        # через _note_success: своей операции у неё нет, и в перечне выполняемых
        # ей не место. Площадка вправе показать баланс и не показать историю, и
        # сводить их в одно состояние значило бы объявить историю работающей по
        # факту баланса.
        self._state.capabilities[Capability.ACCOUNT_TRANSACTIONS] = (
            CapabilityState.SUPPORTED
            if page.rows_total and page.completeness is Completeness.COMPLETE
            else CapabilityState.DEGRADED
        )
        return page

    def read_order(self, order_id: str) -> Generator[Request, Reply, OrderView]:
        """Читает страницу одного заказа.

        Args:
            order_id (str): Номер заказа. Тот самый, что стоит в адресе.

        Yields:
            Request: Просьбы о вводе-выводе.

        Returns:
            OrderView: Заказ в том виде, в каком его отдала страница.

        Raises:
            ValidationError: Если номер непригоден для подстановки.
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        cleaned = order_id.strip()
        if not cleaned or not cleaned.isalnum():
            raise ValidationError(
                "номер заказа обязан состоять из букв и цифр, получено "
                f"{len(cleaned)} знаков иного вида. Проверка идёт до сети: "
                "подставленный в адрес мусор отправил бы запрос неизвестно куда"
            )

        capability = Capability.ORDERS_GET
        observation = yield from self.fetch_ok(capability, ORDER_PATH.format(order_id=cleaned))
        view = parse_order_page(observation.html, observed_at=datetime.now(UTC))

        # Полнота выводится из повреждений, а не объявляется постоянной. Прежде
        # здесь стояло COMPLETE безусловно - и возможность объявлялась
        # работающей даже тогда, когда состояние заказа не прочиталось.
        damaged = any(one.severity is Severity.PAGE for one in view.defects)
        self._note_success(
            capability,
            Completeness.PARTIAL if damaged else Completeness.COMPLETE,
            None,
        )
        return view

    def read_showcase(self, user_id: str) -> Generator[Request, Reply, ShowcasePage]:
        """Читает витрину продавца со страницы его профиля.

        Витрина лежит на той же странице, что и отзывы. Отдельным чтением она
        объявлена потому, что это другой вопрос к площадке: отзывы говорят о
        продавце, витрина - о том, что он продаёт.

        Args:
            user_id (str): Идентификатор продавца.

        Yields:
            Request: Просьбы о вводе-выводе.

        Returns:
            ShowcasePage: Разделы витрины с их предложениями.

        Raises:
            ValidationError: Если идентификатор непригоден для подстановки.
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        cleaned = user_id.strip()
        if not cleaned or not cleaned.isalnum():
            raise ValidationError(
                "идентификатор продавца обязан состоять из букв и цифр, получено "
                f"{len(cleaned)} знаков иного вида. Проверка идёт до сети: "
                "подставленный в адрес мусор отправил бы запрос неизвестно куда"
            )

        capability = Capability.LOTS_SHOWCASE
        observation = yield from self.fetch_ok(capability, SHOWCASE_PATH.format(user_id=cleaned))
        page = parse_showcase(observation.html, observed_at=datetime.now(UTC))
        if not integrity_verified(observation):
            page = unverified(page)
        self._note_success(capability, page.completeness, None)
        return page

    def read_reviews(self, user_id: str) -> Generator[Request, Reply, ReviewsPage]:
        """Читает отзывы с профиля продавца.

        Отдельной страницы у отзывов нет: они лежат на профиле, и запрос идёт
        туда же, куда пошёл бы за именем и оценкой.

        Args:
            user_id (str): Идентификатор продавца. Тот самый, что стоит в адресе
                профиля.

        Yields:
            Request: Просьбы о вводе-выводе.

        Returns:
            ReviewsPage: Разобранная страница отзывов.

        Raises:
            ValidationError: Если идентификатор непригоден для подстановки.
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        cleaned = user_id.strip()
        if not cleaned or not cleaned.isalnum():
            raise ValidationError(
                "идентификатор продавца обязан состоять из букв и цифр, получено "
                f"{len(cleaned)} знаков иного вида. Проверка идёт до сети: "
                "подставленный в адрес мусор отправил бы запрос неизвестно куда"
            )

        capability = Capability.REVIEWS_GET
        observation = yield from self.fetch_ok(capability, PROFILE_PATH.format(user_id=cleaned))
        page = parse_reviews_page(observation.html, observed_at=datetime.now(UTC))
        if not integrity_verified(observation):
            page = unverified(page)
        self._note_success(capability, page.completeness, page)
        return page

    def read_chats(self) -> Generator[Request, Reply, ChatsPage]:
        """Читает список диалогов по тому же порядку шагов.

        Yields:
            Request: Просьбы о вводе-выводе.

        Returns:
            ChatsPage: Разобранная страница.

        Raises:
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        observation = yield from self.fetch_ok(Capability.CHATS_LIST, CHATS_PATH)
        page = parse_chats_page(observation.html, observed_at=datetime.now(UTC))
        if not integrity_verified(observation):
            page = unverified(page)
        self._note_success(Capability.CHATS_LIST, page.completeness, page)
        return page

    def _bind_account(self, reference: str) -> None:
        """Сверяет файл состояния с аккаунтом и закрепляет его за ним.

        ЗАЧЕМ. Файл состояния сверял формат, семейство адаптера и версию
        канонической формы - и не сверял, ЧЬИ в нём записи. Реестр, снятый с
        одного аккаунта, молча применялся к другому.

        Цена этого не в квоте, хотя и в ней тоже. Хуже другое: реестр
        ВЫДАННОГО. Заказ второго аккаунта с тем же номером считался бы уже
        выданным, и товар покупателю не ушёл бы вовсе - без отказа, без строки
        в журнале, без единого следа.

        ПОЧЕМУ ПРИВЯЗКА ЛЕНИВАЯ. Аккаунт становится известен только из ответа
        площадки, а файл открывается в конструкторе: сверить в момент открытия
        нечем. Зато сверить можно в тот момент, когда узнали, - и это раньше
        любой отправки, потому что отправка сама читает страницу диалога.

        ПОЧЕМУ НЕ ПО СЕКРЕТУ. Ключ сессии законно меняется - при смене пароля
        площадка выдаёт новый. Привязка к нему отвергала бы файл после каждой
        смены, и продавец, послушавшись отказа, удалял бы файл вместе с реестром
        выданного. То есть защита от подмены аккаунта приводила бы к повторной
        выдаче.

        Args:
            reference (str): Признак аккаунта: собственный номер либо адрес
                собственного профиля. Пустая строка означает «не узнали», и
                тогда метод не делает ничего.

        Returns:
            None

        Raises:
            CursorIncompatibleError: Если файл записан другим аккаунтом.
        """
        if not reference or self._ledger is None:
            return

        if self._stored_account and self._stored_account != reference:
            raise CursorIncompatibleError(
                f"файл состояния {self._ledger.path} записан аккаунтом "
                f"{self._stored_account!r}, а работаем мы под {reference!r}. "
                "Реестр выданного и пределы отправки принадлежат другому "
                "аккаунту: по чужому реестру заказ считался бы уже выданным, и "
                "товар покупателю не ушёл бы вовсе. Дайте каждому аккаунту свой "
                "файл состояния"
            )

        if not self._stored_account:
            self._stored_account = reference
            self._ledger.update({"account": reference})

    @property
    def delivered(self) -> DeliveryLedger:
        """Реестр выданного по заказам.

        Живёт у движка, а не у автовыдачи, по той же причине, по какой у него
        живёт реестр отправок: файл состояния один, и владеть им должен тот, кто
        его открыл.

        Returns:
            DeliveryLedger: Реестр.
        """
        return self._delivered

    def save_delivery(self) -> None:
        """Сохраняет реестр выданного.

        Зовётся ВПЕРЕДИ отправки товара, как и запись в сам реестр: «сохраним,
        когда подтвердится» означает не сохранить ровно те выдачи, которые
        могли уйти.

        Без файла состояния не делает ничего и об этом не жалуется: отказывать
        здесь нечему, отказывает отправка.

        Returns:
            None
        """
        if self._ledger is None:
            return
        self._ledger.update({"delivery": self._delivered.snapshot()})

    @property
    def price_audit(self) -> PriceAudit:
        """Журнал правок цены.

        Живёт у движка по той же причине, что и реестр выданного: файл
        состояния один, и владеть им должен тот, кто его открыл.

        Returns:
            PriceAudit: Журнал.
        """
        return self._price_audit

    def _save_price_audit(self) -> None:
        """Сохраняет журнал правок цены.

        Зовётся ВПЕРЕДИ отправки формы. «Сохраним, когда подтвердится» означает
        не сохранить ровно те правки, которые могли уйти, а вернуть цену без
        записи о прежней нечем: истории цен у площадки нет.

        Без файла состояния не делает ничего. Отказывать здесь нечему -
        отказывает сама правка, и отказывает раньше.

        Returns:
            None
        """
        if self._ledger is None:
            return
        self._ledger.update({"price_audit": self._price_audit.snapshot()})

    def _save_ledger(self, *, now_ms: int) -> None:
        """Сохраняет реестр отправок в файл состояния.

        Правкой, а не записью целиком: тот же файл держит курсоры и гашение
        повторов, и запись целиком затёрла бы их - перезапуск ушёл бы в холодный
        старт.

        Args:
            now_ms (int): Текущий момент по стенным часам. Нужен, чтобы выбросить
                просроченные записи перед сохранением: реестр иначе растёт
                столько же, сколько живёт аккаунт.

        Returns:
            None
        """
        if self._ledger is None:
            return
        self._state.outbound.forget_expired(now_ms=now_ms, now_s=monotonic())
        self._ledger.update({"outbound": self._state.outbound.snapshot()})

    def read_thread(self, node_id: str) -> Generator[Request, Reply, Thread]:
        """Читает переписку по тому же порядку шагов.

        Args:
            node_id (str): Идентификатор диалога.

        Yields:
            Request: Просьбы о вводе-выводе.

        Returns:
            Thread: Разобранная переписка.

        Raises:
            ValidationError: Если идентификатор непригоден для подстановки.
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        thread, _ = yield from self._read_thread_observed(node_id)
        return thread

    def _read_thread_observed(self, node_id: str) -> Generator[Request, Reply, tuple[Thread, str]]:
        """Читает переписку и заодно снимает адрес собственного профиля.

        Адрес нужен, чтобы определить направление сообщения, а определить его
        можно только по ТОЙ ЖЕ странице: он лежит в меню вошедшего, и другого
        носителя, годного для всякой переписки, нет.

        Метод внутренний, и публичный read_thread адрес выбрасывает. Отдавать
        его наружу значило бы обещать вызывающему, что адрес всегда есть, - а
        он есть не всегда: два узла меню могут разойтись, и тогда снимать
        нечего.

        Args:
            node_id (str): Идентификатор диалога.

        Yields:
            Request: Просьбы о вводе-выводе.

        Returns:
            tuple[Thread, str]: Разобранная переписка и адрес собственного
            профиля. Пустая строка означает, что адрес снять не удалось.

        Raises:
            ValidationError: Если идентификатор непригоден для подстановки.
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        cleaned = node_id.strip()
        if not cleaned or not cleaned.isalnum():
            raise ValidationError(
                "идентификатор диалога обязан состоять из букв и цифр, получено "
                f"{len(cleaned)} знаков иного вида. Проверка идёт до сети: "
                "подставленный в адрес мусор отправил бы запрос неизвестно куда"
            )

        capability = Capability.CHATS_HISTORY
        observation = yield from self.fetch_ok(capability, THREAD_PATH.format(node_id=cleaned))
        thread = parse_thread(
            observation.html,
            observed_at=datetime.now(UTC),
            host=host_of(self._settings.base_url),
        )
        if not integrity_verified(observation):
            thread = unverified(thread)
        self._note_success(capability, thread.completeness, thread)
        # Тот же приём и тот же узел, что у опоры сверки отправки. Второго
        # правила для одного и того же адреса заводить незачем: разойдись они -
        # и своё сообщение считалось бы чужим в одном месте и своим в другом.
        own_href = take_anchor(observation.html).own_href
        # Личность узнаётся заодно с чтением переписки, и сверка идёт здесь же:
        # это раньше любой отправки, потому что отправка сама читает страницу.
        self._bind_account(own_href)
        return thread, own_href

    def fetch_ok(self, capability: Capability, path: str) -> Generator[Request, Reply, Observation]:
        """Получает пригодный для разбора ответ по нормативному порядку шагов.

        Метод общий для всех операций чтения намеренно. Порядок шагов нормативен,
        а скопированный порядок расходится: правку вносят в одно место, забывают
        о втором, и две операции одного клиента начинают вести себя по-разному на
        одной и той же странице.

        Args:
            capability (Capability): Возможность, под которую идёт чтение.
            path (str): Путь запрашиваемой страницы.

        Yields:
            Request: Просьбы о вводе-выводе.

        Returns:
            Observation: Ответ, признанный пригодным для разбора.

        Raises:
            FunoraError: Если ответ непригоден и повтор не помог либо не положен.
        """
        check_capability(
            capability,
            state=self._state.capabilities[capability],
            opted_in=capability in self._state.opted_in,
        )

        host = host_of(self._settings.base_url)
        attempt = 0
        while True:
            attempt += 1
            # Заголовок Retry-After живёт в ответе, а до ответа его нет. Значение
            # держится отдельной переменной, чтобы обработчик не зависел от
            # того, успел ли ответ появиться.
            retry_after_ms: int | None = None
            # Признак решает СТОИМОСТЬ повтора, а не право идти. Повтор при
            # выключенном признаке проходит через бюджет с нулевой ценой:
            # токенов не тратит, но долю более защищённых классов держит и
            # остывание идентичности выжидает.
            #
            # Отменять вызов целиком нельзя. Тогда клиент, получивший 429,
            # повторял бы запрос мимо собственного отступления - шторм повторов
            # стал бы не бесплатным, а неостановимым.
            cost = 1.0 if (attempt == 1 or COUNTS_RETRIES) else 0.0
            yield from self.spend_budget(_class_of(capability), cost=cost)
            try:
                reply = yield Fetch(path)
                if not isinstance(reply, Observation):
                    # Нарушение договора между ядром и драйвером. Ошибка не из
                    # иерархии Funora намеренно: политика повторов её не увидит
                    # и не примет баг драйвера за отказ площадки.
                    raise TypeError(
                        f"на просьбу Fetch ожидалось наблюдение, получено {type(reply)}"
                    )
                observation = reply
                # Переходы уже случились и запросы уже ушли. Бюджет за них
                # списывается вслед, а не заранее: заранее их число неизвестно.
                # Не списывать вовсе нельзя - спецификация требует считать
                # отправленные запросы, и цепочка переходов оказалась бы
                # бесплатной ровно тогда, когда площадка нас куда-то гоняет.
                # То же для переходов: при выключенном признаке они проходят
                # через бюджет ценой ноль, а не мимо него.
                yield from self.settle(
                    observation.requests_sent - 1,
                    _class_of(capability),
                    cost=1.0 if COUNTS_REDIRECTS else 0.0,
                )
                retry_after_ms = observation.retry_after_ms
                # Целостность проверяется ВНУТРИ классификатора, вторым шагом
                # после кода ответа. Прежде она стояла здесь, то есть первой, и
                # это меняло диагноз: ответ 429 либо 503 с оборванным телом
                # становился сетевым отказом, а не ограничением частоты и не
                # техническими работами.
                #
                # Цена перестановки видна как раз на 429. Сетевой отказ
                # повторяется коротким отступлением и не режет ёмкость ведра -
                # то есть клиент продолжал бы стучаться в прежнем темпе ровно
                # тогда, когда площадка сказала «слишком быстро».
                verdict = classify(
                    status=observation.status,
                    final_url=observation.final_url,
                    html=observation.html,
                    expected_host=host,
                    identity_css=DEFAULT_IDENTITY_CSS,
                    declared_length=observation.declared_length,
                    received_length=observation.content_length,
                    content_encoding=observation.content_encoding,
                )
                self.note_locale(observation.html)
                self.note_health(verdict)
                # Вердикт запоминается ДО возможного отказа: проверка сессии
                # отчитывается о состоянии, а не падает от него.
                self._state.last_verdict = verdict
                error = error_for(verdict, session_ever_valid=self._state.session_ever_valid)
                if error is not None:
                    self.note_stop(error)
                    raise error
            except RateLimitedError as exc:
                # Ограничение частоты - про источник целиком, а не про один
                # запрос. Политика повторов решает, когда повторить именно этот;
                # реакция идентичности решает, как пойдут ВСЕ следующие: ёмкость
                # режется вдвое, идентичность остывает.
                #
                # Прежде этого не делалось вовсе: 429 переводился в ошибку и
                # уходил в политику повторов, а ёмкость оставалась прежней -
                # следующий залп был ровно таким же, каким был до ограничения.
                # Признак account_scoped у политики означает, что отступает
                # вся идентичность, а не один запрос. Заголовок при этом уже
                # урезан политикой по max_retry_after_ms.
                self._identity.note_limit(
                    monotonic(),
                    retry_after_ms=retry_after_ms if _scoped(exc) else None,
                )

                # Третья ступень. Третье ограничение в окне - уже не про темп, а
                # про то, как площадка относится к аккаунту: состояние доступа
                # становится rate_limited, и автоматика записи приостанавливается.
                #
                # Разница со второй ступенью в том, кто решает. Пауза второй
                # истекает вместе с остыванием; состояние третьей не истекает -
                # оно снимается успешным ответом либо явным действием
                # пользователя. Иначе клиент вернулся бы писать на площадку,
                # которая трижды сказала «слишком быстро», и не спросил никого.
                if self._identity.limits_seen >= 3:
                    self.enter_health(
                        Health.RATE_LIMITED,
                        reason=f"rate_limited_{self._identity.limits_seen}_in_window",
                    )

                self._note_failure(capability, exc)
                plan = plan_attempt(
                    exc,
                    attempt=attempt,
                    safety=_safety_of(capability),
                    retry_after_ms=retry_after_ms,
                )
                if not plan.retry:
                    # Причина уходит НАРУЖУ, а не только в журнал. Прежде здесь
                    # стоял голый raise, и вызывающий не мог отличить «повторять
                    # нельзя, сперва сверься» от «повторять бессмысленно».
                    setattr(exc, RETRY_REASON_ATTR, plan.reason)
                    raise
                _log.info(
                    "повтор после ограничения частоты через %d мс (попытка %d)",
                    plan.delay_ms,
                    attempt,
                )
                yield Pause(plan.delay_ms)
                attempt += 1
                continue
            except FunoraError as exc:
                self._note_failure(capability, exc)
                plan = plan_attempt(
                    exc,
                    attempt=attempt,
                    # Безопасность берётся из таблицы операций, а не ставится
                    # здесь константой. Все выполняемые сегодня операции -
                    # чтения, и константа совпадала бы с таблицей; но первая же
                    # операция записи получила бы повтор наравне с чтением,
                    # потому что константа о ней не знает.
                    safety=_safety_of(capability),
                    retry_after_ms=retry_after_ms,
                )
                if not plan.retry:
                    setattr(exc, RETRY_REASON_ATTR, plan.reason)
                    raise
                _log.info(
                    "повтор после %s через %d мс (попытка %d, причина %s)",
                    type(exc).__name__,
                    plan.delay_ms,
                    attempt,
                    plan.reason,
                )
                yield Pause(plan.delay_ms)
                continue

            self._state.session_ever_valid = True
            return observation

    def _follow(
        self,
        pending: list[str],
        known: dict[str, frozenset[str]],
        *,
        account_id: str,
        limit: int,
    ) -> Generator[Request, Reply, tuple[tuple[Event, ...], dict[str, frozenset[str]], list[str]]]:
        """Дочитывает переписки диалогов, о которых сказал список.

        Метод существует потому, что событие о новом сообщении иначе не
        порождается вовсе. Список диалогов говорит, что диалог изменился, но не
        говорит чем: сообщение видно только на странице самой переписки.

        Читать все переписки на каждом шаге нельзя - полсотни диалогов дали бы
        полсотни запросов в минуту, а спецификация относит чтение переписки к
        разряду interactive. Поэтому читаются только изменившиеся, и не больше
        предела за шаг.

        Очередь при этом не выбрасывается. Изменись разом больше диалогов, чем
        предел, - остальные дождутся следующих шагов. Выбросить их значило бы
        потерять сообщения молча: событие об изменении диалога уже доставлено,
        курсор диалогов сдвинут, и повода вернуться к такому диалогу больше нет.

        Первое чтение переписки событий не порождает. Отличить новое сообщение
        от давнего в ней нечем: курсора для этой переписки ещё не было, и
        объявить новыми все её сообщения значило бы разослать историю целиком.
        Это то же правило, по которому молчит холодный старт.

        Отказ на одной переписке не отменяет остальные и не отменяет шаг.
        Диалог при этом выбывает из очереди: повторять вечно то, что не
        читается, значило бы запереть очередь навсегда, а следующее изменение
        того же диалога вернёт его обратно.

        Курсоры прочитанного метод НЕ применяет, а возвращает наверх. Разница
        стоила потерянных сообщений: применённый здесь курсор сдвигался до того,
        как обработчик увидел событие, и не откатывался вместе с остальными -
        упавший обработчик терял сообщение навсегда, при зелёном шаге и строке
        «они придут снова» в журнале.

        Args:
            pending (list[str]): Очередь диалогов, ожидающих дочитывания.
                Изменяется на месте: взятые в работу убираются.
            known (dict[str, frozenset[str]]): Курсоры переписок по диалогам.
                Только читается: применяет их вызывающий, после раздачи.
            account_id (str): Идентификатор аккаунта.
            limit (int): Сколько переписок прочитать за этот шаг.

        Yields:
            Request: Просьбы о вводе-выводе.

        Returns:
            tuple[tuple[Event, ...], dict[str, frozenset[str]], list[str]]:
            События о новых сообщениях; курсоры полностью прочитанных переписок;
            диалоги, прочитанные на этом шаге. Последнее нужно, чтобы вернуть их
            в очередь, если раздача не удалась.
        """
        events: list[Event] = []
        cursors: dict[str, frozenset[str]] = {}
        followed: list[str] = []

        for _ in range(max(0, limit)):
            if not pending:
                break
            node_id = pending.pop(0)

            try:
                thread, own_href = yield from self._read_thread_observed(node_id)
            except AuthenticationError:
                # Условие аккаунта, а не этой переписки. Спецификация требует
                # закрываться: продолжать перебирать очередь значило бы стучать
                # ещё столько раз, сколько в ней узлов, - и всё это при
                # заблокированном доступе. Диалог возвращается в очередь: он ни
                # в чём не виноват.
                pending.insert(0, node_id)
                raise
            except (TransportError, BudgetError) as exc:
                # Отказ временный, и повторы внутри чтения его уже не вылечили.
                # Диалог возвращается в очередь и дочитывается следующим шагом:
                # выбросить его значило бы поставить доставку в зависимость от
                # того, напишет ли покупатель ещё раз.
                pending.insert(0, node_id)
                _log.warning(
                    "переписка %s отложена до следующего шага: %s",
                    node_id,
                    type(exc).__name__,
                )
                break
            except FunoraError as exc:
                # Отказ относится к самой переписке: адрес не подставляется,
                # разметка не разбирается. Такой диалог выбывает - повторять
                # вечно нечитаемое значило бы жечь слот каждый шаг. Следующее
                # его изменение вернёт узел обратно.
                _log.warning(
                    "переписка %s не прочитана и выбывает из очереди: %s",
                    node_id,
                    type(exc).__name__,
                )
                continue

            followed.append(node_id)
            if thread.completeness is not Completeness.COMPLETE:
                # Неполно прочитанная переписка объявляется так же, как неполно
                # прочитанный список. Прежде объявлялись только списки, и это
                # была половина правила: для торгового бота переписка - главное
                # место, а неполно прочитанная означает, что часть сообщений
                # покупателя не увидели вовсе.
                #
                # Событие несёт ссылку на диалог: неполон не весь снимок, а одна
                # переписка, и без ссылки получатель не узнает, какая из
                # полусотни прочитана наполовину.
                events.append(
                    incomplete(
                        account_id,
                        thread.observed_at,
                        entity="thread",
                        entity_ref=node_id,
                        reason=thread.reason,
                        rows_total=thread.rows_total,
                        rows_accepted=thread.rows_accepted,
                    )
                )
            fresh = diff_thread(
                known.get(node_id),
                thread,
                account_id=account_id,
                chat_id=node_id,
                own_href=own_href,
            )
            events.extend(fresh)

            # СОГРЕВАНИЕ ПЕРЕПИСКИ, и вот единственное место, откуда его можно
            # позвать честно.
            #
            # Ограничитель исходящих считает переписку холодной, пока не увидит
            # входящего сообщения. Пока звать его было неоткуда, холодной
            # оставалась ВСЯКАЯ переписка: методы согревания были написаны,
            # проверены поодиночке и не вызывались из рабочего кода ни разу.
            #
            # Цена молчания была не мелкой. Продавец, написавший автоответчик,
            # получал cold_outreach_not_declared на каждый ответ покупателю,
            # который сам ему только что написал: три обращения в сутки вместо
            # тридцати сообщений в час. То есть ограничитель запрещал ровно тот
            # случай, ради разрешения которого согревание и заводилось.
            #
            # Греет ТОЛЬКО входящее и только событием о новом сообщении - так
            # объявлено в spec/runtime/budget.yaml, раздел warming. Событие об
            # изменении диалога греть не вправе: счётчик непрочитанного двигает
            # и НАША отправка, и ограничитель, гревшийся на нём, отменял бы сам
            # себя. Направление unknown не греет тоже: тепло требует
            # положительного свидетельства, а не отсутствия опровержения.
            warmed_at = int(thread.observed_at.timestamp() * 1000)
            for event in fresh:
                if event.payload.get("direction") == "inbound":
                    self._state.outbound.note_incoming(node_id, at_ms=warmed_at)
                    break

            # Курсор переписки снимается с ЛЮБОГО чтения, в отличие от курсоров
            # списков. Разница не в небрежности, а в том, что означает событие.
            #
            # Курсор списка, снятый с неполного чтения, теряет выпавшие строки,
            # и при следующем чтении они выглядят новыми заказами: бот выдаёт
            # товар по заказу, который был и раньше. Это утверждение о мире, и
            # оно оказывается ложным.
            #
            # У переписки иначе. Сообщение, выпавшее из неполного чтения и
            # попавшее в следующее, действительно новое - для нас: события о нём
            # никто не получал. message.created говорит «вот сообщение, о
            # котором вам не сообщали», а не «сообщение только что написано».
            # Повторить такое дешевле, чем промолчать, и доставка объявлена
            # как минимум однократной.
            #
            # Цена прежнего правила была высока: первое чтение переписки,
            # оказавшееся неполным, оставляло курсор пустым навсегда, а пустой
            # курсор молчит по правилу первого чтения. Диалог замолкал совсем,
            # тратя запрос на каждом шаге. Для торгового бота первое чтение
            # переписки - это первое сообщение нового покупателя.
            cursors[node_id] = thread_cursor(thread)

        return tuple(events), cursors, followed

    def drain_health(self, account_id: str, observed_at: datetime) -> tuple[Event, ...]:
        """Отдаёт накопленные смены состояния доступа событиями.

        Смены копятся во время чтения и выдаются партией: породить событие
        посреди чтения значило бы отдать его вне партии - без порядка, без
        гашения повторов и без учёта в курсоре.

        Args:
            account_id (str): Идентификатор аккаунта.
            observed_at (datetime): Момент наблюдения, общий для партии.

        Returns:
            tuple[Event, ...]: События protocol.health_changed. Пустой кортеж,
            если состояние не менялось.
        """
        if not self._health_changes:
            return ()
        events = tuple(
            health_changed(
                account_id,
                observed_at,
                before=str(before),
                after=str(after),
                reason=reason,
                writes_paused=after in WRITES_PAUSED_IN,
            )
            for before, after, reason in self._health_changes
        )
        self._health_changes.clear()
        return events

    def enter_health(self, target: Health, *, reason: str) -> None:
        """Переводит состояние доступа принудительно.

        Нужно там, где состояние определяется не вердиктом одного ответа, а
        накопленным счётом: третье ограничение частоты в окне говорит о
        площадке больше, чем каждое из них по отдельности.

        Args:
            target (Health): Новое состояние.
            reason (str): Машиночитаемая причина перехода.

        Returns:
            None
        """
        if target is self._state.health:
            return
        before = self._state.health
        self._state.health = target
        self._health_changes.append((before, target, reason))
        _log.warning(
            "состояние доступа: %s -> %s (%s). Автоматика записи %s",
            before,
            target,
            reason,
            "приостановлена" if target in WRITES_PAUSED_IN else "разрешена",
        )

    def note_locale(self, html: str) -> None:
        """Запоминает локаль страницы и сверяет её с объявленными.

        Локаль вне перечня НЕ отменяет чтение: разбор опирается на классы
        разметки, а не на текст. Отказать из-за неё значило бы отвергнуть
        страницу, которую реализация читает целиком и верно.

        Опускается возможность protocol.locale - она и означает «интерфейс на
        той локали, для которой у адаптера есть шаблоны».

        Args:
            html (str): Разметка прочитанной страницы.

        Returns:
            None
        """
        observed = observe_locale(html)
        if not observed.is_observed:
            self._state.locale = observed
            return

        known = self._state.locale.or_none()
        self._state.locale = observed
        if observed.value == known:
            return

        if observed.value in SUPPORTED_LOCALES:
            self._state.capabilities[Capability.PROTOCOL_LOCALE] = CapabilityState.SUPPORTED
            return

        self._state.capabilities[Capability.PROTOCOL_LOCALE] = CapabilityState.UNSUPPORTED
        _log.warning(
            "интерфейс отдан на локали %r, объявлены %s. Разбор от этого не "
            "ломается - он структурный, - но поля, приходящие текстом, придут "
            "на этом языке",
            observed.value,
            ", ".join(SUPPORTED_LOCALES),
        )

    def note_health(self, verdict: Verdict) -> None:
        """Обновляет состояние доступа по вердикту классификатора.

        Смена копится, а не порождает событие немедленно: события выдаются
        партией на шаге наблюдения, и породить его посреди чтения значило бы
        отдать вызывающему событие вне партии - без порядка, без гашения
        повторов и без учёта в курсоре.

        Переход в то же состояние не копится. Иначе каждый повторный отказ
        порождал бы событие, и поток сообщений о неизменном состоянии заглушил
        бы сообщение о его изменении.

        Args:
            verdict (Verdict): Вердикт классификатора.

        Returns:
            None
        """
        target = HEALTH_BY_VERDICT.get(str(verdict.cls))
        if target is None or target is self._state.health:
            return

        before = self._state.health
        self._state.health = target
        self._health_changes.append((before, target, verdict.reason))
        _log.info(
            "состояние доступа: %s -> %s (%s). Автоматика записи %s",
            before,
            target,
            verdict.reason,
            "приостановлена" if target in WRITES_PAUSED_IN else "разрешена",
        )

    def note_stop(self, error: FunoraError) -> None:
        """Останавливает клиента, если политика ошибки объявила полную остановку.

        Признак fail_closed стоит у отказа в доступе и у страницы проверки.
        Обе - ответ площадки на поведение клиента, а не сбой связи, и повторять
        их бессмысленно: короткое отступление тут запрещено прямо.

        Args:
            error (FunoraError): Ошибка, полученная от классификатора.

        Returns:
            None
        """
        policy = RETRY_POLICIES.get(getattr(error, "stable_id", ""))
        if policy is None or not policy.fail_closed:
            return
        if self._stopped is not None:
            return

        self._stopped = error
        _log.error(
            "клиент остановлен: %s. Возобновление - только явным вызовом resume(): "
            "истекающая остановка означала бы возврат на площадку, которая "
            "отказала в доступе, без чьего-либо ведома",
            type(error).__name__,
        )

    def resume(self) -> None:
        """Снимает полную остановку.

        Решение вернуться принимает человек: он один знает, разобрался ли с
        причиной. Сама по себе остановка не истекает и по времени не снимается.

        Returns:
            None
        """
        if self._stopped is None:
            return
        _log.warning(
            "остановка снята вручную: клиент снова пойдёт на площадку после %s",
            type(self._stopped).__name__,
        )
        self._stopped = None

    @property
    def stopped(self) -> FunoraError | None:
        """Возвращает ошибку, остановившую клиента.

        Returns:
            FunoraError | None: Ошибка либо None, если клиент работает.
        """
        return self._stopped

    def wait_out_cooldown(self) -> Generator[Request, Reply, None]:
        """Выжидает остывание идентичности, если оно идёт.

        Пауза выдаётся вызывающему, а не спится здесь: ядро не спит само -
        спать синхронно и асинхронно разные вещи, а решать, сколько ждать, одна
        и та же.

        Ожидание однократное. Второй проверки нет намеренно: остывание
        назначается ограничением, а не тикает само, и цикл ожидания здесь
        превратил бы одно ограничение в бесконечную паузу, если бы часы пошли
        назад.

        Пауза округляется объявленной величиной, а не литералом. Спецификация
        распространяет правило на всякую паузу, вычисленную из монотонных
        секунд: после неё вызывающий спрашивает то же самое снова, и пауза
        вровень приводит его туда, где условие ещё не выполнено.

        Returns:
            Generator[Request, Reply, None]: Сопрограмма, выдающая паузу.
        """
        now = monotonic()
        if not self._identity.is_cooling(now):
            return

        wait_ms = int((self._identity.cooldown_until - now) * 1000) + WAIT_GUARD_MS
        _log.info(
            "идентичность %s остывает после ограничения: пауза %d мс",
            self._identity.name,
            wait_ms,
        )
        yield Pause(wait_ms)

    def spend_budget(
        self,
        request_class: RequestClass = RequestClass.INTERACTIVE,
        *,
        cost: float = 1.0,
    ) -> Generator[Request, Reply, None]:
        """Занимает бюджет под один отправляемый запрос.

        Расходуется именно отправляемый запрос, а не логическая операция:
        повтор - тоже запрос. Считать иначе означало бы сделать шторм повторов
        бесплатным ровно в тот момент, когда площадке хуже всего.

        Ожидание выполняется просьбой, а не в бюджете: сам бюджет не спит, чтобы
        его можно было проверять числами вместо секунд.

        Yields:
            Request: Просьба подождать, если бюджет занят.

        Returns:
            None

        Raises:
            BudgetExhaustedError: Если ждать пришлось бы дольше предела. Запрос
                при этом не отправляется вовсе.
        """
        # Остывание идентичности - первая половина первой ступени реакции на
        # ограничение частоты, и до сих пор её не соблюдал никто. Спецификация
        # говорит «уменьшить ёмкость вдвое И ВЫДЕРЖАТЬ ПАУЗУ»; ёмкость
        # уменьшалась, пауза считалась, записывалась в журнал - и спрашивал о
        # ней только выбор прокси. При прямом соединении, то есть у всех, кто
        # прокси не завёл, следующий запрос уходил немедленно.
        #
        # Хуже того, урезание ёмкости само по себе почти не тормозит:
        # Budget.scale опускает потолок ведра, а скорость пополнения не трогает.
        # Значит без паузы клиент возвращался к прежнему темпу через несколько
        # секунд - ровно тогда, когда площадка сказала «слишком быстро».
        if self._stopped is not None:
            # Та же ошибка, что остановила клиента, а не новая и не общая:
            # вызывающий обязан видеть причину, а не «клиент остановлен».
            raise self._stopped

        yield from self.wait_out_cooldown()

        # Число попыток объявлено спецификацией, а не выбрано здесь: цикл
        # ожидания превратил бы предел max_wait_ms в пожелание - каждая итерация
        # ждала бы «не дольше предела», а вызов снаружи стал бы неотличим от
        # зависшего процесса.
        waited = 0
        for attempt in range(WAIT_ATTEMPTS):
            reservation = self._budget.require(monotonic(), cost=cost, request_class=request_class)
            if reservation.granted:
                return
            if attempt + 1 == WAIT_ATTEMPTS:
                raise BudgetExhaustedError(
                    f"бюджет не освободился за {waited} мс ожидания "
                    f"(ведро {reservation.bucket}). Запрос не отправлен"
                )

            _log.info(
                "бюджет: ведро %s занято, пауза %d мс",
                reservation.bucket,
                reservation.wait_ms,
            )
            waited += reservation.wait_ms
            yield Pause(reservation.wait_ms)

    def settle(
        self,
        count: int,
        request_class: RequestClass = RequestClass.INTERACTIVE,
        *,
        cost: float = 1.0,
    ) -> Generator[Request, Reply, None]:
        """Доплачивает бюджет за запросы, которые уже ушли.

        Метод нужен переходам. Их число заранее неизвестно, поэтому бюджет за
        них списывается вслед за ответом - а списать вслед можно только тогда,
        когда в ведре есть чем.

        Прежде здесь стоял голый reserve, чей отказ никто не смотрел. Договор у
        него «всё или ничего», поэтому ровно при пустом ведре цепочка переходов
        становилась бесплатной: клиент считал, что потратил один запрос, а
        отправлял до шести. Ведро при этом стояло на нуле постоянно, то есть
        путь был не редким, а основным.

        Отказать здесь нельзя: запросы уже отправлены, и не заплатить за них
        значит соврать бюджету. Поэтому метод ждёт и платит.

        Args:
            count (int): Сколько запросов доплатить. Ноль и меньше - ничего.

        Yields:
            Request: Просьба подождать, если в ведре пусто.

        Returns:
            None
        """
        for _ in range(max(0, count)):
            reservation = self._budget.reserve(monotonic(), cost, request_class=request_class)
            if reservation.granted:
                continue

            yield Pause(reservation.wait_ms)
            # Пауза вычислена ведром точно, поэтому вторая попытка обычно
            # проходит. Не пройти она может, если бюджет делится с другим
            # клиентом и тот успел раньше. Настаивать дальше нельзя: долг не
            # растёт, а зациклиться на нём хуже, чем недосчитать один токен и
            # сказать об этом вслух.
            if not self._budget.reserve(monotonic(), cost, request_class=request_class).granted:
                _log.warning(
                    "бюджет не доплачен за уже отправленный запрос: ведро %s занято",
                    reservation.bucket,
                )

    def watch(
        self,
        router: Router,
        *,
        account_id: str = "self",
        max_iterations: int | None = None,
        schedule: Schedule | None = None,
        state_path: Path | None = None,
        max_threads_per_step: int = 5,
    ) -> Generator[Request, Reply, None]:
        """Ведёт наблюдение: опрашивает площадку и раздаёт события обработчикам.

        Первый проход молчит и выдаёт одно событие watch.primed. Иначе холодный
        старт дал бы лавину «изменений» по всем существующим заказам и диалогам
        сразу.

        Базовый снимок сдвигается только после того, как все обработчики
        отработали. Упавший обработчик оставляет базу на месте, и то же событие
        приходит снова: гарантия доставки - не менее одного раза, и обработчик
        обязан быть идемпотентным.

        Args:
            router (Router): Реестр обработчиков.
            account_id (str): Идентификатор аккаунта для отпечатков событий.
            max_iterations (int | None): Сколько шагов сделать. None означает
                бесконечно; ограничение нужно проверкам и разовым прогонам.
            schedule (Schedule | None): Расписание опроса. По умолчанию из
                спецификации.
            state_path (Path | None): Файл, в котором состояние гашения повторов
                переживает перезапуск. Без него кэш живёт только в памяти, и
                после любого перезапуска повторно приходит всё, что успело
                прийти до него.
            max_threads_per_step (int): Сколько переписок читать за один шаг.
                Предел нужен: изменись разом полсотни диалогов, шаг превратился
                бы в полсотни запросов. Непрочитанные не теряются - они ждут в
                очереди и читаются следующими шагами.

        Yields:
            Request: Просьбы о вводе-выводе и о раздаче событий.

        Returns:
            None

        Raises:
            FunoraError: Любая ошибка чтения, которую не удалось повторить.
        """
        plan = schedule or Schedule()
        dedup = Deduplicator()
        state = StateFile(state_path) if state_path is not None else None
        if state is not None and self._ledger is None:
            # Файл наблюдения становится и реестром отправок. Иначе бот, честно
            # передавший state_path, всё равно отправлял бы без долговечного
            # реестра - и отказывал бы себе сам, не понимая почему.
            #
            # ЧИТАЕТСЯ ДО ОБЪЯВЛЕНИЯ. Прежде признак долговечности ставился
            # раньше чтения, и падение чтения оставляло движок «долговечным» с
            # пустым реестром: защита переставала защищать ровно там, где файл
            # непригоден.
            stored = state.load()
            self._ledger = state
            self._state.outbound.durable = True

            # СЛИЯНИЕ, а не замещение. Прочитанное с диска добавляется к тому,
            # что уже накоплено в памяти, - иначе вход в цикл обнулял бы квоту
            # БЕЗ перезапуска, то есть ровно то, что реестр обязан
            # предотвращать.
            merged = self._state.outbound.snapshot()
            fresh = stored.get("outbound") or {}
            merged["sent"] = [*fresh.get("sent", []), *merged.get("sent", [])]
            merged["incoming"] = {**fresh.get("incoming", {}), **merged.get("incoming", {})}
            self._state.outbound.restore(merged)
            self._delivered.restore(stored.get("delivery") or {})

            # ОТМЕТКА НЕ СНИМАЕТСЯ. Прежде усыновление файла её стирало, и
            # состояние здоровья переставало помнить, что часть отправок уже
            # ушла без долговечного реестра.
            #
            # Отметка говорит о СЕАНСЕ, а не о нынешнем мгновении: контракт
            # называет её единственным способом узнать со стороны, что защита
            # снималась. Снятая задним числом, она отвечает на другой вопрос.
            if UNSAFE_SENDS_WITHOUT_LEDGER in self._unsafe:
                _log.info("реестр появился, но отметка о прежних отправках без него остаётся")

        known_orders: dict[str, str] | None = None
        known_chats: dict[str, str] | None = None
        known_threads: dict[str, frozenset[str]] = {}
        pending: list[str] = []
        # Сколько раз каждое событие уже пробовали доставить. Пустой словарь -
        # штатное состояние: записи заводятся только на события, которые
        # обработчик не принял.
        attempts: dict[str, int] = {}
        greeted = False

        if state is not None:
            stored = state.load()
            restored = dedup.restore(stored.get("dedup", {}), monotonic())
            if restored:
                _log.info("восстановлено записей гашения: %d", restored)

            # Курсор восстанавливается вместе с гашением. Без него перезапуск
            # уходил в холодный старт и молча съедал всё, что изменилось за
            # простой: заказ, оплаченный между остановкой и стартом, не порождал
            # события никогда - ни исключения, ни строки в журнале.
            cursor = stored.get("cursor") or {}
            saved_orders = cursor.get("orders")
            if isinstance(saved_orders, dict):
                known_orders = dict(saved_orders)
            elif saved_orders is not None:
                # Курсор прежней редакции - список идентификаторов без состояний.
                # Читается как «заказы известны, состояния нет»: так перезапуск
                # не порождает лавину событий о создании, а событие об изменении
                # не выдумывается из непрочитанного состояния.
                known_orders = dict.fromkeys(saved_orders, UNREAD_STATUS)
            if cursor.get("chats") is not None:
                known_chats = dict(cursor["chats"])
            known_threads = {
                node: frozenset(ids) for node, ids in (cursor.get("threads") or {}).items()
            }
            # Очередь непрочитанных переписок тоже переживает перезапуск. Не
            # переживи она - диалог, изменившийся перед остановкой и не успевший
            # дочитаться, не дочитался бы уже никогда: событие о нём доставлено,
            # курсор диалогов сдвинут, и повода вернуться к нему больше нет.
            pending = list(cursor.get("pending_threads") or [])
            restored_attempts = stored.get("attempts")
            if isinstance(restored_attempts, dict):
                attempts = {
                    str(key): int(value)
                    for key, value in restored_attempts.items()
                    if isinstance(value, int) and value > 0
                }
            # Здоровались ли уже. Восстановленный курсор любого из списков
            # означает, что здоровались: watch.primed - событие о начале
            # наблюдения, а не о начале процесса.
            greeted = known_orders is not None or known_chats is not None
            if known_orders is not None or known_chats is not None:
                _log.info(
                    "курсор восстановлен: заказов %s, диалогов %s",
                    len(known_orders) if known_orders is not None else "нет",
                    len(known_chats) if known_chats is not None else "нет",
                )

        step = 0
        while max_iterations is None or step < max_iterations:
            step += 1
            orders = yield from self.read_orders()
            chats = yield from self.read_chats()
            now = monotonic()

            chat_events = diff_chats(known_chats, chats, account_id=account_id)
            # Неполное чтение объявляется вслух и в той же партии, что и события
            # по нему. Несдвинутый курсор защищает будущее - выпавшие строки не
            # будут сочтены исчезнувшими, - а настоящее не защищает никак:
            # события по прочитанному порождаются, и обработчик принимает их за
            # полную картину.
            notices = tuple(
                incomplete(
                    account_id,
                    page.observed_at,
                    entity=name,
                    entity_ref=None,
                    reason=page.reason,
                    rows_total=page.rows_total,
                    rows_accepted=page.rows_accepted,
                )
                for name, page in (("orders", orders), ("chats", chats))
                if page.completeness is not Completeness.COMPLETE
            )
            head = dedup.filter(
                (
                    *notices,
                    *diff_orders(known_orders, orders, account_id=account_id),
                    *chat_events,
                ),
                now,
            )

            # В очередь попадают только те диалоги, чьё изменение пережило
            # гашение. Иначе повторно пришедшее событие заставляло бы перечитать
            # переписку, в которой ничего нового нет: курсор её уже сдвинут.
            delivered_ids = {event.id for event in head}
            for event in chat_events:
                if event.id in delivered_ids and event.entity_id not in pending:
                    pending.append(event.entity_id)

            # Очередь ограничена, и предел объявлен спецификацией. Он нужен:
            # очередь пополняется на каждом изменении диалога, а вычерпывается
            # по несколько штук за шаг - у продавца с полусотней активных
            # переписок она растёт быстрее, чем убывает.
            #
            # Выброшенное объявляется вслух. Ограничить и промолчать - худший
            # исход из возможных: сообщение покупателя не будет прочитано
            # никогда, и узнать об этом неоткуда.
            #
            # Выбрасывается ХВОСТ, а не голова: в голове самые давние диалоги, и
            # они ждут дольше всех. Выбросить их значило бы гарантировать, что
            # именно они не дочитаются никогда.
            dropped = 0
            if len(pending) > MAX_QUEUE_DEPTH_PER_KEY:
                dropped = len(pending) - MAX_QUEUE_DEPTH_PER_KEY
                del pending[MAX_QUEUE_DEPTH_PER_KEY:]
                _log.warning(
                    "очередь дочитывания переполнена: %d диалогов выпало, предел %d",
                    dropped,
                    MAX_QUEUE_DEPTH_PER_KEY,
                )

            messages, thread_cursors, followed = yield from self._follow(
                pending,
                known_threads,
                account_id=account_id,
                limit=max_threads_per_step,
            )
            losses = (
                (loss(account_id, orders.observed_at, lost=dropped, reason="queue_overflow"),)
                if dropped
                else ()
            )
            fresh = (*head, *dedup.filter((*losses, *messages), now))

            # Смены состояния доступа идут первыми в партии. Порядок значим:
            # получатель, узнав, что автоматика записи приостановлена, обязан
            # увидеть это ДО событий, на которые он собрался бы отвечать.
            batch = (*self.drain_health(account_id, orders.observed_at), *fresh)
            greeting: Event | None = None
            if not greeted:
                # Холодный старт молчит о данных и говорит один раз о себе:
                # иначе первый запуск дал бы лавину «изменений» по всему, что
                # уже существует. Молчание при этом обеспечивают сами diff_*,
                # возвращающие пустое при отсутствии курсора, - а приветствие
                # только добавляется к партии, а не заменяет её.
                #
                # Замена стоила дорого. Курсор заказов снимается лишь с полного
                # чтения, поэтому одна пропавшая ячейка в одной строке держала
                # признак холодного старта поднятым вечно: события о диалогах
                # выбрасывались, а вместо них каждый шаг уходило одно и то же
                # приветствие. Наблюдение за перепиской замолкало целиком из-за
                # состояния чужой страницы - молча.
                # Признак поднимается ПОСЛЕ раздачи, а не здесь. Поднятый
                # заранее, он терял приветствие навсегда: обработчик падал на
                # первой партии, курсор не двигался, а приветствие второй раз не
                # собиралось. И это не единственная потеря - несдвинутый курсор
                # держит холодный старт, при котором diff_* молчат по правилу
                # первого чтения. Наблюдение замолкало целиком и навсегда, при
                # живом цикле и без единой строки в журнале.
                greeting = primed(account_id, orders.observed_at, ("orders", "chats"))
                batch = (greeting, *fresh)

            # Номер попытки проставляется здесь, а не в строителе событий:
            # строитель не знает, доставлялось ли это событие раньше, - знает
            # цикл. Доставка объявлена как минимум однократной, и событие, на
            # котором обработчик упал, приходит снова тем же отпечатком; без
            # номера попытки обработчик не отличит повтор от нового события.
            #
            # Счётчик пересобирается по партии целиком, а не накапливается:
            # событие, переставшее порождаться (список изменился, курсор ушёл
            # вперёд), само выпадает из счётчика, и он не растёт без предела.
            attempts = {event.id: attempts.get(event.id, 0) + 1 for event in batch}
            batch = tuple(
                replace(event, delivery=Delivery(attempt=attempts[event.id])) for event in batch
            )

            reply = yield Deliver(batch)
            if not isinstance(reply, StepResult):
                raise TypeError(f"на просьбу Deliver ожидался итог раздачи, получено {type(reply)}")
            result = reply

            dedup.commit(result.delivered, now)
            if greeting is not None and greeting.id in {event.id for event in result.delivered}:
                # Поздоровались только тогда, когда приветствие дошло. Иначе
                # второго раза не будет: приветствие собирается один раз за
                # признак, а не за партию.
                greeted = True
            # Доставленное выбывает: гашение повторов больше его не пропустит,
            # и держать номер попытки не для чего.
            for event in result.delivered:
                attempts.pop(event.id, None)

            # Курсор снимается только с полного чтения. Снятый с неполного, он
            # потерял бы выпавшие строки, и при следующем чтении они выглядели
            # бы новыми заказами: бот выдал бы товар по заказу, который был и
            # раньше. Неполное чтение при этом не пропадает - события по нему
            # порождаются, просто курсор остаётся прежним.
            if result.advance:
                if orders.completeness is Completeness.COMPLETE:
                    known_orders = orders_cursor(orders)
                if chats.completeness is Completeness.COMPLETE:
                    known_chats = chats_cursor(chats)
                known_threads.update(thread_cursors)
            else:
                # Прочитанные переписки возвращаются в очередь, и это половина
                # правила, без которой вторая не работает. Событие об изменении
                # диалога к этому моменту доставлено и погашено, повторно оно не
                # придёт - значит без возврата диалог не перечитается уже
                # никогда, сколько бы курсор ни откатывали.
                pending[:0] = [node for node in followed if node not in pending]
                _log.warning(
                    "курсор не сдвинут: обработчик не принял %d событий, они придут снова",
                    len(result.failed),
                )

            if state is not None:
                self._state.outbound.forget_expired(
                    now_ms=int(orders.observed_at.timestamp() * 1000), now_s=monotonic()
                )
                # Сохранение идёт после обработчиков, вместе с фиксацией
                # доставленного. Сохрани мы раньше - перезапуск между записью и
                # обработчиком потерял бы событие: файл говорил бы, что оно
                # доставлено, а обработчик его не видел.
                #
                # ПРАВКА, А НЕ ЗАПИСЬ ЦЕЛИКОМ. У файла несколько владельцев:
                # курсоры и гашение здесь, реестр отправок в send_text, реестр
                # выдач у автовыдачи. Запись целиком стирала бы чужие ключи на
                # каждом шаге - и стирала молча, при зелёном прогоне.
                state.update(
                    {
                        "dedup": dedup.snapshot(now),
                        # Номера попыток переживают перезапуск вместе с гашением.
                        # Иначе перезапуск обнулял бы их, и событие, падавшее
                        # пятый раз, приходило бы с номером один - то есть
                        # выглядело бы новым ровно тогда, когда обработчику
                        # важнее всего знать, что оно не новое.
                        "attempts": attempts,
                        "cursor": {
                            "orders": known_orders,
                            "chats": known_chats,
                            "threads": {node: sorted(ids) for node, ids in known_threads.items()},
                            "pending_threads": pending,
                        },
                        # Реестр отправок пишется вместе с курсорами, а не
                        # вместо них: файл один, и владельцев у него несколько.
                        #
                        # Прополка идёт ЗДЕСЬ, а не только при отправке. Прежде
                        # её звала одна send_text, и бот, который наблюдает и не
                        # пишет, не прополаывал реестр никогда: метки тепла
                        # копились в файле и переживали своё окно.
                        "outbound": self._state.outbound.snapshot(),
                    }
                )

            if result.fatal is not None:
                # Ошибка Funora из обработчика - не его баг, а условие площадки:
                # истёкшая сессия, исчерпанный бюджет. Партия при этом
                # дорабатывается до конца и состояние сохраняется, иначе отказ
                # на первом событии терял бы все остальные.
                raise result.fatal

            yield Pause(plan.note(fresh, now))

    def _note_success(
        self,
        capability: Capability,
        completeness: Completeness,
        page: OrdersPage | ChatsPage | Thread | ReviewsPage | BalancePage | None,
    ) -> None:
        """Записывает состояние возможности по успешному чтению.

        Args:
            capability (Capability): Возможность.
            completeness (Completeness): Полнота прочитанного.
            page (OrdersPage | ChatsPage | Thread): Прочитанная страница. Нужна
                только для подробностей в журнале.

        Returns:
            None
        """
        if completeness is Completeness.COMPLETE:
            self._state.capabilities[capability] = CapabilityState.SUPPORTED
            return

        self._state.capabilities[capability] = CapabilityState.DEGRADED
        if page is None:
            # Чтение, у которого счётчиков строк нет по устройству: одна
            # сущность либо перечень разделов, считающийся иначе. Витрина сюда
            # же: у неё разделы и предложения, а не строки.
            #
            # Состояние возможности при этом понижается так же -
            # повреждённое чтение остаётся повреждённым, о чём бы оно ни было.
            _log.warning("чтение %s неполно: замечены повреждения страницы", capability.value)
            return

        # Предупреждение пишется независимо от того, признал ли вызывающий
        # неполноту. Это единственная защита от того, чтобы признание
        # выродилось в ритуал: молча принятая неполнота перестаёт быть
        # заметной уже на второй неделе.
        _log.warning(
            "чтение %s неполно: %s (%s), собрано %d из %d, повреждений %d",
            capability.value,
            page.completeness,
            page.reason,
            page.rows_accepted,
            page.rows_total,
            len(page.defects),
        )

    def _note_failure(self, capability: Capability, error: FunoraError) -> None:
        """Записывает состояние возможности по неудачному чтению.

        Состояние unsupported не выставляется никогда. Позитивным свидетельством
        отсутствия была бы полученная страница с совпавшим отпечатком и
        отсутствующим разделом заказов; такой страницы никто не видел, и
        выставлять состояние по отказу значило бы принимать сбой сети за
        отсутствие возможности.

        Args:
            capability (Capability): Возможность.
            error (FunoraError): Полученная ошибка.

        Returns:
            None
        """
        if getattr(error, "provisional", False):
            return
        if isinstance(error, NetworkError):
            return
        _log.debug(
            "состояние возможности %s не изменено после %s",
            capability.value,
            type(error).__name__,
        )
