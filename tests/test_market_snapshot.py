"""Проверки снимка выдачи и сравнения двух снимков.

ЧТО ЗДЕСЬ ГЛАВНОЕ. Не то, что разница считается, а то, чего она НЕ утверждает.

Неполный снимок не отличает «предложение пропало» от «мы его не прочитали». Для
следящего за рынком это разница между «конкурент ушёл, можно поднять цену» и
«мы плохо прочитали страницу», и решение по ней принимается деньгами.

Поэтому отсутствия считаются только по двум ПОЛНЫМ снимкам, а появления - по
любым: неполнота могла скрыть предложение прежде, но не могла выдумать его
сейчас.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

import pytest

from funora._result import Completeness
from funora._snapshot import (
    MarketSnapshot,
    SnapshotEntry,
    compare,
    fingerprint_of,
    snapshot_of,
)
from funora.errors import UsageError

NODE: Final[str] = "1908"
WHEN: Final[datetime] = datetime(2026, 8, 31, tzinfo=UTC)


def _entry(
    offer_id: str, *, price: str = "10.00", seller: str = "/users/1/", at: int = 0
) -> SnapshotEntry:
    """Собирает запись снимка.

    Аргументы:
        offer_id (str): предложение.
        price (str): цена текстом.
        seller (str): ссылка на продавца.
        at (int): позиция.

    Возвращает:
        SnapshotEntry: запись.
    """
    return SnapshotEntry(
        offer_id=offer_id,
        price_text=price,
        currency_symbol_text="₽",
        seller_href=seller,
        position=at,
    )


def _snapshot(
    *entries: SnapshotEntry,
    complete: bool = True,
    node: str = NODE,
) -> MarketSnapshot:
    """Собирает снимок из записей.

    Аргументы:
        entries (SnapshotEntry): записи.
        complete (bool): полон ли снимок.
        node (str): раздел.

    Возвращает:
        MarketSnapshot: снимок.
    """
    return MarketSnapshot(
        query_fingerprint=fingerprint_of(node),
        node_id=node,
        taken_at=WHEN,
        completeness=Completeness.COMPLETE if complete else Completeness.PARTIAL,
        reason=None if complete else "rows_damaged",
        rows_total=len(entries),
        rows_accepted=len(entries),
        offers={one.offer_id: one for one in entries},
    )


def test_an_incomplete_snapshot_never_reports_absences() -> None:
    """ГЛАВНАЯ ПРОВЕРКА: по неполному снимку никто не пропадает.

    Предложение, не попавшее в неполное чтение, не исчезло - его не прочитали.
    Объявить это исчезновением значит сказать продавцу «конкурент ушёл» там,
    где мы просто плохо прочитали страницу.

    Возвращает:
        None
    """
    before = _snapshot(_entry("a"), _entry("b"))
    after = _snapshot(_entry("a"), complete=False)

    diff = compare(before, after)

    assert diff.absent == (), "по неполному снимку объявлено исчезновение"
    assert not diff.absences_trusted


def test_an_incomplete_snapshot_on_either_side_stops_absences() -> None:
    """Требует, чтобы неполнота ЛЮБОЙ стороны запрещала вывод об отсутствии.

    Неполный прежний снимок так же опасен: предложение, не прочитанное тогда,
    выглядит появившимся сейчас, а прочитанное тогда и пропавшее теперь -
    исчезнувшим, хотя пропало оно из нашего чтения.

    Возвращает:
        None
    """
    before = _snapshot(_entry("a"), _entry("b"), complete=False)
    after = _snapshot(_entry("a"))

    diff = compare(before, after)
    assert diff.absent == ()
    assert not diff.absences_trusted


def test_two_complete_snapshots_do_report_absences() -> None:
    """Требует, чтобы по двум полным снимкам отсутствие называлось.

    Иначе проверка выше доказывала бы только, что список всегда пуст.

    Возвращает:
        None
    """
    before = _snapshot(_entry("a"), _entry("b"))
    after = _snapshot(_entry("a"))

    diff = compare(before, after)

    assert [one.offer_id for one in diff.absent] == ["b"]
    assert diff.absences_trusted


def test_appearances_are_counted_even_when_incomplete() -> None:
    """Требует считать появления и по неполным снимкам.

    Неполнота могла скрыть предложение прежде, но не могла выдумать его сейчас:
    оно вправду есть, раз прочитано.

    Возвращает:
        None
    """
    before = _snapshot(_entry("a"), complete=False)
    after = _snapshot(_entry("a"), _entry("b"), complete=False)

    diff = compare(before, after)
    assert [one.offer_id for one in diff.appeared] == ["b"]


def test_a_price_change_is_seen() -> None:
    """Требует замечать смену цены у оставшегося предложения.

    Возвращает:
        None
    """
    before = _snapshot(_entry("a", price="10.00"))
    after = _snapshot(_entry("a", price="9.50"))

    diff = compare(before, after)
    assert len(diff.price_changed) == 1
    assert diff.price_changed[0].before.price_text == "10.00"
    assert diff.price_changed[0].after.price_text == "9.50"


def test_a_currency_change_at_the_same_number_is_a_price_change() -> None:
    """Требует считать сменой цены смену ЗНАКА при том же числе.

    Не заметить её значило бы сравнить рубли с долларами и объявить, что цена
    не менялась.

    Возвращает:
        None
    """
    before = _snapshot(_entry("a", price="10.00"))
    after_entry = SnapshotEntry(
        offer_id="a",
        price_text="10.00",
        currency_symbol_text="$",
        seller_href="/users/1/",
        position=0,
    )
    after = _snapshot(after_entry)

    diff = compare(before, after)
    assert len(diff.price_changed) == 1, "смена знака валюты не замечена"


def test_a_moved_offer_is_not_a_change() -> None:
    """Требует, чтобы сдвиг позиции сам по себе изменением не считался.

    Порядок меняется от поднятия ЧУЖОГО лота, и считать это изменением значило
    бы порождать поток пустых событий каждую минуту.

    Возвращает:
        None
    """
    before = _snapshot(_entry("a", at=0), _entry("b", at=1))
    after = _snapshot(_entry("b", at=0), _entry("a", at=1))

    diff = compare(before, after)
    assert diff.appeared == ()
    assert diff.absent == ()
    assert diff.price_changed == ()


def test_a_seller_change_is_seen_apart_from_price() -> None:
    """Требует различать смену продавца и смену цены.

    Возвращает:
        None
    """
    before = _snapshot(_entry("a", seller="/users/1/"))
    after = _snapshot(_entry("a", seller="/users/2/"))

    diff = compare(before, after)
    assert len(diff.seller_changed) == 1
    assert diff.price_changed == ()


def test_snapshots_of_different_queries_are_refused() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ВТОРАЯ: чужие снимки не сравниваются.

    Снимок раздела с фильтром и снимок того же раздела без фильтра описывают
    разные множества. Сравнив их, получишь исчезновение всего, что
    отфильтровано, - и решение о цене по выдуманному исчезновению.

    Возвращает:
        None
    """
    before = _snapshot(_entry("a"), node="1908")
    after = _snapshot(_entry("a"), node="2000")

    with pytest.raises(UsageError) as raised:
        compare(before, after)
    assert "разными запросами" in str(raised.value)


def test_the_fingerprint_is_stable_and_distinguishing() -> None:
    """Требует, чтобы отпечаток совпадал у одного запроса и различал разные.

    Возвращает:
        None
    """
    assert fingerprint_of("1908") == fingerprint_of("1908")
    assert fingerprint_of("1908") != fingerprint_of("1909")


def test_an_offer_without_an_identifier_stays_out_of_the_snapshot() -> None:
    """Требует не класть в снимок предложение без идентификатора.

    Сравнивать его не по чему. Положенное под выдуманным ключом, оно
    породило бы исчезновение на ровном месте: в следующем снимке ключ будет
    другим.

    Возвращает:
        None
    """
    from datetime import datetime as _dt

    from funora._market import parse_market

    html = (
        "<html><body>"
        '<a class="tc-item" data-server="1" data-f-type="x">'
        '<div class="tc-desc"><div class="tc-desc-text">без ссылки</div></div>'
        '<div class="tc-user"><div class="media-user">'
        '<div class="avatar-photo" data-href="https://funpay.com/users/1/"></div>'
        '<div class="media-user-name">продавец</div></div></div>'
        '<div class="tc-price" data-s="1"><div>1.00<span class="unit">€</span></div></div>'
        "</a></body></html>"
    )
    page = parse_market(html, observed_at=_dt.now(UTC))
    shot = snapshot_of(page, node_id=NODE)

    assert shot.offers == {}, "предложение без идентификатора попало в снимок"
    # Потеря при этом ВИДНА: строка прочитана, а в снимок не попала.
    assert shot.rows_total == 1


def test_the_snapshot_carries_the_completeness_of_the_page() -> None:
    """Требует, чтобы полнота снимка бралась У СТРАНИЦЫ, а не выдумывалась.

    Снимок, объявивший себя полным по неполной странице, снимает запрет на
    вывод об исчезновении - то есть ровно ту защиту, ради которой полнота и
    протаскивается.

    Возвращает:
        None
    """
    from datetime import datetime as _dt

    from funora._market import parse_market

    # Строка без ссылки на профиль продавца - повреждение: это единственный
    # носитель продавца, годный для всех строк.
    html = (
        "<html><body>"
        '<a class="tc-item" href="https://funpay.com/lots/offer?id=77" '
        'data-server="1" data-f-type="x">'
        '<div class="tc-desc"><div class="tc-desc-text">описание</div></div>'
        '<div class="tc-user"><div class="media-user">'
        '<div class="media-user-name">продавец</div></div></div>'
        '<div class="tc-price" data-s="1"><div>1.00<span class="unit">€</span></div></div>'
        "</a></body></html>"
    )
    page = parse_market(html, observed_at=_dt.now(UTC))
    shot = snapshot_of(page, node_id=NODE)

    assert page.completeness is not Completeness.COMPLETE, "страница вышла полной"
    assert shot.completeness is page.completeness, "снимок объявил полноту сам"
    assert shot.reason == page.reason
    assert not shot.is_complete
