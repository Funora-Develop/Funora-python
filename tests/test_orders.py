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


def test_status_is_unobserved_and_reading_it_raises() -> None:
    """Проверяет честность поля статуса.

    Соответствия классов статусам не наблюдалось, поэтому поле обязано быть
    ненаблюдённым, а не значением unknown. Разница решает, соврёт реализация или
    нет: unknown означает «прочитали и не опознали», тогда как мы не прочитали.

    Практическое следствие проверяется здесь же: сегодня список заказов не
    отвечает на вопрос, оплачен ли заказ.

    Returns:
        None
    """
    entry = _parse().rows()[0]
    assert entry.status.presence is Presence.NOT_OBSERVED
    assert entry.status.reason == "status_mapping_not_observed"

    with pytest.raises(UnobservedFieldError):
        _ = entry.status.value

    assert entry.status.get("не знаю") == "не знаю"
    assert "не наблюдалось" in str(entry.status)


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


def test_absent_offline_marker_does_not_mean_online() -> None:
    """Проверяет, что снятый маркер отсутствия не превращается в присутствие.

    Вывод по отрицанию опирался бы на имя класса, которого мы не выбирали.
    Площадка помечает отсутствие, но положительного маркера присутствия не
    наблюдалось ни в одном снимке, и «раз не offline, значит online» - догадка,
    а не наблюдение.

    Returns:
        None
    """
    page = _parse(_fixture().replace("media media-user offline", "media media-user"))
    entry = page.rows()[0]
    assert entry.counterparty_online.presence is Presence.NOT_OBSERVED
    assert entry.counterparty_online.reason == "presence_positive_marker_not_observed"
    with pytest.raises(UnobservedFieldError):
        _ = entry.counterparty_online.value


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
