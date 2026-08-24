"""Проверки разбора страницы баланса: балансы и операции по счёту."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
from selectolax.parser import HTMLParser

from funora._account import parse_balance_page
from funora._result import Completeness, Severity
from funora.errors import IncompleteResultError, ProtocolChangedError

FIXTURES: Final[Path] = Path(__file__).resolve().parent / "fixtures" / "pages"

#: Снимок страницы баланса. Три валюты, двадцать пять операций, есть продолжение.
BALANCE: Final[str] = "account-balance.logged.ru"

#: Момент наблюдения. Постоянен нарочно: разбор обязан быть повторяемым.
WHEN: Final[datetime] = datetime(2026, 8, 24, tzinfo=UTC)


def _page(name: str) -> str:
    """Читает снимок страницы.

    Аргументы:
        name (str): имя снимка без расширения.

    Возвращает:
        str: содержимое скелета.
    """
    return (FIXTURES / f"{name}.skeleton.txt").read_text(encoding="utf-8")


def test_the_page_gives_three_balances_not_one() -> None:
    """Требует читать балансы перечнем, а не одним значением.

    Схема объявила перечень с 0.8.0 - по переписи знаков валют, а не по
    разметке. Разметка подтвердила: три узла значения, ровно три.

    Возвращает:
        None
    """
    page = parse_balance_page(_page(BALANCE), WHEN)

    assert len(page.balances) == 3, f"балансов {len(page.balances)}"
    for one in page.balances:
        assert one.value_text.is_observed, f"баланс {one.position}: значение не прочитано"
        assert one.marker_text.is_observed, f"баланс {one.position}: узел перед не прочитан"
    assert [one.position for one in page.balances] == [0, 1, 2]


def test_each_balance_keeps_the_node_that_precedes_it() -> None:
    """Требует связывать значение с идущим перед ним узлом, а не с любым.

    Узлы чередуются, начиная с разделителя: три разделителя на три значения.
    Разделяющий знак дал бы два на три, и строение исключает прочтение
    «разделитель между значениями».

    Возвращает:
        None
    """
    tree = HTMLParser(_page(BALANCE))
    assert len(tree.css("span.balances-delimiter")) == len(tree.css("span.balances-value")), (
        "число узлов разошлось - строение перечня изменилось, и связь надо устанавливать заново"
    )

    # Убрать один узел - и связь объявляется потерянной, а не восстанавливается
    # наугад сдвигом.
    broken = _page(BALANCE).replace('<span class="balances-delimiter">', "<span>", 1)
    page = parse_balance_page(broken, WHEN)
    assert "balance_markers_mismatch" in {one.code for one in page.defects}


def test_a_page_without_balances_is_a_protocol_change() -> None:
    """Требует громкого отказа там, где перечня балансов нет вовсе.

    Возвращает:
        None
    """
    broken = _page(BALANCE).replace('class="balances-list"', 'class="balances-list-renamed"', 1)
    with pytest.raises(ProtocolChangedError, match="перечня балансов"):
        parse_balance_page(broken, WHEN)


def test_every_field_of_every_transaction_is_read() -> None:
    """Требует, чтобы у каждой операции прочиталось каждое поле.

    Возвращает:
        None
    """
    page = parse_balance_page(_page(BALANCE), WHEN)
    rows = page.transactions(accept_incomplete=True)

    assert len(rows) == 25, f"операций {len(rows)}"
    for one in rows:
        for name in (
            "transaction_id",
            "status_class",
            "status_text",
            "title_text",
            "date_text",
            "date_left_text",
            "amount_text",
            "currency_symbol_text",
        ):
            value = getattr(one, name)
            assert value.is_observed, (
                f"операция {one.row_index}: поле {name} не прочитано, причина {value.reason!r}"
            )


def test_the_amount_keeps_the_currency_symbol_out_of_itself() -> None:
    """Требует читать сумму СОБСТВЕННЫМ текстом ячейки, без знака валюты.

    Знак лежит отдельным узлом внутри ячейки, и текст целиком склеил бы их - то
    же устройство, что у цены в списке продаж.

    Возвращает:
        None
    """
    rows = parse_balance_page(_page(BALANCE), WHEN).transactions(accept_incomplete=True)
    for one in rows:
        assert one.currency_symbol_text.value not in one.amount_text.value, (
            f"операция {one.row_index}: знак валюты {one.currency_symbol_text.value!r} "
            f"попал в сумму {one.amount_text.value!r}"
        )


def test_fields_are_looked_for_inside_the_row_and_not_in_the_document() -> None:
    """Требует искать поля внутри строки, а не по всей странице.

    Заголовок таблицы несёт ячейку цены с тем же классом, что и строки: по
    документу таких ячеек двадцать шесть, а строк двадцать пять. Разбор, ищущий
    по документу, сдвинулся бы на одну ячейку и приписал каждой операции чужую
    сумму.

    Возвращает:
        None
    """
    tree = HTMLParser(_page(BALANCE))
    assert len(tree.css(".tc-price")) == len(tree.css(".tc-item")) + 1, (
        "заголовок больше не несёт ячейку цены - проверка перестала проверять"
    )

    # Проверка различительная: правится ОДНА строка, и меняться обязана тоже
    # одна. Сверять одинаковость значений мало - у всех двадцати пяти строк
    # снимка состояние одно и то же, и разбор, берущий его по документу, дал бы
    # ровно тот же ответ.
    original = _page(BALANCE)
    marker = '<div class="tc-status transaction-status">'
    assert original.count(marker) == 25, f"узлов состояния {original.count(marker)}"

    rows_before = parse_balance_page(original, WHEN).transactions(accept_incomplete=True)
    third = rows_before[2].status_text.value

    at = -1
    for _ in range(3):
        at = original.index(marker, at + 1)
    end = original.index("</div>", at)
    broken = original[:at] + original[at:end].replace(third, "T3:cXX") + original[end:]
    assert broken != original, "подмена состояния третьей строки не сработала"

    rows = parse_balance_page(broken, WHEN).transactions(accept_incomplete=True)
    assert rows[2].status_text.value == "T3:cXX", (
        f"третья строка не изменилась: {rows[2].status_text.value!r}. Разбор берёт "
        "состояние не из своей строки"
    )
    for index in (0, 1, 3, 4):
        assert rows[index].status_text.value == third, (
            f"строка {index} изменилась вслед за третьей: "
            f"{rows[index].status_text.value!r}. Разбор ищет поле по документу"
        )


def test_an_unknown_status_class_is_not_observed_rather_than_guessed() -> None:
    """Требует отказаться от незнакомого класса состояния операции.

    Наблюдено одно значение на двадцати пяти строках. Фильтр перечисляет четыре,
    но их имена - локализованный текст, и связать текст с классом нечем.

    Возвращает:
        None
    """
    broken = _page(BALANCE).replace("tc-item transaction-status-complete", "tc-item", 1)
    rows = parse_balance_page(broken, WHEN).transactions(accept_incomplete=True)

    first = rows[0]
    assert not first.status_class.is_observed
    assert first.status_class.reason == "status_class_absent"
    # Прочие строки при этом читаются: повреждение строки не отменяет страницы.
    assert rows[1].status_class.is_observed


def test_the_read_is_partial_because_the_continue_button_is_shown() -> None:
    """Требует объявить чтение неполным по ПОКАЗАННОЙ кнопке догрузки.

    Это вторая сторона контрольной пары. Первая - профиль отзывов, где кнопка
    спрятана при шести из шести и чтение объявляется полным.

    Возвращает:
        None
    """
    page = parse_balance_page(_page(BALANCE), WHEN)

    assert page.completeness is Completeness.PARTIAL, (
        f"полнота {page.completeness}: кнопка догрузки показана, значит есть что догружать"
    )
    assert page.reason == "more_rows_available"

    with pytest.raises(IncompleteResultError, match="результат неполон"):
        page.transactions()

    # Спрятать кнопку - и чтение становится полным. Оба перехода проверяются
    # разом: проверка одного из них проверяла бы половину правила.
    hidden = _page(BALANCE).replace(
        "btn btn-default dyn-table-continue", "btn btn-default dyn-table-continue hidden", 1
    )
    other = parse_balance_page(hidden, WHEN)
    assert other.completeness is Completeness.COMPLETE
    assert other.reason == "all_rows_parsed"
    assert len(other.transactions()) == 25


def test_a_missing_continue_button_is_not_read_as_completeness() -> None:
    """Требует не считать полнотой отсутствие кнопки догрузки.

    Страницы без неё никто не видел. Объявить по её отсутствию полноту значило
    бы вывести знание из ненаходки - ровно та ошибка, из-за которой правило
    догрузки и переписывалось.

    Возвращает:
        None
    """
    broken = _page(BALANCE).replace("dyn-table-continue", "dyn-table-gone", 1)
    page = parse_balance_page(broken, WHEN)

    assert page.completeness is Completeness.PARTIAL
    assert page.reason == "pagination_control_missing"


def test_a_missing_pagination_form_is_a_page_defect() -> None:
    """Требует заметить исчезновение формы догрузки.

    Возвращает:
        None
    """
    broken = _page(BALANCE).replace('class="dyn-table-form"', 'class="dyn-table-form-renamed"', 1)
    page = parse_balance_page(broken, WHEN)

    assert "pagination_form_missing" in {one.code for one in page.defects}
    assert page.completeness is Completeness.PARTIAL
    assert page.reason == "page_defects"
    assert any(one.severity is Severity.PAGE for one in page.defects)


def test_duplicate_transaction_ids_are_a_page_defect() -> None:
    """Требует заметить одинаковые идентификаторы операций.

    Два одинаковых схлопнутся у всякого, кто сложит операции в словарь, и число
    записей молча разойдётся с числом строк.

    Возвращает:
        None
    """
    original = _page(BALANCE)
    broken = original.replace('data-transaction="T9:d#2"', 'data-transaction="T9:d#1"', 1)
    assert broken != original, "подмена идентификатора не сработала"

    page = parse_balance_page(broken, WHEN)
    assert "duplicate_identifiers" in {one.code for one in page.defects}


def test_rows_that_cannot_be_parsed_at_all_are_a_protocol_change() -> None:
    """Требует громкого отказа там, где кандидаты есть, а собрать нечего.

    Возвращает:
        None
    """
    broken = _page(BALANCE).replace('class="dyn-table-body"', 'class="dyn-table-body-renamed"', 1)
    with pytest.raises(ProtocolChangedError, match="контейнера строк"):
        parse_balance_page(broken, WHEN)
