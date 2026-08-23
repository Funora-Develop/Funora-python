"""Проверяет, что непреобразуемое значение даёт повреждение, а не отказ.

Спецификация объявляла два механизма и молчала об отношении между ними:
ParseError жил среди ошибок, повреждение с уровнями page, row и field - среди
моделей, и ни один файл не упоминал другого. На одну и ту же испорченную ячейку
две реализации отвечали бы по-разному.

Решение записано в spec/extraction/rules.yaml -> value_does_not_convert и
принято в пользу того, что делает эталонная реализация: потерять восемь
прочитанных строк из-за девятой хуже, чем сообщить о девятой.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from funora._orders import Completeness, Severity, parse_orders_page
from funora.errors import IncompleteResultError
from funora.extraction import STATUS_BY_CELL_CLASS

#: Каталог со снимками страниц.
PAGES = Path(__file__).resolve().parent / "fixtures" / "pages"

#: Момент наблюдения, общий для проверок.
WHEN = "2026-08-19T12:00:00.000Z"


def _page(broken: bool) -> str:
    """Читает снимок списка продаж, при необходимости ломая одно значение.

    Args:
        broken (bool): Ломать ли класс-носитель состояния у первой строки.

    Returns:
        str: Разметка снимка.
    """
    raw = (PAGES / "orders-trade.logged.ru.skeleton.txt").read_text(encoding="utf-8")
    if not broken:
        return raw
    known = next(iter(STATUS_BY_CELL_CLASS))
    return raw.replace(known, "text-nonexistent", 1)


def test_intact_page_is_complete() -> None:
    """Проверяет исходное состояние: целый снимок читается полностью.

    Без этого следующая проверка ничего не доказывала бы: снимок мог быть
    неполным сам по себе.

    Returns:
        None
    """
    page = parse_orders_page(_page(broken=False), observed_at=WHEN)

    assert page.completeness is Completeness.COMPLETE
    assert page.defects == ()
    assert len(page.rows()) == page.rows_total


def test_broken_value_gives_a_defect_not_a_refusal() -> None:
    """Проверяет, что страница остаётся читаемой, а повреждение названо.

    Отказ от восьми строк из-за одной непрочитанной ячейки оставил бы продавца
    вовсе без сведений там, где он мог бы получить почти все.

    Returns:
        None
    """
    page = parse_orders_page(_page(broken=True), observed_at=WHEN)

    assert page.completeness is Completeness.PARTIAL, (
        "страница с одной испорченной ячейкой обязана быть неполной, а не целой"
    )
    assert page.defects, "повреждение не названо - вызывающему не по чему понять, что не так"
    assert all(defect.severity is not Severity.PAGE for defect in page.defects), (
        "повреждение поднято до уровня страницы: потеря одной ячейки не лишает "
        "смысла остальные строки"
    )


def test_reading_an_incomplete_result_needs_consent() -> None:
    """Проверяет, что неполный результат не выдаётся молча.

    Молчаливая выдача неполного - худший исход: вызывающий принял бы его за
    полную картину и не узнал бы об этом никогда.

    Returns:
        None
    """
    page = parse_orders_page(_page(broken=True), observed_at=WHEN)

    with pytest.raises(IncompleteResultError):
        page.rows()

    rows = page.rows(accept_incomplete=True)
    assert rows, "с явным согласием строки обязаны выдаваться"


def test_the_rest_of_the_page_survives() -> None:
    """Проверяет главное: прочитанное не теряется из-за непрочитанного.

    Returns:
        None
    """
    intact = parse_orders_page(_page(broken=False), observed_at=WHEN)
    broken = parse_orders_page(_page(broken=True), observed_at=WHEN)

    assert len(broken.rows(accept_incomplete=True)) == len(intact.rows()), (
        "строки пропали вместе с одной испорченной ячейкой"
    )
