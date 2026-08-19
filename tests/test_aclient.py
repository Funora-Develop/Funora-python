"""Проверки асинхронного клиента - как сверка с синхронным.

Набор устроен не как «повторим проверки фасада ещё раз». Ядро у двух клиентов
одно, и повторять его проверки было бы враньём: они прошли бы, ничего не проверив.
Проверяется здесь ровно то, чего ядро гарантировать не может, - что драйверы
одинаково исполняют его просьбы.

Три вопроса, на которые набор отвечает.

Совпадает ли результат чтения. Одни и те же заготовленные ответы прогоняются
через оба клиента, и разобранные страницы сравниваются между собой.

Совпадает ли последовательность событий цикла наблюдения, включая молчание
холодного старта и несдвинутый курсор после упавшего обработчика.

Что происходит с обработчиком не того вида. Асинхронный обработчик в синхронном
клиенте обязан отказать вслух: промолчать значило бы зарегистрировать
обработчик, который никогда не выполнится - ни исключения, ни строки в журнале,
просто ничего не происходит.

Сети нет, сна нет: транспорт подставной, паузы считаются.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from test_client import _FakeFetcher, _observation, _page

from funora._aclient import AsyncClient
from funora._budget import Budget
from funora._client import Client
from funora._diff import Event
from funora._poll import Schedule
from funora._watch import Router
from funora.capabilities import Capability, CapabilityState
from funora.errors import (
    ConfigurationError,
    InvalidCredentialsError,
    SessionExpiredError,
    UnsupportedCapabilityError,
    ValidationError,
)
from funora.events import EventType


class _AsyncFakeFetcher(_FakeFetcher):
    """Подставной асинхронный транспорт поверх синхронного.

    Наследование здесь по делу: очередь ответов и счётчик обращений обязаны
    вести себя одинаково, иначе сверка сравнивала бы разные подставы, а не два
    клиента.
    """

    async def fetch(self, path: str) -> object:  # type: ignore[override]
        """Отдаёт следующий заготовленный ответ.

        Args:
            path (str): Запрошенный путь. Не используется.

        Returns:
            object: Заготовленное наблюдение.
        """
        return super().fetch(path)

    async def close(self) -> None:  # type: ignore[override]
        """Закрывает подставной транспорт.

        Returns:
            None
        """


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Подменяет оба вида сна счётчиком пауз.

    Args:
        monkeypatch (pytest.MonkeyPatch): Механизм подмены.

    Returns:
        list[float]: Список длительностей, куда попадает каждая пауза.
    """
    waits: list[float] = []

    def fake_sleep(seconds: float) -> None:
        """Записывает паузу вместо того, чтобы спать.

        Args:
            seconds (float): Длительность.

        Returns:
            None
        """
        waits.append(seconds)

    async def fake_asleep(seconds: float) -> None:
        """То же для асинхронной паузы.

        Args:
            seconds (float): Длительность.

        Returns:
            None
        """
        waits.append(seconds)

    monkeypatch.setattr("funora._client.sleep", fake_sleep)
    monkeypatch.setattr(asyncio, "sleep", fake_asleep)
    return waits


def _sync_client(responses: list[object]) -> Client:
    """Собирает синхронный клиент на подставном транспорте.

    Args:
        responses (list[object]): Заготовленные ответы.

    Returns:
        Client: Клиент.
    """
    return Client(transport=_FakeFetcher(responses), budget=Budget())  # type: ignore[arg-type]


def _async_client(responses: list[object]) -> AsyncClient:
    """Собирает асинхронный клиент на подставном транспорте.

    Args:
        responses (list[object]): Заготовленные ответы.

    Returns:
        AsyncClient: Клиент.
    """
    return AsyncClient(transport=_AsyncFakeFetcher(responses), budget=Budget())  # type: ignore[arg-type]


async def test_orders_read_matches_the_sync_one() -> None:
    """Проверяет совпадение прочитанного списка заказов.

    Returns:
        None
    """
    page = _page("orders-trade.logged.ru")
    with _sync_client([_observation(page)]) as sync:
        a = sync.orders.list()
    async with _async_client([_observation(page)]) as client:
        b = await client.orders.list()

    assert a.completeness is b.completeness
    assert a.rows_total == b.rows_total
    assert [e.order_id for e in a.rows()] == [e.order_id for e in b.rows()]


async def test_chats_read_matches_the_sync_one() -> None:
    """Проверяет совпадение прочитанного списка диалогов.

    Returns:
        None
    """
    page = _page("chat.logged.ru")
    with _sync_client([_observation(page)]) as sync:
        a = sync.chats.list()
    async with _async_client([_observation(page)]) as client:
        b = await client.chats.list()

    assert a.completeness is b.completeness
    assert [c.node_id for c in a.rows()] == [c.node_id for c in b.rows()]


async def test_bad_answer_gets_the_same_diagnosis() -> None:
    """Проверяет, что негодный ответ диагностируется одинаково.

    Диагноз - не мелочь. Гостевая страница при неподтверждённой сессии значит
    «секрет неверен», а после хотя бы одного удачного чтения - «сессия истекла»,
    и лечатся эти два по-разному. Разойдись клиенты здесь, один из двух отправлял
    бы человека не туда.

    Returns:
        None
    """
    good = _observation(_page("orders-trade.logged.ru"))
    guest = _observation(_page("orders-trade.guest.ru"))

    def diagnose_sync(responses: list[object]) -> type[BaseException]:
        """Возвращает тип ошибки, полученной синхронным клиентом.

        Args:
            responses (list[object]): Заготовленные ответы.

        Returns:
            type[BaseException]: Тип поднятой ошибки.
        """
        with _sync_client(responses) as client:
            for _ in range(len(responses) - 1):
                client.orders.list()
            try:
                client.orders.list()
            except BaseException as exc:  # noqa: BLE001
                return type(exc)
        raise AssertionError("ошибки не было")

    async def diagnose_async(responses: list[object]) -> type[BaseException]:
        """То же для асинхронного клиента.

        Args:
            responses (list[object]): Заготовленные ответы.

        Returns:
            type[BaseException]: Тип поднятой ошибки.
        """
        async with _async_client(responses) as client:
            for _ in range(len(responses) - 1):
                await client.orders.list()
            try:
                await client.orders.list()
            except BaseException as exc:  # noqa: BLE001
                return type(exc)
        raise AssertionError("ошибки не было")

    cold = diagnose_sync([guest])
    assert cold is await diagnose_async([guest])
    assert cold is InvalidCredentialsError, "непроверенная сессия - это неверный секрет"

    warm = diagnose_sync([good, guest])
    assert warm is await diagnose_async([good, guest])
    assert warm is SessionExpiredError, "подтверждённая однажды сессия - истёкшая"


async def test_thread_id_is_checked_before_the_network() -> None:
    """Проверяет, что мусор в идентификаторе не доходит до сети.

    Returns:
        None
    """
    fetcher = _AsyncFakeFetcher([])
    async with AsyncClient(transport=fetcher, budget=Budget()) as client:  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            await client.chats.thread("../../etc")
    assert fetcher.calls == 0, "запрос ушёл до проверки идентификатора"


async def test_capability_gate_blocks_before_the_network() -> None:
    """Проверяет, что закрытая возможность останавливает до обращения.

    Returns:
        None
    """
    fetcher = _AsyncFakeFetcher([])
    async with AsyncClient(transport=fetcher, budget=Budget()) as client:  # type: ignore[arg-type]
        client.engine._state.capabilities[Capability.ORDERS_LIST] = CapabilityState.UNSUPPORTED
        with pytest.raises(UnsupportedCapabilityError):
            await client.orders.list()
    assert fetcher.calls == 0


def _watch_pages(rounds: int) -> list[object]:
    """Готовит ответы на несколько шагов наблюдения.

    Args:
        rounds (int): Сколько шагов обеспечить.

    Returns:
        list[object]: Наблюдения в порядке обращения: заказы, диалоги, заказы...
    """
    orders = _observation(_page("orders-trade.logged.ru"))
    chats = _observation(_page("chat.logged.ru"))
    out: list[object] = []
    for _ in range(rounds):
        out.extend((orders, chats))
    return out


async def test_watch_emits_the_same_events(no_sleep: list[float]) -> None:
    """Проверяет совпадение последовательности событий цикла наблюдения.

    Главная проверка набора. Молчание холодного старта, единственное
    watch.primed и отсутствие лавины «изменений» на втором шаге - это правила
    цикла, и оба клиента обязаны исполнять их одинаково.

    Args:
        no_sleep (list[float]): Счётчик пауз.

    Returns:
        None
    """
    seen_sync: list[EventType] = []
    seen_async: list[EventType] = []

    sync_router = Router()
    sync_router.on()(lambda event: seen_sync.append(event.type))

    async def remember(event: Event) -> None:
        """Запоминает тип события.

        Args:
            event (Event): Событие.

        Returns:
            None
        """
        seen_async.append(event.type)

    async_router = Router()
    async_router.on()(remember)

    with _sync_client(_watch_pages(2)) as sync:
        sync.watch(sync_router, max_iterations=2, schedule=Schedule())
    async with _async_client(_watch_pages(2)) as client:
        await client.watch(async_router, max_iterations=2, schedule=Schedule())

    assert seen_sync == seen_async
    assert seen_sync == [EventType.WATCH_PRIMED], "холодный старт обязан молчать о данных"


async def test_failed_handler_keeps_the_cursor_in_both(
    no_sleep: list[float], tmp_path: Path
) -> None:
    """Проверяет, что упавший обработчик одинаково удерживает курсор.

    Если бы асинхронный драйвер счёл отказ обработчика успехом, событие исчезло
    бы навсегда - и заметить это можно было бы только по невыданному товару.

    Args:
        no_sleep (list[float]): Счётчик пауз.
        tmp_path (Path): Каталог для файла состояния.

    Returns:
        None
    """

    async def boom(event: Event) -> None:
        """Падает на любом событии.

        Args:
            event (Event): Событие.

        Returns:
            None

        Raises:
            RuntimeError: Всегда.
        """
        raise RuntimeError("обработчик не смог")

    router = Router()
    router.on()(boom)

    state = tmp_path / "state.json"
    async with _async_client(_watch_pages(1)) as client:
        await client.watch(router, max_iterations=1, schedule=Schedule(), state_path=state)

    saved = state.read_text(encoding="utf-8")
    assert '"orders": null' in saved, "курсор сдвинулся вопреки упавшему обработчику"


def test_async_handler_in_a_sync_client_is_loud() -> None:
    """Проверяет, что асинхронный обработчик в синхронном клиенте отвергается.

    Промолчать здесь значило бы зарегистрировать обработчик, который никогда не
    выполнится: ни исключения, ни строки в журнале - просто ничего не
    происходит, а вызывающий уверен, что подписался.

    Returns:
        None
    """
    from funora._watch import dispatch, primed

    async def handler(event: Event) -> None:
        """Обработчик, который синхронному клиенту дождаться нечем.

        Args:
            event (Event): Событие.

        Returns:
            None
        """

    router = Router()
    router.on()(handler)

    from datetime import UTC, datetime

    event = primed("self", datetime(2026, 8, 19, tzinfo=UTC), "account:self")
    with pytest.raises(ConfigurationError):
        dispatch(router, (event,))


async def test_sync_handler_works_in_an_async_client(no_sleep: list[float]) -> None:
    """Проверяет, что обычная функция принимается асинхронным клиентом.

    Обратная сторона предыдущей проверки: обработчик, которому нечего ждать, -
    законный обработчик, и требовать от него быть сопрограммой не за что.

    Args:
        no_sleep (list[float]): Счётчик пауз.

    Returns:
        None
    """
    seen: list[EventType] = []
    router = Router()
    router.on()(lambda event: seen.append(event.type))

    async with _async_client(_watch_pages(1)) as client:
        await client.watch(router, max_iterations=1, schedule=Schedule())

    assert seen == [EventType.WATCH_PRIMED]


class _AsyncByPath:
    """Адресный подставной транспорт для асинхронного клиента.

    Args:
        orders (str): Страница заказов.
        chats (list[str]): Страницы списка диалогов по обращениям.
        threads (list[str]): Страницы переписки по обращениям.
    """

    def __init__(self, orders: str, chats: list[str], threads: list[str]) -> None:
        self._orders = orders
        self._chats = chats
        self._threads = threads
        self.paths: list[str] = []
        self._chat_calls = 0
        self._thread_calls = 0

    async def fetch(self, path: str):  # type: ignore[no-untyped-def]
        """Отдаёт страницу, отвечающую адресу.

        Args:
            path (str): Запрошенный путь.

        Returns:
            Observation: Наблюдение.
        """
        self.paths.append(path)
        if path.startswith("/orders"):
            body = self._orders
        elif "node=" in path:
            body = self._threads[min(self._thread_calls, len(self._threads) - 1)]
            self._thread_calls += 1
        else:
            body = self._chats[min(self._chat_calls, len(self._chats) - 1)]
            self._chat_calls += 1
        return _observation(body)

    async def close(self) -> None:
        """Закрывает подставной транспорт.

        Returns:
            None
        """


async def test_thread_following_works_through_the_async_driver(no_sleep: list[float]) -> None:
    """Проверяет, что дочитывание переписок работает и в асинхронном клиенте.

    Проверка не дублирует синхронную. Дочитывание устроено вложенной
    сопрограммой: ядро делегирует чтение переписки другой сопрограмме через
    yield from, и драйвер обязан прокрутить эту вложенность. Сломайся
    делегирование - синхронный клиент прошёл бы, а асинхронный нет.

    Args:
        no_sleep (list[float]): Счётчик пауз.

    Returns:
        None
    """
    import re

    dialogs = re.sub(
        r'data-id="[^"]*"',
        lambda _m: f'data-id="{1000 + len(_m.string[: _m.start()]) % 7}"',
        _page("chat.logged.ru"),
    )
    node_id = re.search(r'data-id="(\d+)"', dialogs).group(1)
    moved = dialogs.replace('data-node-msg="T10:d#1"', 'data-node-msg="T10:d#777"', 1)
    again = dialogs.replace('data-node-msg="T10:d#1"', 'data-node-msg="T10:d#888"', 1)

    before = _page("chat-thread.logged.ru")
    after = before.replace('id="T18:adp#10"', 'id="message-fresh"', 1)

    seen: list[Event] = []
    router = Router()
    router.on()(seen.append)

    transport = _AsyncByPath(
        _page("orders-trade.logged.ru"), [dialogs, moved, again], [before, after, after]
    )
    async with AsyncClient(transport=transport, budget=Budget()) as client:  # type: ignore[arg-type]
        await client.watch(router, max_iterations=3)

    assert [p for p in transport.paths if "node=" in p], "переписка не читалась вовсе"
    fresh = [e for e in seen if e.type is EventType.MESSAGE_CREATED]
    assert len(fresh) == 1
    assert fresh[0].ordering_key == f"chat:{node_id}"
