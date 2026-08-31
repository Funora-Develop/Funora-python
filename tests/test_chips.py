"""Проверки второго рынка площадки - предложений по количеству.

ЧЕМ ЧИП ОТЛИЧАЕТСЯ ОТ ЛОТА, и почему разбор у них разный.

Лот - штучное предложение с описанием. Чип - предложение ПО КОЛИЧЕСТВУ: столько-
то единиц по такой цене за единицу. Разница в разметке ровно двумя вещами:
у чипа есть .tc-amount и нет .tc-desc.

Отсюда главная опасность набора: разбор, написанный по образцу лотов, искал бы
описание и объявлял бы повреждённой КАЖДУЮ строку - то есть весь рынок читался
бы неполно, и неполноту эту вызывающий обязан был бы признавать вручную на
каждом чтении.

Наблюдено 31.08.2026 гостем: раздел 1, сто семнадцать строк.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest

from funora._budget import Budget
from funora._chips import parse_chips
from funora._engine import CHIPS_PATH, Engine, Fetch
from funora._transport import Observation, TransportSettings
from funora.capabilities import Capability, CapabilityState
from funora.errors import (
    IncompleteResultError,
    ProtocolChangedError,
    UnsupportedCapabilityError,
    ValidationError,
)

FIXTURES: Final[Path] = Path(__file__).parent / "fixtures" / "pages"
FIXTURE: Final[str] = "chips.trimmed.guest.ru"
NODE: Final[str] = "1"
WHEN: Final[datetime] = datetime(2026, 8, 31, tzinfo=UTC)


def _page() -> str:
    """Читает снимок раздела чипов.

    Возвращает:
        str: разметка снимка.
    """
    return (FIXTURES / f"{FIXTURE}.skeleton.txt").read_text(encoding="utf-8")


def _observation(html: str) -> Observation:
    """Собирает наблюдение, каким его отдаёт транспорт.

    Аргументы:
        html (str): тело ответа.

    Возвращает:
        Observation: наблюдение.
    """
    body = html.encode("utf-8")
    return Observation(
        status=200,
        final_url=f"https://funpay.com/chips/{NODE}/",
        html=html,
        elapsed_ms=10,
        redirects=0,
        content_length=len(body),
        declared_length=len(body),
    )


def _drive(core: Any, *, html: str | None = None) -> tuple[Any, list[Any]]:
    """Прокручивает ядро, отвечая на его просьбы.

    Аргументы:
        core (Any): сопрограмма ядра.
        html (str | None): чем отвечать на чтение.

    Возвращает:
        tuple[Any, list[Any]]: итог и перечень просьб.
    """
    asked: list[Any] = []
    reply: Any = None
    while True:
        try:
            request = core.send(reply)
        except StopIteration as stop:
            return stop.value, asked
        asked.append(request)
        reply = _observation(html if html is not None else _page())


def _engine() -> Engine:
    """Собирает движок без сети.

    Возвращает:
        Engine: движок.
    """
    return Engine(TransportSettings(), Budget())


def test_the_whole_page_is_read_completely() -> None:
    """ГЛАВНАЯ ПРОВЕРКА: страница чипов читается ПОЛНОСТЬЮ.

    Разбор по образцу лотов искал бы описание, не находил и объявлял бы
    повреждённой каждую строку. Полнота здесь и есть доказательство того, что
    отсутствие описания записано положительно, а не забыто.

    Возвращает:
        None
    """
    page = parse_chips(_page(), observed_at=WHEN)

    assert page.completeness.value == "complete", (
        f"страница объявлена неполной: {page.reason}, повреждений "
        f"{[one.code for one in page.defects]}"
    )
    assert page.rows_accepted == page.rows_total
    assert page.offers(), "список пуст"


def test_amount_is_read_apart_from_its_unit() -> None:
    """Требует читать количество отдельно от единицы измерения.

    Склеенные, они не разделяются потом ничем: единица лежит вложенным узлом, и
    общий текст ячейки соединяет их без разделителя.

    Возвращает:
        None
    """
    for one in parse_chips(_page(), observed_at=WHEN).offers():
        assert one.amount_text.is_observed, "количество не прочитано"
        assert one.amount_unit_text.is_observed, "единица измерения не прочитана"
        assert one.amount_unit_text.value not in one.amount_text.value, (
            "единица измерения попала в количество - значит они склеены"
        )


def test_price_is_read_apart_from_the_currency_sign() -> None:
    """Требует читать цену отдельно от знака валюты.

    Возвращает:
        None
    """
    for one in parse_chips(_page(), observed_at=WHEN).offers():
        assert one.price_text.is_observed, "цена не прочитана"
        assert one.currency_symbol_text.is_observed, "знак валюты не прочитан"
        assert one.currency_symbol_text.value not in one.price_text.value


def test_every_row_carries_an_identifier() -> None:
    """Требует, чтобы идентификатор был у каждой строки и был различим.

    Возвращает:
        None
    """
    offers = parse_chips(_page(), observed_at=WHEN).offers()

    assert all(one.offer_id.is_observed for one in offers)
    assert len({one.offer_id.value for one in offers}) == len(offers)


def test_the_seller_comes_from_the_profile_link() -> None:
    """Требует брать продавца из ссылки на профиль.

    Тот же довод, что на рынке лотов: атрибут data-user есть только у поднятых
    строк, и разбор по нему отдал бы продавца у малой доли списка.

    Возвращает:
        None
    """
    for one in parse_chips(_page(), observed_at=WHEN).offers():
        assert one.seller_href.is_observed
        assert "/users/" in one.seller_href.value


def test_presence_is_read_by_the_attribute_not_by_its_value() -> None:
    """Требует читать признак присутствия НАЛИЧИЕМ атрибута.

    Из двух наблюдённых форм строки одна его несёт, другая нет - значит
    отсутствие значимо, и читать надо наличие.

    Возвращает:
        None
    """
    offers = parse_chips(_page(), observed_at=WHEN).offers()
    marks = {one.seller_online for one in offers}

    assert marks == {True, False}, (
        f"на снимке нет обеих форм строки: {marks}. Проверка ничего не различает"
    )


def test_a_damaged_row_makes_the_page_incomplete() -> None:
    """Требует, чтобы потеря продавца делала чтение неполным.

    Возвращает:
        None
    """
    damaged = _page().replace("avatar-photo", "avatar-broken")
    page = parse_chips(damaged, observed_at=WHEN)

    assert page.completeness.value != "complete"
    with pytest.raises(IncompleteResultError):
        page.offers()
    assert page.offers(accept_incomplete=True)


def test_a_page_without_rows_is_refused() -> None:
    """Требует отвергать страницу без строк, а не объявлять рынок пустым.

    Пустой список неотличим от изменившейся разметки.

    Возвращает:
        None
    """
    with pytest.raises(ProtocolChangedError):
        parse_chips("<html><body></body></html>", observed_at=WHEN)


def test_the_operation_reads_the_chips_address() -> None:
    """Требует читать адрес второго рынка, а не первого.

    Пути различаются: /chips/{n}/ против /lots/{n}/. Прочитать не тот значит
    прочитать другой рынок и не заметить этого.

    Возвращает:
        None
    """
    page, asked = _drive(_engine().read_chips(NODE))

    assert len(asked) == 1
    assert isinstance(asked[0], Fetch)
    assert asked[0].path == CHIPS_PATH.format(node_id=NODE)
    assert page.rows_total == 12


@pytest.mark.parametrize("node", ["", "  ", "abc", "1o", "1/../"])
def test_a_bad_section_is_refused_before_the_network(node: str) -> None:
    """Требует отказа до запроса на непригодном номере раздела.

    Аргументы:
        node (str): непригодный номер.

    Возвращает:
        None
    """
    core = _engine().read_chips(node)
    with pytest.raises(ValidationError):
        core.send(None)


def test_reading_marks_its_own_capability() -> None:
    """Требует, чтобы удачное чтение поднимало состояние СВОЕЙ возможности.

    Отдельной от market.offers: страницы разные, и отвечать на них площадка
    вправе по-разному.

    Возвращает:
        None
    """
    engine = _engine()
    engine._state.capabilities[Capability.CHIPS_OFFERS] = CapabilityState.DEGRADED
    before = engine.capability(Capability.MARKET_OFFERS)

    _drive(engine.read_chips(NODE))

    assert engine.capability(Capability.CHIPS_OFFERS) is CapabilityState.SUPPORTED
    assert engine.capability(Capability.MARKET_OFFERS) is before, (
        "чтение чипов выставило состояние рынка лотов"
    )


def test_an_unsupported_capability_stops_the_read() -> None:
    """Требует, чтобы недоступное чтение чипов отказывало.

    Возвращает:
        None
    """
    engine = _engine()
    engine._state.capabilities[Capability.CHIPS_OFFERS] = CapabilityState.UNSUPPORTED

    core = engine.read_chips(NODE)
    with pytest.raises(UnsupportedCapabilityError):
        core.send(None)


def test_the_page_has_no_description_and_that_is_recorded() -> None:
    """Требует, чтобы отсутствие описания было СВОЙСТВОМ СТРАНИЦЫ, а не догадкой.

    Проверка держится за снимок: узла описания на нём нет ни одного. Появись он
    - и запись в spec/extraction/chips.yaml, объявляющая его отсутствующим,
    стала бы неправдой.

    Возвращает:
        None
    """
    from selectolax.parser import HTMLParser

    tree = HTMLParser(_page())
    assert tree.css("a.tc-item"), "в снимке нет строк"
    assert not tree.css(".tc-desc"), "описание на странице чипов ЕСТЬ - запись неверна"
    assert not tree.css(".tc-desc-text")


def _row_html(
    *, href: str = "https://funpay.com/chips/offer?id=77", online: str | None = None
) -> str:
    """Собирает одну строку раздела чипов.

    Разметка ПОДСТАВНАЯ, и это не отступление от правила «только наблюдённое».
    Проверяется разбор, а не площадка: что он делает со строкой, устроенной не
    так, как на снимке. Наблюдать такие строки негде, а вести себя на них разбор
    обязан определённо.

    Аргументы:
        href (str): ссылка строки.
        online (str | None): значение признака присутствия либо None, чтобы
            атрибута не было вовсе.

    Возвращает:
        str: разметка страницы с одной строкой.
    """
    mark = "" if online is None else f' data-online="{online}"'
    return (
        "<html><body>"
        f'<a class="tc-item" href="{href}" data-server="1"{mark}>'
        '<div class="tc-server">сервер</div>'
        '<div class="tc-user"><div class="media-user">'
        '<div class="avatar-photo" data-href="https://funpay.com/users/1/"></div>'
        '<div class="media-user-name">продавец</div></div></div>'
        '<div class="tc-amount">1000<span class="unit">шт</span></div>'
        '<div class="tc-price"><div>1.00<span class="unit">Р</span></div></div>'
        "</a></body></html>"
    )


def test_an_empty_presence_attribute_still_means_present() -> None:
    """Требует читать присутствие НАЛИЧИЕМ атрибута, а не его значением.

    На снимке значение непусто у всех строк, где атрибут есть, и проверка по
    значению проходила бы неотличимо. Придёт пустое - и продавец, который в
    сети, будет объявлен офлайн.

    Возвращает:
        None
    """
    page = parse_chips(_row_html(online=""), observed_at=WHEN)
    one = page.offers(accept_incomplete=True)[0]

    assert one.seller_online is True, "пустое значение атрибута прочитано как отсутствие"


def test_a_missing_presence_attribute_means_absent() -> None:
    """Требует, чтобы отсутствие атрибута означало отсутствие признака.

    Иначе проверка выше доказывала бы лишь, что признак всегда истинен.

    Возвращает:
        None
    """
    page = parse_chips(_row_html(online=None), observed_at=WHEN)
    assert page.offers(accept_incomplete=True)[0].seller_online is False


def test_a_link_without_the_parameter_damages_the_row() -> None:
    """Требует, чтобы пропавший идентификатор был повреждением.

    На живой странице параметр есть у всех строк до единой. Пропади он - это
    смена разметки, а не особенность строки, и молча отдать такую строку значило
    бы выдать неполный список за полный.

    Возвращает:
        None
    """
    page = parse_chips(_row_html(href="https://funpay.com/chips/offer?node=1"), observed_at=WHEN)

    assert page.completeness.value != "complete"
    assert any(one.code == "offer_id_missing" for one in page.defects), (
        f"повреждения не заявлено: {[one.code for one in page.defects]}"
    )
