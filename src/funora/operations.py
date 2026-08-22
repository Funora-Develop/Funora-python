"""Операции служб и их свойства.

Файл порождён из спецификации, править его руками нельзя: правка исчезнет при
следующей сборке. Источник - spec/services/*.yaml в репозитории Funora-spec.
Перестроить: python tools/codegen.py

Безопасность операции - половина нормативного входа решения о повторе.
Вторая половина, класс ошибки, порождается из errors.yaml. Пока эта
половина была рукописной, смена безопасности в спецификации не
отражалась нигде - ни в коде, ни в проверке.

Повторить небезопасную операцию значит выполнить её дважды: отправить
покупателю второе сообщение, поднять лот второй раз, списать деньги
второй раз.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

__all__ = ["Safety", "Operation", "OPERATIONS"]


class Safety(StrEnum):
    """Безопасность операции при повторе.

    safe - повтор ничего не меняет: операция только читает.
    idempotent - повтор с тем же ключом идемпотентности даёт тот же
    результат.
    unsafe - повтор выполняет действие дважды.
    """

    IDEMPOTENT = "idempotent"
    SAFE = "safe"
    UNSAFE = "unsafe"


@dataclass(frozen=True, slots=True)
class Operation:
    """Свойства одной операции службы.

    Attributes:
        name (str): Идентификатор операции.
        capability (str): Возможность, которой операция требует.
        safety (Safety): Безопасность при повторе.
        request_class (str): Класс запроса для бюджета.
        returns (str): Тип результата, как объявлен спецификацией.
    """

    name: str
    capability: str
    safety: Safety
    request_class: str
    returns: str


#: Операции служб по идентификатору.
OPERATIONS: Final[dict[str, Operation]] = {
    "account.get": Operation(
        name="account.get",
        capability="account.profile",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="Account",
    ),
    "account.refresh": Operation(
        name="account.refresh",
        capability="account.profile",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="Account",
    ),
    "capabilities": Operation(
        name="capabilities",
        capability="account.profile",
        safety=Safety.SAFE,
        request_class="poll",
        returns="CapabilityProfile",
    ),
    "catalog.categories": Operation(
        name="catalog.categories",
        capability="catalog.categories",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="Category[]",
    ),
    "catalog.field_schema": Operation(
        name="catalog.field_schema",
        capability="catalog.field_schema",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="FieldSchema",
    ),
    "chats.history": Operation(
        name="chats.history",
        capability="chats.history",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="Thread",
    ),
    "chats.history_before": Operation(
        name="chats.history_before",
        capability="chats.history_pagination",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="Message[]",
    ),
    "chats.list": Operation(
        name="chats.list",
        capability="chats.list",
        safety=Safety.SAFE,
        request_class="poll",
        returns="ChatListEntry[]",
    ),
    "chats.mark_read": Operation(
        name="chats.mark_read",
        capability="chats.mark_read",
        safety=Safety.IDEMPOTENT,
        request_class="automation",
        returns="void",
    ),
    "chats.send_image": Operation(
        name="chats.send_image",
        capability="chats.send_image",
        safety=Safety.UNSAFE,
        request_class="interactive",
        returns="Message",
    ),
    "chats.send_text": Operation(
        name="chats.send_text",
        capability="chats.send_text",
        safety=Safety.UNSAFE,
        request_class="interactive",
        returns="Message",
    ),
    "lots.activate": Operation(
        name="lots.activate",
        capability="lots.activate",
        safety=Safety.IDEMPOTENT,
        request_class="automation",
        returns="Lot",
    ),
    "lots.deactivate": Operation(
        name="lots.deactivate",
        capability="lots.deactivate",
        safety=Safety.IDEMPOTENT,
        request_class="automation",
        returns="Lot",
    ),
    "lots.list_own": Operation(
        name="lots.list_own",
        capability="lots.list_own",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="Lot[]",
    ),
    "lots.promote": Operation(
        name="lots.promote",
        capability="lots.promote",
        safety=Safety.UNSAFE,
        request_class="automation",
        returns="RaiseResult",
    ),
    "lots.update_price": Operation(
        name="lots.update_price",
        capability="lots.update_price",
        safety=Safety.IDEMPOTENT,
        request_class="automation",
        returns="Lot",
    ),
    "market.offers": Operation(
        name="market.offers",
        capability="market.offers",
        safety=Safety.SAFE,
        request_class="monitoring",
        returns="Offer[]",
    ),
    "market.snapshot": Operation(
        name="market.snapshot",
        capability="market.snapshot",
        safety=Safety.SAFE,
        request_class="monitoring",
        returns="MarketSnapshot",
    ),
    "orders.get": Operation(
        name="orders.get",
        capability="orders.get",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="Order",
    ),
    "orders.list": Operation(
        name="orders.list",
        capability="orders.list",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="OrderListEntry[]",
    ),
    "session.health": Operation(
        name="session.health",
        capability="account.profile",
        safety=Safety.SAFE,
        request_class="poll",
        returns="SessionHealth",
    ),
}
