"""Проверки операции чтения публичного списка предложений.

ЧТО ЭТА ОПЕРАЦИЯ ОТКРЫВАЕТ. Она вход в переоценку: прочитать цены соседей,
решить, поменять свою через lots.update_price. До неё цикл замыкать было нечем -
чужие цены читались только глазами.

Цена ошибки здесь не в лишнем запросе, а в РЕШЕНИИ О ЦЕНЕ. Увидев половину
предложений, бот посчитает себя дешевле всех и опустит цену там, где не нужно
было. Отсюда главная проверка набора: неполнота обязана требовать признания.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest

from funora._budget import Budget
from funora._engine import MARKET_PATH, Engine, Fetch
from funora._transport import Observation, TransportSettings
from funora.capabilities import Capability, CapabilityState
from funora.errors import (
    IncompleteResultError,
    NetworkError,
    UnsupportedCapabilityError,
    ValidationError,
)

FIXTURES: Final[Path] = Path(__file__).parent / "fixtures" / "pages"
FIXTURE: Final[str] = "market-offers.trimmed.guest.ru"
NODE: Final[str] = "1908"


def _page() -> str:
    """Читает снимок публичного списка.

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
        final_url=f"https://funpay.com/lots/{NODE}/",
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


def test_the_operation_reads_the_section_page() -> None:
    """Требует, чтобы читался адрес раздела, а не что-нибудь рядом.

    Возвращает:
        None
    """
    page, asked = _drive(_engine().read_market(NODE))

    assert len(asked) == 1, f"просьб {len(asked)}, а страница одна"
    assert isinstance(asked[0], Fetch)
    assert asked[0].path == MARKET_PATH.format(node_id=NODE)
    assert page.rows_total == 24


def test_every_offer_carries_an_identifier() -> None:
    """ГЛАВНОЕ, ЧТО ОТКРЫЛ ФОРМАТ v9: идентификатор чужого предложения.

    Без него операция не могла существовать: адресовать лот было нечем.

    Возвращает:
        None
    """
    page, _ = _drive(_engine().read_market(NODE))
    offers = page.offers()

    assert offers, "список пуст"
    assert all(one.offer_id.is_observed for one in offers), (
        "идентификатор прочитан не у всех предложений"
    )
    assert len({one.offer_id.value for one in offers}) == len(offers), (
        "два предложения получили один идентификатор"
    )


def test_the_price_comes_apart_from_its_currency_sign() -> None:
    """Требует, чтобы цена и знак валюты читались порознь.

    Склеенные, они не разделяются потом ничем: разделителем служит вёрстка, а
    не символ.

    Возвращает:
        None
    """
    page, _ = _drive(_engine().read_market(NODE))
    for one in page.offers():
        assert one.price_text.is_observed
        assert one.currency_symbol_text.is_observed


def test_an_incomplete_list_refuses_to_be_read_silently() -> None:
    """ГЛАВНАЯ ПРОВЕРКА: неполнота требует признания.

    По этому списку принимают решение о ЦЕНЕ. Неполный список неотличим от
    короткого, и бот, увидевший половину предложений, посчитает себя дешевле
    всех.

    Возвращает:
        None
    """
    # Ссылка на профиль продавца - единственный носитель продавца, годный для
    # всех строк. Убрав её, получаем повреждение строки, а с ним неполноту.
    damaged = _page().replace("avatar-photo", "avatar-broken")
    page, _ = _drive(_engine().read_market(NODE), html=damaged)

    with pytest.raises(IncompleteResultError):
        page.offers()

    assert page.offers(accept_incomplete=True), "признавший неполноту не получил ничего"


def test_the_lazy_rows_are_not_treated_as_truncation() -> None:
    """Требует, чтобы ленивая загрузка не считалась усечением.

    Строки с этим классом присутствуют в теле ответа наравне с прочими: класс
    говорит о показе, а не о наличии. Усечённый список пришлось бы дочитывать,
    а этот прочитан весь одним ответом.

    Возвращает:
        None
    """
    page, _ = _drive(_engine().read_market(NODE))

    assert page.rows_lazy > 0, "в снимке нет ни одной ленивой строки - проверять нечего"
    assert page.rows_accepted == page.rows_total, (
        "ленивые строки не собраны: разбор счёл показ наличием"
    )
    assert page.completeness.value == "complete"


@pytest.mark.parametrize("node", ["", "  ", "abc", "19o8", "1908/../"])
def test_a_bad_section_is_refused_before_the_network(node: str) -> None:
    """Требует отказа до запроса на непригодном номере раздела.

    Аргументы:
        node (str): непригодный номер.

    Возвращает:
        None
    """
    core = _engine().read_market(node)
    with pytest.raises(ValidationError):
        core.send(None)


def test_reading_the_market_marks_its_capability() -> None:
    """Требует, чтобы удачное чтение выставляло состояние возможности.

    Возвращает:
        None
    """
    engine = _engine()
    _drive(engine.read_market(NODE))

    assert engine.capability(Capability.MARKET_OFFERS) is CapabilityState.SUPPORTED


def test_an_unsupported_capability_stops_the_read() -> None:
    """Требует, чтобы недоступное чтение рынка отказывало.

    Возвращает:
        None
    """
    engine = _engine()
    engine._state.capabilities[Capability.MARKET_OFFERS] = CapabilityState.UNSUPPORTED

    core = engine.read_market(NODE)
    with pytest.raises(UnsupportedCapabilityError):
        core.send(None)


def test_a_truncated_body_never_becomes_a_list() -> None:
    """Требует, чтобы оборванное тело не превращалось в список предложений.

    Обрыв на передаче по частям даёт правдоподобную разметку с недостачей
    строк. Объявить её списком значило бы принять решение о цене по половине
    рынка - и не узнать об этом.

    Повторы политика делает сама; упорный обрыв кончается отказом, а не
    укороченным списком.

    Возвращает:
        None
    """
    html = _page()
    body = html.encode("utf-8")
    cut = Observation(
        status=200,
        final_url=f"https://funpay.com/lots/{NODE}/",
        html=html,
        elapsed_ms=10,
        redirects=0,
        content_length=len(body),
        # Объявлено больше, чем пришло.
        declared_length=len(body) + 4096,
    )

    core = _engine().read_market(NODE)
    reply: Any = None
    with pytest.raises(NetworkError) as raised:
        for _ in range(30):
            request = core.send(reply)
            reply = cut if isinstance(request, Fetch) else None
    assert "body_truncated" in str(raised.value)


def test_unverifiable_integrity_removes_completeness() -> None:
    """Требует снимать полноту, когда целостность подтвердить нечем.

    Ответ без объявленной длины оборванным не считается - считать его таким
    значило бы отвергать всякую передачу по частям, - но и полным объявлен быть
    не может: недостачу строк в нём заметить не по чему.

    Возвращает:
        None
    """
    html = _page()
    unverifiable = Observation(
        status=200,
        final_url=f"https://funpay.com/lots/{NODE}/",
        html=html,
        elapsed_ms=10,
        redirects=0,
        content_length=len(html.encode("utf-8")),
        declared_length=None,
    )

    core = _engine().read_market(NODE)
    reply: Any = None
    while True:
        try:
            request = core.send(reply)
        except StopIteration as stop:
            page = stop.value
            break
        reply = unverifiable if isinstance(request, Fetch) else None

    assert page.completeness.value == "unknown"
    assert page.reason == "integrity_unverified"
    with pytest.raises(IncompleteResultError):
        page.offers()


def test_the_observed_at_is_the_moment_of_reading() -> None:
    """Требует, чтобы момент наблюдения был проставлен.

    Возвращает:
        None
    """
    before = datetime.now(UTC)
    page, _ = _drive(_engine().read_market(NODE))
    assert before <= page.observed_at <= datetime.now(UTC)


def _row_html(href: str | None) -> str:
    """Собирает одну строку списка с заданной ссылкой.

    Разметка здесь ПОДСТАВНАЯ, и это не отступление от правила «только
    наблюдённое». Проверяется не площадка, а разбор: что он делает со строкой,
    у которой ссылка устроена не так, как на снимке. Наблюдать такие строки
    негде - площадка их не показывает, - а вести себя на них разбор обязан
    определённо.

    Аргументы:
        href (str | None): значение ссылки либо None, чтобы её не было вовсе.

    Возвращает:
        str: разметка страницы с одной строкой.
    """
    link = "" if href is None else f' href="{href}"'
    return (
        "<html><body>"
        f'<a class="tc-item"{link} data-server="1" data-f-type="x">'
        '<div class="tc-desc"><div class="tc-desc-text">описание</div></div>'
        '<div class="tc-user"><div class="media-user">'
        '<div class="avatar-photo" data-href="https://funpay.com/users/1/"></div>'
        '<div class="media-user-name">продавец</div>'
        "</div></div>"
        '<div class="tc-price" data-s="1"><div>1.00<span class="unit">€</span></div></div>'
        "</a></body></html>"
    )


def _only_offer(href: str | None) -> Any:
    """Разбирает страницу с одной строкой и отдаёт предложение.

    Аргументы:
        href (str | None): значение ссылки строки.

    Возвращает:
        Any: разобранное предложение.
    """
    from funora._market import parse_market

    page = parse_market(_row_html(href), observed_at=datetime.now(UTC))
    return page.offers(accept_incomplete=True)[0], page


def test_the_identifier_is_taken_by_name_not_by_position() -> None:
    """Требует брать параметр ПО ИМЕНИ, а не первый попавшийся.

    На снимке параметр в ссылке один, и разбор «взять первый» проходил бы
    неотличимо. Появись перед ним второй - метка кампании, признак сортировки -
    и идентификатором лота стало бы чужое значение.

    Возвращает:
        None
    """
    offer, _ = _only_offer("https://funpay.com/lots/offer?from=search&id=75289502")

    assert offer.offer_id.is_observed
    assert offer.offer_id.value == "75289502", (
        f"взято {offer.offer_id.value!r}: разбор берёт параметр по месту, а не по имени"
    )


def test_an_empty_identifier_is_not_an_identifier() -> None:
    """Требует, чтобы «id=» не сходило за прочитанный лот.

    Пустое значение выглядит наблюдённым - параметр на месте, - и адресовать по
    нему нельзя ничего.

    Возвращает:
        None
    """
    offer, _ = _only_offer("https://funpay.com/lots/offer?id=")

    assert not offer.offer_id.is_observed
    assert "empty" in offer.offer_id.reason


def test_a_row_without_a_link_has_no_identifier_and_says_why() -> None:
    """Требует, чтобы отсутствие ссылки наследовалось причиной.

    Нет ссылки - нет и идентификатора, и причина у обоих одна. Сказать про
    идентификатор «параметра нет» значило бы указать не на то место.

    Возвращает:
        None
    """
    offer, _ = _only_offer(None)

    assert not offer.offer_href.is_observed
    assert not offer.offer_id.is_observed
    assert "carrier_missing" in offer.offer_id.reason, (
        f"причина {offer.offer_id.reason!r} указывает не на ссылку"
    )


def test_a_link_without_the_parameter_damages_the_row() -> None:
    """ГЛАВНАЯ ПРОВЕРКА: пропавший идентификатор - повреждение, а не пустое поле.

    На живой странице параметр есть у ВСЕХ строк до единой - 1417 из 1417.
    Пропади он, это значит смену разметки, а не особенность строки, и молча
    отдать такую строку значило бы выдать неполный список за полный.

    Возвращает:
        None
    """
    offer, page = _only_offer("https://funpay.com/lots/offer?node=1908")

    assert not offer.offer_id.is_observed
    assert page.completeness.value != "complete", "строка без идентификатора сошла за полную"
    assert any(one.code == "offer_id_missing" for one in page.defects), (
        f"повреждения не заявлено: {[one.code for one in page.defects]}"
    )


def test_reading_lifts_the_capability_from_a_lowered_state() -> None:
    """Требует, чтобы удачное чтение ПОДНИМАЛО состояние возможности.

    Начальное состояние этой возможности - supported, и проверка «стало
    supported» на нём проходит сама собой. Мутация это и показала: сними запись
    состояния - ничего не изменится.

    Поэтому состояние сперва опускается.

    Возвращает:
        None
    """
    engine = _engine()
    engine._state.capabilities[Capability.MARKET_OFFERS] = CapabilityState.DEGRADED

    _drive(engine.read_market(NODE))

    assert engine.capability(Capability.MARKET_OFFERS) is CapabilityState.SUPPORTED, (
        "удачное чтение не подняло состояние: положительное свидетельство не записано"
    )
