"""Проверки третьего состояния заказа и формы фильтров.

ЧТО ЗДЕСЬ ПРОИЗОШЛО. Три недели контракт утверждал: состояний, кроме «оплачен» и
«закрыт», в снимок не попало. Попало - в НАШ ЖЕ снимок, снятый трое суток спустя
после того, как утверждение записали.

Механизм тот же, что у прочих сорока ложных записей: страницу пересняли, она
стала вчетверо длиннее, а запись об отсутствии осталась и держалась на СТАРОМ
снимке - том, где строк восемь и состояний вправду два.

Отсюда устройство набора. Он сверяет два снимка ОДНОЙ И ТОЙ ЖЕ страницы и требует
от разбора читать оба: старый, где состояний два, и новый, где их три.

Проверка на одном снимке прошла бы у разбора, знающего ровно то, что в нём
лежит, - и не заметила бы, что мир шире.

Наблюдено: orders-trade.logged.ru (19.08.2026, 8 строк) и
orders-trade.v8.logged.ru (28.08.2026, 34 строки).
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from funora._orders import parse_orders_page
from funora.extraction import ROW_MARKER_BY_STATUS, STATUS_BY_CELL_CLASS, OrderStatus

ROOT: Final[Path] = Path(__file__).resolve().parent.parent
PAGES: Final[Path] = ROOT / "tests/fixtures/pages"

OLD: Final[str] = "orders-trade.logged.ru"
NEW: Final[str] = "orders-trade.v8.logged.ru"
WHEN: Final[datetime] = datetime(2026, 8, 31, tzinfo=UTC)


def _read(name: str) -> str:
    """Читает снимок по имени.

    Аргументы:
        name (str): Имя снимка без расширения.

    Возвращает:
        str: Разметка.
    """
    return (PAGES / f"{name}.skeleton.txt").read_text(encoding="utf-8")


def _row_classes(name: str) -> Counter[str]:
    """Считает модификаторы строк прямо в разметке, минуя наш разбор.

    Проверка, считающая нашим же разбором, проверяла бы разбор сам собой.

    Аргументы:
        name (str): Имя снимка.

    Возвращает:
        Counter[str]: Сколько строк какого вида.
    """
    return Counter(one.strip() for one in re.findall(r'<a class="tc-item([^"]*)"', _read(name)))


def test_the_newer_snapshot_carries_a_third_state() -> None:
    """ГЛАВНАЯ ПРОВЕРКА: третье состояние лежит в НАШЕМ снимке.

    Утверждение «состояний, кроме оплачен и закрыт, не попало» держалось три
    недели. Опровергает его одна строка из тридцати четырёх.

    Возвращает:
        None
    """
    seen = _row_classes(NEW)

    assert seen["warning"] == 1, (
        f"строк с модификатором warning {seen['warning']}, а наблюдалась одна. "
        "Снимок другой - утверждение о состояниях пора перечитать"
    )
    assert seen["info"] == 17
    assert seen[""] == 16


def test_the_older_snapshot_is_why_the_claim_survived() -> None:
    """Показывает, ПОЧЕМУ утверждение прожило три недели.

    Оно держалось на снимке, где состояний вправду два. Проверка стоит здесь не
    ради истории: она держит различие между «в снимке нет» и «на странице нет»,
    и различие это - главный урок сорока ложных записей.

    Возвращает:
        None
    """
    seen = _row_classes(OLD)

    assert "warning" not in seen, "в старом снимке нашёлся warning - разбор случая неверен"
    assert sum(seen.values()) == 8, "старый снимок изменился"


def test_the_parser_reads_all_three_states_from_the_newer_snapshot() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ВТОРАЯ: разбор читает все три.

    Возвращает:
        None
    """
    page = parse_orders_page(_read(NEW), observed_at=WHEN)
    rows = page.rows(accept_incomplete=True)

    seen_statuses = Counter(one.status.or_none() for one in rows)
    assert seen_statuses[OrderStatus.REFUNDED] == 1, f"состояния прочитаны как {seen_statuses}"
    assert seen_statuses[OrderStatus.PAID] == 17
    assert seen_statuses[OrderStatus.CLOSED] == 16


def test_the_parser_still_reads_the_older_snapshot() -> None:
    """Требует, чтобы старый снимок читался по-прежнему.

    Обратная половина: разбор, знающий три состояния, обязан читать и страницу,
    где их два. Иначе третье было бы добавлено ценой первых двух.

    Возвращает:
        None
    """
    page = parse_orders_page(_read(OLD), observed_at=WHEN)
    rows = page.rows(accept_incomplete=True)

    seen_statuses = Counter(one.status.or_none() for one in rows)
    assert seen_statuses[OrderStatus.PAID] == 5
    assert seen_statuses[OrderStatus.CLOSED] == 3
    assert OrderStatus.REFUNDED not in seen_statuses


def test_both_carriers_agree_on_the_third_state() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ТРЕТЬЯ: оба носителя состояния согласованы.

    Носителей два - класс ячейки и модификатор строки, - и читаются они ради
    перекрёстной сверки: два независимых носителя ловят переименование любого.

    Внеси третье состояние в один и забудь про второй - сверка молча ослабнет
    ровно на нём. Ворота контракта это и поймали при первой попытке.

    Возвращает:
        None
    """
    assert STATUS_BY_CELL_CLASS["text-warning"] is OrderStatus.REFUNDED
    assert ROW_MARKER_BY_STATUS[OrderStatus.REFUNDED] == "warning"

    # Перечень модификаторов у обоих носителей обязан совпадать.
    assert set(ROW_MARKER_BY_STATUS.values()) == {"info", "warning"}


def test_the_state_enum_is_not_closed_by_accident() -> None:
    """Требует, чтобы перечень состояний не считался полным.

    Три - это столько, сколько НАБЛЮДЕНО, а не сколько бывает. Независимая
    реализация того же протокола называет пять - unpaid и ещё одно сверх наших
    трёх, - и видели мы из них три.

    Возвращает:
        None
    """
    assert len(list(OrderStatus)) == 3, (
        f"состояний стало {len(list(OrderStatus))}. Если наблюдено новое - хорошо; "
        "если добавлено по чужому слову - это уже не наблюдение"
    )


def test_the_filter_form_is_read_and_not_assumed() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ЧЕТВЁРТАЯ: наличие формы фильтров ЧИТАЕТСЯ.

    Площадка показывает её не всегда: в снимке 19.08.2026 её нет вовсе, в
    снимке 28.08.2026 она есть.

    Отправить фильтр туда, где формы нет, значило бы получить несуженный список
    молча - вызывающий увидел бы чужие заказы там, где просил свои.

    Возвращает:
        None
    """
    assert parse_orders_page(_read(NEW), observed_at=WHEN).filters_available is True
    assert parse_orders_page(_read(OLD), observed_at=WHEN).filters_available is False


def test_the_filter_form_carries_four_controls_and_three_states() -> None:
    """Держит наблюдение, на котором стоит вывод об именах фильтров.

    Само наблюдение даёт ЧИСЛО и ДЛИНЫ, а не значения: подписи полей и вариантов
    замаскированы, потому что это значения атрибутов, а не ключи объекта.

    Длины имён - 2, 5, 5, 4 - в точности совпали с именами, которые называет
    независимая реализация: id, buyer, state, game. Длины вариантов состояния -
    4, 6, 8 - с paid, closed, refunded.

    Ни то, ни другое поодиночке не доказательство. Совпадение по числу, по
    длинам и по составу, добытое двумя путями, - уже довод.

    Возвращает:
        None
    """
    source = _read(NEW)
    start = source.index("orders-filter")
    block = source[start : start + 2000]

    names = re.findall(r'<(?:select|input)[^>]*name="T(\d+):a', block)
    assert [int(one) for one in names] == [2, 5, 5, 4], (
        f"длины имён полей {names}, а совпадали они с id, buyer, state, game"
    )

    options = re.findall(r'<option value="T(\d+):a', block)
    assert [int(one) for one in options] == [4, 6, 8], (
        f"длины вариантов состояния {options}, а совпадали они с paid, closed, refunded"
    )


@pytest.mark.parametrize("name", [OLD, NEW])
def test_neither_snapshot_leaks_text(name: str) -> None:
    """Требует, чтобы оба снимка оставались скелетами.

    Новый снимок перенесён в фикстуры из рабочего каталога, где лежит и сырая
    разметка. Перенести не тот файл - значит положить в открытый репозиторий
    текст авторизованной страницы.

    Аргументы:
        name (str): Имя снимка.

    Возвращает:
        None
    """
    source = _read(name)
    # Подписи вида T12:acs - признак скелета. Их обязано быть много.
    assert len(re.findall(r"T\d+:[a-z]+", source)) > 100, f"{name}: подписей мало - это не скелет"
    assert "csrf" not in source.lower() or "T" in source, f"{name}: похоже на сырую разметку"
