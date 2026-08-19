"""Проверки роутера и цикла наблюдения.

Главная проверка здесь одна: базовый снимок не сдвигается, пока обработчик не
принял событие. Сдвинь его раньше - и событие, на котором обработчик упал,
исчезнет навсегда, а падает он как раз на тех событиях, которые важнее прочих.

Цикл проверяется с подставным транспортом и подменённым сном. Настоящего
ожидания в наборе нет: цикл с интервалом в три секунды проверялся бы минутами.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import funora._client as client_module
from funora._client import Client
from funora._diff import Event
from funora._poll import Schedule
from funora._transport import Observation
from funora._watch import Router, dispatch
from funora.errors import FunoraError, HandlerError
from funora.events import EventType

#: Каталог со снимками страниц.
FIXTURES = Path(__file__).parent / "fixtures" / "pages"


def _event(index: int, event_type: EventType = EventType.ORDER_CREATED) -> Event:
    """Собирает событие для проверок.

    Args:
        index (int): Порядковый номер, из которого строится идентификатор.
        event_type (EventType): Тип события.

    Returns:
        Event: Событие.
    """
    return Event(
        id=f"e{index}",
        type=event_type,
        ordering_key=f"order:{index}",
        entity_id=str(index),
        observed_at=datetime(2026, 8, 19, tzinfo=UTC),
        origin="structural",
        payload={},
    )


def test_handler_receives_its_type() -> None:
    """Проверяет раздачу по типу события.

    Returns:
        None
    """
    router = Router()
    seen: list[str] = []

    @router.on(EventType.ORDER_CREATED)
    def handle(event: Event) -> None:
        seen.append(event.id)

    dispatch(router, (_event(1), _event(2, EventType.MESSAGE_CREATED)))
    assert seen == ["e1"]


def test_catch_all_handler_receives_everything() -> None:
    """Проверяет обработчик без указания типа.

    Он нужен журналированию и метрикам, которым важен поток целиком, а не
    отдельные типы.

    Returns:
        None
    """
    router = Router()
    seen: list[EventType] = []

    @router.on()
    def handle(event: Event) -> None:
        seen.append(event.type)

    dispatch(router, (_event(1), _event(2, EventType.MESSAGE_CREATED)))
    assert len(seen) == 2


def test_failed_handler_blocks_the_baseline() -> None:
    """Проверяет главное правило цикла.

    База сдвигается только после обработчиков. Упавший обработчик оставляет её
    на месте, и то же событие приходит снова.

    Returns:
        None
    """
    router = Router()

    @router.on(EventType.ORDER_CREATED)
    def handle(event: Event) -> None:
        raise ValueError("не смог")

    result = dispatch(router, (_event(1),))
    assert not result.advance
    assert result.failed == (_event(1),)
    assert isinstance(result.errors[0], HandlerError)
    assert isinstance(result.errors[0].__cause__, ValueError)


def test_one_failure_does_not_cancel_other_events() -> None:
    """Проверяет, что отказ обработчика не отменяет соседние события.

    Он отменяет только сдвиг базы. Соседние события к упавшему отношения не
    имеют, и терять их значило бы наказывать за чужую ошибку.

    Returns:
        None
    """
    router = Router()
    seen: list[str] = []

    @router.on(EventType.ORDER_CREATED)
    def handle(event: Event) -> None:
        seen.append(event.id)
        if event.id == "e2":
            raise ValueError("не смог")

    result = dispatch(router, (_event(1), _event(2), _event(3)))
    assert seen == ["e1", "e2", "e3"]
    assert len(result.delivered) == 2
    assert len(result.failed) == 1
    assert not result.advance


def test_event_without_handler_does_not_block() -> None:
    """Проверяет, что событие без подписки не держит базу.

    Подписка на всё подряд не обязанность вызывающего, и база из-за неё стоять
    не должна.

    Returns:
        None
    """
    assert dispatch(Router(), (_event(1),)).advance


def test_funora_error_from_handler_is_not_swallowed() -> None:
    """Проверяет, что ошибка из иерархии Funora проходит наружу.

    Обработчик, вызвавший операцию клиента и получивший истёкшую сессию, не
    должен выглядеть как обработчик с багом: лечится это по-разному.

    Returns:
        None
    """
    router = Router()

    @router.on(EventType.ORDER_CREATED)
    def handle(event: Event) -> None:
        raise FunoraError("сессия истекла")

    with pytest.raises(FunoraError):
        dispatch(router, (_event(1),))


def _page(name: str) -> str:
    """Читает снимок страницы.

    Args:
        name (str): Имя снимка без расширения.

    Returns:
        str: Разметка снимка.
    """
    return (FIXTURES / f"{name}.skeleton.txt").read_text(encoding="utf-8")


def _observation(html: str) -> Observation:
    """Собирает результат обращения из готовой разметки.

    Args:
        html (str): Тело ответа.

    Returns:
        Observation: Наблюдение.
    """
    return replace(
        Observation(
            status=200,
            final_url="https://funpay.com/orders/trade",
            html=html,
            elapsed_ms=10,
            redirects=0,
            content_length=len(html.encode("utf-8")),
        )
    )


class _Cycle:
    """Подставной транспорт, отдающий страницы по кругу.

    Args:
        pages (list[str]): Разметка страниц в порядке выдачи.
    """

    def __init__(self, pages: list[str]) -> None:
        self._pages = pages
        self.calls = 0

    def fetch(self, path: str) -> Observation:
        """Отдаёт следующую страницу.

        Args:
            path (str): Запрошенный путь. Не используется.

        Returns:
            Observation: Наблюдение.
        """
        # По кругу, а не с упором в последнюю страницу. Цикл спрашивает заказы
        # и диалоги по очереди, и упор ломает чередование: на третьем шаге
        # запрос заказов получил бы страницу диалогов.
        page = self._pages[self.calls % len(self._pages)]
        self.calls += 1
        return _observation(page)

    def close(self) -> None:
        """Закрывает подставной транспорт.

        Returns:
            None
        """


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Подменяет сон счётчиком пауз.

    Args:
        monkeypatch (pytest.MonkeyPatch): Механизм подмены.

    Returns:
        list[float]: Длительности, которые цикл собирался проспать.
    """
    slept: list[float] = []
    monkeypatch.setattr(client_module, "sleep", slept.append)
    return slept


def test_cold_start_is_silent(no_sleep: list[float]) -> None:
    """Проверяет, что первый проход не порождает событий данных.

    Иначе холодный старт даёт лавину «изменений» по всем существующим заказам и
    диалогам сразу - при том, что не изменилось ничего.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    orders, chats = _page("orders-trade.logged.ru"), _page("chat.logged.ru")
    router = Router()
    seen: list[EventType] = []

    @router.on()
    def handle(event: Event) -> None:
        seen.append(event.type)

    with Client(transport=_Cycle([orders, chats])) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=1)

    assert seen == [EventType.WATCH_PRIMED]


def test_second_pass_without_changes_is_silent(no_sleep: list[float]) -> None:
    """Проверяет, что неизменное состояние не порождает событий.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    orders, chats = _page("orders-trade.logged.ru"), _page("chat.logged.ru")
    router = Router()
    data: list[EventType] = []

    @router.on(EventType.ORDER_CREATED)
    def handle(event: Event) -> None:
        data.append(event.type)

    transport = _Cycle([orders, chats, orders, chats])
    with Client(transport=transport) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=2)

    assert data == []
    assert transport.calls == 4


def test_new_order_reaches_the_handler(no_sleep: list[float]) -> None:
    """Проверяет сквозной путь события до обработчика.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    orders = _page("orders-trade.logged.ru")
    chats = _page("chat.logged.ru")
    grown = orders.replace(
        'href="https://funpay.com/orders/{n}/"',
        'href="https://funpay.com/orders/777/"',
        1,
    )

    router = Router()
    seen: list[str] = []

    @router.on(EventType.ORDER_CREATED)
    def handle(event: Event) -> None:
        seen.append(event.entity_id)

    with Client(transport=_Cycle([orders, chats, grown, chats])) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=2)

    assert seen == ["777"]


def test_failed_handler_makes_the_event_come_again(no_sleep: list[float]) -> None:
    """Проверяет повторную доставку после отказа обработчика.

    Это и есть смысл правила «база сдвигается после обработчиков»: событие,
    которое обработчик не смог принять, приходит снова, а не исчезает.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    orders = _page("orders-trade.logged.ru")
    chats = _page("chat.logged.ru")
    grown = orders.replace(
        'href="https://funpay.com/orders/{n}/"',
        'href="https://funpay.com/orders/777/"',
        1,
    )

    router = Router()
    attempts: list[str] = []

    @router.on(EventType.ORDER_CREATED)
    def handle(event: Event) -> None:
        attempts.append(event.entity_id)
        raise ValueError("не смог")

    with Client(transport=_Cycle([orders, chats, grown, chats, grown, chats])) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=3)

    assert attempts == ["777", "777"], "событие обязано прийти снова после отказа"


def test_interval_grows_while_nothing_happens(no_sleep: list[float]) -> None:
    """Проверяет, что цикл замедляется в покое.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    orders, chats = _page("orders-trade.logged.ru"), _page("chat.logged.ru")

    with Client(transport=_Cycle([orders, chats])) as client:  # type: ignore[arg-type]
        client.watch(Router(), max_iterations=4, schedule=Schedule())

    assert no_sleep == sorted(no_sleep)
    assert no_sleep[0] < no_sleep[-1]
