"""Проверки общего сбора строк.

Три счётчика ловят три разных вида пропажи, и набор проверяет каждый по
отдельности. Механизм появился после того, как разбор нашёл дыру в предыдущей
его версии: два счётчика считали внутри первого попавшегося контейнера и потому
всегда соглашались друг с другом, пока строки второго контейнера исчезали молча.
"""

from __future__ import annotations

from selectolax.parser import HTMLParser

from funora._result import Severity, collect_rows


def _page(bodies: list[str]) -> HTMLParser:
    """Собирает страницу с заданными контейнерами.

    Args:
        bodies (list[str]): Содержимое каждого контейнера.

    Returns:
        HTMLParser: Разобранный документ.
    """
    inner = "".join(f'<div class="box">{body}</div>' for body in bodies)
    return HTMLParser(f"<html><body>{inner}</body></html>")


ROW = '<a class="row">запись</a>'


def test_single_container_is_counted() -> None:
    """Проверяет обычный случай.

    Returns:
        None
    """
    found = collect_rows(_page([ROW * 3]), ".box", "a.row")
    assert len(found.rows) == 3
    assert found.children == 3
    assert found.containers == 1
    assert not found.defects


def test_rows_in_a_second_container_are_not_lost() -> None:
    """Проверяет главную причину появления этого модуля.

    Прежний разбор брал первый контейнер и считал внутри него оба счётчика.
    Записи второго контейнера исчезали при полноте complete и нуле повреждений -
    отказ был невидим целиком.

    Returns:
        None
    """
    found = collect_rows(_page([ROW * 2, ROW * 2]), ".box", "a.row")
    assert len(found.rows) == 4, "строки обоих контейнеров обязаны быть собраны"
    assert found.containers == 2
    assert not found.defects


def test_renamed_row_class_is_caught_by_the_second_counter() -> None:
    """Проверяет счёт, не зависящий от класса строки.

    Переименование класса при живом контейнере - самый вероятный вид изменения
    вёрстки, и без второго счётчика оно даёт пустой список, неотличимый от
    «записей нет».

    Returns:
        None
    """
    found = collect_rows(_page(['<a class="renamed">з</a>' * 3]), ".box", "a.row")
    assert not found.rows
    assert found.children == 3
    assert any(d.code == "row_selector_undercount" for d in found.defects)
    assert all(d.severity is Severity.PAGE for d in found.defects)


def test_rows_outside_any_container_are_caught() -> None:
    """Проверяет счёт, не зависящий от контейнера.

    Строка, оказавшаяся вне разбираемой области, невидима обоим первым
    счётчикам: они согласны друг с другом внутри контейнера.

    Returns:
        None
    """
    tree = HTMLParser(f'<html><body><div class="box">{ROW}</div>{ROW}</body></html>')
    found = collect_rows(tree, ".box", "a.row")

    assert len(found.rows) == 1
    assert any(d.code == "rows_outside_container" for d in found.defects)


def test_no_container_gives_no_rows_and_no_false_defect() -> None:
    """Проверяет случай отсутствия контейнера.

    Решение о том, что это отказ, принимает разборщик: у него есть, что сказать
    про свою страницу. Общий сбор просто сообщает, что нашёл.

    Returns:
        None
    """
    found = collect_rows(HTMLParser("<html><body></body></html>"), ".box", "a.row")
    assert not found.rows
    assert found.containers == 0
    assert not found.defects
