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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Final

from ._budget import Budget
from ._chats import ChatsPage, parse_chats_page
from ._classify import DEFAULT_IDENTITY_CSS, classify
from ._diff import Event, chats_cursor, diff_chats, diff_orders, orders_cursor
from ._gate import check_capability
from ._host import host_of
from ._orders import Completeness, OrdersPage, parse_orders_page
from ._poll import Deduplicator, Schedule
from ._retry import Safety, plan_attempt
from ._state import StateFile
from ._thread import Thread, parse_thread
from ._transport import Observation, TransportSettings
from ._verdicts import error_for
from ._watch import Router, StepResult, primed
from .capabilities import CAPABILITY_INITIAL, Capability, CapabilityState
from .errors import (
    BudgetExhaustedError,
    FunoraError,
    NetworkError,
    ValidationError,
)

__all__ = ["Fetch", "Pause", "Deliver", "Request", "Engine", "ORDERS_PATH", "CHATS_PATH"]

_log = logging.getLogger("funora.client")

#: Путь страницы списка заказов.
ORDERS_PATH: Final[str] = "/orders/trade"

#: Путь страницы списка диалогов.
CHATS_PATH: Final[str] = "/chat/"

#: Путь страницы отдельной переписки.
THREAD_PATH: Final[str] = "/chat/?node={node_id}"


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
    """

    capabilities: dict[Capability, CapabilityState] = field(
        default_factory=lambda: dict(CAPABILITY_INITIAL)
    )
    session_ever_valid: bool = False
    opted_in: frozenset[Capability] = frozenset()


def check_integrity(observation: Observation) -> None:
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


class Engine:
    """Логика клиента, отделённая от способа выполнять ввод-вывод.

    Args:
        settings (TransportSettings): Настройки транспорта. Нужны ядру ради
            ожидаемого хоста, а не ради сети.
        budget (Budget): Бюджет запросов.
        experimental (frozenset[Capability]): Возможности, включённые вызывающим
            явно.
    """

    __slots__ = ("_budget", "_settings", "_state")

    def __init__(
        self,
        settings: TransportSettings,
        budget: Budget,
        experimental: frozenset[Capability] = frozenset(),
    ) -> None:
        self._settings = settings
        self._budget = budget
        self._state = _State(opted_in=experimental)

    def capability(self, capability: Capability) -> CapabilityState:
        """Возвращает текущее состояние возможности.

        Args:
            capability (Capability): Возможность.

        Returns:
            CapabilityState: Состояние, каким его видит клиент сейчас.
        """
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
        self._note_success(Capability.ORDERS_LIST, page.completeness, page)
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
            yield from self.spend_budget()
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
                for _ in range(max(0, observation.requests_sent - 1)):
                    self._budget.reserve(monotonic())
                retry_after_ms = observation.retry_after_ms
                check_integrity(observation)
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
                yield Pause(plan.delay_ms)
                continue

            self._state.session_ever_valid = True
            return observation

    def spend_budget(self) -> Generator[Request, Reply, None]:
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
        reservation = self._budget.require(monotonic())
        if reservation.granted:
            return

        _log.info(
            "бюджет: ведро %s занято, пауза %d мс",
            reservation.bucket,
            reservation.wait_ms,
        )
        yield Pause(reservation.wait_ms)

        # Вторая попытка обязана быть последней: цикл ожидания здесь превратил бы
        # предел ожидания в пожелание, а вызов снаружи стал бы неотличим от
        # зависшего процесса.
        again = self._budget.require(monotonic())
        if not again.granted:
            raise BudgetExhaustedError(
                f"бюджет не освободился за {reservation.wait_ms} мс ожидания "
                f"(ведро {again.bucket}). Запрос не отправлен"
            )

    def watch(
        self,
        router: Router,
        *,
        account_id: str = "self",
        max_iterations: int | None = None,
        schedule: Schedule | None = None,
        state_path: Path | None = None,
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

        known_orders: frozenset[str] | None = None
        known_chats: dict[str, str] | None = None

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
            if cursor.get("orders") is not None:
                known_orders = frozenset(cursor["orders"])
            if cursor.get("chats") is not None:
                known_chats = dict(cursor["chats"])
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

            cold = known_orders is None or known_chats is None
            events = (
                *diff_orders(known_orders, orders, account_id=account_id),
                *diff_chats(known_chats, chats, account_id=account_id),
            )
            fresh = dedup.filter(events, now)

            batch: tuple[Event, ...]
            if cold:
                # Холодный старт молчит о данных и говорит один раз о себе.
                # Иначе первый запуск дал бы лавину «изменений» по всему, что
                # уже существует.
                batch = (primed(account_id, orders.observed_at, "account:" + account_id),)
            else:
                batch = fresh

            reply = yield Deliver(batch)
            if not isinstance(reply, StepResult):
                raise TypeError(f"на просьбу Deliver ожидался итог раздачи, получено {type(reply)}")
            result = reply

            dedup.commit(result.delivered, now)

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
            else:
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
                        "dedup": dedup.snapshot(),
                        "cursor": {
                            "orders": sorted(known_orders) if known_orders is not None else None,
                            "chats": known_chats,
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
        page: OrdersPage | ChatsPage | Thread,
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
