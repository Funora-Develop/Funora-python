"""Проверки порождения событий из снимков.

Событие - это то, по чему бот действует. Лишнее событие означает выданный
дважды товар, пропущенное - невыданный. Поэтому набор проверяет не только то,
что события возникают, но и то, что они не возникают там, где данных для них
нет.

Отдельная группа проверок - про идентичность события. Если идентификатор
меняется от запуска к запуску, дедупликация перестаёт работать ровно после
перезапуска, то есть там, где она нужнее всего.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from funora._chats import parse_chats_page
from funora._diff import diff_chats, diff_orders, diff_thread, disappeared
from funora._orders import Completeness, parse_orders_page
from funora._thread import parse_thread
from funora.events import ORDERING_KEY, EventType

#: Каталог со снимками страниц.
FIXTURES = Path(__file__).parent / "fixtures" / "pages"

#: Момент наблюдения.
WHEN = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)

#: Идентификатор аккаунта для отпечатков.
ACCOUNT = "12345678"


def _raw(name: str) -> str:
    """Читает снимок страницы.

    Args:
        name (str): Имя снимка без расширения.

    Returns:
        str: Разметка снимка.
    """
    return (FIXTURES / f"{name}.skeleton.txt").read_text(encoding="utf-8")


def _orders(html: str | None = None, when: datetime = WHEN):  # type: ignore[no-untyped-def]
    """Разбирает список заказов.

    Args:
        html (str | None): Разметка. По умолчанию неизменённый снимок.
        when (datetime): Момент наблюдения.

    Returns:
        OrdersPage: Разобранная страница.
    """
    return parse_orders_page(html or _raw("orders-trade.logged.ru"), observed_at=when)


def _chats(html: str | None = None, when: datetime = WHEN):  # type: ignore[no-untyped-def]
    """Разбирает список диалогов.

    Args:
        html (str | None): Разметка. По умолчанию неизменённый снимок.
        when (datetime): Момент наблюдения.

    Returns:
        ChatsPage: Разобранная страница.
    """
    return parse_chats_page(html or _raw("chat.logged.ru"), observed_at=when)


def _thread(html: str | None = None, when: datetime = WHEN):  # type: ignore[no-untyped-def]
    """Разбирает переписку.

    Args:
        html (str | None): Разметка. По умолчанию неизменённый снимок.
        when (datetime): Момент наблюдения.

    Returns:
        Thread: Разобранная переписка.
    """
    return parse_thread(html or _raw("chat-thread.logged.ru"), observed_at=when)


def test_first_snapshot_produces_nothing() -> None:
    """Проверяет, что первое чтение не порождает событий.

    Сравнивать не с чем. Объявить все существующие заказы новыми означало бы
    разослать уведомления обо всех сразу при первом же запуске бота.

    Returns:
        None
    """
    assert diff_orders(None, _orders(), account_id=ACCOUNT) == ()
    assert diff_chats(None, _chats(), account_id=ACCOUNT) == ()
    assert diff_thread(None, _thread(), account_id=ACCOUNT, chat_id="1") == ()


def test_identical_snapshots_produce_nothing() -> None:
    """Проверяет, что неизменное состояние не порождает событий.

    Returns:
        None
    """
    assert diff_orders(_orders(), _orders(), account_id=ACCOUNT) == ()
    assert diff_chats(_chats(), _chats(), account_id=ACCOUNT) == ()


def test_new_order_produces_one_event() -> None:
    """Проверяет появление события о новом заказе.

    Returns:
        None
    """
    before = _orders()
    grown = _raw("orders-trade.logged.ru").replace(
        'href="https://funpay.com/orders/{n}/"',
        'href="https://funpay.com/orders/999/"',
        1,
    )
    events = diff_orders(before, _orders(grown), account_id=ACCOUNT)

    assert len(events) == 1
    assert events[0].type is EventType.ORDER_CREATED
    assert events[0].entity_id == "999"


def test_ordering_key_follows_the_spec_template() -> None:
    """Проверяет, что ключ упорядочивания собран по шаблону спецификации.

    Две реализации, выведшие разные ключи, получат разную степень параллелизма
    и разный наблюдаемый порядок - при полном согласии в том, какие события
    бывают.

    Returns:
        None
    """
    grown = _raw("orders-trade.logged.ru").replace(
        'href="https://funpay.com/orders/{n}/"',
        'href="https://funpay.com/orders/999/"',
        1,
    )
    event = diff_orders(_orders(), _orders(grown), account_id=ACCOUNT)[0]

    template = ORDERING_KEY[EventType.ORDER_CREATED]
    assert event.ordering_key == template.format(order_id="999")


def test_event_id_does_not_depend_on_observation_time() -> None:
    """Проверяет главное свойство идентичности события.

    Момент наблюдения меняется от запуска к запуску. Войди он в отпечаток,
    событие, пережившее перезапуск, получило бы новый идентификатор, и
    дедупликация перестала бы работать ровно там, где она нужнее всего.

    Returns:
        None
    """
    moved = _raw("chat.logged.ru").replace('data-node-msg="T10:d"', 'data-node-msg="T10:x"', 1)

    early = diff_chats(_chats(), _chats(moved, WHEN), account_id=ACCOUNT)
    later = diff_chats(_chats(), _chats(moved, WHEN + timedelta(days=3)), account_id=ACCOUNT)

    assert early[0].id == later[0].id
    assert early[0].observed_at != later[0].observed_at


def test_event_id_depends_on_the_account() -> None:
    """Проверяет, что события разных аккаунтов различимы.

    Два аккаунта в одном процессе увидят похожие изменения. Совпадение
    идентификаторов означало бы, что событие одного гасит событие другого.

    Returns:
        None
    """
    moved = _raw("chat.logged.ru").replace('data-node-msg="T10:d"', 'data-node-msg="T10:x"', 1)

    first = diff_chats(_chats(), _chats(moved), account_id="11111111")
    second = diff_chats(_chats(), _chats(moved), account_id="22222222")

    assert first[0].id != second[0].id


def test_event_id_changes_with_the_revision() -> None:
    """Проверяет, что новое состояние даёт новое событие.

    Иначе второе изменение того же диалога было бы погашено дедупликацией как
    повтор первого.

    Returns:
        None
    """
    base = _raw("chat.logged.ru")
    first = diff_chats(
        _chats(),
        _chats(base.replace('data-node-msg="T10:d"', 'data-node-msg="T10:x"', 1)),
        account_id=ACCOUNT,
    )
    second = diff_chats(
        _chats(),
        _chats(base.replace('data-node-msg="T10:d"', 'data-node-msg="T10:y"', 1)),
        account_id=ACCOUNT,
    )

    assert first[0].id != second[0].id


def test_no_status_change_events_are_ever_produced() -> None:
    """Проверяет, что события об изменении статуса не порождаются.

    Соответствия классов разметки статусам не наблюдалось, статус выдаётся
    ненаблюдённым, и породить из него событие значило бы выдумать факт.

    Returns:
        None
    """
    changed = _raw("orders-trade.logged.ru").replace(
        "tc-status text-primary", "tc-status text-warning"
    )
    events = diff_orders(_orders(), _orders(changed), account_id=ACCOUNT)

    assert all(e.type is not EventType.ORDER_STATUS_CHANGED for e in events)


def test_new_message_produces_an_event_with_origin() -> None:
    """Проверяет событие о новом сообщении и наличие в нём происхождения.

    Обработчик обязан видеть происхождение, не разбирая текст. Но даже
    происхождение system не является подтверждением оплаты.

    Returns:
        None
    """
    before = _thread()
    grown = _raw("chat-thread.logged.ru").replace('id="T18:adp"', 'id="message-777"', 1)
    events = diff_thread(before, _thread(grown), account_id=ACCOUNT, chat_id="42")

    assert len(events) == 1
    assert events[0].type is EventType.MESSAGE_CREATED
    assert events[0].ordering_key == ORDERING_KEY[EventType.MESSAGE_CREATED].format(chat_id="42")
    assert "origin" in events[0].payload


def test_payload_carries_no_personal_data() -> None:
    """Проверяет, что в нагрузку не попадают текст и имена.

    Нагрузка уходит в журналы, очереди и обработчики пользователя. Содержимому
    переписки и именам там делать нечего.

    Returns:
        None
    """
    grown = _raw("chat-thread.logged.ru").replace('id="T18:adp"', 'id="message-777"', 1)
    events = diff_thread(_thread(), _thread(grown), account_id=ACCOUNT, chat_id="42")

    for event in events:
        for key in ("text", "author_name", "preview", "message"):
            assert key not in event.payload


def test_partial_snapshot_forbids_disappearance_conclusions() -> None:
    """Проверяет правило о неполном снимке.

    Запись, не попавшая в частично прочитанную страницу, не исчезла - её не
    прочитали. Разница между этими случаями есть разница между «заказ отменён» и
    «мы не смогли его увидеть», а обработчик по первому вернёт деньги.

    Returns:
        None
    """
    broken = _orders(
        _raw("orders-trade.logged.ru").replace(
            '<div class="tc-status text-primary">', '<div class="tc-gone">'
        )
    )
    assert broken.completeness is Completeness.PARTIAL

    assert not disappeared(broken, _orders())
    assert not disappeared(_orders(), broken)
    assert disappeared(_orders(), _orders())
    assert not disappeared(None, _orders())


@pytest.mark.parametrize("event_type", list(EventType))
def test_every_event_type_has_an_ordering_key(event_type: EventType) -> None:
    """Проверяет полноту таблицы ключей упорядочивания.

    Тип без ключа означает, что реализация выведет его сама, а ключ определяет
    и порядок обработки, и степень параллелизма.

    Args:
        event_type (EventType): Проверяемый тип.

    Returns:
        None
    """
    assert event_type in ORDERING_KEY
    assert "{" in ORDERING_KEY[event_type], "шаблон обязан содержать подстановку"
