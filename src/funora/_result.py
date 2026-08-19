"""Общие понятия разбора: полнота, повреждения, сбор строк.

Файл появился после разбора, нашедшего одну и ту же дыру во всех трёх
разборщиках сразу. Причина была общая: каждый брал первый попавшийся контейнер
строк и считал внутри него оба счётчика, поэтому строки во втором контейнере
исчезали молча - и механизм двух счётчиков, написанный ровно против такого
исчезновения, с ними соглашался.

Пока эти понятия жили в разборе заказов, а два других разборщика их оттуда
импортировали, чинить приходилось бы в трёх местах. Теперь место одно.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from selectolax.parser import HTMLParser, Node

__all__ = ["Severity", "Completeness", "Defect", "RowSet", "collect_rows"]


class Severity(StrEnum):
    """Уровень, на котором обнаружено повреждение разбора.

    Уровни различают, что именно потеряно, потому что решения по ним разные.
    """

    #: Пострадала страница целиком. Записям доверять нельзя.
    PAGE = "page"

    #: Пострадала одна строка. Она отброшена, остальные целы.
    ROW = "row"

    #: Пострадало одно поле одной строки. Строка сохранена.
    FIELD = "field"


class Completeness(StrEnum):
    """Полнота прочитанного.

    Словарь взят из спецификации и не расширяется реализацией: значение уходит
    в решение вызывающего, и лишнее значение здесь означает, что шесть SDK
    ответят на один вопрос по-разному.
    """

    #: Всё разобрано, повреждений нет.
    COMPLETE = "complete"

    #: Часть данных потеряна. Что именно - в перечне повреждений.
    PARTIAL = "partial"

    #: Полноту установить не удалось.
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Defect:
    """Повреждение, обнаруженное при разборе.

    Attributes:
        severity (Severity): Уровень, на котором обнаружено.
        code (str): Машиночитаемый код, например ``row_selector_undercount``.
        detail (str): Пояснение для человека. Содержимого страницы не содержит.
        row_index (int | None): Номер строки при severity ROW и FIELD.
        field_name (str | None): Имя поля при severity FIELD.
    """

    severity: Severity
    code: str
    detail: str
    row_index: int | None = None
    field_name: str | None = None


@dataclass(frozen=True, slots=True)
class RowSet:
    """Строки, найденные на странице, вместе с обнаруженными расхождениями.

    Attributes:
        rows (tuple[Node, ...]): Узлы строк в порядке появления.
        children (int): Сколько прямых потомков у всех контейнеров вместе.
        containers (int): Сколько контейнеров строк нашлось.
        defects (tuple[Defect, ...]): Расхождения счётчиков.
    """

    rows: tuple[Node, ...]
    children: int
    containers: int
    defects: tuple[Defect, ...]


def collect_rows(tree: HTMLParser, container: str, row: str) -> RowSet:
    """Собирает строки по всем контейнерам и сверяет три независимых счёта.

    Счётчиков три, и каждый ловит своё.

    Совпадения селектора строки внутри контейнеров - основной счёт.

    Прямые потомки контейнеров - счёт, не зависящий от класса строки. Он ловит
    самый вероятный вид изменения вёрстки: переименование класса при живом
    контейнере, дающее иначе пустой список, неотличимый от «записей нет».

    Совпадения селектора строки по всему документу - счёт, не зависящий от
    контейнера. Он ловит то, чего не ловят первые два: строки, оказавшиеся во
    втором контейнере либо вне контейнера вовсе. Прежний разбор брал первый
    попавшийся контейнер, и оба счётчика соглашались друг с другом внутри него,
    пока часть записей исчезала молча - при полноте complete и нуле повреждений.

    Args:
        tree (HTMLParser): Разобранный документ.
        container (str): Селектор контейнера строк.
        row (str): Селектор строки.

    Returns:
        RowSet: Найденные строки, счётчики и расхождения.
    """
    containers = tree.css(container)
    rows: list[Node] = []
    children = 0

    for node in containers:
        rows.extend(node.css(row))
        children += len([child for child in node.iter() if child.tag != "-text"])

    defects: list[Defect] = []

    if len(rows) != children:
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="row_selector_undercount",
                detail=(
                    f"селектор строки нашёл {len(rows)}, а прямых потомков у контейнеров {children}"
                ),
            )
        )

    in_document = len(tree.css(row))
    if in_document != len(rows):
        defects.append(
            Defect(
                severity=Severity.PAGE,
                code="rows_outside_container",
                detail=(
                    f"по документу строк {in_document}, а внутри контейнеров "
                    f"{len(rows)}: часть записей лежит вне разбираемой области"
                ),
            )
        )

    return RowSet(
        rows=tuple(rows),
        children=children,
        containers=len(containers),
        defects=tuple(defects),
    )
