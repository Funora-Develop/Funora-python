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

import logging
from collections.abc import Generator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Final, TypeVar

from ._account import BalancePage, parse_balance_page
from ._budget import Budget
from ._chats import ChatsPage, parse_chats_page
from ._classify import DEFAULT_IDENTITY_CSS, Verdict, classify
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
from ._observed import Observed
from ._order import OrderView, parse_order_page
from ._orders import Completeness, OrdersPage, parse_orders_page
from ._poll import Deduplicator, Schedule
from ._result import Defect, Severity
from ._retry import plan_attempt
from ._reviews import ReviewsPage, parse_reviews_page
from ._showcase import ShowcasePage, parse_showcase
from ._state import StateFile
from ._thread import Thread, parse_thread
from ._transport import Observation, TransportSettings
from ._verdicts import error_for
from ._watch import Router, StepResult, health_changed, incomplete, loss, primed
from .budget import (
    COUNTS_REDIRECTS,
    COUNTS_RETRIES,
    MAX_QUEUE_DEPTH_PER_KEY,
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
    FunoraError,
    NetworkError,
    RateLimitedError,
    TransportError,
    ValidationError,
)
from .operations import OPERATIONS, Safety
from .response_classes import (
    HEALTH_BY_VERDICT,
    INITIAL_HEALTH,
    WRITES_PAUSED_IN,
    Health,
)
from .retry import RETRY_POLICIES

__all__ = [
    "Fetch",
    "Pause",
    "Deliver",
    "Request",
    "Engine",
    "ORDERS_PATH",
    "CHATS_PATH",
    "PROFILE_PATH",
    "ORDER_PATH",
    "BALANCE_PATH",
    "SHOWCASE_PATH",
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
Request = Fetch | Pause | Deliver

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
    bound="OrdersPage | ChatsPage | Thread | ReviewsPage | BalancePage | ShowcasePage",
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
        "_settings",
        "_state",
        "_stopped",
    )

    def __init__(
        self,
        settings: TransportSettings,
        budget: Budget,
        experimental: frozenset[Capability] = frozenset(),
        identity: Identity | None = None,
    ) -> None:
        self._settings = settings
        self._budget = budget
        self._state = _State(opted_in=experimental)
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
        return thread

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
                thread = yield from self.read_thread(node_id)
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
            events.extend(
                diff_thread(
                    known.get(node_id),
                    thread,
                    account_id=account_id,
                    chat_id=node_id,
                )
            )
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
                # Сохранение идёт после обработчиков, вместе с фиксацией
                # доставленного. Сохрани мы раньше - перезапуск между записью и
                # обработчиком потерял бы событие: файл говорил бы, что оно
                # доставлено, а обработчик его не видел.
                state.save(
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
