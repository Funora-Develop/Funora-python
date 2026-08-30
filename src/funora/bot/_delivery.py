"""Автовыдача товара: от события о заказе до сообщения покупателю.

ЧЕРТА, ВОКРУГ КОТОРОЙ ВСЁ УСТРОЕНО. Выдача идёт сама только там, где ВСЕ условия
выполнены положительно. Любое незнание - не повод действовать осторожнее, а
повод не действовать: вопрос уходит человеку.

Условий пять, и каждое отвечает на свой вопрос:

1. Состояние заказа прочитано и равно «оплачен». Не «не закрыт»: состояний
   известно два из скольких-то, и заказ в возврате даёт ненаблюдённое значение.
   Правило «если не закрыт, значит ждёт выдачи» на нём и ломается.
2. По этому заказу ещё не выдавали. Реестр выдач переживает перезапуск.
3. Чтение списка продаж полно. Неполное чтение означает, что часть строк не
   разобрана, а не что их нет.
4. Заказ сопоставлен с ОДНИМ собственным лотом. Двусмысленность - отказ.
5. Для этого лота задан товар.

Что здесь СОЗНАТЕЛЬНО не делается: выдача не отменяется. Состояние возврата не
наблюдалось ни разу, отменять нечем, и механизм односторонний. Продавец, которому
это важно, обязан знать заранее.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from .._delivered import Delivery, DeliveryLedger
from .._matching import match_offer
from .._orders import OrderListEntry
from .._own_lots import OwnLot
from .._result import Completeness
from ..extraction import OrderStatus
from ._outbox import SendTicket

__all__ = ["DeliveryPlan", "DeliveryDecision", "AutoDelivery", "HOLD_FOR_OPERATOR"]

_log = logging.getLogger("funora.bot.delivery")

#: Решение: выдать нечего, вопрос уходит человеку.
HOLD_FOR_OPERATOR: Final[str] = "hold_for_operator"


@dataclass(frozen=True, slots=True)
class DeliveryDecision:
    """Что решено по заказу.

    Attributes:
        order_id (str): Заказ.
        will_deliver (bool): Выдаётся ли сам.
        reason (str): Машиночитаемая причина решения.
        offer_id (str): Опознанное предложение. Пустая строка, если не
            опознано.
        candidates (tuple[str, ...]): Подошедшие предложения. Больше одного
            означает, что различить их нечем.
    """

    order_id: str
    will_deliver: bool
    reason: str
    offer_id: str = ""
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DeliveryPlan:
    """Что и кому выдавать.

    Attributes:
        goods (dict[str, str]): Товар по идентификатору предложения. Что именно
            отправить покупателю, когда заказ опознан.
        chat_of (Callable[[str], str]): Как узнать узел диалога по заказу.
            Отдельной функцией, а не полем: узел лежит на странице заказа, идти
            за ним - запрос, и делать этот запрос стоит только тогда, когда всё
            остальное сошлось.
    """

    goods: dict[str, str]
    chat_of: Callable[[str], str]


class AutoDelivery:
    """Автовыдача: решает по заказу и кладёт задание в очередь.

    Сама ничего не отправляет и в сеть не ходит. Отправку выполняет очередь
    исходящих, а значит - тот же поток, что ведёт наблюдение, и с теми же
    пределами.

    Args:
        plan (DeliveryPlan): Что и кому выдавать.
        ledger (DeliveryLedger): Реестр уже выданного.
        send (Callable[[str, str, str], SendTicket]): Как поставить отправку в
            очередь: узел, текст, ключ идемпотентности.
        on_hold (Callable[[DeliveryDecision], None] | None): Что делать с
            заказом, который сам не выдаётся. Без него такой заказ виден только
            в журнале.
    """

    __slots__ = ("_plan", "_ledger", "_send", "_on_hold", "_decisions")

    def __init__(
        self,
        plan: DeliveryPlan,
        ledger: DeliveryLedger,
        send: Callable[[str, str, str], SendTicket],
        on_hold: Callable[[DeliveryDecision], None] | None = None,
    ) -> None:
        self._plan = plan
        self._ledger = ledger
        self._send = send
        self._on_hold = on_hold
        self._decisions: list[DeliveryDecision] = []

    @property
    def decisions(self) -> tuple[DeliveryDecision, ...]:
        """Решения, принятые с начала работы.

        Returns:
            tuple[DeliveryDecision, ...]: Все решения по порядку.
        """
        return tuple(self._decisions)

    def decide(
        self,
        order: OrderListEntry,
        lots: tuple[OwnLot, ...],
        *,
        page_completeness: Completeness,
    ) -> DeliveryDecision:
        """Решает, выдавать ли по заказу, и НИЧЕГО не отправляет.

        Метод чистый нарочно: решение проверяется без сети и без очереди, а
        решение здесь - самое дорогое место всего механизма.

        Args:
            order (OrderListEntry): Строка списка продаж.
            lots (tuple[OwnLot, ...]): Собственные лоты раздела.
            page_completeness (Completeness): Полнота чтения списка продаж.

        Returns:
            DeliveryDecision: Что решено и почему.
        """
        decision = self._decide(order, lots, page_completeness=page_completeness)
        self._decisions.append(decision)
        return decision

    def _decide(
        self,
        order: OrderListEntry,
        lots: tuple[OwnLot, ...],
        *,
        page_completeness: Completeness,
    ) -> DeliveryDecision:
        """Проверяет пять условий по порядку.

        Порядок дешёвых проверок вперёд: сперва то, что не требует разбора
        текста.

        Args:
            order (OrderListEntry): Строка списка продаж.
            lots (tuple[OwnLot, ...]): Собственные лоты раздела.
            page_completeness (Completeness): Полнота чтения списка продаж.

        Returns:
            DeliveryDecision: Что решено и почему.
        """
        if self._ledger.seen(order.order_id):
            return DeliveryDecision(order.order_id, False, "already_delivered")

        if page_completeness is not Completeness.COMPLETE:
            # Неполное чтение означает, что часть строк не разобрана. Выдавать
            # по такому списку значило бы решать по данным, которых нет.
            return DeliveryDecision(order.order_id, False, "orders_page_incomplete")

        if not order.status.is_observed:
            # Состояние не прочитано. Это НЕ «ещё не закрыт»: заказ в возврате
            # или споре даёт ровно такое значение.
            return DeliveryDecision(order.order_id, False, "status_not_observed")

        if order.status.value is not OrderStatus.PAID:
            return DeliveryDecision(order.order_id, False, "status_not_paid")

        matched = match_offer(
            order.description_text,
            {one.offer_id.value: one.description_text for one in lots if one.offer_id.is_observed},
        )
        if not matched.offer_id.is_observed:
            return DeliveryDecision(
                order.order_id,
                False,
                str(matched.offer_id.reason),
                candidates=matched.candidates,
            )

        offer_id = matched.offer_id.value
        if offer_id not in self._plan.goods:
            return DeliveryDecision(order.order_id, False, "no_goods_for_offer", offer_id=offer_id)

        return DeliveryDecision(
            order.order_id,
            True,
            "ready",
            offer_id=offer_id,
            candidates=matched.candidates,
        )

    def handle(
        self,
        order: OrderListEntry,
        lots: tuple[OwnLot, ...],
        *,
        page_completeness: Completeness,
    ) -> SendTicket | None:
        """Решает и, если можно, ставит выдачу в очередь.

        Запись в реестр идёт ВПЕРЕДИ постановки в очередь. Порядок тот же и по
        той же причине, что у реестра отправок: «запишем, когда подтвердится»
        означает не записать ровно те выдачи, которые могли уйти.

        Args:
            order (OrderListEntry): Строка списка продаж.
            lots (tuple[OwnLot, ...]): Собственные лоты раздела.
            page_completeness (Completeness): Полнота чтения списка продаж.

        Returns:
            SendTicket | None: Квитанция отправки либо None, если выдача не
            состоялась.
        """
        decision = self.decide(order, lots, page_completeness=page_completeness)
        if not decision.will_deliver:
            _log.info(
                "заказ %s не выдаётся сам: %s%s",
                decision.order_id,
                decision.reason,
                f", кандидаты {list(decision.candidates)}" if decision.candidates else "",
            )
            if self._on_hold is not None:
                self._on_hold(decision)
            return None

        self._ledger.record(
            Delivery(
                order_id=decision.order_id,
                offer_id=decision.offer_id,
                at_ms=int(datetime.now(UTC).timestamp() * 1000),
                outcome="queued",
            )
        )
        return self._send(
            self._plan.chat_of(decision.order_id),
            self._plan.goods[decision.offer_id],
            # Ключ идемпотентности - сам заказ. По одному заказу выдают один
            # раз, и ключ обязан говорить именно это, а не «эта попытка».
            f"delivery:{decision.order_id}",
        )
