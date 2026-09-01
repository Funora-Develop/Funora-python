"""Проверки возврата средств покупателю.

САМАЯ ОПАСНАЯ ИЗ НАПИСАННЫХ ОПЕРАЦИЙ, и устройство набора определяется этим.

Деньги уходят покупателю, и площадка не предлагает вернуть их обратно ничем.
Отправку сообщения можно исправить извинением, правку цены - обратной правкой,
поднятие стоит суток ожидания. Здесь - чужие деньги.

ЧТО НАБЛЮДЕНО НАМИ, А ЧТО НЕТ. Наблюдён ЗАПРОС целиком: адрес в атрибуте формы,
оба поля в ней же, и признак того, что площадка возврат по этому заказу
предлагает. НЕ наблюдён ответ.

Отсюда три предохранителя, и каждому здесь своя проверка:

  страница читается ДО отправки, и без формы возврата запрос не уходит вовсе -
  этот предохранитель стоит на НАШЕМ наблюдении;
  без явного согласия не уходит ничего;
  ответ без признака отказа ОТВЕРГАЕТСЯ, а отказ остаётся исходом.

Наблюдено 31.08.2026: order.v9.logged.ru.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Final

import pytest

from funora._budget import Budget
from funora._engine import Engine, Fetch, Submit
from funora._refund import REFUND_PATH, parse_refund
from funora._transport import Observation, TransportSettings
from funora.capabilities import Capability, CapabilityState
from funora.errors import (
    PreconditionFailedError,
    ProtocolChangedError,
    UsageError,
    ValidationError,
)
from funora.operations import OPERATIONS

ORDER: Final[str] = "ZVVQ8FKP"
WHEN: Final[datetime] = datetime(2026, 8, 31, tzinfo=UTC)

_APP: Final[str] = json.dumps({"csrf-token": "0123456789abcdef", "userId": "8524891"})


def _order_page(*, refundable: bool) -> str:
    """Собирает страницу заказа.

    Аргументы:
        refundable (bool): Показывает ли площадка форму возврата.

    Возвращает:
        str: Разметка.
    """
    form = (
        '<form action="https://funpay.com/orders/refund" class="modal-refund">'
        '<input name="id" value="ZVVQ8FKP"><input name="csrf_token" value="x">'
        "</form>"
        if refundable
        else "<div>формы возврата площадка не показывает</div>"
    )
    return (
        f"<body data-app-data='{_APP}'>"
        '<button class="navbar-toggle-logged"></button>'
        '<a class="user-link-dropdown" href="/users/8524891/"></a>'
        f'<div class="review-container" data-order="{ORDER}" data-rating="">'
        '<div class="review-item-row" data-author="9310582"></div></div>'
        f"{form}</body>"
    )


def _observation(html: str, url: str) -> Observation:
    """Собирает наблюдение.

    Аргументы:
        html (str): Тело ответа.
        url (str): Конечный адрес.

    Возвращает:
        Observation: Наблюдение.
    """
    raw = html.encode("utf-8")
    return Observation(
        status=200,
        final_url=url,
        html=html,
        elapsed_ms=10,
        redirects=0,
        content_length=len(raw),
        declared_length=len(raw),
    )


class _Scripted:
    """Отвечает страницей заказа и телом ответа на возврат."""

    def __init__(self, *, refundable: bool = True, answer: str | None = None) -> None:
        """Готовит сценарий.

        Аргументы:
            refundable (bool): Показывает ли площадка форму возврата.
            answer (str | None): Тело ответа на возврат.

        Возвращает:
            None
        """
        self.html = _order_page(refundable=refundable)
        self.answer = answer if answer is not None else json.dumps({"error": False, "msg": ""})
        self.submits: list[Submit] = []
        self.fetches: list[Fetch] = []

    def run(self, core: Any) -> Any:
        """Прокручивает ядро.

        Аргументы:
            core (Any): Сопрограмма.

        Возвращает:
            Any: Итог.
        """
        reply: Any = None
        while True:
            try:
                request = core.send(reply)
            except StopIteration as stop:
                return stop.value
            if isinstance(request, Submit):
                self.submits.append(request)
                reply = _observation(self.answer, f"https://funpay.com{request.path}")
            elif isinstance(request, Fetch):
                self.fetches.append(request)
                reply = _observation(self.html, f"https://funpay.com{request.path}")
            else:
                reply = None


def _engine(*, opted_in: bool = True) -> Engine:
    """Собирает движок без сети.

    Аргументы:
        opted_in (bool): Дано ли согласие.

    Возвращает:
        Engine: Движок.
    """
    engine = Engine(TransportSettings(), Budget())
    if opted_in:
        engine._state.opted_in = frozenset({Capability.ORDERS_REFUND})
    return engine


def test_a_page_without_the_refund_form_stops_the_request() -> None:
    """ГЛАВНАЯ ПРОВЕРКА: нет формы возврата - нет и запроса.

    Это единственный предохранитель, стоящий на НАШЕМ наблюдении: присутствие
    формы на странице заказа читается с 24.08.2026.

    Отсутствие означает, что площадка возврата по этому заказу не предлагает -
    заказ чужой, возврат уже сделан либо срок вышел. Лишнее чтение страницы
    здесь дешевле ошибочного возврата на порядок.

    Возвращает:
        None
    """
    script = _Scripted(refundable=False)
    core = _engine().refund_order(ORDER)

    with pytest.raises(PreconditionFailedError) as raised:
        script.run(core)

    assert script.submits == [], "формы возврата нет, а запрос всё равно ушёл"
    assert script.fetches, "страница не прочитана - предохранитель не сработал"
    assert "не отправлен" in str(raised.value)


def test_without_consent_nothing_leaves_at_all() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ВТОРАЯ: без согласия не уходит ни одного запроса.

    Отказ обязан случиться ДО сети - и до чтения страницы тоже: чтение здесь
    бесполезно, раз отправлять всё равно нечего.

    Возвращает:
        None
    """
    script = _Scripted()
    core = _engine(opted_in=False).refund_order(ORDER)

    with pytest.raises(UsageError) as raised:
        script.run(core)

    assert script.submits == [] and script.fetches == []
    text = str(raised.value)
    assert "ДЕНЬГИ ПОКУПАТЕЛЮ" in text, "отказ не называет цены ошибки"
    assert "FunPayAPI" in text, "отказ не называет источника заимствования"


def test_dropping_the_declaration_drops_the_consent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Требует, чтобы снятие объявления снимало и требование согласия.

    Возвращает:
        None
    """
    import dataclasses

    plain = dataclasses.replace(
        OPERATIONS["orders.refund"],
        request_provenance="",
        provenance_source="",
        provenance_rests_on="",
    )
    monkeypatch.setitem(OPERATIONS, "orders.refund", plain)

    script = _Scripted()
    script.run(_engine(opted_in=False).refund_order(ORDER))
    assert len(script.submits) == 1, "объявление снято, а отказ остался"


def test_an_answer_without_the_refusal_flag_is_refused() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ТРЕТЬЯ: нет признака отказа - нет и вывода об успехе.

    Площадка отвечает признаком ОТКАЗА, а не успеха. Считать отсутствие поля
    успехом значило бы объявить возврат состоявшимся, ничего о нём не зная.

    У поднятия то же правило стоило суток ожидания. Здесь оно стоит денег, о
    судьбе которых вызывающий не узнает, - и текст отказа обязан это сказать.

    Возвращает:
        None
    """
    script = _Scripted(answer=json.dumps({"msg": "готово"}))
    core = _engine().refund_order(ORDER)

    with pytest.raises(ProtocolChangedError) as raised:
        script.run(core)

    text = str(raised.value)
    assert "МОГЛИ уйти" in text, "отказ не говорит, что деньги могли уйти"
    assert "повторять не надо" in text, "отказ не отговаривает от повтора"


@pytest.mark.parametrize("payload", ["строка", 42, None, [], {"error": "нет"}, {"error": 1}])
def test_an_unusable_answer_is_refused(payload: Any) -> None:
    """Требует отвергать непригодный ответ, а не толковать его.

    Единица вместо истины исключается отдельно: в Python истина - это единица, и
    error=1 прочиталось бы как отказ, а error=0 как успех, ни разу не будучи
    логическим. У денег такая подмена стоит дороже всего.

    Аргументы:
        payload (Any): Непригодное тело.

    Возвращает:
        None
    """
    with pytest.raises(ProtocolChangedError):
        parse_refund(payload, order_id=ORDER, observed_at=WHEN)


def test_a_refusal_stays_an_outcome_and_carries_its_reason() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ЧЕТВЁРТАЯ: отказ - исход, а не исключение.

    Отказав, площадка называет причину текстом, и текст этот единственное, что о
    причине известно. Бросить здесь исключение значило бы выбросить его вместе с
    исходом.

    Возвращает:
        None
    """
    script = _Scripted(answer=json.dumps({"error": True, "msg": "возврат уже сделан"}))
    result = script.run(_engine().refund_order(ORDER))

    assert result.refunded is False
    assert result.message == "возврат уже сделан"
    assert result.order_id == ORDER


def test_success_is_the_negation_of_the_refusal_flag() -> None:
    """Требует читать успех отрицанием признака отказа.

    Возвращает:
        None
    """
    script = _Scripted()
    result = script.run(_engine().refund_order(ORDER))

    assert result.refunded is True
    assert result.order_id == ORDER


def test_the_request_carries_exactly_the_two_observed_fields() -> None:
    """Требует отправлять ровно то, что наблюдено в форме.

    Оба поля наблюдены нами. Лишнее поле здесь - выдумка, отправленная площадке
    вместе с распоряжением о деньгах.

    Возвращает:
        None
    """
    script = _Scripted()
    script.run(_engine().refund_order(ORDER))

    sent = script.submits[0]
    assert sent.path == REFUND_PATH
    assert set(sent.fields) == {"id", "csrf_token"}
    assert sent.fields["id"] == ORDER


def test_only_one_request_ever_leaves() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ПЯТАЯ: второго запроса не бывает никогда.

    Повтор здесь - ВТОРОЙ ВОЗВРАТ. Контракт объявляет операцию небезопасной и
    требует сверки вместо повтора.

    Возвращает:
        None
    """
    for answer in (
        json.dumps({"error": False}),
        json.dumps({"error": True, "msg": "нельзя"}),
    ):
        script = _Scripted(answer=answer)
        script.run(_engine().refund_order(ORDER))
        assert len(script.submits) == 1, f"на ответе {answer} ушло {len(script.submits)} запросов"


@pytest.mark.parametrize("order", ["", "  ", "ZVV/../", "ZVV-8F", "ZVV 8F"])
def test_a_bad_order_number_is_refused_before_the_network(order: str) -> None:
    """Требует отказа до сети на непригодном номере.

    Здесь это важнее обычного: ушедший запрос распорядился бы деньгами по
    неизвестно какому заказу.

    Аргументы:
        order (str): Непригодный номер.

    Возвращает:
        None
    """
    script = _Scripted()
    with pytest.raises(ValidationError):
        script.run(_engine().refund_order(order))
    assert script.fetches == [] and script.submits == []


def test_the_operation_is_declared_unsafe_and_needs_reconciliation() -> None:
    """Требует, чтобы контракт объявлял операцию небезопасной.

    От этого объявления зависит поведение повтора у всякой реализации, а не
    только у нашей.

    Возвращает:
        None
    """
    contract = OPERATIONS["orders.refund"]
    assert contract.safety.value == "unsafe", "возврат объявлен безопасным"
    assert contract.request_provenance == "third_party_report"
    assert "ЧТЕНИЕ ОТВЕТА" in contract.provenance_rests_on, (
        "не сказано, что непроверено именно чтение ответа, а не запрос"
    )


def test_a_body_that_is_not_json_is_refused_loudly() -> None:
    """Требует отвергать неразобравшееся тело, назвав цену.

    Возвращает:
        None
    """
    script = _Scripted(answer="<html>что-то пошло не так</html>")
    with pytest.raises(ProtocolChangedError) as raised:
        script.run(_engine().refund_order(ORDER))

    assert "МОГЛИ уйти" in str(raised.value)


def test_the_capability_is_marked_after_a_parsed_answer() -> None:
    """Требует выставлять состояние по разобранному ответу.

    Отказ по «возврат уже сделан» говорит, что операция доступна: она сработала
    бы на другом заказе. Недоступной её делает не отказ, а отсутствие права.

    Возвращает:
        None
    """
    engine = _engine()
    _Scripted(answer=json.dumps({"error": True, "msg": "нельзя"})).run(engine.refund_order(ORDER))

    assert engine._state.capabilities[Capability.ORDERS_REFUND] is CapabilityState.SUPPORTED


def test_there_is_no_amount_in_the_result() -> None:
    """Требует, чтобы у исхода не завелось суммы.

    Её не называет ни запрос, ни ответ. Поле суммы здесь было бы выдумкой о
    деньгах - худшей из возможных: вызывающий записал бы её в свою бухгалтерию.

    Возвращает:
        None
    """
    from funora._refund import RefundResult

    fields = set(RefundResult.__dataclass_fields__)
    assert not {one for one in fields if "amount" in one or "sum" in one}, (
        f"у исхода возврата завелась сумма: {sorted(fields)}. Её не называет ни "
        "запрос, ни ответ - значит это выдумка о деньгах"
    )
