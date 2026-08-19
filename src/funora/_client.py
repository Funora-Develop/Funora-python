"""Синхронный клиент: способ выполнить то, о чём просит ядро.

Логики здесь нет. Нормативный порядок шагов, политика повторов, расход бюджета,
сдвиг курсора и правила гашения живут в [_engine.py] и не знают ни о сети, ни о
том, синхронно их крутят или асинхронно. Этот файл - двенадцать строк цикла,
который на просьбу сходить отвечает вызовом, на просьбу подождать - сном, а на
просьбу раздать события - раздачей.

Разделение не эстетическое. Асинхронный клиент отличается ровно этими тремя
строками; напиши мы его вторым файлом целиком - и нормативный порядок шагов
существовал бы в двух экземплярах. Копия расходится, это в проекте уже случалось
трижды с правилом хоста, и один раз ценой сессионного ключа.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from pathlib import Path
from time import sleep
from typing import TYPE_CHECKING, TypeVar

from ._budget import Budget
from ._chats import ChatsPage
from ._engine import Deliver, Engine, Fetch, Pause, Reply, Request
from ._orders import OrdersPage
from ._poll import Schedule
from ._secret import Secret, SecretProvider
from ._thread import Thread
from ._transport import Fetcher, TransportSettings
from ._watch import Router, dispatch
from .capabilities import Capability, CapabilityState
from .errors import ConfigurationError, FunoraError

if TYPE_CHECKING:
    from ._transport import Observation

__all__ = ["Client", "OrdersService", "ChatsService"]

_log = logging.getLogger("funora.client")

#: Тип, которым завершается сопрограмма ядра. Синтаксис PEP 695 не годится:
#: пакет поддерживает Python 3.11, где его ещё нет.
T = TypeVar("T")


class OrdersService:
    """Операции над заказами.

    Сервис - это не слой ради слоя. Он даёт вызывающему одно имя на одну
    операцию и позволяет менять способ чтения, не трогая вызов.

    Args:
        client (Client): Клиент, которому принадлежит сервис.
    """

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def list(self) -> OrdersPage:
        """Читает список заказов.

        Returns:
            OrdersPage: Разобранная страница. Записи выдаются через `entries()`
            либо `rows()`: первый требует признать неполноту, второй отдаёт что
            есть.

        Raises:
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return self._client.run(self._client.engine.read_orders())


class ChatsService:
    """Операции над перепиской.

    Args:
        client (Client): Клиент, которому принадлежит сервис.
    """

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def list(self) -> ChatsPage:
        """Читает список диалогов.

        Returns:
            ChatsPage: Разобранная страница.

        Raises:
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return self._client.run(self._client.engine.read_chats())

    def thread(self, node_id: str) -> Thread:
        """Читает переписку одного диалога.

        Args:
            node_id (str): Идентификатор диалога. Тот самый, что стоит в адресе
                после `?node=`.

        Returns:
            Thread: Разобранная переписка.

        Raises:
            ValidationError: Если идентификатор непригоден для подстановки.
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return self._client.run(self._client.engine.read_thread(node_id))


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

    __slots__ = ("_fetcher", "chats", "engine", "orders")

    def __init__(
        self,
        secret: Secret | SecretProvider | None = None,
        *,
        settings: TransportSettings | None = None,
        experimental: frozenset[Capability] | None = None,
        transport: Fetcher | None = None,
        budget: Budget | None = None,
    ) -> None:
        resolved_settings = settings or TransportSettings()

        if transport is not None:
            self._fetcher = transport
        elif secret is not None:
            resolved = secret if isinstance(secret, Secret) else secret.get("golden_key")
            self._fetcher = Fetcher(resolved, settings=resolved_settings)
        else:
            raise ConfigurationError(
                "клиенту нужен либо секрет, либо готовый транспорт: без них "
                "обратиться к площадке не от кого"
            )

        self.engine = Engine(
            resolved_settings,
            budget or Budget(),
            experimental or frozenset(),
        )
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
        return self.engine.capability(capability)

    def watch(
        self,
        router: Router,
        *,
        account_id: str = "self",
        max_iterations: int | None = None,
        schedule: Schedule | None = None,
        state_path: Path | None = None,
        max_threads_per_step: int = 5,
    ) -> None:
        """Ведёт наблюдение: опрашивает площадку и раздаёт события обработчикам.

        Метод блокирующий и спит между опросами. Цикл целиком описан ядром;
        здесь он только исполняется.

        Args:
            router (Router): Реестр обработчиков.
            account_id (str): Идентификатор аккаунта для отпечатков событий.
            max_iterations (int | None): Сколько шагов сделать. None означает
                бесконечно; ограничение нужно проверкам и разовым прогонам.
            schedule (Schedule | None): Расписание опроса. По умолчанию из
                спецификации.
            state_path (Path | None): Файл, в котором состояние гашения повторов
                переживает перезапуск.
            max_threads_per_step (int): Сколько переписок дочитывать за один
                шаг. Изменившийся диалог говорит, что в нём что-то произошло, но
                само сообщение видно только на странице переписки. Предел нужен:
                изменись разом полсотни диалогов, шаг превратился бы в полсотни
                запросов. Непрочитанные не теряются - они ждут в очереди.

        Returns:
            None

        Raises:
            FunoraError: Любая ошибка чтения, которую не удалось повторить.
        """
        self.run(
            self.engine.watch(
                router,
                account_id=account_id,
                max_iterations=max_iterations,
                schedule=schedule,
                state_path=state_path,
                max_threads_per_step=max_threads_per_step,
            ),
            router=router,
        )

    def run(
        self,
        core: Generator[Request, Reply, T],
        *,
        router: Router | None = None,
    ) -> T:
        """Прокручивает ядро, выполняя то, о чём оно просит.

        Отказ сети не возвращается ядру значением, а бросается внутрь. Иначе
        политику повторов пришлось бы писать здесь второй раз - а она в ядре
        написана и проверена.

        Args:
            core (Generator[Request, Reply, T]): Сопрограмма ядра.
            router (Router | None): Реестр обработчиков. Нужен только тем
                сопрограммам, которые просят раздать события.

        Returns:
            T: То, чем ядро завершилось.

        Raises:
            FunoraError: Любая ошибка, которую ядро не погасило повтором.
        """
        reply: Reply = None
        failure: FunoraError | None = None
        while True:
            try:
                request = core.throw(failure) if failure is not None else core.send(reply)
            except StopIteration as stop:
                return stop.value  # type: ignore[no-any-return]
            failure = None
            reply = None

            if isinstance(request, Pause):
                if request.ms > 0:
                    sleep(request.ms / 1000)
            elif isinstance(request, Fetch):
                try:
                    reply = self._fetch(request.path)
                except FunoraError as exc:
                    failure = exc
            elif isinstance(request, Deliver):
                if router is None:
                    raise ConfigurationError(
                        "ядро просит раздать события, но реестр обработчиков не передан"
                    )
                reply = dispatch(router, request.events)

    def _fetch(self, path: str) -> Observation:
        """Выполняет одно обращение к площадке.

        Args:
            path (str): Путь страницы.

        Returns:
            Observation: Результат обращения.

        Raises:
            FunoraError: При сетевом отказе либо непригодном ответе.
        """
        return self._fetcher.fetch(path)
