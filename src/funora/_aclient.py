"""Асинхронный клиент: тот же способ, но через ожидание.

Файл читается рядом с [_client.py], и это не совпадение, а условие. Оба -
драйверы одного ядра из [_engine.py]: на просьбу сходить отвечают обращением, на
просьбу подождать - паузой, на просьбу раздать события - раздачей. Отличаются
ровно тремя строками, в которых стоит ``await``.

Нормативного порядка шагов здесь нет. Политики повторов нет. Расхода бюджета,
сдвига курсора, правил гашения - нет. Всё это написано один раз и проверено один
раз; сюда оно попадает готовым.

Обработчики принимаются и обычные, и асинхронные. Обычный вызывается как есть,
сопрограмма дожидается. Обратное - асинхронный обработчик в синхронном клиенте -
отвергается вслух: промолчать значило бы зарегистрировать обработчик, который
никогда не выполнится.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from ._budget import Budget
from ._chats import ChatsPage
from ._engine import Deliver, Engine, Fetch, Pause, Reply, Request
from ._orders import OrdersPage
from ._poll import Schedule
from ._secret import Secret, SecretProvider
from ._thread import Thread
from ._transport import AsyncFetcher, TransportSettings
from ._watch import Router, adispatch
from .capabilities import Capability, CapabilityState
from .errors import ConfigurationError, FunoraError

if TYPE_CHECKING:
    from ._transport import Observation

__all__ = ["AsyncClient", "AsyncOrdersService", "AsyncChatsService"]

_log = logging.getLogger("funora.client")

#: Тип, которым завершается сопрограмма ядра.
T = TypeVar("T")


class AsyncOrdersService:
    """Операции над заказами.

    Args:
        client (AsyncClient): Клиент, которому принадлежит сервис.
    """

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def list(self) -> OrdersPage:
        """Читает список заказов.

        Returns:
            OrdersPage: Разобранная страница. Записи выдаются через `entries()`
            либо `rows()`: первый требует признать неполноту, второй отдаёт что
            есть.

        Raises:
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return await self._client.run(self._client.engine.read_orders())


class AsyncChatsService:
    """Операции над перепиской.

    Args:
        client (AsyncClient): Клиент, которому принадлежит сервис.
    """

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def list(self) -> ChatsPage:
        """Читает список диалогов.

        Returns:
            ChatsPage: Разобранная страница.

        Raises:
            FunoraError: Если ответ непригоден либо разметка изменилась.
        """
        return await self._client.run(self._client.engine.read_chats())

    async def thread(self, node_id: str) -> Thread:
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
        return await self._client.run(self._client.engine.read_thread(node_id))


class AsyncClient:
    """Асинхронный клиент площадки.

    Args:
        secret (Secret | SecretProvider | None): Сессионный секрет либо его
            источник. Не нужен, если передан готовый транспорт.
        settings (TransportSettings | None): Настройки транспорта.
        experimental (frozenset[Capability] | None): Возможности, которые
            вызывающий включает явно, соглашаясь на возможную смену контракта.
        transport (AsyncFetcher | None): Готовый транспорт. Нужен там, где
            вызывающий собирает его сам, и в проверках.
        budget (Budget | None): Общий бюджет запросов. Передаётся, когда в одном
            процессе живут несколько клиентов: площадке видна сетевая
            идентичность, а не то, сколько клиентов мы завели у себя.

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
        transport: AsyncFetcher | None = None,
        budget: Budget | None = None,
    ) -> None:
        resolved_settings = settings or TransportSettings()

        if transport is not None:
            self._fetcher = transport
        elif secret is not None:
            resolved = secret if isinstance(secret, Secret) else secret.get("golden_key")
            self._fetcher = AsyncFetcher(resolved, settings=resolved_settings)
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
        self.orders = AsyncOrdersService(self)
        self.chats = AsyncChatsService(self)

    async def __aenter__(self) -> AsyncClient:
        """Входит в асинхронный контекстный менеджер.

        Returns:
            AsyncClient: Сам объект.
        """
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Закрывает соединения при выходе.

        Args:
            *exc (object): Сведения об исключении. Не используются.

        Returns:
            None
        """
        await self.close()

    async def close(self) -> None:
        """Закрывает пул соединений.

        Returns:
            None
        """
        await self._fetcher.close()

    def capability(self, capability: Capability) -> CapabilityState:
        """Возвращает текущее состояние возможности.

        Args:
            capability (Capability): Возможность.

        Returns:
            CapabilityState: Состояние, каким его видит клиент сейчас.
        """
        return self.engine.capability(capability)

    async def watch(
        self,
        router: Router,
        *,
        account_id: str = "self",
        max_iterations: int | None = None,
        schedule: Schedule | None = None,
        state_path: Path | None = None,
    ) -> None:
        """Ведёт наблюдение: опрашивает площадку и раздаёт события обработчикам.

        Метод не блокирует поток: между опросами он отдаёт управление циклу
        событий. Сам цикл наблюдения целиком описан ядром и совпадает с
        синхронным до строки.

        Args:
            router (Router): Реестр обработчиков. Обработчики могут быть как
                обычными функциями, так и сопрограммами.
            account_id (str): Идентификатор аккаунта для отпечатков событий.
            max_iterations (int | None): Сколько шагов сделать. None означает
                бесконечно; ограничение нужно проверкам и разовым прогонам.
            schedule (Schedule | None): Расписание опроса. По умолчанию из
                спецификации.
            state_path (Path | None): Файл, в котором состояние гашения повторов
                переживает перезапуск.

        Returns:
            None

        Raises:
            FunoraError: Любая ошибка чтения, которую не удалось повторить.
        """
        await self.run(
            self.engine.watch(
                router,
                account_id=account_id,
                max_iterations=max_iterations,
                schedule=schedule,
                state_path=state_path,
            ),
            router=router,
        )

    async def run(
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
                result: T = stop.value
                return result
            failure = None
            reply = None

            if isinstance(request, Pause):
                if request.ms > 0:
                    await asyncio.sleep(request.ms / 1000)
            elif isinstance(request, Fetch):
                try:
                    reply = await self._fetch(request.path)
                except FunoraError as exc:
                    failure = exc
            elif isinstance(request, Deliver):
                if router is None:
                    raise ConfigurationError(
                        "ядро просит раздать события, но реестр обработчиков не передан"
                    )
                reply = await adispatch(router, request.events)

    async def _fetch(self, path: str) -> Observation:
        """Выполняет одно обращение к площадке.

        Args:
            path (str): Путь страницы.

        Returns:
            Observation: Результат обращения.

        Raises:
            FunoraError: При сетевом отказе либо непригодном ответе.
        """
        return await self._fetcher.fetch(path)
