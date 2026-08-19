"""Проверки разбора списка заказов.

Больше половины набора работает на намеренно испорченной разметке, и это не
избыточность. Разбор целой страницы проверить легко, и такая проверка почти
ничего не стоит: если бы он не работал, это заметили бы сразу. Дорого стоит
другое - поведение в тот день, когда площадка поменяет вёрстку, потому что
именно тогда молчаливый отказ выглядит как «заказов нет», и продавец узнаёт о
нём от покупателя, а не от клиента.

Каждая порча воспроизводит правдоподобное изменение: переименован класс строки,
исчезла ячейка статуса, список опустел. Ожидается всегда одно и то же - громкий
отказ либо честно объявленная неполнота, но никогда не пустой успех.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from funora._observed import Presence
from funora._orders import Completeness, Severity, parse_orders_page
from funora.errors import IncompleteResultError, ProtocolChangedError, UnobservedFieldError
from funora.extraction import OrderStatus

#: Каталог со снимками страниц.
FIXTURES = Path(__file__).parent / "fixtures" / "pages"

#: Момент наблюдения. Задан явно, чтобы разбор оставался повторяемым.
WHEN = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def _fixture(name: str = "orders-trade.logged.ru") -> str:
    """Читает снимок страницы.

    Args:
        name (str): Имя снимка без расширения.

    Returns:
        str: Разметка снимка.
    """
    return (FIXTURES / f"{name}.skeleton.txt").read_text(encoding="utf-8")


def _parse(html: str | None = None):  # type: ignore[no-untyped-def]
    """Разбирает снимок с фиксированным моментом наблюдения.

    Args:
        html (str | None): Разметка. По умолчанию неизменённый снимок.

    Returns:
        OrdersPage: Результат разбора.
    """
    return parse_orders_page(html if html is not None else _fixture(), observed_at=WHEN)


def test_intact_page_is_complete() -> None:
    """Проверяет разбор неизменённой страницы.

    Returns:
        None
    """
    page = _parse()
    assert page.completeness is Completeness.COMPLETE
    assert page.reason == "all_rows_parsed"
    assert page.rows_total == 3
    assert page.rows_accepted == 3
    assert page.rows_rejected == 0
    assert not page.defects
    assert len(page.rows()) == 3


def test_renamed_row_class_is_loud_not_empty() -> None:
    """Проверяет самый вероятный вид изменения разметки.

    Класс строки переименован, контейнер жив и по-прежнему содержит три
    элемента. Разбор, полагающийся на один селектор строки, вернул бы пустой
    список - неотличимый от «заказов нет», то есть отказ был бы тихим. Второй,
    независимый от класса строки счётчик прямых детей контейнера ловит это
    и переводит отказ в громкий.

    Returns:
        None
    """
    broken = _fixture().replace('class="tc-item info"', 'class="tc-row info"')
    assert 'class="tc-row info"' in broken, "порча не применилась, проверка бессмысленна"

    with pytest.raises(ProtocolChangedError) as exc:
        _parse(broken)
    assert "ни одной" in str(exc.value)


def test_empty_container_is_unknown_not_complete() -> None:
    """Проверяет, что пустой список не выдаётся за полный результат.

    Снимка страницы без заказов у проекта нет, поэтому отличить продавца без
    продаж от переименованного класса строки нечем. Это сознательно неудобно:
    объявить полноту COMPLETE значило бы утверждать то, чего мы не проверяли.

    Returns:
        None
    """
    html = _fixture()
    start = html.index('<div class="tc tc-finance')
    body_start = html.index('<div class="dyn-table-body">', start)
    body_end = html.index("</div>", html.rindex("tc-item", body_start))
    emptied = html[:body_start] + '<div class="dyn-table-body"></div>' + html[body_end:]

    page = _parse(emptied)
    assert page.completeness is Completeness.UNKNOWN
    assert page.reason == "empty_list_not_observed"
    assert page.rows_total == 0


def test_incomplete_result_requires_acknowledgement() -> None:
    """Проверяет, что неполный результат не выдаётся молча.

    Молча отданный неполный список неотличим от полного, и обработчик примет
    решение по данным, которых нет.

    Returns:
        None
    """
    broken = _fixture().replace('<div class="tc-status text-primary">', '<div class="tc-gone">')
    page = _parse(broken)
    assert page.completeness is Completeness.PARTIAL

    with pytest.raises(IncompleteResultError):
        page.rows()

    assert len(page.rows(accept_incomplete=True)) == 3


def test_field_missing_in_all_rows_is_page_level() -> None:
    """Проверяет повышение уровня повреждения с поля до страницы.

    Исчезновение одной ячейки в одной строке - случайность. Исчезновение той же
    ячейки во всех строках - изменение вёрстки, и без повышения уровня отказ
    выглядел бы как сумма невинных пропусков.

    Returns:
        None
    """
    broken = _fixture().replace('<div class="tc-status text-primary">', '<div class="tc-gone">')
    page = _parse(broken)

    page_defects = [d for d in page.defects if d.severity is Severity.PAGE]
    assert any(d.code == "field_missing_in_all_rows" for d in page_defects)
    assert page.reason == "page_defects"


def test_unmapped_carrier_is_not_unknown_status() -> None:
    """Проверяет честность поля статуса на состоянии, которого нет в таблице.

    Наблюдались два состояния из скольких-то. Носитель, которого в таблице нет,
    обязан дать ненаблюдённое значение, а не unknown. Разница решает, соврёт
    реализация или нет: unknown означает «прочитали и не опознали», тогда как мы
    не прочитали вовсе.

    Returns:
        None
    """
    html = _fixture("orders-trade.states.logged.ru").replace(
        "tc-status text-success", "tc-status text-danger"
    )
    entry = _parse(html).rows(accept_incomplete=True)[1]

    assert entry.status.presence is Presence.NOT_OBSERVED
    assert entry.status.reason == "status_carrier_not_mapped"
    with pytest.raises(UnobservedFieldError):
        _ = entry.status.value
    assert entry.status.get("не знаю") == "не знаю"

    # Носитель при этом сохраняется: по нему и узнают, как выглядит состояние,
    # которого в таблице ещё нет.
    assert entry.status_carrier.value == "text-danger"


def test_status_is_read_from_both_carriers() -> None:
    """Проверяет чтение состояния заказа на снимке с двумя состояниями.

    Ожидаемая последовательность взята не из кода, а из страницы: человек
    прочитал её и назвал слова вместе с цветом, и пять «Оплачен» стоят там же,
    где голубая подсветка строки.

    Returns:
        None
    """
    page = _parse(_fixture("orders-trade.states.logged.ru"))
    assert page.completeness is Completeness.COMPLETE
    assert not page.defects

    assert [e.status.value for e in page.rows()] == [
        OrderStatus.PAID,
        OrderStatus.CLOSED,
        OrderStatus.CLOSED,
        OrderStatus.PAID,
        OrderStatus.CLOSED,
        OrderStatus.PAID,
        OrderStatus.PAID,
        OrderStatus.PAID,
    ]


def test_disagreeing_carriers_are_loud() -> None:
    """Проверяет, что расхождение носителей не сводится к одному из ответов.

    Главная проверка набора. Ради неё носителей и читается два: переименуй
    площадка любой из них - и второй не согласится. Молча выбрать один значило
    бы угадывать там, где ответ - «оплачен ли заказ», то есть решение бота
    выдавать товар.

    Returns:
        None
    """
    # Модификатор строки снят у оплаченного заказа, ячейка не тронута. Так
    # выглядело бы переименование info в что-нибудь другое.
    html = _fixture("orders-trade.states.logged.ru").replace(
        '<a class="tc-item info"', '<a class="tc-item"', 1
    )
    page = _parse(html)

    entry = page.rows(accept_incomplete=True)[0]
    assert entry.status.presence is Presence.NOT_OBSERVED
    assert entry.status.reason == "status_carriers_disagree"

    codes = {(d.field_name, d.code) for d in page.defects}
    assert ("status", "status_carriers_disagree") in codes


def test_renamed_status_class_does_not_change_the_answer_quietly() -> None:
    """Проверяет, что переименование цветового класса не меняет ответ молча.

    Обратная сторона предыдущей проверки: ломается второй носитель, а не первый.

    Returns:
        None
    """
    html = _fixture("orders-trade.states.logged.ru").replace("text-primary", "text-blue")
    page = _parse(html)

    for entry in page.rows(accept_incomplete=True):
        if entry.status_carrier.value == "text-blue":
            assert entry.status.presence is Presence.NOT_OBSERVED, (
                "переименованный класс дал значение вместо отказа"
            )


def test_status_carrier_is_the_class_not_the_text() -> None:
    """Проверяет, что носителем статуса служит класс, а не текст ячейки.

    Текст локализован, и составить по нему соответствие статусам нельзя: сменив
    язык аккаунта, площадка вернула бы другие значения для тех же состояний.
    Класс от языка не зависит - именно поэтому спецификация и называет носителем
    его.

    Returns:
        None
    """
    entry = _parse().rows()[0]
    assert entry.status_carrier.value == "text-primary"


def test_status_carrier_survives_a_language_change() -> None:
    """Проверяет независимость носителя от языка интерфейса.

    Проверка подменяет только текст ячейки, оставляя классы нетронутыми: так
    выглядит смена языка аккаунта. Носитель обязан остаться прежним.

    Returns:
        None
    """
    translated = _fixture().replace(
        '<div class="tc-status text-primary">', '<div class="tc-status text-primary">'
    )
    before = _parse().rows()[0].status_carrier.value
    after = _parse(translated).rows()[0].status_carrier.value
    assert before == after == "text-primary"


def test_guest_page_is_protocol_changed() -> None:
    """Проверяет разбор страницы без таблицы заказов.

    Гостевая страница до разбора не доходит: её отсекает классификатор. Но если
    дойдёт, пустой список возвращать нельзя.

    Returns:
        None
    """
    with pytest.raises(ProtocolChangedError):
        _parse(_fixture("orders-trade.guest.ru"))


def test_row_without_href_is_rejected_page_survives() -> None:
    """Проверяет, что одна негодная строка не отменяет страницу.

    Соседние заказы к сломавшемуся отношения не имеют, и терять их из-за него
    значит терять деньги там, где достаточно было пометить одну запись негодной.

    Returns:
        None
    """
    html = _fixture()
    first = html.index('<a class="tc-item info" href=')
    end = html.index(">", first)
    broken = html[:first] + '<a class="tc-item info"' + html[end:]

    page = _parse(broken)
    assert page.rows_accepted == 2
    assert page.rows_rejected == 1
    assert page.completeness is Completeness.PARTIAL
    assert any(d.code == "order_id_not_extractable" for d in page.defects)
    assert len(page.rows(accept_incomplete=True)) == 2


def test_counters_add_up() -> None:
    """Проверяет арифметический инвариант счётчиков.

    Расхождение счётчиков означает, что часть строк потерялась незаметно для
    самого разбора, - то есть отчёт о полноте перестал соответствовать
    действительности.

    Returns:
        None
    """
    for html in (_fixture(), _fixture().replace('class="tc-item info"', 'class="tc-item"', 1)):
        page = _parse(html)
        assert page.rows_accepted + page.rows_rejected == page.rows_total
        assert len(page) == page.rows_accepted


def test_parse_is_deterministic() -> None:
    """Проверяет повторяемость разбора.

    Разбор обязан давать тот же результат на сохранённом снимке спустя полгода,
    иначе фикстуры бесполезны как способ заметить изменение вёрстки.

    Returns:
        None
    """
    a, b = _parse(), _parse()
    assert a.completeness is b.completeness
    assert a.rows_accepted == b.rows_accepted
    assert a.rows() == b.rows()


def test_fields_are_scoped_to_the_row() -> None:
    """Проверяет, что поля ищутся внутри строки, а не по документу.

    Заголовок таблицы несёт те же классы ячеек: селектор .tc-price по документу
    находит четыре элемента при трёх заказах. Разбор, ищущий по документу,
    приписал бы всем строкам одно и то же значение из заголовка.

    Returns:
        None
    """
    rows = _parse().rows()
    amounts = [r.amount_text.or_none() for r in rows]
    assert len(amounts) == 3
    assert all(a is not None for a in amounts)


def test_page_length_is_available_without_acknowledgement() -> None:
    """Проверяет, что число собранных записей доступно всегда.

    Узнать, сколько записей собрано, нужно как раз для того, чтобы решить,
    признавать ли неполноту. Требовать признания для этого значило бы требовать
    решения до того, как для него появились данные.

    Returns:
        None
    """
    broken = _fixture().replace('<div class="tc-status text-primary">', '<div class="tc-gone">')
    page = _parse(broken)
    assert page.completeness is not Completeness.COMPLETE
    assert len(page) == 3


def test_offline_marker_is_read_as_absence() -> None:
    """Проверяет, что наблюдённый маркер отсутствия читается значением False.

    Returns:
        None
    """
    entries = _parse().rows()
    assert all(e.counterparty_online.value is False for e in entries)


def test_presence_is_read_in_both_directions() -> None:
    """Проверяет чтение присутствия на снимке, где есть оба класса.

    Снимок сделан в момент, когда трое покупателей были в сети. До него
    наблюдался только offline, и признак был односторонним.

    Returns:
        None
    """
    page = _parse(_fixture("orders-trade.states.logged.ru"))
    assert [e.counterparty_online.value for e in page.rows()] == [
        False,
        False,
        False,
        False,
        False,
        True,
        True,
        True,
    ]


def test_absent_marker_does_not_mean_online() -> None:
    """Проверяет, что снятый маркер не превращается в присутствие.

    Словарь классов закрыт по наблюдению, но не по умолчанию. Правило «нет
    offline, значит online» выглядело бы работающим ровно до переименования
    класса: стань он is-offline - и каждый контрагент молча оказался бы
    присутствующим.

    Returns:
        None
    """
    page = _parse(_fixture().replace("media media-user offline", "media media-user"))
    entry = page.rows()[0]
    assert entry.counterparty_online.presence is Presence.NOT_OBSERVED
    assert entry.counterparty_online.reason == "presence_marker_not_recognised"
    with pytest.raises(UnobservedFieldError):
        _ = entry.counterparty_online.value


def test_two_presence_markers_at_once_are_not_observed() -> None:
    """Проверяет, что противоречивая разметка не даёт ответа.

    Разметка, объявляющая пользователя одновременно в сети и не в сети, ответа
    не содержит, и выбрать один из двух классов было бы выдумкой.

    Returns:
        None
    """
    page = _parse(_fixture().replace("media media-user offline", "media media-user offline online"))
    entry = page.rows()[0]
    assert entry.counterparty_online.presence is Presence.NOT_OBSERVED
    assert entry.counterparty_online.reason == "presence_marker_not_recognised"


def test_renamed_offline_class_does_not_invent_presence() -> None:
    """Проверяет, что переименование класса не делает всех присутствующими.

    Это и есть цена вывода по отрицанию: переименуй площадка offline в
    is-offline, и каждый контрагент молча стал бы присутствующим - без
    повреждения, без исключения, без строки в журнале.

    Returns:
        None
    """
    page = _parse(_fixture().replace("media-user offline", "media-user is-offline"))
    assert all(e.counterparty_online.presence is Presence.NOT_OBSERVED for e in page.rows())


def test_unobserved_presence_is_not_a_defect() -> None:
    """Проверяет, что присутствующий контрагент не считается повреждением.

    Ненаблюдённость присутствия - граница наблюдений, а не поломка разметки.
    Считать её повреждением значило бы помечать негодной каждую строку, где
    контрагент на месте.

    Returns:
        None
    """
    page = _parse(_fixture().replace("media media-user offline", "media media-user"))
    assert page.completeness is Completeness.COMPLETE
    assert not [d for d in page.defects if d.field_name == "counterparty_online"]


def test_counterparty_link_comes_from_the_user_cell() -> None:
    """Проверяет, что ссылка на профиль берётся из ячейки контрагента.

    Раньше брался первый ``[data-href]`` строки. В снимке их два, и оба ведут на
    одного человека, поэтому ошибки не было видно. Появись такой атрибут в
    описании лота - и контрагентом молча стал бы адрес товара.

    Returns:
        None
    """
    html = _fixture().replace(
        '<div class="order-desc">',
        '<div class="order-desc" data-href="ловушка">',
        1,
    )
    page = _parse(html)
    assert "ловушка" not in page.rows()[0].counterparty_href.value


def test_missing_user_cell_is_still_a_defect() -> None:
    """Проверяет, что пропажа ячейки контрагента остаётся заметной.

    Смягчение вывода о присутствии не должно заодно скрыть настоящую поломку:
    исчезнувшая ячейка - это изменение разметки, и о нём нужно узнать.

    Returns:
        None
    """
    page = _parse(_fixture().replace("tc-user", "tc-user-renamed"))
    codes = {(d.field_name, d.code) for d in page.defects}
    assert ("counterparty_href", "field_not_observed") in codes
    assert ("counterparty_name", "field_not_observed") in codes


def test_cosmetic_class_does_not_break_status() -> None:
    """Проверяет, что лишний класс на разметке не отменяет чтение состояния.

    Главная проверка этой партии. Прежняя реализация сравнивала строку классов
    целиком, и добавленный площадкой ``hidden-xs`` делал состояние нечитаемым на
    всех восьми строках сразу - при полноте complete и нуле повреждений. То есть
    ответ на вопрос «оплачен ли заказ» пропадал молча, а именно молчание тут
    дороже всего.

    Returns:
        None
    """
    intact = _fixture("orders-trade.states.logged.ru")
    for spoiled in (
        intact.replace('"tc-status text-primary"', '"tc-status text-primary hidden-xs"'),
        intact.replace('"tc-item info"', '"tc-item info hover"'),
        intact.replace('"tc-status text-success"', '"tc-status text-success col-lg-2"'),
    ):
        page = _parse(spoiled)
        assert page.completeness is Completeness.COMPLETE
        assert all(e.status.is_observed for e in page.rows()), "косметика отменила состояние"


def test_renamed_cell_class_is_loud_when_the_row_still_marks_it() -> None:
    """Проверяет, что переименование цветового класса не проходит тихо.

    Ячейка не опознана, а модификатор строки говорит «оплачен» - значит,
    изменилась разметка, а не появилось новое состояние. Разница между этими
    двумя случаями и есть всё, ради чего читается второй носитель.

    Returns:
        None
    """
    page = _parse(_fixture("orders-trade.states.logged.ru").replace("text-primary", "text-blue"))
    assert page.completeness is Completeness.PARTIAL
    assert any(d.code == "status_carriers_disagree" for d in page.defects)


def test_a_new_state_is_quiet() -> None:
    """Проверяет, что состояние вне таблицы не считается поломкой.

    Обратная сторона предыдущей проверки. Заказ в возврате - обычное дело, и
    помечать из-за него страницу повреждённой значило бы кричать на нормальную
    работу. Значение при этом всё равно ненаблюдённое.

    Returns:
        None
    """
    page = _parse(
        _fixture("orders-trade.states.logged.ru").replace(
            '"tc-status text-success"', '"tc-status text-danger"'
        )
    )
    assert page.completeness is Completeness.COMPLETE
    assert not page.defects
    unread = [e for e in page.rows() if not e.status.is_observed]
    assert len(unread) == 3
    assert {e.status.reason for e in unread} == {"status_carrier_not_mapped"}


def test_no_readable_status_at_all_is_a_page_defect() -> None:
    """Проверяет последний рубеж: ни одного прочитанного состояния на странице.

    Переименование класса закрытого заказа иначе не ловится ничем - у закрытого
    нет положительного модификатора строки, и отличить переименование от нового
    состояния по одной строке нельзя даже в принципе. По всей странице - можно:
    восемь заказов разом в новом состоянии неправдоподобны.

    Returns:
        None
    """
    all_closed = (
        _fixture("orders-trade.states.logged.ru")
        .replace('"tc-item info"', '"tc-item"')
        .replace("text-primary", "text-success")
        .replace("text-success", "text-green")
    )
    page = _parse(all_closed)
    assert page.completeness is Completeness.PARTIAL
    assert any(d.code == "status_unreadable_on_every_row" for d in page.defects)


def test_description_and_category_are_separate() -> None:
    """Проверяет, что название лота и раздел не склеены.

    У описания наблюдалось ровно два потомка. Текст контейнера целиком склеивал
    их без пометки границы, и склейка выглядела безобидной ровно до того, как по
    описанию начнут что-нибудь искать.

    Returns:
        None
    """
    entry = _parse(_fixture("orders-trade.states.logged.ru")).rows()[0]
    assert entry.description_text.is_observed
    assert entry.category_text.is_observed
    assert entry.description_text.value != entry.category_text.value
    assert entry.category_text.value not in entry.description_text.value


def test_currency_symbol_and_time_ago_are_read() -> None:
    """Проверяет чтение двух полей, которые прежде терялись.

    Символ валюты лежит в отдельном узле ячейки цены, давность - в отдельной
    ячейке даты. Оба наблюдались во всех восьми строках, и оба разбор прежде не
    читал вовсе.

    Returns:
        None
    """
    page = _parse(_fixture("orders-trade.states.logged.ru"))
    for entry in page.rows():
        assert entry.currency_symbol_text.is_observed
        assert entry.time_ago_text.is_observed
        assert entry.time_ago_text.value != entry.time_text.value


def test_blank_attribute_is_empty_not_a_link() -> None:
    """Проверяет, что пустой атрибут не выдаётся за наблюдённый адрес.

    Пустая строка, выданная как адрес, подставится в запрос и уведёт его
    неизвестно куда. Разница между «пусто» и «не наблюдалось» здесь стоит
    дороже обычного.

    Returns:
        None
    """
    import re as _re

    blank = _re.sub(r'data-href="[^"]*"', 'data-href=""', _fixture("orders-trade.states.logged.ru"))
    entry = _parse(blank).rows(accept_incomplete=True)[0]
    assert entry.counterparty_href.presence is Presence.EMPTY
    assert entry.counterparty_href.presence is not Presence.PRESENT
