"""Слой бота: наблюдение, реакция и отправка из чужого потока.

Подпакет, а не отдельный дистрибутив: он держится на внутренностях ядра, а
внутренности между дистрибутивами не тянут.

ЧТО ЗДЕСЬ ЕСТЬ И ЧЕГО НЕТ. Есть одно: единственный порядок, при котором
посторонний поток - скажем, обработчик телеграм-бота - может попросить об
отправке, не портя счёт ограничителя исходящих. Задание кладётся в очередь,
разбирает её тот же поток, что ведёт наблюдение.

АВТОВЫДАЧА ЗДЕСЬ ЕСТЬ, и устроена она вокруг одной черты: выдача идёт сама
только там, где все условия выполнены ПОЛОЖИТЕЛЬНО. Любое незнание - не повод
действовать осторожнее, а повод не действовать: вопрос уходит человеку.

Идентификатора предложения в заказе нет, и опознаётся лот по описанию - тому
самому свободному тексту, часть которого дописывает покупатель. Поэтому
уверенность у опознания всегда выведенная, а двусмысленность - отказ, а не
выбор наугад.

Управления лотами и рассылки нет. Операций записи над лотами никто не
наблюдал, а пределы отправки объявлены и соблюдаются. Подробности - в
docs/guide/bot.md.
"""

from __future__ import annotations

from .._delivered import Delivery, DeliveryLedger
from ._delivery import HOLD_FOR_OPERATOR, AutoDelivery, DeliveryDecision, DeliveryPlan
from ._outbox import MAX_PENDING, Outbox, SendCommand, SendTicket
from ._runtime import MAX_SENDS_PER_IDLE, Bot
from ._spool import MAX_SPOOLED, Spool, SpoolEntry, SpoolOutcome

__all__ = [
    "Bot",
    "Outbox",
    "SendCommand",
    "SendTicket",
    "MAX_PENDING",
    "MAX_SENDS_PER_IDLE",
    # очередь между процессами
    "Spool",
    "SpoolEntry",
    "SpoolOutcome",
    "MAX_SPOOLED",
    # автовыдача
    "AutoDelivery",
    "DeliveryPlan",
    "DeliveryDecision",
    "DeliveryLedger",
    "Delivery",
    "HOLD_FOR_OPERATOR",
]
