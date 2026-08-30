r"""Операции служб и их свойства.

Файл порождён из спецификации, править его руками нельзя: правка исчезнет при
следующей сборке. Источник - spec/services/*.yaml в репозитории Funora-spec.
Перестроить: .venv\Scripts\python.exe tools/codegen.py

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
        errors (tuple[str, ...]): Устойчивые идентификаторы ошибок,
            которыми операция вправе завершиться. Ровно то, что
            вызывающий выписывает в except.

            Перечень объявлен спецификацией на каждую операцию и до
            сих пор до пакета не доходил: генератор принимал ключ
            errors и выбрасывал его. Расхождение между обещанным и
            возбуждаемым не ловило ничто, и вызывающий, выписавший
            except по контракту, ловил не всё.
        audit (str): Что операция обязана сохранить до того, как
            выполнится. Пустая строка означает, что аудита ей не
            предписано.

            Ключ принимался и выбрасывался: спецификация требовала
            аудита, пакет о требовании не знал, и связать отказ
            операции с объявлением было нечем.
        audit_fail_closed (bool): Отказывает ли операция, когда
            сохранять некуда. Ложь означает либо отсутствие аудита,
            либо аудит, которым разрешено пренебречь.
    """

    name: str
    capability: str
    safety: Safety
    request_class: str
    returns: str
    errors: tuple[str, ...]
    audit: str = ""
    audit_fail_closed: bool = False


#: Операции служб по идентификатору.
OPERATIONS: Final[dict[str, Operation]] = {
    "account.balance": Operation(
        name="account.balance",
        capability="account.balance",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="BalancePage",
        errors=(
            "funora.auth.session_expired",
            "funora.protocol.changed",
            "funora.transport",
        ),
    ),
    "account.get": Operation(
        name="account.get",
        capability="account.profile",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="Account",
        errors=(
            "funora.auth.session_expired",
            "funora.protocol.changed",
            "funora.transport",
        ),
    ),
    "account.refresh": Operation(
        name="account.refresh",
        capability="account.profile",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="Account",
        errors=(
            "funora.auth.session_expired",
            "funora.protocol.changed",
            "funora.transport",
        ),
    ),
    "capabilities": Operation(
        name="capabilities",
        capability="account.profile",
        safety=Safety.SAFE,
        request_class="poll",
        returns="CapabilityProfile",
        errors=(),
    ),
    "catalog.categories": Operation(
        name="catalog.categories",
        capability="catalog.categories",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="CatalogPage",
        errors=(
            "funora.protocol.changed",
            "funora.transport",
        ),
    ),
    "catalog.field_schema": Operation(
        name="catalog.field_schema",
        capability="catalog.field_schema",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="FieldSchema",
        errors=(
            "funora.capability.unsupported",
            "funora.protocol.changed",
            "funora.transport",
        ),
    ),
    "chats.history": Operation(
        name="chats.history",
        capability="chats.history",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="Thread",
        errors=(
            "funora.domain.not_found",
            "funora.auth.session_expired",
            "funora.protocol.changed",
            "funora.protocol.unexpected_response",
            "funora.transport",
        ),
    ),
    "chats.history_before": Operation(
        name="chats.history_before",
        capability="chats.history_pagination",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="Message[]",
        errors=(
            "funora.capability.unsupported",
            "funora.state.cursor_incompatible",
            "funora.transport",
        ),
    ),
    "chats.list": Operation(
        name="chats.list",
        capability="chats.list",
        safety=Safety.SAFE,
        request_class="poll",
        returns="ChatsPage",
        errors=(
            "funora.auth.session_expired",
            "funora.protocol.changed",
            "funora.protocol.unexpected_response",
            "funora.transport",
        ),
    ),
    "chats.mark_read": Operation(
        name="chats.mark_read",
        capability="chats.mark_read",
        safety=Safety.IDEMPOTENT,
        request_class="automation",
        returns="void",
        errors=(
            "funora.capability.unsupported",
            "funora.transport",
        ),
    ),
    "chats.send_image": Operation(
        name="chats.send_image",
        capability="chats.send_image",
        safety=Safety.UNSAFE,
        request_class="interactive",
        returns="SendResult",
        errors=(
            "funora.capability.unsupported",
            "funora.budget.exhausted",
            "funora.transport",
        ),
    ),
    "chats.send_text": Operation(
        name="chats.send_text",
        capability="chats.send_text",
        safety=Safety.UNSAFE,
        request_class="interactive",
        returns="SendResult",
        errors=(
            "funora.domain.not_found",
            "funora.budget.exhausted",
            "funora.auth.session_expired",
            "funora.transport.timeout",
            "funora.transport",
        ),
    ),
    "lots.activate": Operation(
        name="lots.activate",
        capability="lots.activate",
        safety=Safety.IDEMPOTENT,
        request_class="automation",
        returns="Lot",
        errors=(
            "funora.capability.unsupported",
            "funora.domain.not_found",
            "funora.auth.session_expired",
            "funora.transport",
        ),
    ),
    "lots.deactivate": Operation(
        name="lots.deactivate",
        capability="lots.deactivate",
        safety=Safety.IDEMPOTENT,
        request_class="automation",
        returns="Lot",
        errors=(
            "funora.capability.unsupported",
            "funora.domain.not_found",
            "funora.auth.session_expired",
            "funora.transport",
        ),
    ),
    "lots.list_own": Operation(
        name="lots.list_own",
        capability="lots.list_own",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="OwnLotsPage",
        errors=(
            "funora.auth.session_expired",
            "funora.protocol.changed",
            "funora.protocol.unexpected_response",
            "funora.transport",
        ),
    ),
    "lots.promote": Operation(
        name="lots.promote",
        capability="lots.promote",
        safety=Safety.UNSAFE,
        request_class="automation",
        returns="RaiseResult",
        errors=(
            "funora.capability.unsupported",
            "funora.domain.precondition_failed",
            "funora.transport.timeout",
            "funora.transport",
        ),
    ),
    "lots.showcase": Operation(
        name="lots.showcase",
        capability="lots.showcase",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="ShowcasePage",
        errors=(
            "funora.auth.session_expired",
            "funora.protocol.changed",
            "funora.transport",
        ),
    ),
    "lots.update_price": Operation(
        name="lots.update_price",
        capability="lots.update_price",
        safety=Safety.IDEMPOTENT,
        request_class="automation",
        returns="Lot",
        errors=(
            "funora.capability.unsupported",
            "funora.domain.precondition_failed",
            "funora.domain.not_found",
            "funora.auth.session_expired",
            "funora.transport",
        ),
        audit="before_state",
        audit_fail_closed=True,
    ),
    "market.offers": Operation(
        name="market.offers",
        capability="market.offers",
        safety=Safety.SAFE,
        request_class="monitoring",
        returns="Offer[]",
        errors=(
            "funora.protocol.changed",
            "funora.transport.rate_limited",
            "funora.transport",
        ),
    ),
    "market.snapshot": Operation(
        name="market.snapshot",
        capability="market.snapshot",
        safety=Safety.SAFE,
        request_class="monitoring",
        returns="MarketSnapshot",
        errors=(
            "funora.protocol.changed",
            "funora.transport.rate_limited",
            "funora.transport",
        ),
    ),
    "orders.get": Operation(
        name="orders.get",
        capability="orders.get",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="OrderView",
        errors=(
            "funora.domain.not_found",
            "funora.auth.session_expired",
            "funora.protocol.changed",
            "funora.transport",
        ),
    ),
    "orders.list": Operation(
        name="orders.list",
        capability="orders.list",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="OrdersPage",
        errors=(
            "funora.auth.session_expired",
            "funora.protocol.changed",
            "funora.protocol.unexpected_response",
            "funora.transport",
        ),
    ),
    "reviews.get": Operation(
        name="reviews.get",
        capability="reviews.get",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="ReviewsPage",
        errors=(
            "funora.auth.session_expired",
            "funora.protocol.changed",
            "funora.transport",
        ),
    ),
    "session.health": Operation(
        name="session.health",
        capability="account.profile",
        safety=Safety.SAFE,
        request_class="poll",
        returns="SessionHealth",
        errors=(
            "funora.auth.session_expired",
            "funora.auth.access_blocked",
            "funora.auth.challenge_required",
            "funora.transport",
        ),
    ),
}
