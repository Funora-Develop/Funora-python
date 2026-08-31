"""Проверки чтения собственных лотов продавца.

Страница читается ради ОДНОГО: идентификатора предложения. Витрина на профиле
показывает те же лоты и даже больше полей, но идентификатора не даёт - он лежит
в строке запроса ссылки, а строку запроса скелет заменяет одной подписью.

Идентификатор нужен всем четырём операциям записи над лотами. Без него они
адресовали бы лот наугад.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
from selectolax.parser import HTMLParser

from funora._own_lots import OwnLotsPage, parse_own_lots
from funora._result import Completeness, Severity
from funora.errors import IncompleteResultError, ProtocolChangedError

FIXTURES: Final[Path] = Path(__file__).resolve().parent / "fixtures" / "pages"

#: Страница своих лотов раздела: двадцать строк.
OWN: Final[str] = "lots-trade.logged.ru"

#: Публичная витрина профиля: сто пятьдесят восемь предложений.
SHOWCASE: Final[str] = "user.logged.ru"

#: Момент наблюдения. Постоянен нарочно: разбор обязан быть повторяемым.
WHEN: Final[datetime] = datetime(2026, 8, 30, tzinfo=UTC)


def _page(name: str = OWN) -> str:
    """Читает снимок страницы.

    Аргументы:
        name (str): имя снимка без расширения.

    Возвращает:
        str: содержимое скелета.
    """
    return (FIXTURES / f"{name}.skeleton.txt").read_text(encoding="utf-8")


def _parsed(html: str | None = None) -> OwnLotsPage:
    """Разбирает страницу своих лотов.

    Аргументы:
        html (str | None): разметка либо None для снимка.

    Возвращает:
        OwnLotsPage: разобранная страница.
    """
    return parse_own_lots(html if html is not None else _page(), observed_at=WHEN)


def test_every_row_gives_its_own_offer_identifier() -> None:
    """Требует прочесть идентификатор У КАЖДОЙ строки, и все разные.

    Ради него страница и читается. Одинаковые идентификаторы означали бы, что
    читается не то: операции записи адресовали бы один лот вместо двадцати.

    Возвращает:
        None
    """
    page = _parsed()

    assert page.completeness is Completeness.COMPLETE, page.reason
    assert page.rows_total == 20, page.rows_total

    ids = [one.offer_id for one in page.lots()]
    assert all(one.is_observed for one in ids), [one.reason for one in ids if not one.is_observed]
    assert len({one.value for one in ids}) == 20, "идентификаторы повторяются"


def test_the_showcase_cannot_give_what_this_page_gives() -> None:
    """Требует, чтобы страница давала то, чего витрина не даёт.

    Проверка держит ПРИЧИНУ существования операции. Окажись идентификатор и на
    витрине - операция была бы лишней, и об этом стоило бы узнать от проверки, а
    не от читателя через полгода.

    Возвращает:
        None
    """
    own = HTMLParser(_page(OWN))
    showcase = HTMLParser(_page(SHOWCASE))

    with_id = [one for one in own.css("a.tc-item") if "data-offer" in (one.attributes or {})]
    assert len(with_id) == 20, len(with_id)

    on_showcase = [
        one for one in showcase.css("a.tc-item") if "data-offer" in (one.attributes or {})
    ]
    assert not on_showcase, (
        f"идентификатор нашёлся и на витрине ({len(on_showcase)} строк): "
        "отдельная операция тогда не нужна"
    )


def test_cells_are_read_inside_the_row_not_across_the_page() -> None:
    """Требует читать ячейки ВНУТРИ строки.

    Классы .tc-server, .tc-desc и .tc-price встречаются на странице по двадцать
    одному разу: двадцать строк плюс ШАПКА таблицы с теми же классами.

    Счёт по ячейке разошёлся бы с числом лотов на единицу, и разошёлся бы молча.

    Возвращает:
        None
    """
    tree = HTMLParser(_page())

    assert len(tree.css(".tc-price")) == 21, len(tree.css(".tc-price"))
    assert len(tree.css(".tc-server")) == 21
    assert len(tree.css("a.tc-item")) == 20

    # ПЕРВАЯ ячейка цены на странице - шапочная, и data-s у неё нет вовсе: там
    # стоят data-sort-field и data-sort-type. Значит поиск по документу отдал бы
    # значение сортировки ненаблюдённым у всех двадцати строк разом.
    header = tree.css(".tc-price")[0]
    assert "data-s" not in (header.attributes or {}), "шапка несёт data-s - проверка пуста"

    page = _parsed()
    assert page.rows_total == 20
    assert all(one.price_text.is_observed for one in page.lots())
    assert all(one.sort_value.is_observed for one in page.lots()), (
        "значение сортировки не прочиталось: ячейка цены найдена не внутри строки"
    )


def test_the_price_is_separated_from_the_currency_symbol() -> None:
    """Требует отделить цену от знака валюты.

    Общий текст ячейки склеил бы их в одно значение, а разделить потом нечем:
    знак валюты у разных валют разной длины.

    Возвращает:
        None
    """
    one = _parsed().lots()[0]

    assert one.price_text.is_observed
    assert one.currency_symbol_text.is_observed
    assert one.currency_symbol_text.value not in one.price_text.value, (
        f"знак валюты остался в цене: {one.price_text.value!r}"
    )


def test_the_page_does_claim_whether_a_lot_is_shown() -> None:
    """ПРОВЕРКА ПЕРЕВЁРНУТА 31.08.2026, и переворот этот - исправление ошибки.

    Здесь стояло обратное: модель НЕ должна обещать признака показа, потому что
    на странице его нет. Утверждение было неверным, а проверка его сторожила -
    то есть закрепляла неправду и не давала её заметить.

    Признак есть: выключенная строка несёт класс warning. Не наблюдался он
    потому, что у владельца все лоты были включены и снимок вышел однородным.

    Отсутствие признака на однородном снимке не значит его отсутствия на
    странице. Правило «отсутствие объявляется только по положительному
    признаку» было нарушено ровно здесь.

    Returns:
        None
    """
    import dataclasses

    from funora._own_lots import OwnLot

    names = {f.name for f in dataclasses.fields(OwnLot)}
    assert "is_active" in names, (
        "модель перестала обещать признак показа. Он наблюдён - класс warning у "
        "выключенной строки, снимок lots-trade.off.logged.ru"
    )

    # И признак этот обязан вправду быть на снимке, а не только в модели.
    assert "warning" in _page("lots-trade.off.logged.ru"), (
        "на снимке с выключенным лотом нет класса, ради которого он снят"
    )


def test_a_row_without_an_identifier_is_a_defect_and_not_silent() -> None:
    """Требует громко заметить строку без идентификатора.

    Строка без него бесполезна для операций записи, и молчать нельзя:
    вызывающий принял бы пробел за лот без идентификатора.

    Возвращает:
        None
    """
    html = _page()
    spoiled = html.replace("data-offer=", "data-was-offer=", 1)
    assert spoiled != html, "атрибут не снялся"

    page = _parsed(spoiled)

    assert page.completeness is Completeness.PARTIAL
    assert "offer_id_missing" in {one.code for one in page.defects}
    assert any(one.severity is Severity.ROW for one in page.defects)

    with pytest.raises(IncompleteResultError, match="не полностью"):
        page.lots()

    assert len(page.lots(accept_incomplete=True)) == 20


def test_an_empty_page_is_a_protocol_change_not_an_empty_list() -> None:
    """Требует громкого отказа на странице без строк и без кнопки.

    Пустой список вернуть нельзя: он неотличим от смены разметки, а разница
    решает, заводить ли лот заново.

    Возвращает:
        None
    """
    with pytest.raises(ProtocolChangedError, match="неотличим"):
        _parsed("<html><body></body></html>")


def test_the_raise_button_gives_both_arguments_of_the_future_request() -> None:
    """Требует прочесть оба довода кнопки поднятия.

    Самого запроса поднятия никто не наблюдал, и операции нет. Но доводы, лежащие
    на странице, читать стоит: без них наблюдение поднятия пришлось бы делать
    вслепую.

    Возвращает:
        None
    """
    page = _parsed()

    assert page.raise_game_id.is_observed, page.raise_game_id.reason
    assert page.raise_node_id.is_observed, page.raise_node_id.reason
    assert page.raise_game_id.value != page.raise_node_id.value


def test_a_page_with_the_button_but_no_lots_is_read_as_empty() -> None:
    """Требует различать пустой раздел и смену разметки.

    Раздел без единого лота - обычное состояние: продавец мог всё снять. Кнопка
    поднятия при этом остаётся, и она же служит признаком, что страница та самая.

    Возвращает:
        None
    """
    html = _page()
    at = html.index('<a class="tc-item"')
    end = html.rindex("</a>") + len("</a>")
    without = html[:at] + html[end:]
    assert 'class="tc-item"' not in without, "строки не убрались"

    page = _parsed(without)

    assert page.rows_total == 0
    assert page.completeness is Completeness.COMPLETE
    assert page.lots() == ()
    assert page.raise_game_id.is_observed, "кнопка поднятия обязана остаться признаком страницы"


def test_the_description_never_carries_the_server_name() -> None:
    """Требует читать вложенный узел описания, а не ячейку целиком.

    Полный текст ячейки .tc-desc равен «имя сервера, приклеенное спереди к
    описанию»: внутри неё лежит и обёртка .tc-visible-inside с дублем колонки
    сервера.

    Решение читать ячейку выглядит очевидным по имени класса и ошибается на
    двадцати строках из двадцати. Ошибка при этом ПРАВДОПОДОБНА: описание вправду
    там, просто с лишним словом впереди.

    Возвращает:
        None
    """
    tree = HTMLParser(_page())
    row = tree.css("a.tc-item")[0]

    outer = " ".join((row.css_first(".tc-desc").text() or "").split())
    inner = " ".join((row.css_first(".tc-desc-text").text() or "").split())
    server = " ".join((row.css_first(".tc-server").text() or "").split())

    assert outer != inner, "ячейка и вложенный узел совпали - проверка ничего не стережёт"
    assert outer.startswith(server), (outer, server)

    page = _parsed()
    for lot in page.lots():
        assert lot.description_text.is_observed
        assert not lot.description_text.value.startswith(lot.server_text.value), (
            f"в описание попало имя сервера: {lot.description_text.value[:40]!r}"
        )


def test_the_sort_value_is_one_and_the_same_in_every_row() -> None:
    """Требует замечать, что значение сортировки во всех строках одно.

    Доказано номером: у всех двадцати стоит один и тот же, а одинаковый номер при
    одном имени атрибута в пределах документа означает одно значение.

    ПОЧЕМУ оно одно - не установлено, и проверка этого не утверждает. Она держит
    сам факт: разбор, отдающий двадцать РАЗНЫХ значений, читал бы не тот атрибут.

    Возвращает:
        None
    """
    page = _parsed()
    values = {one.sort_value.value for one in page.lots()}

    assert len(values) == 1, f"значений сортировки {len(values)}, а на снимке одно: {values}"

    # А идентификаторов - двадцать. Разница между атрибутами существенна, и
    # перепутать их значило бы адресовать один лот вместо двадцати.
    assert len({one.offer_id.value for one in page.lots()}) == 20


def test_the_disabled_lot_is_told_apart_from_the_rest() -> None:
    """ГЛАВНАЯ ПРОВЕРКА: выключенный лот отличается от включённых.

    Три недели модель утверждала, что признака показа на этой странице нет
    вовсе. Строки одинаковы, пока все лоты включены - а у владельца все и были
    включены, и снимок вышел однородным.

    Отсутствие признака на однородном снимке не значит его отсутствия на
    странице. Это ровно тот случай, ради которого заведено правило «отсутствие
    объявляется только по положительному признаку», и правило было нарушено.

    Возвращает:
        None
    """
    page = parse_own_lots(_page("lots-trade.off.logged.ru"), observed_at=WHEN)
    lots = page.lots(accept_incomplete=True)

    marks = [one.is_active for one in lots]
    assert marks.count(False) == 1, f"выключенных строк {marks.count(False)}, а была одна"
    assert marks.count(True) == 3, f"включённых строк {marks.count(True)}, а было три"


def test_a_page_of_enabled_lots_says_they_are_enabled() -> None:
    """Требует, чтобы однородный снимок давал все True, а не все False.

    Иначе проверка выше проходила бы и при разборе, читающем признак наоборот.

    Возвращает:
        None
    """
    page = parse_own_lots(_page(OWN), observed_at=WHEN)
    lots = page.lots(accept_incomplete=True)

    assert lots, "в снимке нет строк"
    assert all(one.is_active for one in lots), (
        "на снимке, где все лоты включены, разбор нашёл выключенные"
    )


def test_the_marker_is_matched_as_a_word_not_a_substring() -> None:
    """Требует искать класс СЛОВОМ, а не подстрокой.

    На снимке разница неразличима: там класс стоит отдельным словом, и оба
    способа дают одно. Появись у площадки класс, начинающийся с того же корня -
    warning-soft, warnings, - и поиск подстрокой объявил бы лот выключенным на
    ровном месте.

    Разметка здесь подставная: проверяется разбор, а не площадка.

    Returns:
        None
    """
    html = _page("lots-trade.off.logged.ru").replace(
        'class="tc-item warning"', 'class="tc-item warning-soft"', 1
    )
    lots = parse_own_lots(html, observed_at=WHEN).lots(accept_incomplete=True)

    assert all(one.is_active for one in lots), (
        "класс найден подстрокой: warning-soft прочитан как warning"
    )
