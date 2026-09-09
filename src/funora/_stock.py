"""Общий разбор показанного наличия в колонке и поле формы."""

from typing import Final

from selectolax.parser import Node

from ._observed import Observed

# Граница разбора SDK, а не объявленный площадкой предел запасов.
_MAX_DIGITS: Final[int] = 18


def parse_stock_text(raw: str | None) -> Observed[int]:
    """Не подменяет отсутствие, пустоту и неизвестный формат нулём."""
    if raw is None:
        return Observed.missing("stock_not_shown")
    value = raw.strip()
    if not value:
        return Observed.missing("stock_empty")
    if not value.isascii() or not value.isdecimal():
        return Observed.missing("stock_format_unknown")
    if len(value) > _MAX_DIGITS:
        return Observed.missing("stock_out_of_range")
    return Observed.present(int(value))


def parse_stock_column(row: Node, selector: str) -> Observed[int]:
    """Читает единственную ячейку внутри строки, исключая заголовки."""
    cells = row.css(selector)
    if len(cells) > 1:
        return Observed.missing("stock_ambiguous")
    return parse_stock_text(cells[0].text() if cells else None)
