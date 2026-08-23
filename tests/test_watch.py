"""Проверки роутера и цикла наблюдения.

Главная проверка здесь одна: базовый снимок не сдвигается, пока обработчик не
принял событие. Сдвинь его раньше - и событие, на котором обработчик упал,
исчезнет навсегда, а падает он как раз на тех событиях, которые важнее прочих.

Цикл проверяется с подставным транспортом и подменённым сном. Настоящего
ожидания в наборе нет: цикл с интервалом в три секунды проверялся бы минутами.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

import pytest

import funora._client as client_module
import funora._engine as engine_module
from funora._budget import Budget
from funora._client import Client
from funora._diff import Event
from funora._engine import Engine, Pause
from funora._poll import Schedule
from funora._transport import Observation, TransportSettings
from funora._watch import (
    PRODUCIBLE,
    Router,
    adispatch,
    dispatch,
    health_changed,
    incomplete,
    loss,
    primed,
)
from funora.budget import RequestClass
from funora.errors import (
    AccessBlockedError,
    ConfigurationError,
    FunoraError,
    HandlerCancelledError,
    HandlerError,
    RateLimitedError,
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
        account_id="12345678",
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


def test_handler_failure_reaches_the_caller(no_sleep: list[float]) -> None:
    """Проверяет, что причина отказа обработчика доходит до вызывающего.

    Прежде она не доходила никуда. Итог раздачи держит отказы в поле errors, а
    ядро читает у него delivered, advance, fatal и длину failed - errors не
    читал никто. В журнале оставалась строка «курсор не сдвинут: обработчик не
    принял N событий» без единого слова о том, что случилось, а событие
    приходило снова каждый шаг. Бесконечно.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    orders, chats = _page("orders-trade.logged.ru"), _page("chat.logged.ru")
    router = Router()

    @router.on()
    def handle(event: Event) -> None:
        raise ValueError("опечатка в обработчике")

    caught: list[HandlerError] = []
    with Client(transport=_Cycle([orders, chats])) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=1, on_handler_error=caught.append)

    assert len(caught) == 1
    assert isinstance(caught[0], HandlerError)
    # Причина обязана быть на месте: без неё вызывающий знает, что упало, и не
    # знает почему, - а это ровно то состояние, из которого правку не сделать.
    assert isinstance(caught[0].__cause__, ValueError)
    assert str(caught[0].__cause__) == "опечатка в обработчике"


def test_working_handler_reports_nothing(no_sleep: list[float]) -> None:
    """Проверяет, что исправный обработчик не поднимает ложной тревоги.

    Обратная половина: приёмник, срабатывающий всегда, неотличим от
    несрабатывающего никогда.

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

    caught: list[HandlerError] = []
    with Client(transport=_Cycle([orders, chats])) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=1, on_handler_error=caught.append)

    assert seen == [EventType.WATCH_PRIMED]
    assert caught == []


def test_handler_failure_keeps_its_traceback_in_the_log(
    no_sleep: list[float], caplog: pytest.LogCaptureFixture
) -> None:
    """Проверяет, что в журнал уходит трассировка, а не одно имя класса.

    Вызывающий, не передавший on_handler_error, обязан узнать причину хотя бы
    из журнала. Прежде там стояло имя класса исключения и больше ничего.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.
        caplog (pytest.LogCaptureFixture): Перехват журнала.

    Returns:
        None
    """
    orders, chats = _page("orders-trade.logged.ru"), _page("chat.logged.ru")
    router = Router()

    @router.on()
    def handle(event: Event) -> None:
        raise ValueError("опечатка в обработчике")

    with (
        caplog.at_level(logging.WARNING, logger="funora"),
        Client(transport=_Cycle([orders, chats])) as client,  # type: ignore[arg-type]
    ):
        client.watch(router, max_iterations=1)

    failures = [rec for rec in caplog.records if "обработчик упал" in rec.getMessage()]
    assert failures, "отказ обработчика не попал в журнал вовсе"
    assert failures[0].exc_info is not None, "в журнале нет трассировки"
    assert "опечатка в обработчике" in caplog.text


def test_cancelled_handler_is_a_failure_not_a_crash() -> None:
    """Проверяет, что отменившийся обработчик не пробивает раздачу насквозь.

    CancelledError - потомок BaseException, а не Exception, поэтому общая ветка
    её не ловила и она уходила мимо раздачи. Вместе с ней терялась вся партия:
    и доставленное, и недоставленное, - а курсор не сохранялся.

    Returns:
        None
    """
    router = Router()

    @router.on(EventType.ORDER_CREATED)
    async def handle(event: Event) -> None:
        raise asyncio.CancelledError

    result = asyncio.run(adispatch(router, (_event(1),)))

    assert result.fatal is None
    assert isinstance(result.errors[0], HandlerCancelledError)
    assert isinstance(result.errors[0].__cause__, asyncio.CancelledError)
    assert result.failed == (_event(1),)


def test_cancelling_the_task_still_cancels_it() -> None:
    """Проверяет, что отмена задачи извне проходит насквозь.

    Обратная половина, и без неё правка была бы хуже болезни: проглотив чужую
    отмену, раздача сделала бы задачу неотменяемой - обработчик продолжал бы
    работу после того, как её попросили прекратить.

    Returns:
        None
    """
    router = Router()
    started = asyncio.Event()

    @router.on(EventType.ORDER_CREATED)
    async def handle(event: Event) -> None:
        started.set()
        await asyncio.sleep(60)

    async def scenario() -> bool:
        task = asyncio.ensure_future(adispatch(router, (_event(1),)))
        await started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return True
        return False

    assert asyncio.run(scenario()), "отмена задачи была проглочена раздачей"


def test_the_engine_passes_the_declared_class(no_sleep: list[float]) -> None:
    """Проверяет, что класс доходит от операции до бюджета.

    Слабое место всей связки. Класс объявлен у каждой операции, порог считается
    по классу - но если движок не передаст его, всё пройдёт как interactive, и
    доли снова не будут значить ничего. Ровно так и было.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    orders, chats = _page("orders-trade.logged.ru"), _page("chat.logged.ru")
    router = Router()

    @router.on()
    def handle(event: Event) -> None:
        return None

    with Client(transport=_Cycle([orders, chats])) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=1)
        demanded = set(client.engine._budget._demanded_at)

    assert RequestClass.POLL in demanded, (
        "цикл обновлений потратил бюджет не как poll. Класс объявлен у операции "
        f"chats.list и обязан дойти до ведра; дошли: "
        f"{sorted(x.value for x in demanded)}"
    )
    assert RequestClass.INTERACTIVE in demanded, "чтение списка продаж не дошло как interactive"


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
            # Длину объявляет и настоящий сервер. Без неё целостность тела
            # подтвердить нечем, и чтение больше не считается полным - подставной
            # ответ без заголовка изображал бы не площадку, а редкий её сбой.
            declared_length=len(html.encode("utf-8")),
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

    # Часы двигаются вместе со сном, и это не украшение проверки. Бюджет
    # пополняется по времени: подмена, глотающая сон и оставляющая часы на
    # месте, показывает ведро, которое не пополняется НИКОГДА. Проверка тогда
    # проходит или падает по причине, которой в жизни не бывает.
    started = monotonic()
    offset = [0.0]

    def fake_sleep(seconds: float) -> None:
        """Считает паузу и продвигает часы на неё же.

        Args:
            seconds (float): Сколько цикл собирался проспать.

        Returns:
            None
        """
        slept.append(seconds)
        offset[0] += seconds

    def fake_monotonic() -> float:
        """Возвращает время с учётом проспанного.

        Returns:
            float: Монотонные секунды.
        """
        return started + offset[0]

    monkeypatch.setattr(client_module, "sleep", fake_sleep)
    monkeypatch.setattr(engine_module, "monotonic", fake_monotonic)
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


def test_unreadable_thread_leaves_the_queue(no_sleep: list[float], tmp_path: Path) -> None:
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
    state = tmp_path / "state.json"
    transport, events = _follow_run(
        [masked, _moved(masked, "777"), _moved(masked, "888")], 3, state_path=state
    )

    assert transport.threads_read() == [], "адрес с маской не должен доходить до сети"
    assert any(e.type is EventType.CHAT_UNREAD_CHANGED for e in events), (
        "отказ чтения переписки не должен отменять событие о диалоге"
    )
    # Очередь осматривается явно. Без этого «выбывает из очереди» оставалось
    # объявленным в трёх местах и не проверенным ни одним.
    waiting = json.loads(state.read_text(encoding="utf-8"))["payload"]["cursor"]["pending_threads"]
    assert waiting == [], "нечитаемый диалог остался в очереди и жжёт слот каждый шаг"


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


def _dialogs_changing(steps: int) -> list[str]:
    """Готовит список диалогов, меняющийся на каждом шаге.

    Args:
        steps (int): Сколько шагов обеспечить.

    Returns:
        list[str]: Страницы списка диалогов по обращениям.
    """
    base = _numeric_dialogs(_page("chat.logged.ru"))
    return [base] + [_moved(base, f"{index}9") for index in range(1, steps)]


def test_rejected_message_comes_again(no_sleep: list[float]) -> None:
    """Проверяет главное правило проекта для событий о сообщениях.

    База сдвигается только после того, как обработчик принял событие. Для
    заказов это закреплено давно; для сообщений проверки не было - и правило
    оказалось нарушено ровно там, где его не проверяли.

    Цена нарушения предельная: курсор переписки сдвигался до раздачи и не
    откатывался, поэтому упавший обработчик терял сообщение НАВСЕГДА. При этом
    в журнал писалась строка «они придут снова», а шаг оставался зелёным.

    Returns:
        None
    """
    thread_a = _page("chat-thread.logged.ru")
    thread_b = thread_a.replace('id="T18:adp#10"', 'id="message-fresh"', 1)

    seen: list[str] = []
    failed = [False]
    router = Router()

    @router.on(EventType.MESSAGE_CREATED)
    def handle(event: Event) -> None:
        if not failed[0]:
            failed[0] = True
            # Обычное исключение, а не из иерархии Funora: та означает условие
            # площадки и валит шаг целиком, а здесь проверяется отказ самого
            # обработчика.
            raise ValueError("не смог")
        seen.append(event.payload["message_id"])

    transport = _ByPath(
        _page("orders-trade.logged.ru"),
        _dialogs_changing(8),
        [thread_a] + [thread_b] * 8,
    )
    with Client(transport=transport) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=8)

    assert seen, "непринятое сообщение обязано прийти снова - иначе оно потеряно навсегда"
    assert seen[0] == "message-fresh"


def test_thread_cursor_survives_a_restart(no_sleep: list[float], tmp_path: Path) -> None:
    """Проверяет, что курсоры переписок переживают перезапуск.

    Не переживи они - каждое чтение после перезапуска становится «первым» и
    молчит по правилу. Сообщения, пришедшие за простой, не порождают события
    никогда, и заметить это нечем: шаг зелёный, журнал молчит.

    Returns:
        None
    """
    thread_a = _page("chat-thread.logged.ru")
    thread_b = thread_a.replace('id="T18:adp#10"', 'id="message-fresh"', 1)
    state = tmp_path / "state.json"

    first = _ByPath(_page("orders-trade.logged.ru"), _dialogs_changing(3), [thread_a] * 3)
    with Client(transport=first) as client:  # type: ignore[arg-type]
        client.watch(Router(), max_iterations=3, state_path=state)

    saved = json.loads(state.read_text(encoding="utf-8"))["payload"]["cursor"]
    assert saved.get("threads"), "курсоры переписок не сохранены"

    # Перезапуск. Новое сообщение обязано дойти: курсор восстановлен, значит
    # чтение уже не «первое» и молчать ему не с чего.
    seen: list[str] = []
    router = Router()
    router.on(EventType.MESSAGE_CREATED)(lambda e: seen.append(e.payload["message_id"]))

    second = _ByPath(_page("orders-trade.logged.ru"), _dialogs_changing(3), [thread_b] * 3)
    with Client(transport=second) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=2, state_path=state)

    assert seen == ["message-fresh"], "после перезапуска сообщение не дошло"


def test_partial_first_read_does_not_silence_the_dialog(no_sleep: list[float]) -> None:
    """Проверяет, что неполное первое чтение переписки не хоронит диалог.

    Курсор переписки снимается с ЛЮБОГО чтения, в отличие от курсоров списков, и
    разница не в небрежности.

    Курсор списка, снятый с неполного чтения, теряет выпавшие строки, и при
    следующем чтении они выглядят новыми заказами: бот выдаёт товар по заказу,
    который был и раньше. Это утверждение о мире, и оно оказывается ложным.

    У переписки иначе. Сообщение, выпавшее из неполного чтения и попавшее в
    следующее, действительно новое - для нас: события о нём никто не получал.
    Повторить такое дешевле, чем промолчать.

    Цена прежнего правила: пустой курсор молчит по правилу первого чтения, а
    неполное первое чтение оставляло его пустым навсегда. Диалог замолкал
    совсем, тратя запрос на каждом шаге. Для торгового бота первое чтение
    переписки - это первое сообщение нового покупателя.

    Returns:
        None
    """

    def flawed(html: str) -> str:
        """Делает чтение переписки неполным, не теряя ни одного сообщения.

        Лишний прямой потомок контейнера - переименованный класс, полоса
        непрочитанного, кнопка догрузки - даёт расхождение счётчиков и полноту
        partial. Разбор при этом читает все одиннадцать сообщений: важно, что
        неполнота берётся не из потери данных, а из недоверия к разметке.

        Args:
            html (str): Разметка переписки.

        Returns:
            str: Она же, читаемая неполно.
        """
        marker = '<div class="chat-message-list">'
        cut = html.index(marker) + len(marker)
        return html[:cut] + '<div class="chat-stripe"></div>' + html[cut:]

    whole = _page("chat-thread.logged.ru")
    fresh = whole.replace('id="T18:adp#10"', 'id="message-fresh"', 1)
    assert fresh != whole, "порча не применилась, проверка бессмысленна"

    seen: list[str] = []
    router = Router()
    router.on(EventType.MESSAGE_CREATED)(lambda e: seen.append(e.payload["message_id"]))

    # Неполны ВСЕ чтения: именно так выглядит изменение шаблона на стороне
    # площадки. При прежнем правиле курсор не снимался никогда, а пустой курсор
    # молчит - диалог замолкал совсем, тратя запрос на каждом шаге.
    transport = _ByPath(
        _page("orders-trade.logged.ru"),
        _dialogs_changing(5),
        [flawed(whole), flawed(fresh), flawed(fresh), flawed(fresh)],
    )
    with Client(transport=transport) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=5)

    assert "message-fresh" in seen, "при неполных чтениях диалог замолчал навсегда"


def test_primed_comes_once_even_if_orders_are_partial(no_sleep: list[float]) -> None:
    """Проверяет, что неполное чтение заказов не глушит поток навсегда.

    Курсор заказов снимается только с полного чтения, а признак холодного старта
    считался общим на оба списка. Пока страница заказов читалась неполно, цикл
    считал себя холодным вечно: события о диалогах выбрасывались, а вместо них
    каждый шаг уходило одно и то же watch.primed.

    Одной пропавшей ячейки в одной строке заказов хватало, чтобы наблюдение за
    перепиской замолчало целиком - молча.

    Returns:
        None
    """
    orders = _page("orders-trade.logged.ru")
    # Ячейка времени пропала в одной строке: полнота partial, строки читаются.
    broken = orders.replace('<div class="tc-date-left">', '<div class="tc-gone">', 1)
    assert broken != orders

    seen: list[EventType] = []
    router = Router()
    router.on()(lambda e: seen.append(e.type))

    transport = _ByPath(broken, _dialogs_changing(4), [_page("chat-thread.logged.ru")] * 4)
    with Client(transport=transport) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=4)

    assert seen.count(EventType.WATCH_PRIMED) == 1, (
        "watch.primed обязано прийти один раз, а не на каждом шаге"
    )
    assert EventType.CHAT_UNREAD_CHANGED in seen, (
        "события о диалогах выброшены из-за состояния чужой страницы"
    )


class _FailingThreads(_ByPath):
    """Транспорт, отказывающий на чтении переписки заданной ошибкой.

    Args:
        orders (str): Страница заказов.
        chats (list[str]): Списки диалогов по обращениям.
        error (Exception): Чем отказывать на адресе переписки.
    """

    def __init__(self, orders: str, chats: list[str], error: Exception) -> None:
        super().__init__(orders, chats, [""])
        self._error = error

    def fetch(self, path: str) -> Observation:
        """Отдаёт список либо отказывает на переписке.

        Args:
            path (str): Запрошенный путь.

        Returns:
            Observation: Наблюдение для списков.

        Raises:
            Exception: Заданная ошибка на адресе переписки.
        """
        if "node=" in path:
            self.paths.append(path)
            raise self._error
        return super().fetch(path)


def test_blocked_access_stops_the_step_instead_of_hammering(
    no_sleep: list[float], tmp_path: Path
) -> None:
    """Проверяет, что условие аккаунта закрывает шаг, а не перебирает очередь.

    Отказ вида «доступ заблокирован» относится к аккаунту, а не к переписке.
    Прежде он попадал под общий перехват: все диалоги очереди по очереди
    выбывали молча, шаг завершался успехом, очередь сохранялась пустой, а клиент
    стучался столько раз, сколько узлов в очереди, - при заблокированном
    доступе.

    Спецификация про такие ошибки говорит прямо: остановка, короткий
    автоматический повтор запрещён.

    Returns:
        None
    """
    state = tmp_path / "state.json"
    transport = _FailingThreads(
        _page("orders-trade.logged.ru"),
        _dialogs_changing(3),
        AccessBlockedError("доступ закрыт"),
    )

    with Client(transport=transport) as client, pytest.raises(AccessBlockedError):  # type: ignore[arg-type]
        client.watch(Router(), max_iterations=3, state_path=state, max_threads_per_step=5)

    assert len(transport.threads_read()) == 1, (
        "после первого отказа аккаунта клиент продолжил перебирать очередь"
    )


def test_rate_limited_thread_returns_to_the_queue(no_sleep: list[float], tmp_path: Path) -> None:
    """Проверяет, что временный отказ откладывает переписку, а не выбрасывает её.

    Выбросить диалог по временному отказу значило бы поставить доставку
    сообщения в зависимость от того, напишет ли покупатель ещё раз. Написавший
    один раз и ждущий ответа не напишет.

    Returns:
        None
    """
    state = tmp_path / "state.json"
    transport = _FailingThreads(
        _page("orders-trade.logged.ru"),
        _dialogs_changing(3),
        RateLimitedError("слишком часто"),
    )

    with Client(transport=transport) as client:  # type: ignore[arg-type]
        client.watch(Router(), max_iterations=3, state_path=state, max_threads_per_step=5)

    waiting = json.loads(state.read_text(encoding="utf-8"))["payload"]["cursor"]["pending_threads"]
    assert waiting, "диалог выброшен по временному отказу"

    # Перебора очереди нет: попытки уходят к одному и тому же диалогу, а не по
    # разу к каждому из пяти. Само повторение внутри одной попытки - работа
    # политики повторов, и она здесь ни при чём.
    assert len(set(transport.threads_read())) == 1, (
        "после временного отказа клиент пошёл перебирать остальную очередь"
    )


def test_redirects_are_paid_for_even_when_the_bucket_is_empty() -> None:
    """Проверяет, что переходы оплачиваются и при пустом ведре.

    Число переходов заранее неизвестно, поэтому бюджет за них списывается вслед
    за ответом. Прежде списание шло голым reserve, чей отказ никто не смотрел, -
    и ровно при пустом ведре цепочка переходов становилась бесплатной: клиент
    считал, что потратил один запрос, а отправлял до шести.

    Returns:
        None
    """
    budget = Budget()
    engine = Engine(TransportSettings(), budget)

    # Ведро осушается: дальше каждый запрос обязан ждать.
    while budget.reserve(monotonic()).granted:
        pass

    core = engine.settle(3)
    waits = 0
    reply = None
    while True:
        try:
            request = core.send(reply)
        except StopIteration:
            break
        reply = None
        if isinstance(request, Pause):
            waits += 1
            # Ведро пополняется само по часам; здесь просто отмечаем просьбу.
            assert request.ms > 0, "пауза обязана быть ненулевой при пустом ведре"

    assert waits == 3, "за каждый уже отправленный запрос обязана быть доплата"


def test_rejected_message_comes_again_even_if_the_list_goes_quiet(
    no_sleep: list[float],
) -> None:
    """Проверяет вторую половину правила: возврат диалога в очередь.

    Откатить курсор переписки мало. Событие об изменении диалога к моменту
    отказа уже доставлено и погашено, повторно оно не придёт - и если список
    диалогов после этого замер, диалог не перечитается уже никогда, сколько бы
    курсор ни откатывали.

    Список замирает не в теории: покупатель написал один раз и ждёт ответа.

    Returns:
        None
    """
    base = _numeric_dialogs(_page("chat.logged.ru"))
    # Первое изменение вызывает первое чтение - оно молчит по правилу. Второе
    # даёт событие, на котором обработчик падает. Дальше список замирает: именно
    # так и выглядит покупатель, написавший один раз и ждущий ответа.
    chats = [base, _moved(base, "77"), _moved(base, "88")] + [_moved(base, "88")] * 6

    thread_a = _page("chat-thread.logged.ru")
    thread_b = thread_a.replace('id="T18:adp#10"', 'id="message-fresh"', 1)

    seen: list[str] = []
    failed = [False]
    router = Router()

    @router.on(EventType.MESSAGE_CREATED)
    def handle(event: Event) -> None:
        if not failed[0]:
            failed[0] = True
            raise ValueError("не смог")
        seen.append(event.payload["message_id"])

    transport = _ByPath(_page("orders-trade.logged.ru"), chats, [thread_a] + [thread_b] * 8)
    with Client(transport=transport) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=8)

    assert seen == ["message-fresh"], (
        "диалог не перечитан: список замер, а из очереди узел не вернулся"
    )


def test_extra_requests_of_one_fetch_are_charged() -> None:
    """Проверяет, что чтение оплачивает все ушедшие запросы, а не один.

    Число переходов заранее неизвестно, поэтому за них платят вслед за ответом.
    Проверка стоит на уровне ядра, а не помощника: пропасть могло именно
    обращение к доплате, а не она сама.

    Returns:
        None
    """

    class _Redirecting:
        """Транспорт, сообщающий о четырёх ушедших запросах."""

        def fetch(self, path: str) -> Observation:
            """Отдаёт наблюдение с четырьмя отправленными запросами.

            Args:
                path (str): Путь. Не используется.

            Returns:
                Observation: Наблюдение.
            """
            body = _page("orders-trade.logged.ru")
            return replace(_observation(body), requests_sent=4)

        def close(self) -> None:
            """Закрывает транспорт.

            Returns:
                None
            """

    # Мерка снимается с нетронутого бюджета: подсчёт разрешений опустошает
    # ведро, и мерить им же тот бюджет, что проверяем, нельзя.
    before = _tokens(Budget())

    budget = Budget()
    with Client(transport=_Redirecting(), budget=budget) as client:  # type: ignore[arg-type]
        client.orders.list()

    assert before - _tokens(budget) == 4, "оплачен не весь ушедший трафик"


def _tokens(budget: Budget) -> int:
    """Считает, сколько разрешений бюджет ещё выдаст подряд.

    Args:
        budget (Budget): Бюджет.

    Returns:
        int: Число разрешений.
    """
    count = 0
    while budget.reserve(monotonic()).granted:
        count += 1
    return count


def test_subscribing_to_an_unproduced_event_is_refused() -> None:
    """Проверяет отказ в подписке на вид, которого реализация не порождает.

    Перечисление объявляет шестнадцать видов, реализация порождает пять. Прочие
    одиннадцать принимались без возражений и не срабатывали ни разу, а молчание
    обработчика неотличимо от «ничего не произошло»: продавец, подписавшийся на
    отзывы, увидел бы ровно то же, что при отсутствии новых отзывов.

    Однажды это уже случилось с message.created. Починили тогда одно событие,
    правила не написали.

    Returns:
        None
    """
    router = Router()
    never = sorted(set(EventType) - PRODUCIBLE, key=str)
    assert never, "если порождается всё, проверка бессмысленна - её надо убрать"

    for kind in never:
        with pytest.raises(ConfigurationError) as exc:
            router.on(kind)
        assert str(kind) in str(exc.value), "отказ не называет вид, о котором речь"

    assert not router.by_type, "отвергнутая подписка всё же попала в реестр"


def test_refusal_names_what_is_available() -> None:
    """Проверяет, что отказ говорит, на что подписаться можно.

    Отказ без перечня оставляет вызывающего гадать, и первое, что он сделает, -
    полезет читать исходники. Перечень в сообщении отвечает на вопрос сразу.

    Returns:
        None
    """
    with pytest.raises(ConfigurationError) as exc:
        Router().on(EventType.REVIEW_CHANGED)

    message = str(exc.value)
    for kind in PRODUCIBLE:
        assert str(kind) in message


def test_subscribing_to_a_produced_event_works() -> None:
    """Проверяет, что отказ не задел то, что порождается.

    Returns:
        None
    """
    router = Router()
    for kind in PRODUCIBLE:
        router.on(kind)(lambda event: None)
    assert len(router.by_type) == len(PRODUCIBLE)


def test_catch_all_is_not_refused() -> None:
    """Проверяет, что подписка на весь поток остаётся без ограничений.

    Такой обработчик просит поток целиком, а не конкретный вид, и молчания в нём
    нет: он видит ровно то, что пришло. Журналированию и метрикам нужен именно
    он.

    Returns:
        None
    """
    router = Router()
    router.on()(lambda event: None)
    assert len(router.catch_all) == 1


def test_every_declared_kind_is_actually_produced() -> None:
    """Проверяет, что перечень порождаемого заработан, а не объявлен.

    Без этой проверки правило разворачивается наизнанку: вид вписывают в
    перечень, подписка проходит, обработчик молчит - то же самое молчание, но
    теперь с разрешения. Поэтому каждый объявленный вид обязан вправду
    возникнуть на снимках.

    Returns:
        None
    """
    from funora._chats import parse_chats_page
    from funora._diff import diff_chats, diff_orders, diff_thread
    from funora._orders import parse_orders_page
    from funora._thread import parse_thread

    when = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    account = "12345678"

    def read(name: str) -> str:
        """Читает снимок страницы.

        Args:
            name (str): Имя снимка без расширения.

        Returns:
            str: Разметка снимка.
        """
        return (FIXTURES / f"{name}.skeleton.txt").read_text(encoding="utf-8")

    orders = parse_orders_page(read("orders-trade.logged.ru"), observed_at=when)
    chats = parse_chats_page(read("chat.logged.ru"), observed_at=when)
    thread = parse_thread(read("chat-thread.logged.ru"), observed_at=when)

    produced = {
        *(
            event.type
            for event in diff_thread(frozenset(), thread, account_id=account, chat_id="42")
        ),
        *(
            event.type
            for event in diff_chats(
                {entry.node_id: "прежняя" for entry in chats.rows(accept_incomplete=True)},
                chats,
                account_id=account,
            )
        ),
        *(event.type for event in diff_orders({}, orders, account_id=account)),
        *(
            event.type
            for event in diff_orders(
                dict.fromkeys(
                    (entry.order_id for entry in orders.rows(accept_incomplete=True)),
                    "выдуманное",
                ),
                orders,
                account_id=account,
            )
        ),
        primed(account, when, ("orders", "chats")).type,
        incomplete(
            account,
            when,
            entity="orders",
            reason="page_defects",
            rows_total=8,
            rows_accepted=8,
        ).type,
        loss(account, when, lost=1, reason="queue_overflow").type,
        health_changed(
            account,
            when,
            before="authenticated",
            after="rate_limited",
            reason="http_429",
            writes_paused=True,
        ).type,
    }

    assert produced >= PRODUCIBLE, (
        f"объявлено порождаемым, но не порождается: {PRODUCIBLE - produced} - "
        "подписка на такой вид пройдёт, а обработчик промолчит"
    )
    assert produced <= PRODUCIBLE, (
        f"порождается, но не объявлено: {produced - PRODUCIBLE} - "
        "подписка на такой вид будет отвергнута зря"
    )


def _orders_missing_a_field() -> str:
    """Портит снимок заказов так, чтобы разбор объявил неполноту.

    Ячейка состояния убирается во всех строках: убрать её у части недостаточно,
    повреждение повышается до уровня страницы только когда поле пропало везде.

    Returns:
        str: Разметка с неполнотой уровня страницы.
    """
    html = _page("orders-trade.logged.ru")
    for carrier in ("text-primary", "text-success"):
        html = html.replace('<div class="tc-status ' + carrier + '">', '<div class="tc-gone">')
    return html


def test_incomplete_read_tells_the_handler(no_sleep: list[float]) -> None:
    """Проверяет, что неполное чтение доходит до обработчика.

    Цикл умел обращаться с неполным чтением и молчал о нём. Курсор он не двигал
    - это верно и защищает будущее: выпавшие строки не будут сочтены
    исчезнувшими. Настоящее несдвинутый курсор не защищает никак: события по
    прочитанному порождаются, и обработчик принимает их за полную картину.

    Для торгового бота это значит обработать часть заказов как все.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    seen: list[Event] = []
    router = Router()
    router.on()(seen.append)

    transport = _Cycle([_orders_missing_a_field(), _page("chat.logged.ru")])
    with Client(transport=transport) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=1)

    notices = [event for event in seen if event.type is EventType.SNAPSHOT_INCOMPLETE]
    assert notices, "неполное чтение прошло молча"

    payload = notices[0].payload
    assert payload["entity"] == "orders"
    assert payload["rows_accepted"] <= payload["rows_total"]
    assert payload["reason_code"], "причина неполноты не названа"


def test_complete_read_says_nothing_about_incompleteness(no_sleep: list[float]) -> None:
    """Проверяет, что полное чтение события о неполноте не даёт.

    Без этой проверки правило вырождается в «сообщать всегда», и получатель
    привыкает не читать.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    seen: list[Event] = []
    router = Router()
    router.on()(seen.append)

    transport = _Cycle([_page("orders-trade.logged.ru"), _page("chat.logged.ru")])
    with Client(transport=transport) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=1)

    assert not [e for e in seen if e.type is EventType.SNAPSHOT_INCOMPLETE]


def test_the_same_incompleteness_does_not_repeat_every_step(no_sleep: list[float]) -> None:
    """Проверяет, что неизменная неполнота не приходит на каждом шаге.

    Неполнота держится, пока площадка не поправит вёрстку, - то есть шагами и
    часами. Событие на каждом шаге превратило бы её в шум, а шум перестают
    читать. Гашение повторов делает это само, потому что версия сложена из того,
    что при неизменной неполноте не меняется. Состав объявлен нормативно в
    spec/events/delivery.yaml -> revision_parts и сверяется поведенчески в
    tests/test_revision_source.py: повторять его здесь значило бы завести второе
    объявление, а прежняя редакция этой строки как раз и разошлась с кодом.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    seen: list[Event] = []
    router = Router()
    router.on()(seen.append)

    broken = _orders_missing_a_field()
    transport = _Cycle([broken, _page("chat.logged.ru")])
    with Client(transport=transport) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=3)

    notices = [e for e in seen if e.type is EventType.SNAPSHOT_INCOMPLETE]
    assert len(notices) == 1, (
        f"о той же неполноте сообщено {len(notices)} раз за три шага - это шум"
    )


def test_incompleteness_that_grew_is_reported_again() -> None:
    """Проверяет, что изменившаяся неполнота доходит заново.

    Переход от «двух строк не хватает» к «не хватает двадцати» - новость, и
    гасить её вместе с прежней нельзя. Проверяется на самих отпечатках: провести
    это через цикл значило бы собирать две разные порчи одной страницы, а
    проверяется здесь правило отпечатка.

    Returns:
        None
    """
    when = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    first = incomplete(
        "acc",
        when,
        entity="orders",
        reason="page_defects",
        rows_total=8,
        rows_accepted=6,
    )
    grew = incomplete(
        "acc",
        when,
        entity="orders",
        reason="page_defects",
        rows_total=8,
        rows_accepted=2,
    )
    same = incomplete(
        "acc",
        when,
        entity="orders",
        reason="page_defects",
        rows_total=8,
        rows_accepted=6,
    )
    other = incomplete(
        "acc",
        when,
        entity="chats",
        reason="page_defects",
        rows_total=8,
        rows_accepted=6,
    )

    assert first.id == same.id, "та же неполнота обязана гаситься как повтор"
    assert first.id != grew.id, "выросшая неполнота обязана дойти заново"
    assert first.id != other.id, "неполнота другого списка - другое событие"


def test_redelivery_is_marked_as_a_repeat(no_sleep: list[float]) -> None:
    """Проверяет, что повторная доставка отличима от первой.

    Доставка объявлена как минимум однократной: событие, на котором обработчик
    упал, приходит снова тем же отпечатком. Без номера попытки обработчик не
    отличает повтор от нового события - а это ровно тот случай, ради которого
    гарантию и формулируют: выдать товар дважды дешевле не становится оттого,
    что второй раз был повтором.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    attempts: list[int] = []
    router = Router()

    @router.on(EventType.ORDER_CREATED)
    def refuse(event: Event) -> None:
        """Отказывается принимать событие, запомнив номер попытки.

        Args:
            event (Event): Событие.

        Returns:
            None

        Raises:
            ValueError: Всегда - отказ обработчика.
        """
        attempts.append(event.delivery.attempt)
        raise ValueError("обработчик не принял")

    orders, chats = _page("orders-trade.logged.ru"), _page("chat.logged.ru")
    # На втором шаге в списке появляется заказ, на третьем список тот же.
    # Курсор не двигается - обработчик не принял, - и событие приходит снова.
    grown = _renamed_first_order(orders)
    transport = _Cycle([orders, chats, grown, chats, grown, chats])
    with Client(transport=transport) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=3)

    assert attempts, "события о заказах не дошли ни разу"
    assert max(attempts) > 1, (
        "все доставки помечены первой попыткой - обработчик не отличит повтор "
        f"от нового события; номера: {sorted(set(attempts))}"
    )
    assert min(attempts) == 1, "первая доставка обязана быть первой попыткой"


def test_counter_holds_only_what_is_still_undelivered(
    no_sleep: list[float], tmp_path: Path
) -> None:
    """Проверяет, что счётчик попыток не тащит доставленное.

    Счётчик переживает перезапуск вместе с гашением повторов, то есть пишется на
    диск. Запись о доставленном событии там не нужна ни для чего: гашение
    повторов его больше не пропустит. Счётчик, из которого ничего не выбывает,
    растёт файлом состояния - и это не гипотетически, а по одной записи на
    каждое событие, дошедшее за всё время работы.

    Проверяется по файлу, а не по номерам попыток у обработчика: номера
    пересобираются по партии, и лишняя запись в них не видна. Видна она ровно
    там, где вредит.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.
        tmp_path (Path): Временный каталог для файла состояния.

    Returns:
        None
    """
    state_path = tmp_path / "state.json"
    orders, chats = _page("orders-trade.logged.ru"), _page("chat.logged.ru")
    grown = _renamed_first_order(orders)

    seen: list[Event] = []
    router = Router()
    router.on()(seen.append)

    with Client(transport=_Cycle([orders, chats, grown, chats])) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=2, state_path=state_path)

    assert seen, "события не дошли - проверять нечего"
    assert {event.delivery.attempt for event in seen} == {1}, (
        "принятое с первого раза событие помечено повтором"
    )

    saved = json.loads(state_path.read_text(encoding="utf-8"))["payload"]
    assert saved.get("attempts") == {}, (
        f"в сохранённом счётчике осталось {saved.get('attempts')} - "
        "это записи о доставленном, и они будут копиться вечно"
    )


def test_attempt_survives_a_restart(no_sleep: list[float], tmp_path: Path) -> None:
    """Проверяет, что номер попытки переживает перезапуск.

    Перезапуск, обнуляющий счётчик, выдаёт событие, падавшее пятый раз, за
    новое - то есть врёт ровно тогда, когда обработчику важнее всего знать, что
    оно не новое. А перезапуск после серии отказов - обычное дело: так и
    лечат зависший обработчик.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.
        tmp_path (Path): Временный каталог для файла состояния.

    Returns:
        None
    """
    state = tmp_path / "state.json"
    orders, chats = _page("orders-trade.logged.ru"), _page("chat.logged.ru")

    def run(iterations: int, pages: list[str]) -> list[int]:
        """Прогоняет цикл с отказывающим обработчиком.

        Args:
            iterations (int): Сколько шагов сделать.
            pages (list[str]): Страницы, которые отдаёт подставной транспорт.

        Returns:
            list[int]: Номера попыток, которые увидел обработчик.
        """
        seen: list[int] = []
        router = Router()

        @router.on(EventType.ORDER_CREATED)
        def refuse(event: Event) -> None:
            """Отказывается принимать событие.

            Args:
                event (Event): Событие.

            Returns:
                None

            Raises:
                ValueError: Всегда.
            """
            seen.append(event.delivery.attempt)
            raise ValueError("обработчик не принял")

        with Client(transport=_Cycle(pages)) as client:  # type: ignore[arg-type]
            client.watch(router, max_iterations=iterations, state_path=state)
        return seen

    grown = _renamed_first_order(orders)
    # Первый прогон: заказ появляется на втором шаге и не принимается.
    first = run(2, [orders, chats, grown, chats])
    # Второй прогон начинается с восстановленного курсора, и заказ приходит
    # снова - обработчик его так и не принял.
    second = run(1, [grown, chats])

    assert first and second
    assert second[0] > max(first), (
        f"после перезапуска номер попытки {second[0]} не продолжил ряд {first} - счётчик обнулился"
    )


def test_failure_on_the_first_batch_does_not_silence_the_watch(
    no_sleep: list[float],
) -> None:
    """Проверяет, что отказ на приветствии не убивает наблюдение навсегда.

    Самая дорогая из найденных потерь. Признак «поздоровались» поднимался ДО
    раздачи, и обработчик, упавший на первой партии, забирал с собой всё: курсор
    не двигался, потому что партия не принята; приветствие второй раз не
    собиралось, потому что признак уже поднят; а несдвинутый курсор держит
    холодный старт, при котором diff_* молчат по правилу первого чтения.

    Цикл продолжал работать, ходить на площадку и тратить бюджет - и не
    порождал больше ни одного события. Ни исключения, ни строки в журнале.

    Отказ на первой партии - не выдуманный случай: первое, что делает
    обработчик, это обращается к своей базе, а первое обращение и падает, если
    база ещё не поднялась.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    orders, chats = _page("orders-trade.logged.ru"), _page("chat.logged.ru")
    grown = _renamed_first_order(orders)

    seen: list[EventType] = []
    router = Router()

    @router.on()
    def handle(event: Event) -> None:
        """Падает на первом приветствии и принимает всё остальное.

        Args:
            event (Event): Событие.

        Returns:
            None

        Raises:
            ValueError: На первом приветствии.
        """
        seen.append(event.type)
        if event.type is EventType.WATCH_PRIMED and seen.count(EventType.WATCH_PRIMED) == 1:
            raise ValueError("база ещё не поднялась")

    transport = _Cycle([orders, chats, orders, chats, grown, chats])
    with Client(transport=transport) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=3)

    assert seen.count(EventType.WATCH_PRIMED) == 2, (
        "приветствие не пришло второй раз - признак «поздоровались» поднят "
        "до раздачи, и наблюдение замолчало навсегда"
    )
    assert EventType.ORDER_CREATED in seen, (
        "после принятого приветствия наблюдение так и не заработало: курсор "
        "остался на холодном старте"
    )


def test_greeting_is_not_repeated_after_it_was_taken(no_sleep: list[float]) -> None:
    """Проверяет, что принятое приветствие не приходит снова.

    Обратная сторона починки. Признак, поднимаемый по факту доставки, легко
    сделать не поднимающимся вовсе - и тогда приветствие уходит каждый шаг, а
    события данных не уходят никогда: тот самый случай, из-за которого признак
    когда-то и появился.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    orders, chats = _page("orders-trade.logged.ru"), _page("chat.logged.ru")

    seen: list[EventType] = []
    router = Router()
    router.on()(lambda event: seen.append(event.type))

    with Client(transport=_Cycle([orders, chats])) as client:  # type: ignore[arg-type]
        client.watch(router, max_iterations=3)

    assert seen.count(EventType.WATCH_PRIMED) == 1, (
        f"приветствие пришло {seen.count(EventType.WATCH_PRIMED)} раз за три шага"
    )


def test_incomplete_thread_tells_the_handler(no_sleep: list[float]) -> None:
    """Проверяет, что неполно прочитанная переписка не проходит молча.

    Объявлялись только списки, и это была половина правила. Для торгового бота
    переписка - главное место: неполно прочитанная означает, что часть сообщений
    покупателя не увидели вовсе, а событий по прочитанной части при этом пришло
    сколько-то, и выглядят они как вся переписка.

    Событие несёт ссылку на диалог: неполон не весь снимок, а одна переписка, и
    без ссылки получатель не узнает, какая из полусотни прочитана наполовину.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    dialogs = _numeric_dialogs(_page("chat.logged.ru"))
    thread = _page("chat-thread.logged.ru")
    # Отметка времени убирается у всех сообщений: повреждение поднимается до
    # уровня страницы только когда поле пропало везде.
    broken = thread.replace('class="chat-msg-date"', 'class="chat-msg-gone"')
    assert broken != thread, "порча не применилась - проверка бессмысленна"

    _, events = _follow_run(
        [dialogs, _moved(dialogs, "777")],
        2,
        [broken, broken],
    )

    notices = [
        event
        for event in events
        if event.type is EventType.SNAPSHOT_INCOMPLETE and event.payload.get("entity") == "thread"
    ]
    assert notices, "неполно прочитанная переписка прошла молча"
    assert notices[0].payload.get("entity_ref"), (
        "событие не называет диалог - получатель не узнает, какая переписка прочитана наполовину"
    )


def test_complete_thread_says_nothing(no_sleep: list[float]) -> None:
    """Проверяет, что целиком прочитанная переписка события о неполноте не даёт.

    Без этой проверки правило вырождается в «сообщать всегда», и получатель
    привыкает не читать.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    dialogs = _numeric_dialogs(_page("chat.logged.ru"))

    _, events = _follow_run([dialogs, _moved(dialogs, "777")], 2)

    assert not [
        event
        for event in events
        if event.type is EventType.SNAPSHOT_INCOMPLETE and event.payload.get("entity") == "thread"
    ]


def test_queue_overflow_is_announced_not_silent(
    no_sleep: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверяет, что переполнение очереди не проходит молча.

    Очередь дочитывания пополняется на каждом изменении диалога, а вычерпывается
    по несколько штук за шаг: у продавца с полусотней активных переписок она
    растёт быстрее, чем убывает. Предел объявлен спецификацией и до сих пор не
    применялся - очередь была неограниченной.

    Ограничить её и промолчать было бы худшим исходом: сообщение покупателя не
    прочитается никогда, и узнать об этом неоткуда. Поэтому выброшенное
    объявляется событием потери.

    Предел подменяется на маленький: набрать полтораста диалогов в наборе
    негде.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.
        monkeypatch (pytest.MonkeyPatch): Механизм подмены.

    Returns:
        None
    """
    import funora._engine as engine_module

    monkeypatch.setattr(engine_module, "MAX_QUEUE_DEPTH_PER_KEY", 3)

    dialogs = _numeric_dialogs(_page("chat.logged.ru"))
    # Второе чтение сдвигает позиции у всех диалогов сразу: в очередь попадает
    # столько узлов, сколько их на странице.
    moved = re.sub(r'data-node-msg="[^"]*"', 'data-node-msg="T10:d#сдвинуто"', dialogs)
    assert moved != dialogs, "порча не применилась - проверка бессмысленна"

    _, events = _follow_run([dialogs, moved], 2)

    losses = [event for event in events if event.type is EventType.EVENT_LOSS]
    assert losses, "очередь переполнилась молча"
    assert losses[0].payload["lost"] > 0
    assert losses[0].payload["reason_code"] == "queue_overflow"


def test_queue_within_the_limit_reports_no_loss(no_sleep: list[float]) -> None:
    """Проверяет, что непереполненная очередь событий потери не даёт.

    Без этой проверки правило вырождается в «сообщать всегда», и получатель
    привыкает не читать.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.

    Returns:
        None
    """
    dialogs = _numeric_dialogs(_page("chat.logged.ru"))

    _, events = _follow_run([dialogs, _moved(dialogs, "777")], 2)

    assert not [event for event in events if event.type is EventType.EVENT_LOSS]


def test_overflow_drops_the_tail_not_the_head(
    no_sleep: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверяет, что при переполнении выбывает хвост, а не голова.

    В голове очереди самые давние диалоги - они ждут дольше всех. Выбросить их
    значило бы гарантировать, что именно они не дочитаются никогда, а
    дочитываться будут только свежие.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.
        monkeypatch (pytest.MonkeyPatch): Механизм подмены.

    Returns:
        None
    """
    import funora._engine as engine_module

    monkeypatch.setattr(engine_module, "MAX_QUEUE_DEPTH_PER_KEY", 2)

    dialogs = _numeric_dialogs(_page("chat.logged.ru"))
    moved = re.sub(r'data-node-msg="[^"]*"', 'data-node-msg="T10:d#сдвинуто"', dialogs)

    transport, _ = _follow_run([dialogs, moved], 2)

    read = transport.threads_read()
    assert read, "ни одна переписка не прочитана"
    first_on_page = re.search(r'data-id="(\d+)"', dialogs).group(1)
    assert read[0] == f"/chat/?node={first_on_page}", (
        f"первой прочитана {read[0]}, а не голова очереди - выбывает не хвост"
    )


def test_overflow_drops_the_newest_and_keeps_the_oldest(
    no_sleep: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверяет, что за пределом очереди выбывает хвост, а не голова.

    В голове самые давние диалоги: они ждут дольше всех, и повода вернуться к
    ним больше нет - событие об изменении доставлено, курсор сдвинут. Выброси
    их, и именно они не дочитаются НИКОГДА, сколько бы шагов ни прошло.

    Хвост же выбывает временно: диалог, изменившийся снова, вернётся в очередь
    следующим шагом.

    Args:
        no_sleep (list[float]): Счётчик пауз вместо сна.
        monkeypatch (pytest.MonkeyPatch): Механизм подмены.

    Returns:
        None
    """
    import funora._engine as engine_module

    limit = 3
    monkeypatch.setattr(engine_module, "MAX_QUEUE_DEPTH_PER_KEY", limit)

    dialogs = _numeric_dialogs(_page("chat.logged.ru"))
    moved = re.sub(r'data-node-msg="[^"]*"', 'data-node-msg="T10:d#сдвинуто"', dialogs)
    assert moved != dialogs, "порча не применилась - проверка бессмысленна"

    transport, _ = _follow_run([dialogs, moved], 2)

    order = re.findall(r'data-id="([^"]*)"', moved)
    read = [
        match.group(1)
        for path in transport.paths
        if (match := re.search(r"node=([^&]+)", path)) is not None
    ]
    assert read, "ни одна переписка не дочитана - проверять нечего"

    positions = [order.index(node) for node in read if node in order]
    assert positions, "дочитанные узлы не с этой страницы"
    assert max(positions) < limit, (
        f"дочитаны узлы на позициях {sorted(positions)} при пределе очереди "
        f"{limit}. Значит уцелел хвост, а не голова, и самые давние диалоги "
        "не дочитаются никогда"
    )
