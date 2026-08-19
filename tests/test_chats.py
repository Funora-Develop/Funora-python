"""Проверки разбора списка диалогов.

Как и у заказов, больше половины набора работает на испорченной разметке. Разница
в том, что здесь ошибка тише: пустой список диалогов выглядит правдоподобнее
пустого списка заказов, и заметить подмену на глаз почти невозможно.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from funora._chats import parse_chats_page
from funora._observed import Confidence, Presence
from funora._orders import Completeness, Severity
from funora.errors import IncompleteResultError, ProtocolChangedError

#: Каталог со снимками страниц.
FIXTURES = Path(__file__).parent / "fixtures" / "pages"

#: Момент наблюдения. Задан явно, чтобы разбор оставался повторяемым.
WHEN = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def _fixture(name: str = "chat.logged.ru") -> str:
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
        ChatsPage: Результат разбора.
    """
    return parse_chats_page(html if html is not None else _fixture(), observed_at=WHEN)


def test_intact_page_is_complete() -> None:
    """Проверяет разбор неизменённой страницы.

    Returns:
        None
    """
    page = _parse()
    assert page.completeness is Completeness.COMPLETE
    # Числа выводятся из снимка: прибитые ломались бы при каждой пересъёмке.
    assert page.rows_total == page.rows_accepted
    assert page.rows_total >= 2, "снимок обязан содержать хотя бы два диалога"
    assert not page.defects


def test_thread_page_gives_the_same_list() -> None:
    """Проверяет, что открытая переписка не меняет разбор списка.

    Список диалогов присутствует на обеих страницах, и отличаться он не должен:
    иначе разбор зависел бы от того, что пользователь открыл в браузере.

    Returns:
        None
    """
    without = _parse()
    with_thread = _parse(_fixture("chat-thread.logged.ru"))
    assert without.rows_accepted == with_thread.rows_accepted
    assert without.completeness is with_thread.completeness


def test_positions_are_opaque_strings() -> None:
    """Проверяет, что позиции читаются как строки, а не как числа.

    Счётчик общий для площадки, поэтому разность двух значений одного диалога
    измеряет сообщения всей площадки, а не пропущенные здесь. Число, полученное
    вычитанием, выглядит осмысленным и означает не то, что кажется. Строка этой
    ошибки не допускает.

    Returns:
        None
    """
    entry = _parse().rows()[0]
    assert isinstance(entry.last_message_position.value, str)
    assert isinstance(entry.own_position.value, str)


def test_unread_is_marked_as_inferred() -> None:
    """Проверяет честность признака непрочитанного.

    Расхождения позиций при непрочитанном диалоге мы не видели ни разу, поэтому
    правило выведено, а не наблюдено. Пометка обязывает вызывающего считаться с
    тем, что вывод неверен.

    Returns:
        None
    """
    entry = _parse().rows()[0]
    assert entry.unread.presence is Presence.PRESENT
    assert entry.unread.confidence is Confidence.INFERRED


def test_derived_unread_agrees_with_the_badge() -> None:
    """Сверяет выведенный признак со структурным.

    Счётчик непрочитанного в шапке наблюдается структурно и от трактовки позиций
    не зависит. Если он скрыт, а выведенное правило насчитало непрочитанные
    диалоги, значит правило неверно - и узнать об этом лучше здесь, чем в работе.

    Returns:
        None
    """
    page = _parse()
    derived = sum(1 for row in page.rows() if row.unread.value)
    badge_visible = page.unread_badge_visible.value

    if not badge_visible:
        assert derived == 0, (
            "счётчик непрочитанного скрыт, а правило насчитало непрочитанные диалоги"
        )


def test_renamed_row_class_is_loud_not_empty() -> None:
    """Проверяет самый вероятный вид изменения разметки.

    Пустой список диалогов выглядит правдоподобно: у продавца действительно может
    не быть переписок. Второй, независимый от класса строки счётчик - единственное,
    что отличает это от переименованного класса.

    Returns:
        None
    """
    broken = _fixture().replace('class="contact-item"', 'class="contact-row"')
    assert 'class="contact-row"' in broken, "порча не применилась, проверка бессмысленна"

    with pytest.raises(ProtocolChangedError):
        _parse(broken)


def test_missing_widget_is_protocol_changed() -> None:
    """Проверяет отказ при отсутствии блока переписки.

    Returns:
        None
    """
    with pytest.raises(ProtocolChangedError):
        _parse(_fixture("orders-trade.logged.ru"))


def test_missing_positions_degrade_the_page() -> None:
    """Проверяет реакцию на исчезновение атрибутов позиции.

    Позиции - единственное, на чём держится признак непрочитанного. Их
    исчезновение во всех строках означает изменение вёрстки, а не отсутствие
    непрочитанного.

    Returns:
        None
    """
    broken = _fixture().replace("data-node-msg=", "data-gone=")
    page = _parse(broken)

    assert page.completeness is Completeness.PARTIAL
    assert any(
        d.severity is Severity.PAGE and d.code == "field_missing_in_all_rows" for d in page.defects
    )

    with pytest.raises(IncompleteResultError):
        page.rows()

    entry = page.rows(accept_incomplete=True)[0]
    assert entry.unread.presence is Presence.NOT_OBSERVED
    assert entry.unread.reason == "positions_incomplete"


def test_counters_add_up() -> None:
    """Проверяет арифметический инвариант счётчиков.

    Returns:
        None
    """
    page = _parse()
    assert page.rows_accepted + page.rows_rejected == page.rows_total
    assert len(page) == page.rows_accepted


def test_parse_is_deterministic() -> None:
    """Проверяет повторяемость разбора.

    Returns:
        None
    """
    assert _parse().rows() == _parse().rows()
