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
        assert event.payload["previous"] == OrderStatus.PAID
        assert event.payload["current"] == OrderStatus.CLOSED
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


def test_each_message_gets_its_own_fingerprint() -> None:
    """Проверяет, что отпечаток события о сообщении зависит от сообщения.

    Самый дорогой пробел из найденных разбором. Версией сущности служит
    идентификатор сообщения; замени её константой - и все сообщения одного
    диалога получат один отпечаток. Прогон через настоящее гашение повторов: из
    трёх сообщений доставляется одно, два гасятся как повторы. Между опросами
    хуже: второе сообщение через десять минут не доставляется вовсе.

    Ни одна проверка этого не удерживала: отпечаток сравнивали сам с собой.

    Returns:
        None
    """
    thread = _thread()
    events = diff_thread(frozenset(), thread, account_id=ACCOUNT, chat_id="42")
    assert len(events) > 2, "в снимке должно быть больше двух сообщений"

    fingerprints = {event.id for event in events}
    assert len(fingerprints) == len(events), (
        "у сообщений одного диалога совпали отпечатки - гашение повторов оставит от них одно"
    )


def test_message_fingerprint_survives_a_reread() -> None:
    """Проверяет, что отпечаток не зависит от момента чтения.

    Повторное чтение той же переписки обязано давать те же отпечатки: иначе
    гашение повторов перестаёт работать ровно там, где нужно, - при опросе раз в
    несколько секунд.

    Returns:
        None
    """
    first = diff_thread(frozenset(), _thread(), account_id=ACCOUNT, chat_id="42")
    later = diff_thread(
        frozenset(), _thread(when=WHEN + timedelta(days=3)), account_id=ACCOUNT, chat_id="42"
    )
    assert [e.id for e in first] == [e.id for e in later]


def test_chat_event_names_the_dialog_it_is_about() -> None:
    """Проверяет, что событие о диалоге названо диалогом, а не чем попало.

    Проверка неочевидно нужная: подстановка номера строки, адреса и пустой
    строки вместо идентификатора диалога проходила незамеченной. У заказов та же
    подмена роняла семь проверок - слепота была именно к диалогам.

    Сверяется с разметкой, а не с самим событием: сравнение ordering_key с
    entity_id тавтологично, ключ из него и построен.

    Returns:
        None
    """
    page = _chats()
    known = {}
    for entry in page.rows():
        known[entry.node_id] = "старая-позиция"

    events = diff_chats(known, page, account_id=ACCOUNT)
    assert events, "изменение позиции обязано дать события"

    node_ids = {entry.node_id for entry in page.rows()}
    for event in events:
        assert event.entity_id in node_ids, (
            f"событие названо {event.entity_id!r}, а такого диалога на странице нет"
        )


def test_status_change_names_the_order_it_is_about() -> None:
    """Проверяет то же для события о смене состояния заказа.

    Прежняя проверка была тавтологичной: она сверяла ключ упорядочивания с
    полем, из которого он и собран. Подстановка чужого номера заказа и вовсе
    несуществующего проходила незамеченной.

    Returns:
        None
    """
    before = _with_ids_orders()
    after = before.replace('"tc-item info"', '"tc-item"').replace(
        '"tc-status text-primary"', '"tc-status text-success"'
    )

    page_before = _orders(before)
    page_after = _orders(after)
    events = diff_orders(orders_cursor(page_before), page_after, account_id=ACCOUNT)
    assert events

    on_page = {entry.order_id for entry in page_after.rows()}
    for event in events:
        assert event.entity_id in on_page, (
            f"событие названо {event.entity_id!r}, а такого заказа на странице нет"
        )


def test_messages_keep_the_order_they_appear_in() -> None:
    """Проверяет порядок событий внутри одного ключа упорядочивания.

    Все сообщения диалога делят ключ chat:{chat_id}, и восстановить порядок по
    идентификатору нельзя: числовая его форма - догадка, а не наблюдение.
    Порядок кортежа - единственный сигнал, и разворот этого кортежа проходил
    незамеченным.

    Два сообщения одного диалога, пришедшие в обратном порядке, - это выданный
    не тот товар.

    Returns:
        None
    """
    thread = _thread()
    events = diff_thread(frozenset(), thread, account_id=ACCOUNT, chat_id="42")

    in_markup = [
        message.message_id.value
        for message in thread.messages(accept_incomplete=True)
        if message.message_id.is_observed
    ]
    in_events = [event.payload["message_id"] for event in events]
    assert in_events == in_markup, "порядок событий разошёлся с порядком в разметке"


def _with_ids_orders() -> str:
    """Готовит страницу заказов с различимыми идентификаторами.

    Returns:
        str: Разметка списка заказов.
    """
    return _raw()


def _read_marker(html: str, mark: str) -> str:
    """Двигает отметку прочтения первого диалога, не трогая позицию сообщения.

    Так и выглядит прочтение: последнее сообщение осталось прежним, а отметка
    прочтения аккаунта переехала. Признак непрочитанного выводится расхождением
    этих двух позиций.

    Args:
        html (str): Разметка списка диалогов.
        mark (str): Чем пометить новую отметку прочтения.

    Returns:
        str: Разметка с изменившимся признаком у первого диалога.
    """
    return html.replace('data-user-msg="T10:d#1"', f'data-user-msg="T10:d#{mark}"', 1)


def test_reading_a_dialog_produces_an_event() -> None:
    """Проверяет, что смена признака непрочитанного порождает событие.

    Событие называется «изменилось непрочитанное», а замечало только сдвиг
    позиции последнего сообщения. Прочтение диалога позицию не двигает - и
    события не давало вовсе, при том что признак лежал прямо в нагрузке.

    Returns:
        None
    """
    unread = _chats(_read_marker(_raw("chat.logged.ru"), "999"))
    first = unread.rows()[0]
    assert first.unread.value is True, "порча не сделала диалог непрочитанным"

    # Прочитали: отметка вернулась к позиции сообщения, само сообщение прежнее.
    read = _chats(_raw("chat.logged.ru"))
    events = diff_chats(chats_cursor(unread), read, account_id=ACCOUNT)

    about = [e for e in events if e.entity_id == first.node_id]
    assert about, "прочтение диалога не дало события"
    assert about[0].payload["unread"] is False


def test_reading_event_is_not_eaten_by_dedup() -> None:
    """Проверяет, что у события о прочтении свой отпечаток.

    Вторая половина той же починки. Версией сущности была позиция последнего
    сообщения, а прочтение её не двигает - событие о прочтении получило бы
    отпечаток уже доставленного и было бы съедено гашением повторов. Снова
    молча.

    Returns:
        None
    """
    unread = _chats(_read_marker(_raw("chat.logged.ru"), "999"))
    read = _chats(_raw("chat.logged.ru"))
    node = unread.rows()[0].node_id

    became_unread = diff_chats(chats_cursor(read), unread, account_id=ACCOUNT)
    became_read = diff_chats(chats_cursor(unread), read, account_id=ACCOUNT)

    one = next(e.id for e in became_unread if e.entity_id == node)
    two = next(e.id for e in became_read if e.entity_id == node)
    assert one != two, "у двух разных состояний диалога совпали отпечатки"


def test_learning_to_infer_unread_is_not_a_change() -> None:
    """Проверяет, что появление вывода не выдаётся за изменение диалога.

    Признак непрочитанного выводится из двух позиций, и одна из них может быть
    ненаблюдённой. Переход из невыведенного в выведенный говорит, что мы
    научились выводить, а не что диалог переменился. То же правило, что у
    состояния заказа.

    Returns:
        None
    """
    whole = _raw("chat.logged.ru")
    # Отметка прочтения переехала в другой атрибут: вывод сделать не из чего.
    blind = whole.replace("data-user-msg=", "data-was-user-msg=")

    page_blind = _chats(blind)
    assert not page_blind.rows(accept_incomplete=True)[0].unread.is_observed

    events = diff_chats(chats_cursor(page_blind), _chats(whole), account_id=ACCOUNT)
    assert events == (), "появление вывода выдано за изменение диалога"


def test_cursor_of_the_previous_format_does_not_flood() -> None:
    """Проверяет, что курсор прежней редакции не даёт лавину событий.

    Курсор переживает перезапуск, и сохранённый прежней редакцией содержит голые
    позиции без флага. Прочитанный буквально, он не совпал бы ни с одним новым
    состоянием - и первое же чтение после обновления выдало бы по событию на
    каждый из полусотни диалогов.

    Returns:
        None
    """
    page = _chats()
    legacy = {
        entry.node_id: entry.last_message_position.value
        for entry in page.rows()
        if entry.last_message_position.is_observed
    }
    assert legacy, "курсор прежней формы не собрался"

    assert diff_chats(legacy, page, account_id=ACCOUNT) == (), (
        "курсор прежней редакции дал события на ровном месте"
    )


def _free_text_on_pages() -> set[str]:
    """Собирает со снимков всё, что на страницу пишут люди.

    Собираются поля, значение которых - произвольный текст: сообщение, имя
    собеседника, начало последнего сообщения, название лота. Позиции,
    идентификаторы и состояния сюда не входят: они не свободный текст, и класть
    их в нагрузку нормально.

    Returns:
        set[str]: Непустые значения свободнотекстовых полей со всех снимков.
    """
    free: set[str] = set()

    for message in _thread().messages(accept_incomplete=True):
        for field in (message.text, message.author_name, message.time_text, message.time_full_text):
            if field.is_observed and field.value:
                free.add(field.value)
        if message.external_links.is_observed:
            free.update(message.external_links.value)

    for entry in _chats().rows(accept_incomplete=True):
        for field in (entry.counterparty_name, entry.preview_text, entry.time_text):
            if field.is_observed and field.value:
                free.add(field.value)

    for order in _orders().rows(accept_incomplete=True):
        for field in (
            order.description_text,
            order.category_text,
            order.counterparty_name,
            order.time_text,
        ):
            if field.is_observed and field.value:
                free.add(field.value)

    return free


def _all_payload_strings() -> list[tuple[str, str, object]]:
    """Собирает нагрузку всех событий, какие снимки способны породить.

    Курсор нигде не None: при None обе функции списков возвращают пустоту -
    первое чтение событий не даёт, чтобы не выдать полсотни диалогов разом. Я на
    этом и попался: собиратель звали с None, событий двух видов из четырёх не
    возникало вовсе, и проверка нагрузки их не смотрела. Отсюда же и утверждение
    ниже - оно держит именно этот промах.

    Returns:
        list[tuple[str, str, object]]: Тип события, имя поля нагрузки, значение.
    """
    thread = _thread()
    chats = _chats()
    orders = _orders()

    # Пустой словарь, а не None: заказы известны как множество, и пустое
    # множество делает все заказы страницы новыми.
    created = diff_orders({}, orders, account_id=ACCOUNT)
    changed = diff_orders(
        dict.fromkeys(
            (entry.order_id for entry in orders.rows(accept_incomplete=True)),
            "выдуманное-состояние",
        ),
        orders,
        account_id=ACCOUNT,
    )
    moved = diff_chats(
        {entry.node_id: "прежняя-позиция" for entry in chats.rows(accept_incomplete=True)},
        chats,
        account_id=ACCOUNT,
    )
    messages = diff_thread(frozenset(), thread, account_id=ACCOUNT, chat_id="42")

    events = [*messages, *moved, *created, *changed]
    # Перечислены те виды, что порождает этот модуль. Остальные объявлены
    # спецификацией, но порождаются другими слоями либо ещё никем: сверяться со
    # всем перечислением значило бы завести проверку, которая падает всегда.
    expected = {
        EventType.MESSAGE_CREATED,
        EventType.CHAT_UNREAD_CHANGED,
        EventType.ORDER_CREATED,
        EventType.ORDER_STATUS_CHANGED,
    }
    kinds = {event.type for event in events}
    assert kinds == expected, (
        f"собрались не все виды событий: не хватает {expected - kinds} - "
        "нагрузка недостающих останется непроверенной"
    )

    return [
        (str(event.type), name, value) for event in events for name, value in event.payload.items()
    ]


def test_payload_carries_no_free_text_from_the_page() -> None:
    """Проверяет, что нагрузка события не выносит написанное людьми.

    Событие уходит обработчику, а оттуда - в журнал, в очередь, в чужую систему.
    Текст сообщения, имя собеседника и название лота туда попадать не должны:
    вызывающий, которому они нужны, читает страницу сам и знает, что делает.

    Проверка нужна не ради сегодняшнего кода - сегодня он чист. Она нужна ради
    той правки, в которой кто-нибудь допишет в нагрузку `text`, потому что так
    удобнее, и ни одна проверка этого не заметит. Число ссылок в нагрузке есть,
    сами ссылки - нет, и ровно эту границу проверка и держит.

    Returns:
        None
    """
    free = _free_text_on_pages()
    assert len(free) > 5, "со снимков не собралось свободного текста - проверять нечего"

    for event_type, name, value in _all_payload_strings():
        if not isinstance(value, str):
            continue
        assert value not in free, f"нагрузка {event_type} несёт в поле {name!r} текст со страницы"


def test_payload_carries_no_long_text_at_all() -> None:
    """Проверяет то же правило способом, не зависящим от разбора.

    Предыдущая проверка сверяется с тем, что прочитал разбор, и потому слепа к
    тексту, который в нагрузку попал бы иначе - куском разметки, срезом строки,
    склейкой. Здесь проверяется грубый признак: в нагрузке нет длинных строк.

    Все нынешние строковые поля нагрузки - идентификаторы, состояния, адреса и
    названия перечислений. Длинных среди них нет и быть не должно.

    Returns:
        None
    """
    limit = 120
    for event_type, name, value in _all_payload_strings():
        if not isinstance(value, str):
            continue
        assert len(value) <= limit, (
            f"нагрузка {event_type} несёт в поле {name!r} строку длиной {len(value)}"
        )


def test_created_order_payload_describes_its_own_row() -> None:
    """Проверяет, что адрес и номер строки в нагрузке - той самой строки.

    Нагрузка события о новом заказе несёт адрес и порядковый номер строки, и
    сверять их было нечем: проверки смотрели на идентификатор сущности, а эти
    два поля брались на веру. Подстановка адреса соседней строки прошла бы
    незамеченной, а обработчик по такому адресу открыл бы чужой заказ.

    Returns:
        None
    """
    page = _orders()
    events = diff_orders({}, page, account_id=ACCOUNT)
    assert len(events) > 2, "снимок обязан дать больше двух новых заказов"

    rows = {entry.order_id: entry for entry in page.rows(accept_incomplete=True)}
    seen_rows = set()
    for event in events:
        entry = rows[event.entity_id]
        assert event.payload["href"] == entry.href
        assert event.payload["row_index"] == entry.row_index
        seen_rows.add(event.payload["row_index"])

    # Номера строк различны: одинаковый номер у всех прошёл бы посимвольную
    # сверку выше, если бы её мутировали на «первую строку для всех».
    assert len(seen_rows) == len(events)


def test_status_change_payload_describes_its_own_row() -> None:
    """Проверяет то же для события о смене состояния заказа.

    Returns:
        None
    """
    page = _orders()
    known = dict.fromkeys(
        (entry.order_id for entry in page.rows(accept_incomplete=True)),
        "выдуманное-состояние",
    )
    events = diff_orders(known, page, account_id=ACCOUNT)
    assert len(events) > 2

    rows = {entry.order_id: entry for entry in page.rows(accept_incomplete=True)}
    for event in events:
        assert event.payload["href"] == rows[event.entity_id].href
        assert event.payload["previous"] == "выдуманное-состояние"
        assert event.payload["current"] == str(rows[event.entity_id].status.value)
