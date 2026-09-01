"""Проверки структурного чтения заказов пачкой.

ЧЕМ ЭТА ОПЕРАЦИЯ ОТЛИЧАЕТСЯ ОТ ВСЕХ ПРОЧИХ: своего наблюдения здесь НЕТ НИ
ОДНОГО.

У поднятия наблюдён запрос. У отзыва - два поля из четырёх. У расчёта цены - итог
на странице. Здесь не наблюдено ничего: ни адреса, ни полей запроса, ни ключей
ответа. Всё известно от независимой реализации того же протокола.

Отсюда и главные проверки набора:

  чужой перечень состояний НЕ ПРОСАЧИВАЕТСЯ в наш - незнакомое состояние
  отдаётся строкой и остаётся ненаблюдённым значением;
  спрошенное и полученное разведены - молчаливая потеря одного заказа из десяти
  выглядела бы как «такого заказа нет»;
  сумма не превращается в дробное число - её считают деньгами.

ЭТО МОГЛО БЫ СОБРАТЬ Order, И НЕ СОБИРАЕТ. Отдельная проверка следит, чтобы
модель контракта не построили на ответе, которого никто не видел.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Final

import pytest

from funora._budget import Budget
from funora._engine import Engine, Query
from funora._order_details import (
    BATCH_MAX,
    ORDER_DETAILS_PATH,
    OrderDetailsBatch,
    parse_order_details,
)
from funora._transport import Observation, TransportSettings
from funora.capabilities import Capability, CapabilityState
from funora.errors import ProtocolChangedError, ValidationError
from funora.extraction import OrderStatus

WHEN: Final[datetime] = datetime(2026, 8, 31, tzinfo=UTC)
FIRST: Final[str] = "ZVVQ8FKP"
SECOND: Final[str] = "AB12CD34"


def _record(**over: Any) -> dict[str, Any]:
    """Собирает запись заказа в том виде, в каком её описал сторонний источник.

    Аргументы:
        over (Any): Что переопределить.

    Возвращает:
        dict[str, Any]: Запись.
    """
    base: dict[str, Any] = {
        "order_uid": FIRST,
        "status": "paid",
        "amount": 1234.56,
        "currency": "RUB",
        "buyer": {"user_id": 9310582, "name": "покупатель"},
        "seller": {"user_id": 8524891, "name": "продавец"},
        "section": {"type_id": "lot", "local_id": 1908},
    }
    base.update(over)
    return base


def _answer(*records: dict[str, Any]) -> str:
    """Собирает тело ответа.

    Аргументы:
        records (dict[str, Any]): Записи заказов.

    Возвращает:
        str: Тело JSON.
    """
    return json.dumps(
        {"status": "SUCCESS", "data": {one["order_uid"]: one for one in records}},
        ensure_ascii=False,
    )


class _Scripted:
    """Отвечает на структурный вопрос одним телом."""

    def __init__(self, body: str | None = None) -> None:
        """Готовит сценарий.

        Аргументы:
            body (str | None): Тело ответа.

        Возвращает:
            None
        """
        self.body = body if body is not None else _answer(_record())
        self.queries: list[Query] = []

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
            if isinstance(request, Query):
                self.queries.append(request)
                raw = self.body.encode("utf-8")
                reply = Observation(
                    status=200,
                    final_url=f"https://funpay.com{request.path}",
                    html=self.body,
                    elapsed_ms=10,
                    redirects=0,
                    content_length=len(raw),
                    declared_length=len(raw),
                )
            else:
                reply = None


def _engine() -> Engine:
    """Собирает движок без сети.

    Возвращает:
        Engine: Движок.
    """
    return Engine(TransportSettings(), Budget())


def test_a_foreign_status_does_not_leak_into_our_enum() -> None:
    """ГЛАВНАЯ ПРОВЕРКА: чужое состояние не становится нашим.

    Сторонний источник называет пять состояний. Мы наблюдали три - в разметке
    списка продаж. Дописать остальные по чужому слову значило бы выдать чужое
    знание за своё наблюдение.

    Незнакомое состояние отдаётся СТРОКОЙ, а значение перечня остаётся
    ненаблюдённым - то есть вызывающий видит, что мы его не узнали.

    Возвращает:
        None
    """
    batch = parse_order_details(
        json.loads(_answer(_record(status="partially_refunded"))),
        asked=(FIRST,),
        observed_at=WHEN,
    )
    one = batch.found[0]

    assert one.status_text == "partially_refunded", "строка состояния потерялась"
    assert one.status.or_none() is None, "чужое состояние выдано за наблюдённое"
    assert "partially_refunded" in (one.status.reason or ""), (
        "причина не называет, ЧТО именно не узнали"
    )


def test_a_known_status_is_given_both_ways() -> None:
    """Обратная половина: знакомое состояние отдаётся и строкой, и значением.

    Без неё предыдущая проверка проходила бы у разбора, который не узнаёт
    вообще ничего.

    Возвращает:
        None
    """
    batch = parse_order_details(json.loads(_answer(_record())), asked=(FIRST,), observed_at=WHEN)
    one = batch.found[0]

    assert one.status_text == "paid"
    assert one.status.or_none() is OrderStatus.PAID


def test_our_status_enum_stayed_at_three() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ВТОРАЯ: наш перечень не вырос от чужого знания.

    Соблазн дописать сюда unpaid и partially_refunded велик: они называются,
    они правдоподобны, и без них два состояния из пяти читаются как
    ненаблюдённые.

    И всё же дописать их значило бы объявить наблюдённым то, чего мы не видели.

    Возвращает:
        None
    """
    assert {one.value for one in OrderStatus} == {"paid", "closed", "refunded"}, (
        "перечень состояний изменился. Наблюдены три - в разметке списка продаж; "
        "прочие два известны только от стороннего источника"
    )


def test_what_was_asked_and_what_came_back_are_kept_apart() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ТРЕТЬЯ: потерянный заказ назван поимённо.

    Площадка вправе вернуть не все заказы, о которых спросили. Молчаливая
    потеря одного из десяти выглядела бы как «такого заказа нет», и вызывающий
    решил бы, что заказ отменён.

    Возвращает:
        None
    """
    batch = parse_order_details(
        json.loads(_answer(_record())), asked=(FIRST, SECOND), observed_at=WHEN
    )

    assert batch.asked == (FIRST, SECOND)
    assert [one.order_uid for one in batch.found] == [FIRST]
    assert batch.missing == (SECOND,), "потерянный заказ не назван"


def test_nothing_missing_is_an_observation_too() -> None:
    """Требует, чтобы пустой перечень недостающих означал наблюдение.

    Возвращает:
        None
    """
    batch = parse_order_details(json.loads(_answer(_record())), asked=(FIRST,), observed_at=WHEN)
    assert batch.missing == ()


def test_the_amount_is_not_turned_into_a_float() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ЧЕТВЁРТАЯ: сумма не становится дробным числом.

    Числом она здесь и приходит - и это половина того, чего не хватало Money.
    Но сохранить её дробным значило бы потерять точность там, где считают
    деньги: 0.1 + 0.2 даёт 0.30000000000000004 всюду, где считают двоичной
    дробью.

    Возвращает:
        None
    """
    batch = parse_order_details(json.loads(_answer(_record())), asked=(FIRST,), observed_at=WHEN)
    amount = batch.found[0].amount

    assert isinstance(amount.value, str), "сумма отдана не строкой"
    assert amount.value == "1234.56"


def test_the_two_sides_are_separated() -> None:
    """Требует, чтобы покупатель и продавец пришли порознь.

    Это третье из того, чего не хватало Order: страница заказа показывает
    ОДНОГО контрагента и не помечает структурно, на которой стороне мы сами.

    Возвращает:
        None
    """
    one = parse_order_details(
        json.loads(_answer(_record())), asked=(FIRST,), observed_at=WHEN
    ).found[0]

    assert one.buyer_id.or_none() == "9310582"
    assert one.seller_id.or_none() == "8524891"
    assert one.buyer_name.or_none() == "покупатель"
    assert one.currency.or_none() == "RUB", "валюта пришла не кодом"


def test_order_is_still_not_built_from_this() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ПЯТАЯ: Order из этого НЕ собирается.

    Точка снимает все три причины, по которым Order не строится. Соблазн
    собрать его тут же - велик.

    И всё же модель контракта, построенная на ответе, которого никто не видел, -
    худший вид обещания: второй SDK прочтёт её как описание наблюдённого.

    Проверка следит, чтобы обещание не появилось раньше наблюдения.

    Возвращает:
        None
    """
    import funora

    assert not hasattr(funora, "Order"), (
        "в пакете появился Order. Если он собран из структурного чтения - это "
        "обещание на ответе, которого мы не видели ни разу"
    )
    assert hasattr(funora, "OrderDetails"), "проекция пропала - проверка стала пустой"


@pytest.mark.parametrize(
    "body",
    [
        '{"data": {}}',
        '{"status": "FAIL", "data": {}}',
        '{"status": "SUCCESS"}',
        '{"status": "SUCCESS", "data": "не объект"}',
        '"строка"',
        "42",
    ],
)
def test_an_unusable_answer_is_refused(body: str) -> None:
    """Требует отвергать непригодный ответ, а не толковать его.

    Пустой словарь data и отсутствующий - разные вещи: первый означает «ни один
    не найден», второй - что мы читаем не тот ответ.

    Аргументы:
        body (str): Непригодное тело.

    Возвращает:
        None
    """
    with pytest.raises(ProtocolChangedError):
        parse_order_details(json.loads(body), asked=(FIRST,), observed_at=WHEN)


def test_an_empty_data_is_not_a_failure() -> None:
    """Обратная половина: пустой словарь - это «ни один не найден».

    Возвращает:
        None
    """
    batch = parse_order_details({"status": "SUCCESS", "data": {}}, asked=(FIRST,), observed_at=WHEN)
    assert batch.found == ()
    assert batch.missing == (FIRST,)


def test_a_record_without_a_status_is_refused() -> None:
    """Требует отвергать заказ без состояния.

    По этому признаку выдают товар. Считать заказ оплаченным, не увидев
    признака, значило бы отдать товар за неоплаченный.

    Возвращает:
        None
    """
    broken = json.dumps({"status": "SUCCESS", "data": {FIRST: {"amount": 100}}})
    with pytest.raises(ProtocolChangedError) as raised:
        parse_order_details(json.loads(broken), asked=(FIRST,), observed_at=WHEN)
    assert "выдают товар" in str(raised.value)


def test_the_request_carries_no_csrf_token() -> None:
    """ГЛАВНАЯ ПРОВЕРКА ШЕСТАЯ: защитного токена здесь нет вовсе.

    Это отличает семейство /api/ от форм. Отправить его сюда значило бы сходить
    за ним на страницу - лишний запрос ради поля, которого точка не ждёт.

    Возвращает:
        None
    """
    script = _Scripted()
    script.run(_engine().read_order_details((FIRST,)))

    sent = script.queries[0]
    assert sent.path == ORDER_DETAILS_PATH
    assert isinstance(sent.payload, dict)
    assert "csrf_token" not in sent.payload
    assert sent.payload["order_uids"] == [FIRST]
    assert sent.payload["include"] == ["details", "users"]


@pytest.mark.parametrize(
    "ids",
    [
        (),
        tuple(f"ID{one:06d}" for one in range(BATCH_MAX + 1)),
        (FIRST, FIRST),
        ("",),
        ("ZVV/../",),
    ],
)
def test_a_bad_batch_is_refused_before_the_network(ids: tuple[str, ...]) -> None:
    """Требует отказа ДО сети на непригодной пачке.

    Верхняя граница взята у стороннего источника, и откуда она там - неизвестно.
    Нарушать неизвестное ограничение дороже, чем сделать два запроса.

    Повторы отвергаются отдельно: ответ приходит словарём по идентификатору, и
    повтор занял бы место в пачке, ничего не добавив.

    Аргументы:
        ids (tuple[str, ...]): Непригодная пачка.

    Возвращает:
        None
    """
    script = _Scripted()
    with pytest.raises(ValidationError):
        script.run(_engine().read_order_details(ids))
    assert script.queries == []


def test_a_full_batch_is_allowed() -> None:
    """Обратная половина: пачка ровно в предел проходит.

    Возвращает:
        None
    """
    ids = tuple(f"ID{one:06d}" for one in range(BATCH_MAX))
    script = _Scripted(json.dumps({"status": "SUCCESS", "data": {}}))
    batch = script.run(_engine().read_order_details(ids))

    assert isinstance(batch, OrderDetailsBatch)
    assert len(script.queries) == 1
    assert batch.missing == ids


def test_the_capability_is_marked_after_a_parsed_answer() -> None:
    """Требует выставлять состояние по разобранному ответу.

    Возвращает:
        None
    """
    engine = _engine()
    _Scripted().run(engine.read_order_details((FIRST,)))
    assert engine._state.capabilities[Capability.ORDERS_DETAILS] is CapabilityState.SUPPORTED


def test_a_body_that_is_not_json_is_refused() -> None:
    """Требует отвергать неразобравшееся тело.

    Возвращает:
        None
    """
    script = _Scripted("<html>нет</html>")
    with pytest.raises(ProtocolChangedError):
        script.run(_engine().read_order_details((FIRST,)))


@pytest.mark.parametrize("raw", [True, False])
def test_a_boolean_amount_is_not_read_as_a_number(raw: bool) -> None:
    """Требует, чтобы логическое не сошло за сумму.

    Истина в Python - это единица. Сумма True прочиталась бы как «один рубль», и
    вызывающий, показывающий продавцу цену заказа, напечатал бы её без единого
    признака беды.

    Аргументы:
        raw (bool): Логическое вместо числа.

    Возвращает:
        None
    """
    batch = parse_order_details(
        json.loads(_answer(_record(amount=raw))), asked=(FIRST,), observed_at=WHEN
    )
    amount = batch.found[0].amount

    assert amount.or_none() is None, f"логическое {raw} прочиталось как сумма"
    assert "not_a_value" in (amount.reason or "")
