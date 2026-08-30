"""Проверки автовыдачи товара.

ЗДЕСЬ САМАЯ ВЫСОКАЯ ЦЕНА ОШИБКИ ВО ВСЁМ ПРОЕКТЕ, и она несимметрична. Лишний
отказ - продавец выдаст руками. Лишняя выдача - чужой товар покупателю, и
отменить её нечем: состояние возврата не наблюдалось ни разу.

Поэтому набор устроен вокруг ОТКАЗОВ. Проверок, что выдача состоялась, здесь
две; проверок, что она НЕ состоялась при каждом виде незнания, - вдвое больше.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

import pytest

from funora._delivered import Delivery, DeliveryLedger
from funora._matching import match_offer, normalized_for_match
from funora._observed import Confidence, Observed
from funora._orders import OrderListEntry
from funora._own_lots import OwnLot
from funora._result import Completeness
from funora.bot._delivery import AutoDelivery, DeliveryDecision, DeliveryPlan
from funora.bot._outbox import SendCommand, SendTicket
from funora.extraction import OrderStatus

WHEN: Final[datetime] = datetime(2026, 8, 30, tzinfo=UTC)


def _order(
    *,
    order_id: str = "A1",
    status: OrderStatus | None = OrderStatus.PAID,
    description: str | None = "Аккаунт Steam с играми",
) -> OrderListEntry:
    """Собирает строку списка продаж.

    Аргументы:
        order_id (str): Идентификатор заказа.
        status (OrderStatus | None): Состояние либо None для ненаблюдённого.
        description (str | None): Описание либо None для ненаблюдённого.

    Возвращает:
        OrderListEntry: Строка списка.
    """
    missing: Observed[str] = Observed.missing("not_checked")
    return OrderListEntry(
        order_id=order_id,
        href="/orders/A1/",
        row_index=0,
        status=(
            Observed.present(status, Confidence.OBSERVED)  # type: ignore[arg-type]
            if status is not None
            else Observed.missing("status_carrier_not_mapped")
        ),
        status_carrier=missing,
        order_number_text=missing,
        description_text=(Observed.present(description) if description is not None else missing),
        category_text=missing,
        counterparty_name=missing,
        counterparty_href=missing,
        counterparty_online=Observed.missing("not_checked"),
        price=None,
        amount_text=missing,
        currency_symbol_text=missing,
        time_text=missing,
        time_ago_text=missing,
    )


def _lot(offer_id: str, description: str) -> OwnLot:
    """Собирает собственный лот.

    Аргументы:
        offer_id (str): Идентификатор предложения.
        description (str): Описание лота.

    Возвращает:
        OwnLot: Лот.
    """
    missing: Observed[str] = Observed.missing("not_checked")
    return OwnLot(
        offer_id=Observed.present(offer_id),
        offer_href=missing,
        server_text=missing,
        description_text=Observed.present(description),
        price_text=missing,
        currency_symbol_text=missing,
        sort_value=missing,
        row_index=0,
    )


def _delivery(*, goods: dict[str, str] | None = None) -> tuple[AutoDelivery, list[SendCommand]]:
    """Собирает автовыдачу с подставной очередью.

    Аргументы:
        goods (dict[str, str] | None): Товар по предложению.

    Возвращает:
        tuple[AutoDelivery, list[SendCommand]]: Автовыдача и перечень заданий.
    """
    queued: list[SendCommand] = []

    def send(chat_id: str, text: str, key: str) -> SendTicket:
        """Изображает постановку в очередь.

        Возвращает:
            SendTicket: Квитанция.
        """
        command = SendCommand(chat_id=chat_id, text=text, idempotency_key=key)
        queued.append(command)
        return SendTicket(command=command)

    plan = DeliveryPlan(
        goods=goods if goods is not None else {"L2": "вот ваш товар"},
        chat_of=lambda order_id: f"chat-{order_id}",
    )
    return AutoDelivery(plan, DeliveryLedger(), send), queued


LOTS: Final[tuple[OwnLot, ...]] = (
    _lot("L1", "Аккаунт Steam"),
    _lot("L2", "Аккаунт Steam с играми"),
    _lot("L3", "Ключ Origin"),
)


def test_a_paid_order_matched_to_one_lot_is_delivered() -> None:
    """Проверяет положительный случай: всё сошлось, товар уходит.

    Возвращает:
        None
    """
    delivery, queued = _delivery()
    ticket = delivery.handle(_order(), LOTS, page_completeness=Completeness.COMPLETE)

    assert ticket is not None, "заказ не выдан при всех сошедшихся условиях"
    assert len(queued) == 1
    assert queued[0].text == "вот ваш товар"
    assert queued[0].chat_id == "chat-A1"
    assert queued[0].idempotency_key == "delivery:A1", (
        "ключ идемпотентности обязан говорить о ЗАКАЗЕ: по одному заказу выдают один раз"
    )


def test_an_unread_status_never_delivers() -> None:
    """Требует НЕ выдавать, когда состояние заказа не прочитано.

    Это и есть заказ в возврате, споре или отказе: состояний известно два, а
    бывает их больше, и третье даёт ненаблюдённое значение.

    Возвращает:
        None
    """
    delivery, queued = _delivery()
    decision = delivery.decide(_order(status=None), LOTS, page_completeness=Completeness.COMPLETE)

    assert decision.will_deliver is False
    assert decision.reason == "status_not_observed"
    assert not queued


def test_a_closed_order_is_not_delivered() -> None:
    """Требует НЕ выдавать по закрытому заказу.

    Возвращает:
        None
    """
    delivery, _ = _delivery()
    decision = delivery.decide(
        _order(status=OrderStatus.CLOSED), LOTS, page_completeness=Completeness.COMPLETE
    )

    assert decision.will_deliver is False
    assert decision.reason == "status_not_paid"


def test_an_incomplete_page_never_delivers() -> None:
    """Требует НЕ выдавать по неполно прочитанному списку продаж.

    Неполное чтение означает, что часть строк не разобрана. Выдавать по такому
    списку значило бы решать по данным, которых нет.

    Возвращает:
        None
    """
    delivery, queued = _delivery()
    decision = delivery.decide(_order(), LOTS, page_completeness=Completeness.PARTIAL)

    assert decision.will_deliver is False
    assert decision.reason == "orders_page_incomplete"
    assert not queued


def test_two_indistinguishable_lots_stop_the_delivery() -> None:
    """Требует ОТКАЗА, а не выбора, когда подошли два несвязанных лота.

    Это главное отличие от готовых решений той же задачи: они берут при
    нескольких совпадениях самое длинное описание. Для выдачи товара это выбор
    наугад, и цена ошибки - чужой товар покупателю.

    Возвращает:
        None
    """
    delivery, queued = _delivery(goods={"L1": "первый", "L2": "второй"})
    forked = (_lot("L1", "Аккаунт Steam"), _lot("L2", "сто часов"))

    decision = delivery.decide(
        _order(description="Аккаунт Steam, сто часов"),
        forked,
        page_completeness=Completeness.COMPLETE,
    )

    assert decision.will_deliver is False
    assert decision.reason == "offer_match_ambiguous"
    assert decision.candidates == ("L1", "L2"), (
        "перечень кандидатов пуст: человеку не видно, какие лоты неразличимы"
    )
    assert not queued


def test_a_chain_of_nested_lots_takes_the_longest() -> None:
    """Разрешает единственный случай нескольких совпадений: вложенную цепочку.

    Цепочка означает, что лоты описаны уточнением друг друга, и длиннейший
    описывает точнее. Это не выбор наугад: развилки здесь нет.

    Возвращает:
        None
    """
    delivery, queued = _delivery()
    decision = delivery.decide(_order(), LOTS, page_completeness=Completeness.COMPLETE)

    assert decision.will_deliver is True
    assert decision.offer_id == "L2", "выбран не самый точный лот цепочки"
    assert decision.candidates == ("L1", "L2")


def test_an_order_without_a_description_is_not_matched() -> None:
    """Требует отказа, когда описания заказа нет вовсе.

    Возвращает:
        None
    """
    delivery, _ = _delivery()
    decision = delivery.decide(
        _order(description=None), LOTS, page_completeness=Completeness.COMPLETE
    )

    assert decision.will_deliver is False
    assert decision.reason == "order_description_not_observed"


def test_an_offer_without_goods_is_not_delivered() -> None:
    """Требует отказа, когда лот опознан, а товара для него не задано.

    Возвращает:
        None
    """
    delivery, queued = _delivery(goods={"L9": "чужой товар"})
    decision = delivery.decide(_order(), LOTS, page_completeness=Completeness.COMPLETE)

    assert decision.will_deliver is False
    assert decision.reason == "no_goods_for_offer"
    assert decision.offer_id == "L2", "лот опознан, и это надо сказать человеку"
    assert not queued


def test_the_same_order_is_never_delivered_twice() -> None:
    """Требует, чтобы по одному заказу выдали ровно один раз.

    Возвращает:
        None
    """
    delivery, queued = _delivery()
    first = delivery.handle(_order(), LOTS, page_completeness=Completeness.COMPLETE)
    second = delivery.handle(_order(), LOTS, page_completeness=Completeness.COMPLETE)

    assert first is not None
    assert second is None, "выдали дважды по одному заказу"
    assert len(queued) == 1
    assert delivery.decisions[-1].reason == "already_delivered"


def test_the_ledger_is_written_before_the_send() -> None:
    """Требует записывать выдачу ВПЕРЕДИ постановки в очередь.

    «Запишем, когда подтвердится» означает не записать ровно те выдачи, которые
    могли уйти. Проверяется тем, что запись видна уже изнутри отправки.

    Возвращает:
        None
    """
    ledger = DeliveryLedger()
    seen: list[bool] = []

    def send(chat_id: str, text: str, key: str) -> SendTicket:
        """Смотрит, есть ли запись о выдаче в момент отправки.

        Возвращает:
            SendTicket: Квитанция.
        """
        seen.append(ledger.seen("A1"))
        return SendTicket(command=SendCommand(chat_id=chat_id, text=text, idempotency_key=key))

    plan = DeliveryPlan(goods={"L2": "товар"}, chat_of=lambda one: "chat")
    AutoDelivery(plan, ledger, send).handle(_order(), LOTS, page_completeness=Completeness.COMPLETE)

    assert seen == [True], (
        "в момент отправки записи о выдаче ещё не было: перезапуск между "
        "постановкой и записью выдал бы товар второй раз"
    )


def test_a_held_order_reaches_the_operator() -> None:
    """Требует, чтобы невыданный заказ доходил до человека.

    Иначе он виден только в журнале, а журнал читают после происшествия.

    Возвращает:
        None
    """
    held: list[DeliveryDecision] = []
    plan = DeliveryPlan(goods={}, chat_of=lambda one: "chat")
    delivery = AutoDelivery(
        plan,
        DeliveryLedger(),
        lambda chat, text, key: SendTicket(
            command=SendCommand(chat_id=chat, text=text, idempotency_key=key)
        ),
        on_hold=held.append,
    )

    delivery.handle(_order(), LOTS, page_completeness=Completeness.COMPLETE)

    assert len(held) == 1
    assert held[0].reason == "no_goods_for_offer"


def test_the_ledger_survives_a_restart() -> None:
    """Требует, чтобы реестр выдач переживал перезапуск.

    Реестр в памяти означает повторную выдачу при каждом перезапуске процесса.

    Возвращает:
        None
    """
    ledger = DeliveryLedger()
    ledger.record(Delivery(order_id="A1", offer_id="L2", at_ms=1, outcome="queued"))

    other = DeliveryLedger()
    other.restore(ledger.snapshot())

    assert other.seen("A1") is True
    record = other.get("A1")
    assert record is not None and record.offer_id == "L2"


def test_a_record_without_its_key_is_dropped_not_guessed() -> None:
    """Требует пропускать неполную запись, а не достраивать её умолчанием.

    Достроенная запись сказала бы «выдавали», не зная чего: покупатель не
    получит товар, а реестр будет уверен, что получил.

    Возвращает:
        None
    """
    ledger = DeliveryLedger()
    ledger.restore({"done": [{"offer_id": "L2", "at_ms": 1}, {"order_id": "A2"}]})

    assert len(ledger) == 0, "неполная запись достроена вместо того, чтобы выпасть"


def test_the_first_record_of_an_order_wins() -> None:
    """Требует не затирать первую запись о выдаче второй.

    Вторая означала бы, что мы выдали дважды, и затирать след первой значило бы
    прятать именно то, ради чего реестр заведён.

    Возвращает:
        None
    """
    ledger = DeliveryLedger()
    ledger.record(Delivery(order_id="A1", offer_id="L2", at_ms=1, outcome="queued"))
    ledger.record(Delivery(order_id="A1", offer_id="L9", at_ms=2, outcome="queued"))

    record = ledger.get("A1")
    assert record is not None and record.offer_id == "L2"


@pytest.mark.parametrize(
    ("order_text", "lot_text"),
    [
        ("Аккаунт  Steam", "Аккаунт Steam"),
        ("АККАУНТ STEAM", "аккаунт steam"),
        ("Аккаунт\nSteam", "Аккаунт Steam"),
    ],
)
def test_matching_survives_spacing_and_case(order_text: str, lot_text: str) -> None:
    """Требует, чтобы регистр и пробелы сопоставлению не мешали.

    Площадка показывает текст в разметке, и переносы строк в нём случайны.

    Аргументы:
        order_text (str): Описание заказа.
        lot_text (str): Описание лота.

    Возвращает:
        None
    """
    outcome = match_offer(Observed.present(order_text), {"L1": Observed.present(lot_text)})
    assert outcome.offer_id.is_observed, f"{order_text!r} не совпало с {lot_text!r}"


def test_the_match_is_never_claimed_as_observed() -> None:
    """Требует, чтобы опознание лота всегда было ВЫВЕДЕННЫМ.

    Правило опирается на текст, часть которого пишет покупатель. Объявить такое
    наблюдением значило бы обещать вызывающему то, чего разбор не знает.

    Возвращает:
        None
    """
    outcome = match_offer(
        Observed.present("Аккаунт Steam!"), {"L1": Observed.present("Аккаунт Steam")}
    )

    assert outcome.offer_id.is_observed
    assert outcome.offer_id.confidence is Confidence.INFERRED, (
        f"опознание объявлено как {outcome.offer_id.confidence}: правило "
        "опирается на чужой текст и наблюдением быть не может"
    )


def test_an_empty_lot_description_matches_nothing() -> None:
    """Требует, чтобы лот с пустым описанием не совпадал ни с чем.

    Пустая строка входит в любую: один такой лот совпал бы разом со всеми
    заказами, и выдача пошла бы по первому попавшемуся.

    Возвращает:
        None
    """
    outcome = match_offer(
        Observed.present("любой заказ"), {"L1": Observed.present("   "), "L2": Observed.empty("")}
    )
    assert not outcome.offer_id.is_observed
    assert outcome.offer_id.reason == "no_offer_matched"


def test_the_order_text_is_the_haystack_not_the_needle() -> None:
    """Требует искать описание ЛОТА в описании ЗАКАЗА, а не наоборот.

    Обратное вхождение означало бы, что лот описан подробнее заказа, - а это
    признак не того лота.

    Возвращает:
        None
    """
    outcome = match_offer(
        Observed.present("Steam"), {"L1": Observed.present("Аккаунт Steam с играми")}
    )
    assert not outcome.offer_id.is_observed, "лот подошёл по обратному вхождению"


def test_normalization_is_shared_with_nothing_surprising() -> None:
    """Закрепляет, что именно делает нормализация.

    Возвращает:
        None
    """
    assert normalized_for_match("  Аккаунт\n\tSTEAM  ") == "аккаунт steam"
