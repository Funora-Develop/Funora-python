"""Проверки возможностей у операций над лотом.

ЗАЧЕМ ОТДЕЛЬНЫЙ НАБОР. Возможность - это ответ на вопрос «а можно ли». У чтения
формы и у правки цены ответы РАЗНЫЕ, и разошлись они не для порядка: страницы
две, и отвечать на них площадка вправе по-разному.

Пока чтение формы шло под возможностью списка своих лотов, успех на списке
объявлял бы форму доступной, ни разу её не спросив. А правка цены не
спрашивала своей возможности вовсе - объявленная площадкой недоступной, она всё
равно уходила запросом.
"""

from __future__ import annotations

from typing import Any

import pytest
from test_update_price import NODE, OFFER, _observation, _page, _revision

from funora._budget import Budget
from funora._engine import IMPLEMENTED, Engine, Fetch, Submit
from funora._transport import TransportSettings
from funora.capabilities import Capability, CapabilityState
from funora.errors import UnsupportedCapabilityError


def _engine(**kwargs: Any) -> Engine:
    """Собирает движок с долговечным журналом правок.

    Аргументы:
        kwargs (Any): что передать движку сверх обычного.

    Возвращает:
        Engine: движок.
    """
    return Engine(TransportSettings(), Budget(), unsafe_price_changes_without_audit=True, **kwargs)


def _drive(engine: Engine, core: Any) -> Any:
    """Прокручивает ядро, отвечая на его просьбы.

    Аргументы:
        engine (Engine): движок, чьё это ядро.
        core (Any): сопрограмма.

    Возвращает:
        Any: итог сопрограммы.
    """
    reply: Any = None
    while True:
        try:
            request = core.send(reply)
        except StopIteration as stop:
            return stop.value
        if isinstance(request, Fetch):
            reply = _observation(_page(), url="https://funpay.com/lots/offerEdit")
        elif isinstance(request, Submit):
            reply = _observation("<html></html>", url=f"https://funpay.com/lots/{NODE}/trade")
        else:
            reply = None


def test_both_operations_are_declared_executable() -> None:
    """Требует, чтобы обе операции числились выполняемыми.

    Перечней «что мы умеем» два - фасад и IMPLEMENTED, - и разойтись им нельзя.
    Разошлись они молча один раз: capability(LOTS_UPDATE_PRICE) отвечал «операции
    под неё нет» при работающем client.lots.update_price.

    Возвращает:
        None
    """
    assert Capability.LOTS_FORM in IMPLEMENTED
    assert Capability.LOTS_UPDATE_PRICE in IMPLEMENTED

    engine = _engine()
    assert engine.capability(Capability.LOTS_FORM) is CapabilityState.UNKNOWN
    assert engine.capability(Capability.LOTS_UPDATE_PRICE) is CapabilityState.UNKNOWN


def test_reading_the_form_marks_its_own_capability_supported() -> None:
    """Требует, чтобы удачное чтение выставляло состояние ЧТЕНИЯ.

    Возвращает:
        None
    """
    engine = _engine()
    _drive(engine, engine.read_lot_form(NODE, OFFER))

    assert engine.capability(Capability.LOTS_FORM) is CapabilityState.SUPPORTED
    assert engine.capability(Capability.LOTS_UPDATE_PRICE) is CapabilityState.UNKNOWN, (
        "чтение формы объявило доступной ПРАВКУ, которой не делало"
    )


def test_reading_the_form_does_not_touch_the_list_capability() -> None:
    """Требует, чтобы чтение формы не выставляло состояние чужой страницы.

    Пока чтение шло под возможностью списка, успех на одной странице объявлял
    доступной другую.

    Возвращает:
        None
    """
    engine = _engine()
    before = engine.capability(Capability.LOTS_LIST_OWN)
    _drive(engine, engine.read_lot_form(NODE, OFFER))

    assert engine.capability(Capability.LOTS_LIST_OWN) is before


def test_a_successful_save_marks_the_write_capability_supported() -> None:
    """Требует, чтобы удачное сохранение выставляло состояние ЗАПИСИ.

    Состояние ставится по положительному свидетельству: сохранение состоялось и
    привело туда, куда наблюдалось.

    Возвращает:
        None
    """
    engine = _engine()
    _drive(engine, engine.update_price(NODE, OFFER, "2.50", expected_revision=_revision()))

    assert engine.capability(Capability.LOTS_UPDATE_PRICE) is CapabilityState.SUPPORTED


def test_an_unsupported_write_capability_stops_the_operation() -> None:
    """ГЛАВНАЯ ПРОВЕРКА: недоступная правка не уходит запросом.

    Возвращает:
        None
    """
    engine = _engine()
    engine._state.capabilities[Capability.LOTS_UPDATE_PRICE] = CapabilityState.UNSUPPORTED

    core = engine.update_price(NODE, OFFER, "2.50", expected_revision=_revision())
    with pytest.raises(UnsupportedCapabilityError):
        core.send(None)


def test_an_unsupported_write_capability_still_allows_reading() -> None:
    """Требует, чтобы недоступная правка не отнимала право посмотреть.

    Обнаружив, что менять цену нельзя, вызывающий обязан сохранить возможность
    узнать, что у лота стоит сейчас.

    Возвращает:
        None
    """
    engine = _engine()
    engine._state.capabilities[Capability.LOTS_UPDATE_PRICE] = CapabilityState.UNSUPPORTED

    form = _drive(engine, engine.read_lot_form(NODE, OFFER))
    assert form.offer_id


def test_an_unsupported_read_capability_stops_the_read() -> None:
    """Требует, чтобы недоступное чтение формы отказывало.

    Возвращает:
        None
    """
    engine = _engine()
    engine._state.capabilities[Capability.LOTS_FORM] = CapabilityState.UNSUPPORTED

    core = engine.read_lot_form(NODE, OFFER)
    with pytest.raises(UnsupportedCapabilityError):
        core.send(None)
