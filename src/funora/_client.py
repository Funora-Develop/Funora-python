"""Клиент: единственное место, где части собираются вместе.

Всё, что здесь вызывается, чистое: разбор, классификация, планировщик повторов,
ворота возможности. Грязное только одно - обращение к сети и сон между
попытками, и оно собрано в этом файле нарочно. Так проверяется всё остальное без
сети, а этот слой остаётся достаточно тонким, чтобы его можно было прочитать
целиком.

Порядок шагов нормативен и записан в spec/protocol/response-classes.yaml. Две
реализации, проверившие условия в разном порядке, разойдутся именно на той
странице, ради которой правило написано.

Бюджет расходуется на каждый отправляемый запрос, включая повторы: считать
логические операции значило бы сделать шторм повторов бесплатным ровно тогда,
когда площадке хуже всего. Числа бюджета помечены в спецификации провизорными,
потому что измерить настоящие пороги можно только намеренным их превышением.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic, sleep
from typing import Final

from ._budget import Budget
from ._chats import ChatsPage, parse_chats_page
from ._classify import DEFAULT_IDENTITY_CSS, classify
from ._diff import diff_chats, diff_orders
from ._gate import check_capability
from ._orders import Completeness, OrdersPage, parse_orders_page
from ._poll import Deduplicator, Schedule
from ._retry import Safety, plan_attempt
from ._secret import Secret, SecretProvider
from ._thread import Thread, parse_thread
from ._transport import Fetcher, Observation, TransportSettings
from ._verdicts import error_for
from ._watch import Router, dispatch, primed
from .capabilities import CAPABILITY_INITIAL, Capability, CapabilityState
from .errors import (
    BudgetExhaustedError,
    ConfigurationError,
    FunoraError,
    NetworkError,
    ValidationError,
)

__all__ = ["Client", "OrdersService", "ChatsService"]

_log = logging.getLogger("funora.client")

#: Путь страницы списка заказов.
_ORDERS_PATH: Final[str] = "/orders/trade"

#: Путь страницы списка диалогов.
_CHATS_PATH: Final[str] = "/chat/"

#: Путь страницы отдельной переписки.
_THREAD_PATH: Final[str] = "/chat/?node={node_id}"


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
    """

    capabilities: dict[Capability, CapabilityState] = field(
        default_factory=lambda: dict(CAPABILITY_INITIAL)
    )
    session_ever_valid: bool = False
    opted_in: frozenset[Capability] = frozenset()


def _check_integrity(observation: Observation) -> None:
    """Проверяет, что тело ответа получено целиком.

    Шаг стоит до классификации намеренно. Страница, оборванная посреди таблицы,
    проходит классификацию как пригодная и разбор как полный: вызывающий
    получает половину заказов и ноль повреждений. Это правдоподобный неверный
    ответ, о неверности которого узнать неоткуда.

    Args:
        observation (Observation): Результат обращения.

    Returns:
        None

    Raises:
        NetworkError: Если полученная длина меньше объявленной.
    """
    declared = observation.declared_length
    if declared is None or observation.content_length >= declared:
        return
    raise NetworkError(
        f"тело ответа получено не целиком: объявлено {declared} байт, "
        f"получено {observation.content_length}. Это обрыв соединения, "
        "а не изменение разметки"
    )


class OrdersService:
    """Операции над заказами.

    Args:
        client (Client): Клиент, которому принадлежит служба.
    """

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def list(self) -> OrdersPage:
        """Читает список заказов.

        Возвращает сокращённые записи, а не полные заказы: страница не даёт ни
        валюты, ни машиночитаемого времени, ни статуса. Подробности в
        :class:`~funora._orders.OrderListEntry`.

        Признавать неполноту здесь не нужно и нельзя: страница возвращается как
        есть, а решение принимается там, где спрашивают записи. Иначе признание
        пришлось бы давать до того, как стало известно, что признавать.

        Returns:
            OrdersPage: Записи вместе с полнотой и перечнем повреждений.
            Получить записи можно методом :meth:`~funora._orders.OrdersPage.rows`.

        Raises:
            FunoraError: Любая ошибка из иерархии Funora. Какая именно, решает
                таблица соответствия вердиктов из спецификации.
        """
        return self._client._read_orders()


class ChatsService:
    """Операции над перепиской.

    Args:
        client (Client): Клиент, которому принадлежит служба.
    """

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def list(self) -> ChatsPage:
        """Читает список диалогов.

        Признак непрочитанного в записях помечен как выведенный: расхождения
        позиций при непрочитанном диалоге не наблюдалось ни разу, и правило может
        оказаться неверным. Структурный признак у страницы один - счётчик в
        шапке, и он доступен в поле unread_badge_visible.

        Returns:
            ChatsPage: Записи вместе с полнотой и перечнем повреждений.

        Raises:
            FunoraError: Любая ошибка из иерархии Funora.
        """
        return self._client._read_chats()

    def thread(self, node_id: str) -> Thread:
        """Читает переписку целиком.

        Происхождение каждого сообщения определяется разметкой, а не текстом, и
        при расхождении признаков объявляется неизвестным. Но даже верно
        опознанное сообщение площадки не является подтверждением оплаты: оно
        могло относиться к другому заказу, устареть, прийти по отменённому
        платежу. Площадка предупреждает об этом сама, первым сообщением в каждом
        диалоге.

        Args:
            node_id (str): Идентификатор диалога, он же поле node_id записи
                списка.

        Returns:
            Thread: Сообщения вместе с полнотой и перечнем повреждений.

        Raises:
            ValidationError: Если идентификатор пуст или содержит посторонние
                знаки. Проверка идёт до сети: подставленный в адрес мусор
                отправил бы запрос неизвестно куда.
            FunoraError: Любая ошибка из иерархии Funora.
        """
        return self._client._read_thread(node_id)


class Client:
    """Клиент площадки.

    Args:
        secret (Secret | SecretProvider | None): Сессионный секрет либо его
            источник. Не нужен, если передан готовый транспорт.
        settings (TransportSettings | None): Настройки транспорта.
        experimental (frozenset[Capability] | None): Возможности, которые
            вызывающий включает явно, соглашаясь на возможную смену контракта.
        transport (Fetcher | None): Готовый транспорт. Нужен там, где вызывающий
            собирает его сам, и в проверках: создание транспорта поднимает
            контекст TLS, а это полсекунды на каждый вызов, из-за чего набор
            проверок начинают выключать.
        budget (Budget | None): Общий бюджет запросов. Передаётся, когда в одном
            процессе живут несколько клиентов: площадке видна сетевая
            идентичность, а не то, сколько клиентов мы завели у себя, и общий
            предел обходится ровно тем, что каждый заводит свой бюджет.

    Raises:
        ConfigurationError: Если не передано ни секрета, ни транспорта. Повтор
            здесь не поможет, исправлять надо вызов.
    """

    __slots__ = ("_budget", "_fetcher", "_settings", "_state", "chats", "orders")

    def __init__(
        self,
        secret: Secret | SecretProvider | None = None,
        *,
        settings: TransportSettings | None = None,
        experimental: frozenset[Capability] | None = None,
        transport: Fetcher | None = None,
        budget: Budget | None = None,
    ) -> None:
        self._settings = settings or TransportSettings()

        if transport is not None:
            self._fetcher = transport
        elif secret is not None:
            resolved = secret if isinstance(secret, Secret) else secret.get("golden_key")
            self._fetcher = Fetcher(resolved, settings=self._settings)
        else:
            raise ConfigurationError(
                "клиенту нужен либо секрет, либо готовый транспорт: без них "
                "обратиться к площадке не от кого"
            )

        self._budget = budget or Budget()
        self._state = _State(opted_in=experimental or frozenset())
        self.orders = OrdersService(self)
        self.chats = ChatsService(self)

    def __enter__(self) -> Client:
        """Входит в контекстный менеджер.

        Returns:
            Client: Сам объект.
        """
        return self

    def __exit__(self, *exc: object) -> None:
        """Закрывает соединения при выходе.

        Args:
            *exc (object): Сведения об исключении. Не используются.

        Returns:
            None
        """
        self.close()

    def close(self) -> None:
        """Закрывает пул соединений.

        Returns:
            None
        """
        self._fetcher.close()

    def capability(self, capability: Capability) -> CapabilityState:
        """Возвращает текущее состояние возможности.

        Args:
            capability (Capability): Возможность.

        Returns:
            CapabilityState: Состояние, каким его видит клиент сейчас.
        """
        return self._state.capabilities[capability]

    def _read_orders(self) -> OrdersPage:
        """Выполняет чтение списка заказов по нормативному порядку шагов.

        Returns:
            OrdersPage: Разобранная страница.

        Raises:
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        observation = self._fetch_ok(Capability.ORDERS_LIST, _ORDERS_PATH)
        page = parse_orders_page(observation.html, observed_at=datetime.now(UTC))
        self._note_success(Capability.ORDERS_LIST, page.completeness, page)
        return page

    def _fetch_ok(self, capability: Capability, path: str) -> Observation:
        """Получает пригодный для разбора ответ по нормативному порядку шагов.

        Метод общий для всех операций чтения намеренно. Порядок шагов нормативен,
        а скопированный порядок расходится: правку вносят в одно место, забывают
        о втором, и две операции одного клиента начинают вести себя по-разному на
        одной и той же странице.

        Args:
            capability (Capability): Возможность, под которую идёт чтение.
            path (str): Путь запрашиваемой страницы.

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

        host = self._settings.base_url.split("//", 1)[-1].split("/", 1)[0]
        attempt = 0
        while True:
            attempt += 1
            # Заголовок Retry-After живёт в ответе, а до ответа его нет. Значение
            # держится отдельной переменной, чтобы обработчик не зависел от
            # того, успел ли ответ появиться.
            retry_after_ms: int | None = None
            self._spend_budget()
            try:
                observation = self._fetcher.fetch(path)
                retry_after_ms = observation.retry_after_ms
                _check_integrity(observation)
                verdict = classify(
                    status=observation.status,
                    final_url=observation.final_url,
                    html=observation.html,
                    expected_host=host,
                    identity_css=DEFAULT_IDENTITY_CSS,
                )
                error = error_for(verdict, session_ever_valid=self._state.session_ever_valid)
                if error is not None:
                    raise error
            except FunoraError as exc:
                self._note_failure(capability, exc)
                plan = plan_attempt(
                    exc,
                    attempt=attempt,
                    safety=Safety.SAFE,
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
                sleep(plan.delay_ms / 1000)
                continue

            self._state.session_ever_valid = True
            return observation

    def _read_chats(self) -> ChatsPage:
        """Выполняет чтение списка диалогов по тому же порядку шагов.

        Returns:
            ChatsPage: Разобранная страница.

        Raises:
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        observation = self._fetch_ok(Capability.CHATS_LIST, _CHATS_PATH)
        page = parse_chats_page(observation.html, observed_at=datetime.now(UTC))
        self._note_success(Capability.CHATS_LIST, page.completeness, page)
        return page

    def _read_thread(self, node_id: str) -> Thread:
        """Выполняет чтение переписки по тому же порядку шагов.

        Args:
            node_id (str): Идентификатор диалога.

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
        observation = self._fetch_ok(capability, _THREAD_PATH.format(node_id=cleaned))
        thread = parse_thread(
            observation.html,
            observed_at=datetime.now(UTC),
            host=self._settings.base_url.split("//", 1)[-1].split("/", 1)[0],
        )
        self._note_success(capability, thread.completeness, thread)
        return thread

    def watch(
        self,
        router: Router,
        *,
        account_id: str = "self",
        max_iterations: int | None = None,
        schedule: Schedule | None = None,
    ) -> None:
        """Ведёт наблюдение: опрашивает площадку и раздаёт события обработчикам.

        Метод блокирующий и спит между опросами. Это единственное место, где
        цикл сходится: расписание, гашение повторов, порождение событий и раздача
        по обработчикам проверены по отдельности и в сеть не ходят.

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

        Returns:
            None

        Raises:
            FunoraError: Любая ошибка чтения, которую не удалось повторить.
        """
        plan = schedule or Schedule()
        dedup = Deduplicator()
        orders_base: OrdersPage | None = None
        chats_base: ChatsPage | None = None
        step = 0

        while max_iterations is None or step < max_iterations:
            step += 1
            orders = self.orders.list()
            chats = self.chats.list()
            now = monotonic()

            if orders_base is None or chats_base is None:
                orders_base, chats_base = orders, chats
                dispatch(router, (primed(account_id, orders.observed_at, "account:" + account_id),))
                sleep(plan.note((), now) / 1000)
                continue

            events = (
                *diff_orders(orders_base, orders, account_id=account_id),
                *diff_chats(chats_base, chats, account_id=account_id),
            )
            fresh = dedup.filter(events, now)
            result = dispatch(router, fresh)

            if result.advance:
                orders_base, chats_base = orders, chats
                dedup.commit(result.delivered, now)
            else:
                # Доставленные события всё равно запоминаются: они дошли, и
                # повторять их незачем. Не запоминаются только непринятые, и
                # именно они придут снова.
                dedup.commit(result.delivered, now)
                _log.warning(
                    "база не сдвинута: обработчик не принял %d событий, они придут снова",
                    len(result.failed),
                )

            sleep(plan.note(fresh, now) / 1000)

    def _spend_budget(self) -> None:
        """Занимает бюджет под один отправляемый запрос.

        Расходуется именно отправляемый запрос, а не логическая операция:
        повтор - тоже запрос. Считать иначе означало бы сделать шторм повторов
        бесплатным ровно в тот момент, когда площадке хуже всего.

        Ожидание выполняется здесь, а не в бюджете: сам бюджет не спит, чтобы
        его можно было проверять числами вместо секунд.

        Returns:
            None

        Raises:
            BudgetExhaustedError: Если ждать пришлось бы дольше предела. Запрос
                при этом не отправляется вовсе.
        """
        reservation = self._budget.require(monotonic())
        if reservation.granted:
            return

        _log.info(
            "бюджет: ведро %s занято, пауза %d мс",
            reservation.bucket,
            reservation.wait_ms,
        )
        sleep(reservation.wait_ms / 1000)

        # Вторая попытка обязана быть последней: цикл ожидания здесь превратил бы
        # предел ожидания в пожелание, а вызов снаружи стал бы неотличим от
        # зависшего процесса.
        again = self._budget.require(monotonic())
        if not again.granted:
            raise BudgetExhaustedError(
                f"бюджет не освободился за {reservation.wait_ms} мс ожидания "
                f"(ведро {again.bucket}). Запрос не отправлен"
            )

    def _note_success(
        self,
        capability: Capability,
        completeness: Completeness,
        page: OrdersPage | ChatsPage | Thread,
    ) -> None:
        """Записывает состояние возможности по успешному чтению.

        Args:
            capability (Capability): Возможность.
            completeness (Completeness): Полнота прочитанного.
            page (OrdersPage | ChatsPage | Thread): Прочитанная страница. Нужна только
                для подробностей в журнале.

        Returns:
            None
        """
        if completeness is Completeness.COMPLETE:
            self._state.capabilities[capability] = CapabilityState.SUPPORTED
            return

        self._state.capabilities[capability] = CapabilityState.DEGRADED
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
