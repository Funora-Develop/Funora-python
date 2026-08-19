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
from funora._diff import (
    UNREAD_STATUS,
    chats_cursor,
    diff_chats,
    diff_orders,
    diff_thread,
    orders_cursor,
    thread_cursor,
)
from funora._orders import Completeness, parse_orders_page
from funora._thread import parse_thread
from funora.events import ORDERING_KEY, EventType
from funora.extraction import OrderStatus

#: Каталог со снимками страниц.
FIXTURES = Path(__file__).parent / "fixtures" / "pages"

#: Момент наблюдения.
WHEN = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)

#: Идентификатор аккаунта для отпечатков.
ACCOUNT = "12345678"


def _raw(name: str = "orders-trade.logged.ru") -> str:
    """Читает снимок страницы.

    Args:
        name (str): Имя снимка без расширения. По умолчанию список заказов.

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
    assert diff_orders(orders_cursor(_orders()), _orders(), account_id=ACCOUNT) == ()
    assert diff_chats(chats_cursor(_chats()), _chats(), account_id=ACCOUNT) == ()


def _one_more_order() -> str:
    """Дописывает в снимок ещё один заказ с новым идентификатором.

    Формат v5 нумерует идентификаторы, поэтому заказы снимка уже различимы и
    разводить их вручную больше не нужно. Нужен другой - тот, которого в снимке
    не было: именно его появление и есть событие.

    Returns:
        str: Разметка с одним лишним заказом.
    """
    html = _raw()
    first = html.index('<a class="tc-item')
    end = html.index("</a>", first) + len("</a>")
    row = html[first:end]
    fresh = row.replace("/orders/{n9}/", "/orders/{n999}/")
    assert fresh != row, "идентификатор первой строки не найден, порча бессмысленна"
    return html[:first] + fresh + html[first:]


def test_new_order_produces_one_event() -> None:
    """Проверяет появление события о новом заказе.

    Returns:
        None
    """
    before = _orders()
    grown = _one_more_order()
    events = diff_orders(orders_cursor(before), _orders(grown), account_id=ACCOUNT)

    assert len(events) == 1
    assert events[0].type is EventType.ORDER_CREATED
    assert events[0].entity_id == "{n999}"


def test_ordering_key_follows_the_spec_template() -> None:
    """Проверяет, что ключ упорядочивания собран по шаблону спецификации.

    Две реализации, выведшие разные ключи, получат разную степень параллелизма
    и разный наблюдаемый порядок - при полном согласии в том, какие события
    бывают.

    Returns:
        None
    """
    event = diff_orders(orders_cursor(_orders()), _orders(_one_more_order()), account_id=ACCOUNT)[0]

    template = ORDERING_KEY[EventType.ORDER_CREATED]
    assert event.ordering_key == template.format(order_id="{n999}")


def test_event_id_does_not_depend_on_observation_time() -> None:
    """Проверяет главное свойство идентичности события.

    Момент наблюдения меняется от запуска к запуску. Войди он в отпечаток,
    событие, пережившее перезапуск, получило бы новый идентификатор, и
    дедупликация перестала бы работать ровно там, где она нужнее всего.

    Returns:
        None
    """
    moved = _raw("chat.logged.ru").replace(
        'data-node-msg="T10:d#1"', 'data-node-msg="T10:d#999"', 1
    )

    early = diff_chats(chats_cursor(_chats()), _chats(moved, WHEN), account_id=ACCOUNT)
    later = diff_chats(
        chats_cursor(_chats()), _chats(moved, WHEN + timedelta(days=3)), account_id=ACCOUNT
    )

    assert early[0].id == later[0].id
    assert early[0].observed_at != later[0].observed_at


def test_event_id_depends_on_the_account() -> None:
    """Проверяет, что события разных аккаунтов различимы.

    Два аккаунта в одном процессе увидят похожие изменения. Совпадение
    идентификаторов означало бы, что событие одного гасит событие другого.

    Returns:
        None
    """
    moved = _raw("chat.logged.ru").replace(
        'data-node-msg="T10:d#1"', 'data-node-msg="T10:d#999"', 1
    )

    first = diff_chats(chats_cursor(_chats()), _chats(moved), account_id="11111111")
    second = diff_chats(chats_cursor(_chats()), _chats(moved), account_id="22222222")

    assert first[0].id != second[0].id


def test_event_id_changes_with_the_revision() -> None:
    """Проверяет, что новое состояние даёт новое событие.

    Иначе второе изменение того же диалога было бы погашено дедупликацией как
    повтор первого.

    Returns:
        None
    """
    base = _raw("chat.logged.ru")
    cursor = chats_cursor(_chats())
    first = diff_chats(
        cursor,
        _chats(base.replace('data-node-msg="T10:d#1"', 'data-node-msg="T10:d#999"', 1)),
        account_id=ACCOUNT,
    )
    second = diff_chats(
        cursor,
        _chats(base.replace('data-node-msg="T10:d#1"', 'data-node-msg="T10:d#888"', 1)),
        account_id=ACCOUNT,
    )

    assert first[0].id != second[0].id


def test_status_change_produces_an_event() -> None:
    """Проверяет событие об изменении состояния заказа.

    Событие было объявлено в спецификации и не порождалось никогда: состояние
    было ненаблюдаемым, и породить его значило бы выдумать факт. Теперь
    состояние читается, и переход «оплачен - закрыт» наблюдаем.

    Returns:
        None
    """
    before = _raw()
    after = before.replace('"tc-item info"', '"tc-item"').replace(
        '"tc-status text-primary"', '"tc-status text-success"'
    )

    cursor = orders_cursor(_orders(before))
    events = diff_orders(cursor, _orders(after), account_id=ACCOUNT)

    assert {e.type for e in events} == {EventType.ORDER_STATUS_CHANGED}
    assert len(events) == 5, "оплаченных заказов в снимке пять"
    for event in events:
        assert event.payload["from"] == OrderStatus.PAID
        assert event.payload["to"] == OrderStatus.CLOSED
        assert event.ordering_key == f"order:{event.entity_id}"


def test_unchanged_status_produces_nothing() -> None:
    """Проверяет, что повторное чтение того же состояния молчит.

    Returns:
        None
    """
    html = _raw()
    cursor = orders_cursor(_orders(html))
    assert diff_orders(cursor, _orders(html), account_id=ACCOUNT) == ()


def test_learning_to_read_a_status_is_not_a_change() -> None:
    """Проверяет, что переход из непрочитанного в прочитанное не событие.

    Самая важная проверка этой пары. Такой переход говорит о том, что мы
    научились читать, а не о том, что заказ изменился, - и обработчик, получив
    его, выдал бы товар по заказу, с которым ничего не происходило.

    Возникает это не умозрительно: ровно так выглядит первое чтение после того,
    как в таблицу соответствия добавили новое состояние.

    Returns:
        None
    """
    html = _raw()
    unreadable = html.replace("text-primary", "text-blue").replace("text-success", "text-green")

    blind = orders_cursor(_orders(unreadable))
    assert set(blind.values()) == {UNREAD_STATUS}, "порча не сделала состояния нечитаемыми"

    # Прозрели: состояния читаются. Событий об изменении быть не должно.
    events = diff_orders(blind, _orders(html), account_id=ACCOUNT)
    assert events == ()

    # И в обратную сторону: ослепли - тоже не событие.
    seeing = orders_cursor(_orders(html))
    assert diff_orders(seeing, _orders(unreadable), account_id=ACCOUNT) == ()


def test_status_change_event_is_stable_across_reads() -> None:
    """Проверяет, что повторное чтение изменения даёт тот же отпечаток.

    Отпечаток строится от нового состояния, а не от момента наблюдения. Иначе
    каждое чтение давало бы новое событие, и гашение повторов перестало бы
    работать ровно там, где оно нужно, - при опросе раз в несколько секунд.

    Returns:
        None
    """
    before = _raw()
    after = before.replace('"tc-item info"', '"tc-item"').replace(
        '"tc-status text-primary"', '"tc-status text-success"'
    )
    cursor = orders_cursor(_orders(before))

    first = diff_orders(cursor, _orders(after), account_id=ACCOUNT)
    second = diff_orders(cursor, _orders(after), account_id=ACCOUNT)
    assert [e.id for e in first] == [e.id for e in second]


def _one_more_message() -> str:
    """Дописывает в переписку ещё одно сообщение с новым идентификатором.

    Идентификатор берётся из снимка, а не пишется в проверке: подпись зависит от
    формата, и записанная руками она разъезжается с ним молча - проверка тогда
    сравнивает несуществующее с несуществующим и проходит.

    Returns:
        str: Разметка с одним лишним сообщением.
    """
    import re as _re

    html = _raw("chat-thread.logged.ru")
    found = _re.search(r'<div class="chat-msg-item[^"]*" id="([^"]*)"', html)
    assert found is not None, "в снимке не нашлось ни одного сообщения"

    start = found.start()
    end = html.index("</div>", html.index('class="chat-msg-body"', start)) + len("</div>")
    return html[:start] + html[start:end].replace(found.group(1), "message-777", 1) + html[start:]


def test_new_message_produces_an_event_with_origin() -> None:
    """Проверяет событие о новом сообщении и наличие в нём происхождения.

    Обработчик обязан видеть происхождение, не разбирая текст. Но даже
    происхождение system не является подтверждением оплаты.

    Returns:
        None
    """
    before = _thread()
    grown = _one_more_message()
    events = diff_thread(thread_cursor(before), _thread(grown), account_id=ACCOUNT, chat_id="42")

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
    grown = _one_more_message()
    events = diff_thread(thread_cursor(_thread()), _thread(grown), account_id=ACCOUNT, chat_id="42")

    for event in events:
        for key in ("text", "author_name", "preview", "message"):
            assert key not in event.payload


def test_partial_snapshot_does_not_make_old_orders_new() -> None:
    """Проверяет главную причину перехода на курсор.

    Строка, отброшенная из-за поломки разметки, отсутствует в прочитанной
    странице. Сравнивай мы снимок со снимком, при следующем чтении тот же заказ
    выглядел бы новым - и бот выдал бы товар по заказу, который существовал и
    раньше.

    Курсор хранит известное и не теряет его от одной испорченной строки.

    Returns:
        None
    """
    raw = _raw("orders-trade.logged.ru")
    distinct = raw

    full = _orders(distinct)
    cursor = orders_cursor(full)
    assert len(set(cursor)) == full.rows_total, "идентификаторы обязаны быть различимы"
    assert set(cursor.values()) <= {OrderStatus.PAID, OrderStatus.CLOSED}

    first = distinct.index('<a class="tc-item info" href=')
    end_of_tag = distinct.index(">", first)
    broken = distinct[:first] + '<a class="tc-item info"' + distinct[end_of_tag:]
    damaged = _orders(broken)

    assert damaged.completeness is Completeness.PARTIAL
    assert len(damaged.rows(accept_incomplete=True)) == full.rows_total - 1

    # Курсор снят с полного чтения и не пострадал, поэтому повторное полное
    # чтение событий не порождает.
    assert diff_orders(cursor, full, account_id=ACCOUNT) == ()

    # И неполное чтение тоже: выпавшая строка не превращается в новый заказ.
    assert diff_orders(cursor, damaged, account_id=ACCOUNT) == ()


def test_new_order_is_seen_even_in_a_partial_read() -> None:
    """Проверяет, что неполное чтение не теряет по-настоящему новый заказ.

    Защита обязана отсекать ложное, а не всё подряд: заказ, которого нет в
    курсоре, новый независимо от того, целиком ли прочиталась страница.

    Returns:
        None
    """
    html = _raw()
    known = [e.order_id for e in _orders(html).rows()]

    # В курсоре все заказы, кроме первого: он и обязан оказаться единственным
    # новым, несмотря на то что чтение неполно.
    cursor = dict.fromkeys(known[1:], UNREAD_STATUS)
    broken = html.replace('<div class="tc-status text-primary">', '<div class="tc-gone">')
    damaged = _orders(broken)
    assert damaged.completeness is Completeness.PARTIAL

    events = diff_orders(cursor, damaged, account_id=ACCOUNT)
    assert [e.entity_id for e in events] == [known[0]]


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
