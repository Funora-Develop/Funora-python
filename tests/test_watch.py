"""Проверки роутера и цикла наблюдения.

Главная проверка здесь одна: базовый снимок не сдвигается, пока обработчик не
принял событие. Сдвинь его раньше - и событие, на котором обработчик упал,
исчезнет навсегда, а падает он как раз на тех событиях, которые важнее прочих.

Цикл проверяется с подставным транспортом и подменённым сном. Настоящего
ожидания в наборе нет: цикл с интервалом в три секунды проверялся бы минутами.
"""

from __future__ import annotations

import json
import re
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
from funora.errors import (
    FunoraError,
    HandlerError,
    SessionExpiredError,
    StateSchemaIncompatibleError,
)
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


def test_funora_error_does_not_tear_the_batch_apart() -> None:
    """Проверяет, что ошибка площадки не обрывает раздачу посреди партии.

    Раньше здесь стоял raise, и партия рвалась: накопленные delivered и failed
    пропадали, курсор не сохранялся, цикл падал целиком. Условие площадки при
    этом никуда не девалось - оно просто уносило с собой все остальные события
    партии.

    Теперь партия дорабатывается, а ошибка возвращается отдельным полем: она не
    баг обработчика, а условие площадки, и вызывающий обязан её увидеть.

    Returns:
        None
    """
    router = Router()
    seen: list[str] = []

    @router.on(EventType.ORDER_CREATED)
    def handle(event: Event) -> None:
        seen.append(event.id)
        if event.id == "e2":
            raise FunoraError("сессия истекла")

    result = dispatch(router, (_event(1), _event(2), _event(3)))

    assert seen == ["e1", "e2", "e3"], "партия обязана дойти до конца"
    assert isinstance(result.fatal, FunoraError)
    assert result.failed == (_event(2),)
    assert not result.advance


def test_no_fatal_error_when_handlers_are_fine() -> None:
    """Проверяет, что поле ошибки площадки пусто при штатной работе.

    Returns:
        None
    """
    router = Router()

    @router.on(EventType.ORDER_CREATED)
    def handle(event: Event) -> None:
        return None

    assert dispatch(router, (_event(1),)).fatal is None


def test_ordinary_handler_bug_is_not_fatal() -> None:
    """Проверяет, что обычное исключение обработчика не считается условием площадки.

    Опечатка в обработчике и истёкшая сессия лечатся по-разному, и склеивать их
    значило бы останавливать цикл из-за чужого бага либо продолжать работу при
    недействительной сессии.

    Returns:
        None
    """
    router = Router()

    @router.on(EventType.ORDER_CREATED)
    def handle(event: Event) -> None:
        raise ValueError("опечатка")

    result = dispatch(router, (_event(1),))
    assert result.fatal is None
    assert isinstance(result.errors[0], HandlerError)


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


def _renamed_first_order(html: str) -> str:
    """Меняет идентификатор первого заказа на заведомо новый.

    Для наблюдения это неотличимо от появления заказа: в курсоре такого
    идентификатора нет. Именно так и проверяется весь путь события - от разбора
    до обработчика.

    Идентификатор ищется в разметке, а не пишется здесь: формат снимка нумерует
    их, и записанное руками значение разъезжается с ним молча - подстановка
    тогда не срабатывает, событие не возникает, и проверка падает не там, где
    сломалось.

    Args:
        html (str): Разметка снимка списка заказов.

    Returns:
        str: Та же разметка с переименованным первым заказом.
    """
    found = re.search(r'href="https://funpay\.com/orders/([^/"]+)/"', html)
    assert found is not None, "в снимке не нашлось ни одной ссылки на заказ"
    return html.replace(found.group(0), 'href="https://funpay.com/orders/777/"', 1)


def test_new_order_reaches_the_handler(no_sleep: list[float]) -> None:
    """Проверяет сквозной путь события до обработчика.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    orders = _page("orders-trade.logged.ru")
    chats = _page("chat.logged.ru")
    grown = _renamed_first_order(orders)

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
    grown = _renamed_first_order(orders)

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


def test_watch_survives_a_restart(no_sleep: list[float], tmp_path: Path) -> None:
    """Проверяет, что событие не приходит повторно после перезапуска.

    Кэш только в памяти означает, что после любого перезапуска повторно приходит
    всё, что успело прийти до него. Для обработчика, выдающего товар, это
    выданный дважды товар при каждом перезапуске процесса.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.
        tmp_path (Path): Временный каталог для файла состояния.

    Returns:
        None
    """
    orders = _page("orders-trade.logged.ru")
    chats = _page("chat.logged.ru")
    grown = _renamed_first_order(orders)
    state_path = tmp_path / "state.json"

    seen: list[str] = []
    router = Router()

    @router.on(EventType.ORDER_CREATED)
    def handle(event: Event) -> None:
        seen.append(event.entity_id)

    with Client(transport=_Cycle([orders, chats, grown, chats])) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=2, state_path=state_path)

    assert seen == ["777"]
    assert state_path.is_file(), "состояние обязано сохраниться"

    # Второй процесс: тот же заказ уже есть на первой же странице, поэтому
    # холодный старт его не заметит. Но если бы заметил, гашение обязано
    # отработать по восстановленному состоянию.
    with Client(transport=_Cycle([grown, chats, grown, chats])) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=2, state_path=state_path)

    assert seen == ["777"], "после перезапуска событие пришло повторно"


def test_watch_refuses_foreign_state(no_sleep: list[float], tmp_path: Path) -> None:
    """Проверяет отказ на файле состояния чужого формата.

    Молчаливый старт с нуля неотличим от штатной работы и приводит к повторной
    обработке всего, что уже обработано.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.
        tmp_path (Path): Временный каталог.

    Returns:
        None
    """
    state_path = tmp_path / "state.json"
    state_path.write_text('{"format": "чужой", "payload": {}}', encoding="utf-8")

    orders, chats = _page("orders-trade.logged.ru"), _page("chat.logged.ru")
    with (
        Client(transport=_Cycle([orders, chats])) as client,  # type: ignore[arg-type]
        pytest.raises(StateSchemaIncompatibleError),
    ):
        client.watch(Router(), max_iterations=1, state_path=state_path)


def _orders_with(ids: list[str]) -> str:
    """Готовит страницу заказов с заданными идентификаторами.

    Формат снимка нумерует идентификаторы, и заказы в нём уже различимы. Замена
    нужна не ради этого, а ради управляемости: проверке нужно знать заранее,
    какой заказ она объявит новым, а нумерация зависит от порядка обхода и от
    состава страницы.

    Заменяются ровно первые len(ids) строк; остальные остаются как были.

    Args:
        ids (list[str]): Идентификаторы, по одному на строку снимка.

    Returns:
        str: Разметка страницы.

    Raises:
        AssertionError: Если строк в снимке меньше, чем запрошено.
    """
    page = _page("orders-trade.logged.ru")
    for number in ids:
        # Поиск привязан к классу строки. Без привязки первым совпадением
        # оказывалась ссылка меню на /orders/trade, и заменялась она, а не
        # заказ: проверка проходила, ничего не подставив.
        found = re.search(r'class="tc-item[^"]*" href="[^"]*/orders/(?!\d+/")[^"]+"', page)
        assert found is not None, "в снимке кончились незаменённые строки"
        page = page.replace(
            found.group(0),
            found.group(0).split(" href=")[0] + f' href="https://funpay.com/orders/{number}/"',
            1,
        )
    return page


def test_restart_does_not_swallow_what_changed_while_it_was_down(
    no_sleep: list[float], tmp_path: Path
) -> None:
    """Проверяет главное, ради чего курсор сохраняется.

    Заказ, оплаченный между остановкой и запуском, обязан породить событие.
    Раньше перезапуск уходил в холодный старт и молча съедал всё, что изменилось
    за простой: ни исключения, ни строки в журнале, а товар не выдан. Отказа
    обработчика для этого не требовалось - хватало планового обновления бота.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.
        tmp_path (Path): Временный каталог для файла состояния.

    Returns:
        None
    """
    state_path = tmp_path / "state.json"
    chats = _page("chat.logged.ru")
    before = _orders_with(["101", "102", "103"])
    after = _orders_with(["101", "102", "104"])

    seen: list[str] = []
    router = Router()

    @router.on(EventType.ORDER_CREATED)
    def handle(event: Event) -> None:
        seen.append(event.entity_id)

    with Client(transport=_Cycle([before, chats])) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=1, state_path=state_path)

    assert seen == [], "холодный старт обязан молчать о данных"

    # Процесс остановлен. Пока он стоял, появился заказ 104.
    with Client(transport=_Cycle([after, chats])) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=1, state_path=state_path)

    assert seen == ["104"], "изменение за простой обязано дойти до обработчика"


def test_partial_read_does_not_move_the_cursor(no_sleep: list[float], tmp_path: Path) -> None:
    """Проверяет, что неполное чтение не сдвигает курсор.

    Строка, выпавшая из неполного чтения, при следующем чтении выглядела бы
    новым заказом, и бот выдал бы товар по заказу, который был и раньше.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.
        tmp_path (Path): Временный каталог.

    Returns:
        None
    """
    state_path = tmp_path / "state.json"
    chats = _page("chat.logged.ru")
    whole = _orders_with(["101", "102", "103"])

    # У первой строки пропал адрес: она отбрасывается, чтение неполное.
    first = whole.index('<a class="tc-item info" href=')
    end = whole.index(">", first)
    damaged = whole[:first] + '<a class="tc-item info"' + whole[end:]

    seen: list[str] = []
    router = Router()

    @router.on(EventType.ORDER_CREATED)
    def handle(event: Event) -> None:
        seen.append(event.entity_id)

    with Client(transport=_Cycle([whole, chats, damaged, chats, whole, chats])) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=3, state_path=state_path)

    assert seen == [], "заказ 101 существовал всё время и новым не является"


def test_platform_error_from_handler_reaches_the_caller(
    no_sleep: list[float], tmp_path: Path
) -> None:
    """Проверяет, что условие площадки из обработчика доходит до вызывающего.

    Партия при этом дорабатывается и состояние сохраняется: отказ на первом
    событии не должен уносить с собой остальные.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.
        tmp_path (Path): Временный каталог.

    Returns:
        None
    """
    state_path = tmp_path / "state.json"
    chats = _page("chat.logged.ru")
    before = _orders_with(["101", "102", "103"])
    after = _orders_with(["101", "102", "104"])

    router = Router()

    @router.on(EventType.ORDER_CREATED)
    def handle(event: Event) -> None:
        raise SessionExpiredError("сессия истекла")

    with Client(transport=_Cycle([before, chats])) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=1, state_path=state_path)

    with (
        Client(transport=_Cycle([after, chats])) as client,  # type: ignore[arg-type]
        pytest.raises(SessionExpiredError),
    ):
        client.watch(router, max_iterations=1, state_path=state_path)

    assert state_path.is_file(), "состояние обязано сохраниться до подъёма ошибки"


class _ByPath:
    """Подставной транспорт, отвечающий по адресу, а не по счётчику.

    Счётчик годился, пока цикл спрашивал ровно две страницы по очереди. С
    дочитыванием переписок порядок обращений перестал быть постоянным, и
    транспорт, отдающий страницы по кругу, выдал бы на запрос заказов страницу
    переписки - проверка сломалась бы не там, где ошибка.

    Args:
        orders (str): Страница заказов.
        chats (list[str]): Страницы списка диалогов, по одной на обращение.
            Последняя повторяется, если обращений больше.
        threads (list[str]): Страницы переписки, так же по обращениям.
    """

    def __init__(self, orders: str, chats: list[str], threads: list[str]) -> None:
        self._orders = orders
        self._chats = chats
        self._threads = threads
        self.paths: list[str] = []
        self._chat_calls = 0
        self._thread_calls = 0

    def fetch(self, path: str) -> Observation:
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

    def threads_read(self) -> list[str]:
        """Возвращает адреса прочитанных переписок.

        Returns:
            list[str]: Адреса в порядке обращения.
        """
        return [path for path in self.paths if "node=" in path]

    def close(self) -> None:
        """Закрывает подставной транспорт.

        Returns:
            None
        """


def _numeric_dialogs(html: str) -> str:
    """Заменяет замаскированные идентификаторы диалогов числовыми.

    Маска скелета превращает идентификатор в подпись вида ``T9:d#1``, а разбор
    отвергает такое до сети: в адрес подставляется только буквенно-цифровое.
    Отказ правильный, но проверять на нём чтение переписки нельзя - оно до сети
    просто не доходит.

    Args:
        html (str): Разметка списка диалогов.

    Returns:
        str: Она же с числовыми идентификаторами.
    """
    counter = [1000]

    def swap(_match: re.Match[str]) -> str:
        """Выдаёт следующий числовой идентификатор.

        Args:
            _match (re.Match[str]): Совпадение. Не используется.

        Returns:
            str: Атрибут с числовым значением.
        """
        counter[0] += 1
        return f'data-id="{counter[0]}"'

    return re.sub(r'data-id="[^"]*"', swap, html)


def _moved(html: str, mark: str) -> str:
    """Двигает позицию последнего сообщения у первого диалога.

    Так выглядит изменение диалога с точки зрения списка: позиция сдвинулась, а
    что именно произошло, по списку не видно - за этим и читается переписка.

    Args:
        html (str): Разметка списка диалогов.
        mark (str): Чем пометить новую позицию.

    Returns:
        str: Разметка с изменившимся первым диалогом.
    """
    return html.replace('data-node-msg="T10:d#1"', f'data-node-msg="T10:d#{mark}"', 1)


def _follow_run(
    chats: list[str],
    steps: int,
    threads: list[str] | None = None,
    **watch_args: object,
) -> tuple[_ByPath, list[Event]]:
    """Прокручивает цикл наблюдения с адресным транспортом.

    Args:
        chats (list[str]): Страницы списка диалогов по обращениям.
        steps (int): Сколько шагов сделать.
        threads (list[str] | None): Страницы переписки по обращениям.
        **watch_args (object): Прочие аргументы watch.

    Returns:
        tuple[_ByPath, list[Event]]: Транспорт и полученные события.
    """
    seen: list[Event] = []
    router = Router()
    router.on()(seen.append)

    transport = _ByPath(
        _page("orders-trade.logged.ru"),
        chats,
        threads or [_page("chat-thread.logged.ru")],
    )
    with Client(transport=transport) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=steps, **watch_args)  # type: ignore[arg-type]
    return transport, seen


def test_cold_start_reads_no_threads(no_sleep: list[float]) -> None:
    """Проверяет, что первый запуск переписок не читает.

    Холодный старт молчит о данных, событий о диалогах не порождает, и читать
    ему нечего. Прочитай он полсотни переписок при первом же запуске - это была
    бы полсотня запросов на ровном месте.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    transport, events = _follow_run([_numeric_dialogs(_page("chat.logged.ru"))], 1)
    assert transport.threads_read() == []
    assert [str(e.type) for e in events] == ["watch.primed"]


def test_changed_dialog_is_followed_and_yields_a_message(no_sleep: list[float]) -> None:
    """Проверяет главное: событие о новом сообщении доходит до обработчика.

    До этой правки оно не порождалось никогда. Тип был объявлен, функция
    порождения написана и проверена, но цикл переписки не читал - и обработчик
    новых сообщений не срабатывал ни разу, молча.

    Шагов три, и меньше нельзя. Первый - холодный старт. На втором диалог
    меняется, переписка читается впервые, и событий она не даёт: курсора для неё
    ещё не было, а объявить новыми все её сообщения значило бы разослать историю
    целиком. На третьем диалог меняется снова - и вот теперь новое сообщение
    видно как новое.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    dialogs = _numeric_dialogs(_page("chat.logged.ru"))
    node_id = re.search(r'data-id="(\d+)"', dialogs).group(1)

    before = _page("chat-thread.logged.ru")
    after = before.replace('id="T18:adp#10"', 'id="message-fresh"', 1)
    assert after != before, "порча не применилась, проверка бессмысленна"

    transport, events = _follow_run(
        [dialogs, _moved(dialogs, "777"), _moved(dialogs, "888")],
        3,
        [before, after, after],
    )

    assert transport.threads_read() == [f"/chat/?node={node_id}"] * 2
    new_messages = [e for e in events if e.type is EventType.MESSAGE_CREATED]
    assert len(new_messages) == 1, "новое сообщение обязано дойти ровно один раз"
    # Сущность события - диалог, а не сообщение: порядок сохраняется внутри
    # диалога, и ключ упорядочивания строится из него. Само сообщение опознаётся
    # нагрузкой и версией, из которой собран отпечаток.
    assert new_messages[0].ordering_key == f"chat:{node_id}"
    assert new_messages[0].entity_id == node_id
    assert new_messages[0].payload["message_id"] == "message-fresh"
    assert new_messages[0].payload["origin"] in {"system", "human", "unknown"}


def test_unreadable_thread_leaves_the_queue(no_sleep: list[float]) -> None:
    """Проверяет, что нечитаемая переписка не заваливает шаг и не запирает очередь.

    Идентификатор, который не подставить в адрес, отвергается до сети. Отказ
    правильный, но он не должен ни отменять шаг, ни оставаться в очереди
    навсегда: повторять вечно то, что не читается, значило бы больше не прочитать
    ничего.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    masked = _page("chat.logged.ru")
    transport, events = _follow_run([masked, _moved(masked, "777"), _moved(masked, "888")], 3)

    assert transport.threads_read() == [], "адрес с маской не должен доходить до сети"
    assert any(e.type is EventType.CHAT_UNREAD_CHANGED for e in events), (
        "отказ чтения переписки не должен отменять событие о диалоге"
    )


def test_queue_is_bounded_and_drains(no_sleep: list[float], tmp_path: Path) -> None:
    """Проверяет предел чтений за шаг и то, что остальное не теряется.

    Изменись разом полсотни диалогов - шаг превратился бы в полсотни запросов.
    Предел это закрывает, но выбросить непрочитанное нельзя: событие о диалоге
    уже доставлено, курсор сдвинут, и повода вернуться к нему больше нет.
    Поэтому очередь ждёт следующих шагов и переживает перезапуск.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.
        tmp_path (Path): Временный каталог.

    Returns:
        None
    """
    dialogs = _numeric_dialogs(_page("chat.logged.ru"))
    # Двигаются позиции сразу у трёх диалогов: столько же и попадёт в очередь.
    many = dialogs
    for mark in ("#1", "#2", "#3"):
        many = many.replace(f'data-node-msg="T10:d{mark}"', f'data-node-msg="T10:d#9{mark[-1]}"', 1)
    assert many != dialogs

    state = tmp_path / "state.json"
    transport, _ = _follow_run([dialogs, many, many], 2, max_threads_per_step=1, state_path=state)
    assert len(transport.threads_read()) == 1, "предел за шаг не соблюдён"

    saved = json.loads(state.read_text(encoding="utf-8"))
    waiting = saved["payload"]["cursor"]["pending_threads"]
    assert len(waiting) == 2, "непрочитанные обязаны ждать в очереди, а не пропадать"


def test_queue_survives_a_restart(no_sleep: list[float], tmp_path: Path) -> None:
    """Проверяет, что очередь дочитывания переживает перезапуск.

    Не переживи она - диалог, изменившийся перед остановкой и не успевший
    дочитаться, не дочитался бы уже никогда: событие о нём доставлено, курсор
    диалогов сдвинут, и повода вернуться к нему больше нет.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.
        tmp_path (Path): Временный каталог.

    Returns:
        None
    """
    dialogs = _numeric_dialogs(_page("chat.logged.ru"))
    many = dialogs
    for mark in ("#1", "#2"):
        many = many.replace(f'data-node-msg="T10:d{mark}"', f'data-node-msg="T10:d#9{mark[-1]}"', 1)

    state = tmp_path / "state.json"
    _follow_run([dialogs, many], 2, max_threads_per_step=1, state_path=state)
    left = json.loads(state.read_text(encoding="utf-8"))["payload"]["cursor"]["pending_threads"]
    assert left, "после первого запуска в очереди должно что-то остаться"

    # Второй запуск: список диалогов не меняется, новых событий нет - и всё
    # равно очередь обязана дочитаться.
    transport, _ = _follow_run([many], 1, max_threads_per_step=1, state_path=state)
    assert transport.threads_read(), "очередь не пережила перезапуск"
