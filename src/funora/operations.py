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
        request_provenance (str): Своими ли глазами мы видели тот
            запрос, который операция отправляет.

            Ось отдельная от возможности, и путать их нельзя.
            Возможность говорит о ПЛОЩАДКЕ - есть ли у аккаунта
            право. Происхождение говорит о НАС.

            Пустая строка означает, что операция стоит целиком на
            нашем наблюдении. Значение third_party_report означает,
            что часть запроса известна от независимой реализации
            того же протокола, а нашего наблюдения на ней нет.
        provenance_source (str): Кто именно сообщил. Обязателен при
            third_party_report: без имени сообщение неотличимо от
            выдумки, и проверить его нечем.
        provenance_rests_on (str): Какая ИМЕННО часть запроса не
            проверена нами. Без этого читающий переносит недоверие
            либо на всё сразу, либо ни на что.
    """

    name: str
    capability: str
    safety: Safety
    request_class: str
    returns: str
    errors: tuple[str, ...]
    audit: str = ""
    audit_fail_closed: bool = False
    request_provenance: str = ""
    provenance_source: str = ""
    provenance_rests_on: str = ""


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
    "account.switch_currency": Operation(
        name="account.switch_currency",
        capability="account.switch_currency",
        safety=Safety.IDEMPOTENT,
        request_class="automation",
        returns="CurrencySwitch",
        errors=(
            "funora.capability.unsupported",
            "funora.auth.session_expired",
            "funora.protocol.changed",
            "funora.transport",
        ),
        request_provenance="third_party_report",
        provenance_source="FunPayAPI (бот FunPayCardinal), account.py, get_exchange_rate.",
        provenance_rests_on=(
            "Адрес, имена полей запроса и ключи ответа. НАБЛЮДЁН НАМИ только сам переключатель в "
            "шапке: пункты его несут код ISO 4217 в data-cy, и перечисляют они ПРОЧИЕ валюты, не "
            "текущую. То есть мы видели, какие коды бывают, и не видели, как их отправить."
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
    "chats.buyer_viewing": Operation(
        name="chats.buyer_viewing",
        capability="chats.buyer_viewing",
        safety=Safety.SAFE,
        request_class="poll",
        returns="BuyerViewing[]",
        errors=(
            "funora.capability.unsupported",
            "funora.auth.session_expired",
            "funora.protocol.changed",
            "funora.transport",
        ),
        request_provenance="third_party_report",
        provenance_source=(
            "FunPayAPI (бот FunPayCardinal), account.py, get_buyers_viewing и "
            "__parse_buyer_viewing."
        ),
        provenance_rests_on=(
            "ТОЛЬКО ОТВЕТ. Подписка наблюдена НАМИ - объект вида c-p-u лежит в семи наших записях "
            "канала, и состав его известен: признак, идентификатор из восьми цифр, метка из "
            "восьми знаков.\nНе наблюдено, ЧТО приходит в ответ на такую подписку. Известно от "
            "сторонней реализации: при пустом признаке покупатель не смотрит ничего, иначе внутри "
            "лежит разметка со ссылкой на лот."
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
        returns="ChatHistory",
        errors=(
            "funora.capability.unsupported",
            "funora.state.cursor_incompatible",
            "funora.protocol.unexpected_response",
            "funora.transport",
        ),
        request_provenance="third_party_report",
        provenance_source=(
            "FunPayAPI (бот FunPayCardinal), account.py, get_chat_history - адрес chat/history, "
            "параметры node и last_message, заголовок x-requested-with, и разбор ответа по ключам "
            "chat.messages с полями id, author, html."
        ),
        provenance_rests_on=(
            "ВЕСЬ ЗАПРОС ЦЕЛИКОМ, а не вывод о нём: этой точки мы не наблюдали ни разу. Наш "
            "собственный способ прочитать переписку - страница /chat/?node=, и она отдаёт только "
            "то, что площадка показала сразу. Чужое здесь и адрес, и имена обоих параметров, и "
            "форма ответа.\nОтдельно чужим остаётся НАПРАВЛЕНИЕ: что курсор отдаёт сообщения "
            "СТАРШЕ него, а не младше. Именно это утверждение операция проверяет сама - см. "
            "примечание о сверке."
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
        request_provenance="third_party_report",
        provenance_source=(
            "FunPayAPI (бот FunPayCardinal), account.py, send_message - флаг leave_as_unread "
            "убирает диалог из подписки, и это единственное, чем он отличается."
        ),
        provenance_rests_on=(
            "НЕ форма запроса, а ВЫВОД о его действии. Форма наша: наши записи показывают, что "
            "страница при открытом диалоге опрашивает канал с подпиской на него. Чужое - "
            "утверждение, что именно подписка снимает пометку непрочитанного."
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
    "chips.calculate_prices": Operation(
        name="chips.calculate_prices",
        capability="chips.calculate_prices",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="PriceCalculation",
        errors=(
            "funora.capability.unsupported",
            "funora.auth.session_expired",
            "funora.protocol.changed",
            "funora.transport",
        ),
        request_provenance="third_party_report",
        provenance_source="FunPayAPI (бот FunPayCardinal), account.py, calc.",
        provenance_rests_on=(
            "Адрес, имена полей запроса и ключи ответа. НАБЛЮДЁН НАМИ только итог расчёта на "
            "странице правки лота - таблица .table-buyers-prices, которую эта же точка и "
            "перерисовывает - но на странице ЛОТА, не чипов. Здесь непроверено и то, и другое."
        ),
    ),
    "chips.offers": Operation(
        name="chips.offers",
        capability="chips.offers",
        safety=Safety.SAFE,
        request_class="monitoring",
        returns="ChipsPage",
        errors=(
            "funora.capability.unsupported",
            "funora.protocol.changed",
            "funora.transport.rate_limited",
            "funora.transport",
        ),
    ),
    "lots.activate": Operation(
        name="lots.activate",
        capability="lots.activate",
        safety=Safety.IDEMPOTENT,
        request_class="automation",
        returns="LotForm",
        errors=(
            "funora.capability.unsupported",
            "funora.domain.not_found",
            "funora.auth.session_expired",
            "funora.transport",
        ),
        request_provenance="third_party_report",
        provenance_source="FunPayAPI (бот FunPayCardinal), types.py, LotFields.renew_fields.",
        provenance_rests_on=(
            "Вид запроса при СНЯТОМ флажке active. Оба наших снимка сохранения сняты с "
            "отмеченным; сторонний источник шлёт поле всегда, при выключенном лоте - пустой "
            "строкой, тогда как наше рассуждение говорило, что снятый флажок не уходит вовсе."
        ),
    ),
    "lots.calculate_prices": Operation(
        name="lots.calculate_prices",
        capability="lots.calculate_prices",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="PriceCalculation",
        errors=(
            "funora.capability.unsupported",
            "funora.auth.session_expired",
            "funora.protocol.changed",
            "funora.transport",
        ),
        request_provenance="third_party_report",
        provenance_source="FunPayAPI (бот FunPayCardinal), account.py, calc.",
        provenance_rests_on=(
            "Адрес, имена полей запроса и ключи ответа. НАБЛЮДЁН НАМИ только итог расчёта на "
            "странице правки лота - таблица .table-buyers-prices, которую эта же точка и "
            "перерисовывает. То есть мы видели, ЧТО она возвращает, и не видели, КАК её спросить."
        ),
    ),
    "lots.deactivate": Operation(
        name="lots.deactivate",
        capability="lots.deactivate",
        safety=Safety.IDEMPOTENT,
        request_class="automation",
        returns="LotForm",
        errors=(
            "funora.capability.unsupported",
            "funora.domain.not_found",
            "funora.auth.session_expired",
            "funora.transport",
        ),
        request_provenance="third_party_report",
        provenance_source="FunPayAPI (бот FunPayCardinal), types.py, LotFields.renew_fields.",
        provenance_rests_on=(
            "Вид запроса при СНЯТОМ флажке active. Оба наших снимка сохранения сняты с "
            "отмеченным; сторонний источник шлёт поле всегда, при выключенном лоте - пустой "
            "строкой, тогда как наше рассуждение говорило, что снятый флажок не уходит вовсе."
        ),
    ),
    "lots.form": Operation(
        name="lots.form",
        capability="lots.form",
        safety=Safety.SAFE,
        request_class="automation",
        returns="LotForm",
        errors=(
            "funora.capability.unsupported",
            "funora.domain.not_found",
            "funora.auth.session_expired",
            "funora.protocol.changed",
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
        returns="LotForm",
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
        returns="MarketPage",
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
    "orders.details": Operation(
        name="orders.details",
        capability="orders.details",
        safety=Safety.SAFE,
        request_class="interactive",
        returns="OrderDetailsBatch",
        errors=(
            "funora.capability.unsupported",
            "funora.auth.session_expired",
            "funora.protocol.changed",
            "funora.transport",
        ),
        request_provenance="third_party_report",
        provenance_source="FunPayAPI (бот FunPayCardinal), account.py, get_orders_by_ids.",
        provenance_rests_on=(
            "ВСЁ. Ни адреса, ни полей запроса, ни ключей ответа мы не наблюдали ни разу - ни в "
            "снимках, ни в записях запросов, ни в пробах.\nЭто отличает точку от прочих на "
            "вторичном источнике: у поднятия наблюдён запрос, у отзыва - два поля из четырёх, у "
            "расчёта цены - итог на странице. Здесь не наблюдено ничего."
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
    "orders.refund": Operation(
        name="orders.refund",
        capability="orders.refund",
        safety=Safety.UNSAFE,
        request_class="automation",
        returns="RefundResult",
        errors=(
            "funora.capability.unsupported",
            "funora.domain.precondition_failed",
            "funora.domain.not_found",
            "funora.auth.session_expired",
            "funora.transport",
        ),
        request_provenance="third_party_report",
        provenance_source="FunPayAPI (бот FunPayCardinal), account.py, refund.",
        provenance_rests_on=(
            "НЕ ЗАПРОС, А ЧТЕНИЕ ОТВЕТА. Оба поля запроса наблюдены НАМИ: форма возврата лежит на "
            "странице заказа, и в ней csrf_token и id. Адрес - в её же атрибуте action.\nНе "
            "наблюдено, ЧЕМ площадка отвечает. Известно от независимой реализации: объект с "
            "признаком отказа error и сообщением msg."
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
    "reviews.leave": Operation(
        name="reviews.leave",
        capability="reviews.leave",
        safety=Safety.IDEMPOTENT,
        request_class="automation",
        returns="ReviewResult",
        errors=(
            "funora.capability.unsupported",
            "funora.domain.not_found",
            "funora.auth.session_expired",
            "funora.transport",
        ),
        request_provenance="third_party_report",
        provenance_source=(
            "FunPayAPI (бот FunPayCardinal), account.py, send_review и delete_review."
        ),
        provenance_rests_on=(
            "Состав полей запроса и форма ответа. Два поля из четырёх - номер заказа и "
            "идентификатор автора - наблюдены НАМИ: они лежат атрибутами data-order и data-author "
            "на странице заказа и уже читаются разбором. Не наблюдены имена полей запроса, адрес "
            "и то, что приходит в ответ."
        ),
    ),
    "reviews.remove": Operation(
        name="reviews.remove",
        capability="reviews.remove",
        safety=Safety.IDEMPOTENT,
        request_class="automation",
        returns="ReviewResult",
        errors=(
            "funora.capability.unsupported",
            "funora.domain.not_found",
            "funora.auth.session_expired",
            "funora.transport",
        ),
        request_provenance="third_party_report",
        provenance_source=(
            "FunPayAPI (бот FunPayCardinal), account.py, send_review и delete_review."
        ),
        provenance_rests_on=(
            "Состав полей запроса и форма ответа. Два поля из четырёх - номер заказа и "
            "идентификатор автора - наблюдены НАМИ: они лежат атрибутами data-order и data-author "
            "на странице заказа и уже читаются разбором. Не наблюдены имена полей запроса, адрес "
            "и то, что приходит в ответ."
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
